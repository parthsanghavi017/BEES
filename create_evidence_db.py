#!/usr/bin/env python
import os
import sys

# Ensure the app folder is in the Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.database import init_evidence_db, EvidenceSessionLocal, LocalEvidence

def parse_tsv_and_populate():
    tsv_path = os.path.join("Var_DB", "Var_DB_24_06.txt")
    if not os.path.exists(tsv_path):
        print(f"Error: Legacy evidence TSV not found at {tsv_path}")
        sys.exit(1)

    print("Initializing encrypted SQLCipher database...")
    init_evidence_db()

    session = EvidenceSessionLocal()
    
    print("Reading and parsing legacy TSV data...")
    count = 0
    try:
        with open(tsv_path, "r", encoding="utf-8") as f:
            header = f.readline()  # skip header line
            
            for line in f:
                if not line.strip():
                    continue
                parts = line.split("\t")
                if len(parts) < 10:
                    continue
                
                # Column index mapping:
                # 0: Gene
                # 1: Alteration_type
                # 2: Alteration
                # 3: Cancer
                # 4: DOID
                # 5: Drug
                # 6: biomarker_type
                # 7: Response
                # 8: Tier
                # 9: Level (Standard level designation A, B, C, D)
                # 10: Level_details / Guidelines
                # 11: PMIDs
                
                gene = parts[0].strip()
                alteration = parts[2].strip()
                cancer = parts[3].strip()
                doid = parts[4].strip()
                drug = parts[5].strip() if parts[5].strip() else None
                biomarker_type = parts[6].strip() if parts[6].strip() else None
                response = parts[7].strip() if parts[7].strip() else None
                
                # Standardize Tier (e.g. "1" -> "Tier 1")
                raw_tier = parts[8].strip()
                if raw_tier in ("1", "Tier 1", "Tier I"):
                    tier = "Tier 1"
                elif raw_tier in ("2", "Tier 2", "Tier II"):
                    tier = "Tier 2"
                elif raw_tier in ("3", "Tier 3", "Tier III"):
                    tier = "Tier 3"
                else:
                    tier = f"Tier {raw_tier}" if raw_tier else "Tier 3"

                # Standardize Level (e.g. "A" -> "Level A")
                raw_level = parts[9].strip()
                if len(raw_level) == 1 and raw_level.upper() in ("A", "B", "C", "D"):
                    level = f"Level {raw_level.upper()}"
                elif raw_level.upper().startswith("LEVEL "):
                    level = raw_level.strip()
                else:
                    level = f"Level {raw_level}" if raw_level else "Level VUS"

                pmids = parts[11].strip() if len(parts) > 11 and parts[11].strip() else None
                
                evidence = LocalEvidence(
                    gene=gene,
                    alteration=alteration,
                    cancer=cancer,
                    doid=doid,
                    drug=drug,
                    biomarker_type=biomarker_type,
                    response=response,
                    tier=tier,
                    level=level,
                    pmids=pmids
                )
                session.add(evidence)
                count += 1
                
                if count % 1000 == 0:
                    print(f"  Loaded {count} entries...")
                    
        print("Committing loaded records to SQLCipher database...")
        session.commit()
        print(f"Successfully loaded and encrypted {count} local evidence items!")
    except Exception as e:
        session.rollback()
        print(f"Error occurred during population: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        session.close()

if __name__ == "__main__":
    parse_tsv_and_populate()
