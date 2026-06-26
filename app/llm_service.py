import json
import logging
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from civicpy import civic
import ollama
logger = logging.getLogger(__name__)



def format_protein_change(protein: str, consequence: str = "") -> str:
    if consequence and "splice" in consequence.lower():
        return ""
    if not protein:
        return ""
    clean = protein.strip()
    has_prefix = clean.lower().startswith("p.")
    if has_prefix:
        clean = clean[2:]
    clean = clean.replace("(", "").replace(")", "").replace("[", "").replace("]", "")
    clean = clean.strip()
    if clean in ("?", "", "unknown") or "?" in clean:
        return ""
    return f"p.{clean}"


def parse_local_gene_description(gene_name: str) -> str:
    import os
    import re
    app_dir = os.path.dirname(os.path.abspath(__file__))
    gene_desc_dir = os.path.join(os.path.dirname(app_dir), "Gene_Desc")
    file_path = os.path.join(gene_desc_dir, f"{gene_name.upper().strip()}.txt")
    if not os.path.exists(file_path):
        return ""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        significance_pattern = re.compile(
            r"##\s*Clinical\s+Significance\s*\n(.*?)(?=\n##|$)", 
            re.IGNORECASE | re.DOTALL
        )
        significance_match = significance_pattern.search(content)
        if not significance_match:
            return ""
        significance_text = significance_match.group(1).strip()
        references_pattern = re.compile(
            r"##\s*References\s*\n(.*)", 
            re.IGNORECASE | re.DOTALL
        )
        references_match = references_pattern.search(content)
        appropriate_references = []
        if references_match:
            references_text = references_match.group(1).strip()
            ref_lines = [line.strip() for line in references_text.split("\n") if line.strip()]
            for line in ref_lines:
                key_match = re.search(r"\(([^)]+)\)", line)
                if key_match:
                    ref_key = key_match.group(1)
                    if ref_key in significance_text:
                        appropriate_references.append(line)
        renumbered_references = []
        for idx, ref in enumerate(appropriate_references, 1):
            ref_cleaned = ref
            bracket_match = re.match(r"^\[\s*\d+\.\s+(\([^)]+\))", ref)
            nobracket_match = re.match(r"^\d+\.\s+(\([^)]+\))", ref)
            if bracket_match:
                ref_cleaned = re.sub(r"^\[\s*\d+\.\s+", f"[{idx}. ", ref)
            elif nobracket_match:
                ref_cleaned = re.sub(r"^\d+\.\s+", f"{idx}. ", ref)
            renumbered_references.append(ref_cleaned)
        output = f"## Clinical Significance\n{significance_text}"
        if renumbered_references:
            output += "\n\n## References\n" + "\n\n".join(renumbered_references)
        return output
    except Exception as e:
        logger.error(f"Error parsing local gene description for {gene_name}: {e}")
        return ""


