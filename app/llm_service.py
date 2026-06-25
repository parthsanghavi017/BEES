import json
import logging
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from civicpy import civic
import ollama

logger = logging.getLogger(__name__)

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
            
            # Extract chromosome and genomic change details from HGVSg (e.g. 17:7673803G>A)
            chromosome = "Unknown"
            genomic_change = hgvsg
            if ":" in hgvsg:
                parts = hgvsg.split(":")
                chromosome = parts[0]
                genomic_change = parts[1]

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

            # Task 1: Gene Summary
            task1_prompt = (
                f"Task: Synthesize a precise, context-aware biological summary explaining the basic function of "
                f"the gene {gene_name} in the context of this specific variant type ({consequence}, {impact} impact).\n\n"
                f"Inputs:\n"
                f"- Gene: {gene_name}\n"
                f"- Variant: {hgvsg} ({protein})\n"
                f"- Consequence: {consequence}\n"
                f"- Exon: {exon}\n"
                f"- Gene Description Reference:\n{gene_description}\n\n"
                f"Generate a biological summary using ONLY the reference text. Do not create new facts."
            )

            # Task 2: Variant Summary
            task2_prompt = (
                f"Task: Convert the following structured mutational data into a clean, professional, grammatically "
                f"sound narrative description suitable for a clinical molecular pathology report.\n\n"
                f"Inputs:\n"
                f"- Chromosome: {chromosome}\n"
                f"- Genomic Change (HGVSg): {hgvsg}\n"
                f"- cDNA Change (HGVSc): {cdna}\n"
                f"- Protein Change (HGVSp): {protein}\n"
                f"- Transcript ID: {transcript_id}\n"
                f"- Feature Type: {transcript_type}\n"
                f"- Consequence: {consequence}\n"
                f"- Impact: {impact}\n\n"
                f"Generate a professional clinical narrative summarizing these variant metrics."
            )

            # Query Ollama for Task 1 & 2
            try:
                # Task 1 query
                t1_response = await client.chat(
                    model="medgemma:4b",
                    messages=[
                        {"role": "system", "content": system_instruction},
                        {"role": "user", "content": task1_prompt}
                    ]
                )
                gene_summary = t1_response["message"]["content"].strip()
                gene_summaries.append(f"### Variant {idx} ({gene_name} {protein}):\n{gene_summary}")
            except Exception as e:
                logger.error(f"Ollama Task 1 failed for variant {hgvsg}: {e}")
                gene_summaries.append(f"### Variant {idx} ({gene_name} {protein}):\n[Error synthesizing gene summary: {e}]")

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
                variant_narratives.append(f"### Variant {idx} ({gene_name} {protein}):\n{var_narrative}")
            except Exception as e:
                logger.error(f"Ollama Task 2 failed for variant {hgvsg}: {e}")
                variant_narratives.append(f"### Variant {idx} ({gene_name} {protein}):\n[Error synthesizing variant narrative: {e}]")

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
                        f"Task: Generate a cohesive clinical summary narrative for the following evidence entry. "
                        f"Seamlessly weave together the Citation ID and description while explicitly integrating the "
                        f"evidence type, status, and direction without altering the underlying raw clinical observation.\n\n"
                        f"Inputs:\n"
                        f"- Citation ID: {citation_id}\n"
                        f"- Evidence Description: {description}\n"
                        f"- Status: {status}\n"
                        f"- Evidence Type: {ev_type}\n"
                        f"- Evidence Direction: {direction}\n"
                        f"- Clinical Significance: {significance}\n"
                        f"- Disease: {disease}\n"
                        f"- Therapies: {therapies}\n\n"
                        f"Generate a single, cohesive clinical narrative."
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

                evidence_summaries.append(f"### Variant {idx} ({gene_name} {protein}) Evidence:\n" + "\n\n".join(ev_blocks))
            else:
                evidence_summaries.append(f"### Variant {idx} ({gene_name} {protein}) Evidence:\nNo accepted level A, B, or D evidence items found.")

        return {
            "gene_analysis": "\n\n".join(gene_summaries),
            "variant_narrative": "\n\n".join(variant_narratives),
            "evidence_records": "\n\n".join(evidence_summaries)
        }
