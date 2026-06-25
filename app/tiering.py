def calculate_variant_tier(gene: str, alteration: str, patient_doid: str, evidence_record) -> tuple[str, str]:
    """
    Apply the AMP/ASCO/CAP variant tiering guidelines based on disease DOID:
    - EXACT MATCH (DOID matches): Return the exact Tier and Level from the database.
    - MISMATCH TIER 1 (DOID mismatch, but DB record is Tier 1): Return Tier 2, Level C.
    - MISMATCH TIER 2 (DOID mismatch, but DB record is Tier 2): Return Tier 3, Level VUS.
    - NO MATCH (No record found in DB / None): Return Tier 3, Level VUS.
    
    Returns: (tier_str, level_str)
    """
    if not evidence_record:
        return "Tier 3", "Level VUS"
        
    db_doid = getattr(evidence_record, "doid", "").strip().upper()
    patient_doid_clean = patient_doid.strip().upper()
    
    # EXACT MATCH
    if db_doid == patient_doid_clean:
        return getattr(evidence_record, "tier", "Tier 3"), getattr(evidence_record, "level", "Level VUS")
        
    # DOID MISMATCH
    db_tier = getattr(evidence_record, "tier", "Tier 3")
    if "1" in db_tier:
        return "Tier 2", "Level C"
    elif "2" in db_tier:
        return "Tier 3", "Level VUS"
        
    return "Tier 3", "Level VUS"