class LlmSynthesisService:
    @staticmethod
    def filter_and_remap_evidence(evidence_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Deterministic data filtering and remapping layer:
        - Status Filter: Only allow records where status is 'accepted' (case-insensitive).
        - Evidence Level Remapping: Retain A and B. Remap C to D. Discard E.
        """
        cleaned_evidence = []
        for e in evidence_list:
            # 1. Status Filter
            # If status is not present, default to 'accepted' (e.g. for local db curated rows)
            status = str(e.get("status", "accepted")).strip().lower()
            if status != "accepted":
                logger.info(f"Silently discarding evidence with status '{status}'")
                continue

            # 2. Level Remapping
            level_raw = str(e.get("level", "")).strip().upper()
            
            # Extract letter (e.g., 'LEVEL C' -> 'C', 'C' -> 'C')
            level_letter = level_raw.replace("LEVEL", "").strip()
            
            if level_letter in ("A", "B"):
                # Retain as-is
                pass
            elif level_letter == "C":
                # Remap C to D
                if level_raw.startswith("LEVEL"):
                    e["level"] = "Level D"
                else:
                    e["level"] = "D"
                logger.info("Remapped Evidence Level C to D")
            elif level_letter == "E":
                # Discard E
                logger.info("Discarded Evidence Level E")
                continue
            
            cleaned_evidence.append(e)
            
        return cleaned_evidence

    @classmethod
    async def synthesize_report(cls, case_id: int, confirmed_variants: List[Dict[str, Any]]) -> Dict[str, str]:
        """
        Asynchronously calls local Ollama medgemma:4b model to synthesize clinical summaries.
        Applies strict system prompts to prevent hallucinations.
        """
        if not confirmed_variants:
            return {
                "gene_analysis": "No confirmed variants selected for this report.",
                "variant_narrative": "No confirmed variants selected for this report.",
                "evidence_records": "No confirmed variants selected for this report."
            }

        # Summaries are reserved exclusively for Tier 1 and Tier 2 variants. Filter out Tier 3 / VUS.
        synthesis_variants = []
        for v in confirmed_variants:
            t_str = str(v.get("tier", "")).lower()
            if "tier 1" in t_str or "tier 2" in t_str:
                synthesis_variants.append(v)

        if not synthesis_variants:
            return {
                "gene_analysis": "No Tier 1 or 2 variants confirmed.",
                "variant_narrative": "No Tier 1 or 2 variants confirmed.",
                "evidence_records": "No Tier 1 or 2 variants confirmed."
            }

        gene_summaries = []
        variant_narratives = []
        evidence_summaries = []

        # System prompt to restrict model to synthesis and prevent clinical claim hallucinations
        system_instruction = (
            "You are a Senior Clinical Bioinformatics AI assistant. Your task is to synthesize the provided genomic "
            "data and clinical facts into clear, professional report narratives. You are strictly forbidden from "
            "making any clinical claims, assumptions, explanations, or statements that are not explicitly present in "
            "the input data. Do not address 'Why' questions or speculate on mechanisms. Rely ONLY on the provided text "
            "and facts. If the input is empty or insufficient, write a concise summary reflecting only the available facts."
        )

        client = ollama.AsyncClient(host="http://127.0.0.1:11434")

        for idx, variant in enumerate(synthesis_variants, 1):
            gene_name = variant.get("gene", "Unknown Gene")
            hgvsg = variant.get("hgvsg", "Unknown HGVSg")
            cdna = variant.get("cdna", "Unknown HGVSc")
            protein = variant.get("protein", "Unknown HGVSp")
            impact = variant.get("impact", "Unknown Impact")
            consequence = variant.get("consequence", "Unknown Consequence")
            transcript_id = variant.get("transcript_id", "Unknown Transcript")
            transcript_type = variant.get("transcript_type", "Unknown Feature Type")
            exon = variant.get("exon", "Unknown Exon")
            
            # Fetch Gene Description from civicpy
            gene_description = ""
            try:
                civic_gene = civic.get_gene_by_name(gene_name.upper().strip())
                if civic_gene and hasattr(civic_gene, "description") and civic_gene.description:
                    gene_description = civic_gene.description
            except Exception as e:
                logger.error(f"Failed to fetch gene description from civicpy for {gene_name}: {e}")

            # Fallback to live GraphQL query if description not found in local cache
            if not gene_description:
                try:
                    gene_description = fetch_civic_gene_description_live(gene_name)
                except Exception as e:
                    logger.error(f"Failed to fetch live gene description for {gene_name}: {e}")

            # Fallback to local Gene_Desc folder if civic description is empty
            if not gene_description:
                try:
                    gene_description = parse_local_gene_description(gene_name)
                except Exception as e:
                    logger.error(f"Failed to parse local gene description for {gene_name}: {e}")

            if not gene_description:
                gene_description = f"No curated gene description is available in the local knowledgebase for {gene_name}."

            # Pre-filter and remap evidence records
            raw_evidence = variant.get("evidence_json", [])
            cleaned_evidence = cls.filter_and_remap_evidence(raw_evidence)

            # Format protein change and check if splice variant
            is_splice = consequence and "splice" in consequence.lower()
            p_notation = format_protein_change(protein, consequence)
            c_notation = cdna.strip() if cdna else ""

            # Determine the variant title header pattern (splice: Gene c.; non-splice: Gene p. c.)
            if is_splice:
                header_detail = f"{gene_name} {c_notation}".strip()
            elif p_notation:
                header_detail = f"{gene_name} {p_notation} {c_notation}".strip()
            else:
                header_detail = f"{gene_name} {c_notation}".strip()

            # Task 1: Gene Summary (Directly assign CIViC Gene Description without Ollama query)
            gene_summaries.append(f"### Variant {idx} ({header_detail}):\n{gene_description}")

            # Task 2: Variant Summary
            splice_status = ""
            if is_splice:
                if "+" in cdna:
                    splice_status = "splice donor site"
                elif "-" in cdna:
                    splice_status = "splice acceptor site"
                else:
                    splice_status = "splice site"

            # Format AF as percentage for prompt
            af_val = variant.get("af", 0.0)
            if isinstance(af_val, str):
                try:
                    af_val = float(af_val)
                except ValueError:
                    af_val = 0.0
            af_pct = f"{af_val * 100:.2f}%"

            task2_prompt = (
                f"Task: Convert the following structured mutational data into a clean, professional, grammatically "
                f"sound narrative description suitable for a clinical molecular pathology report. "
                f"You must NOT include any chromosomal coordinates, genomic positions, or specific nucleotide changes "
                f"(such as 'G>A' or 'G to A') in the narrative.\n\n"
                f"Inputs:\n"
                f"- Gene: {gene_name}\n"
                f"- Transcript ID: {transcript_id}\n"
                f"- Feature Type: {transcript_type}\n"
                f"- Consequence: {consequence}\n"
                f"- Impact: {impact}\n"
                f"- Exon: {exon}\n"
                f"- Allele Frequency: {af_pct}\n"
            )
            if is_splice:
                task2_prompt += f"- Splice Status: {splice_status}\n"
                task2_prompt += f"- Note: Indicate that the variant is a splice variant and state its status ({splice_status}). Do not show a protein change (p.) notation. Include the Allele Frequency ({af_pct}) in the narrative.\n"
            else:
                task2_prompt += f"- Protein Change (HGVSp): {p_notation}\n"
                task2_prompt += f"- Note: Show the protein change ({p_notation}) without brackets. Include the Allele Frequency ({af_pct}) in the narrative.\n"

            task2_prompt += "\nGenerate a professional clinical narrative summarizing these variant metrics under the constraints specified."

            try:
                # Task 2 query
                t2_response = await client.chat(
                    model="medgemma:4b",
                    messages=[
                        {"role": "system", "content": system_instruction},
                        {"role": "user", "content": task2_prompt}
                    ]
                )
                var_narrative = t2_response["message"]["content"].strip()
                variant_narratives.append(f"### Variant {idx} ({header_detail}):\n{var_narrative}")
            except Exception as e:
                logger.error(f"Ollama Task 2 failed for variant {hgvsg}: {e}")
                variant_narratives.append(f"### Variant {idx} ({header_detail}):\n[Error synthesizing variant narrative: {e}]")

            # Task 3: Evidence Summary
            if cleaned_evidence:
                ev_blocks = []
                for ev_idx, ev in enumerate(cleaned_evidence, 1):
                    citation_id = ev.get("pmids", "OncoKB")
                    description = ev.get("description", "")
                    if not description:
                        # Fallback description if none is present (e.g. from local db)
                        description = f"Variant tier is {ev.get('tier')} with level {ev.get('level')} for {ev.get('cancer')} treatment with {ev.get('drug')} yielding {ev.get('response')}."

                    status = ev.get("status", "accepted")
                    ev_type = ev.get("biomarker_type", "None")
                    direction = ev.get("evidence_direction", "SUPPORTS")
                    significance = ev.get("response", "None")
                    disease = ev.get("cancer", "Unknown Cancer")
                    therapies = ev.get("drug", "None")

                    task3_prompt = (
                        f"Task: Generate a clinical summary narrative by rewriting the provided evidence description. "
                        f"Sourcing from the provided CIViC evidence description, rewrite it into a cohesive clinical summary "
                        f"weaving in the Citation ID ({citation_id}), status, type, direction, significance, disease, and therapies "
                        f"without altering the underlying clinical observation or adding any external claims.\n\n"
                        f"Inputs:\n"
                        f"- Citation ID: {citation_id}\n"
                        f"- Raw CIViC Evidence Description: {description}\n"
                        f"- Status: {status}\n"
                        f"- Evidence Type: {ev_type}\n"
                        f"- Evidence Direction: {direction}\n"
                        f"- Clinical Significance: {significance}\n"
                        f"- Disease: {disease}\n"
                        f"- Therapies: {therapies}\n\n"
                        f"Generate a single, cohesive clinical narrative by rewriting the raw description."
                    )

                    try:
                        t3_response = await client.chat(
                            model="medgemma:4b",
                            messages=[
                                {"role": "system", "content": system_instruction},
                                {"role": "user", "content": task3_prompt}
                            ]
                        )
                        ev_summary = t3_response["message"]["content"].strip()
                        ev_blocks.append(f"**Evidence {ev_idx} [{citation_id}]:** {ev_summary}")
                    except Exception as e:
                        logger.error(f"Ollama Task 3 failed for evidence {citation_id}: {e}")
                        ev_blocks.append(f"**Evidence {ev_idx} [{citation_id}]:** [Error generating summary: {e}]")

                evidence_summaries.append(f"### Variant {idx} ({header_detail}) Evidence:\n" + "\n\n".join(ev_blocks))
            else:
                evidence_summaries.append(f"### Variant {idx} ({header_detail}) Evidence:\nNo accepted level A, B, or D evidence items found.")

        return {
            "gene_analysis": "\n\n".join(gene_summaries),
            "variant_narrative": "\n\n".join(variant_narratives),
            "evidence_records": "\n\n".join(evidence_summaries)
        }
