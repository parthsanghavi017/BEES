import logging
import re
from typing import List, Dict, Any
from civicpy import civic

logger = logging.getLogger(__name__)

def preload_civic_cache():
    """
    Preload civicpy cache into memory on server startup to make first-time queries instant.
    """
    try:
        logger.info("Initializing civicpy cache loading...")
        civic.load_cache()
        logger.info("civicpy cache loaded successfully.")
    except Exception as e:
        logger.error(f"Failed to preload civicpy cache: {e}")

def map_civic_level_to_tier_level(civic_level: str) -> tuple[str, str]:
    """
    Maps CIViC level designation (A, B, C, D) to standard AMP/ASCO/CAP tier/level labels.
    """
    level_upper = civic_level.strip().upper() if civic_level else ""
    if level_upper == "A":
        return "Tier 1", "Level A"
    elif level_upper == "B":
        return "Tier 1", "Level B"
    elif level_upper == "C":
        return "Tier 2", "Level C"
    elif level_upper == "D":
        return "Tier 2", "Level D"
    return "Tier 3", "Level VUS"

def standardize_pmids(pmids_str: str) -> str:
    """
    Standardizes PMIDs from database or CIViC to be semicolon-separated and prefixed with PMID:.
    """
    if not pmids_str or pmids_str.strip() in ("", ";", "None", "none"):
        return "OncoKB"
    
    # Split by comma or semicolon
    raw_list = re.split(r'[,;]', pmids_str)
    standardized = []
    for p in raw_list:
        p_clean = p.strip()
        if not p_clean or p_clean.upper() in ("", ";", "NONE"):
            standardized.append("OncoKB")
        elif p_clean.isdigit():
            standardized.append(f"PMID:{p_clean}")
        else:
            p_upper = p_clean.upper()
            if p_upper.startswith("PMID:"):
                suffix = p_clean[5:].strip()
                standardized.append(f"PMID:{suffix}")
            else:
                standardized.append(p_clean)
                
    if not standardized:
        return "OncoKB"
    return ";".join(standardized)

def normalize_alt(alt: str) -> str:
    """
    Normalizes alteration designations for uniform comparisons.
    """
    if not alt:
        return ""
    alt = alt.strip().upper()
    if alt.startswith("P.") or alt.startswith("C."):
        alt = alt[2:]
    alt = alt.replace("(", "").replace(")", "").replace("[", "").replace("]", "")
    return alt.strip()

def matches_variant(var_gene: str, var_protein: str, var_cdna: str, var_consequence: str, var_exon: str, db_alt: str, db_aliases: list = None, db_hgvs: list = None) -> bool:
    """
    Applies ontology tier rules to match exact, codon-level, exon-level, and generic database annotations.
    """
    norm_protein = normalize_alt(var_protein)
    norm_cdna = normalize_alt(var_cdna)
    norm_db = normalize_alt(db_alt)
    
    # 1. Exact match on protein or cDNA alteration string
    if norm_protein and norm_protein == norm_db:
        return True
    if norm_cdna and norm_cdna == norm_db:
        return True
        
    # 2. Exact match in aliases or HGVS expressions (mostly for CIViC)
    if db_aliases:
        for alias in db_aliases:
            norm_alias = normalize_alt(alias)
            if (norm_protein and norm_protein == norm_alias) or (norm_cdna and norm_cdna == norm_alias):
                return True
    if db_hgvs:
        for hgvs in db_hgvs:
            norm_hgvs = normalize_alt(hgvs)
            if (norm_protein and norm_protein == norm_hgvs) or (norm_cdna and norm_cdna == norm_hgvs):
                return True
            # Substring match for full transcripts
            if norm_cdna and norm_cdna in norm_hgvs:
                return True
            if norm_protein and norm_protein in norm_hgvs:
                return True



    # 6. Codon-level match
    # Extract codon number from protein change (e.g., V272M -> 272)
    codon_match = re.search(r'\d+', norm_protein)
    if codon_match:
        codon_num = codon_match.group()
        db_codon_match = re.search(r'\d+', norm_db)
        if db_codon_match and db_codon_match.group() == codon_num:
            # Check that it's not a different specific amino acid change (e.g. V272L vs V272M)
            is_generic_db = any(word in norm_db for word in ["MUT", "CODON", "ALTERATION"]) or norm_db == codon_num or norm_db == f"V{codon_num}" or norm_db == f"P{codon_num}" or norm_db == f"R{codon_num}"
            is_codon_only = re.match(r'^[A-Z]?\d+$', norm_db) is not None
            if is_generic_db or is_codon_only:
                return True

    # 7. Exon-level match (e.g. EXON 12 MUTATION)
    exon_db_match = re.search(r'EXON\s*(\d+)', norm_db)
    if exon_db_match:
        db_exon = int(exon_db_match.group(1))
        if var_exon and var_exon != "." and var_exon != "None":
            var_exon_match = re.search(r'\d+', str(var_exon))
            if var_exon_match:
                var_exon_num = int(var_exon_match.group())
                # Allow +/- 1 exon discrepancy due to different transcripts (RefSeq vs Ensembl)
                if abs(db_exon - var_exon_num) <= 1:
                    return True

    # 8. Generic/Mutation Type match (e.g., MISSENSE, FRAMESHIFT, SPLICE, LOSS-OF-FUNCTION, or generic MUTATION)
    db_clean = norm_db.replace("VARIANT", "").replace("MUTATION", "").replace("CHANGE", "").strip()
    conseq_lower = var_consequence.lower() if var_consequence else ""
    
    if db_clean in ("MUT", "MUTATION"):
        return True
        
    if "MISSENSE" in db_clean:
        if "missense" in conseq_lower:
            return True
            
    if "FRAMESHIFT" in db_clean or "FS" in db_clean:
        if "frameshift" in conseq_lower:
            return True
            
    if "SPLICE" in db_clean:
        if "splice" in conseq_lower:
            return True
            
    if "LOSS-OF-FUNCTION" in db_clean or "LOSS OF FUNCTION" in db_clean or "LOF" in db_clean:
        # Standard LOF consequences
        if any(term in conseq_lower for term in ["frameshift", "stop_gained", "splice_donor", "splice_acceptor"]):
            return True

    return False

