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

        for idx, variant in enumerate(confirmed_variants, 1):
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

            if not gene_description:
                gene_description = f"No curated gene description is available in the local knowledgebase for {gene_name}."

            # Pre-filter and remap evidence records
            raw_evidence = variant.get("evidence_json", [])
            cleaned_evidence = cls.filter_and_remap_evidence(raw_evidence)

            # Format protein change and check if splice variant
            is_splice = consequence and "splice" in consequence.lower()
            p_notation = format_protein_change(protein, consequence)

            # Task 1: Gene Summary (Directly assign CIViC Gene Description without Ollama query)
            if p_notation:
                gene_summaries.append(f"### Variant {idx} ({gene_name} {p_notation}):\n{gene_description}")
            else:
                gene_summaries.append(f"### Variant {idx} ({gene_name}):\n{gene_description}")

            # Task 2: Variant Summary
            splice_status = ""
            if is_splice:
                if "+" in cdna:
                    splice_status = "splice donor site"
                elif "-" in cdna:
                    splice_status = "splice acceptor site"
                else:
                    splice_status = "splice site"

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
            )
            if is_splice:
                task2_prompt += f"- Splice Status: {splice_status}\n"
                task2_prompt += f"- Note: Indicate that the variant is a splice variant and state its status ({splice_status}). Do not show a protein change (p.) notation.\n"
            else:
                task2_prompt += f"- Protein Change (HGVSp): {p_notation}\n"
                task2_prompt += f"- Note: Show the protein change ({p_notation}) without brackets.\n"

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
                if p_notation:
                    variant_narratives.append(f"### Variant {idx} ({gene_name} {p_notation}):\n{var_narrative}")
                else:
                    variant_narratives.append(f"### Variant {idx} ({gene_name}):\n{var_narrative}")
            except Exception as e:
                logger.error(f"Ollama Task 2 failed for variant {hgvsg}: {e}")
                if p_notation:
                    variant_narratives.append(f"### Variant {idx} ({gene_name} {p_notation}):\n[Error synthesizing variant narrative: {e}]")
                else:
                    variant_narratives.append(f"### Variant {idx} ({gene_name}):\n[Error synthesizing variant narrative: {e}]")

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

                if p_notation:
                    evidence_summaries.append(f"### Variant {idx} ({gene_name} {p_notation}) Evidence:\n" + "\n\n".join(ev_blocks))
                else:
                    evidence_summaries.append(f"### Variant {idx} ({gene_name}) Evidence:\n" + "\n\n".join(ev_blocks))
            else:
                if p_notation:
                    evidence_summaries.append(f"### Variant {idx} ({gene_name} {p_notation}) Evidence:\nNo accepted level A, B, or D evidence items found.")
                else:
                    evidence_summaries.append(f"### Variant {idx} ({gene_name}) Evidence:\nNo accepted level A, B, or D evidence items found.")

        return {
            "gene_analysis": "\n\n".join(gene_summaries),
            "variant_narrative": "\n\n".join(variant_narratives),
            "evidence_records": "\n\n".join(evidence_summaries)
        }