def fetch_civic_evidence(gene: str, protein: str, cdna: str, consequence: str = "", exon: str = "") -> List[Dict[str, Any]]:
    """
    Query variant evidence using civicpy locally from cache.
    Returns all matching evidence items using exact, codon, exon, and generic rules.
    """
    results = []
    if not gene:
        return results

    try:
        # Fetch the gene by HGNC symbol from local cache
        try:
            civic_gene = civic.get_gene_by_name(gene.strip().upper())
        except Exception:
            return results

        if not civic_gene or not civic_gene.variants:
            return results

        # Iterate over all variants of the gene in CIViC
        for v in civic_gene.variants:
            aliases = v.aliases if hasattr(v, "aliases") else []
            hgvs = getattr(v, "hgvs_expressions", [])
            
            # Check if this CIViC variant matches our patient's variant
            if matches_variant(gene, protein, cdna, consequence, exon, v.name, aliases, hgvs):
                mp = v.single_variant_molecular_profile
                if not mp or not mp.evidence_items:
                    continue
                    
                for e in mp.evidence_items:
                    # Skip rejected evidence items
                    if hasattr(e, "status") and e.status.lower() == "rejected":
                        continue

                    # Extract disease name and DOID
                    disease_name = "None"
                    doid = "DOID:162"  # fallback
                    if e.disease:
                        disease_name = e.disease.name
                        if hasattr(e.disease, "doid") and e.disease.doid:
                            doid = f"DOID:{e.disease.doid}"

                    # Extract therapies
                    drugs_list = [t.name for t in e.therapies] if e.therapies else []
                    drug = ", ".join(drugs_list) if drugs_list else "None"

                    # Extract PubMed ID / source
                    pmid = "None"
                    if e.source:
                        if hasattr(e.source, "source_type") and e.source.source_type.upper() == "PUBMED":
                            pmid = f"PMID:{e.source.citation_id}"
                        elif hasattr(e.source, "citation") and e.source.citation:
                            pmid = e.source.citation
                    pmid = standardize_pmids(pmid)
                    first_pmid = pmid.split(";")[0].strip() if pmid else "OncoKB"
                    if not first_pmid:
                        first_pmid = "OncoKB"

                    # Map evidence type and significance
                    biomarker_type = getattr(e, "evidence_type", "None")
                    if biomarker_type:
                        biomarker_type = biomarker_type.lower()
                    
                    response = getattr(e, "significance", "None")

                    # Map Tier and Level
                    civic_level = getattr(e, "evidence_level", "")
                    tier, level = map_civic_level_to_tier_level(civic_level)
                    eid = f"EID{e.id}" if hasattr(e, "id") and e.id else ""

                    results.append({
                        "tier": tier,
                        "level": level,
                        "biomarker_type": biomarker_type,
                        "cancer": disease_name,
                        "doid": doid,
                        "drug": drug,
                        "response": response,
                        "pmids": first_pmid,
                        "source": "CIViC",
                        "description": getattr(e, "description", ""),
                        "eid": eid
                    })
    except Exception as ex:
        logger.error(f"civicpy query failed for {gene} {protein} {cdna}: {ex}")

    return results
