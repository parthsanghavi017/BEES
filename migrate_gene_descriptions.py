import os
import re
import sys
from app.database import init_evidence_db, EvidenceSessionLocal, LocalGeneDescription

def migrate():
    # Make sure tables are created
    init_evidence_db()
    
    session = EvidenceSessionLocal()
    
    gene_desc_dir = "Gene_Desc"
    if not os.path.exists(gene_desc_dir):
        print(f"Error: Gene_Desc folder not found at {gene_desc_dir}")
        sys.exit(1)
        
    files = [f for f in os.listdir(gene_desc_dir) if f.endswith(".txt")]
    print(f"Found {len(files)} files to migrate.")
    
    significance_pattern = re.compile(
        r"##\s*Clinical\s+Significance\s*\n(.*?)(?=\n##|$)", 
        re.IGNORECASE | re.DOTALL
    )
    references_pattern = re.compile(
        r"##\s*References\s*\n(.*)", 
        re.IGNORECASE | re.DOTALL
    )
    
    count = 0
    skipped = 0
    for filename in files:
        gene_name = filename[:-4].upper().strip() # remove .txt
        file_path = os.path.join(gene_desc_dir, filename)
        
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
                
            significance_match = significance_pattern.search(content)
            if not significance_match:
                skipped += 1
                continue
            clinical_desc = significance_match.group(1).strip()
            
            references_match = references_pattern.search(content)
            references = references_match.group(1).strip() if references_match else None
            
            # Check if already exists, update or skip
            existing = session.query(LocalGeneDescription).filter(LocalGeneDescription.gene == gene_name).first()
            if existing:
                existing.clinical_desc = clinical_desc
                existing.references = references
            else:
                new_desc = LocalGeneDescription(
                    gene=gene_name,
                    clinical_desc=clinical_desc,
                    references=references
                )
                session.add(new_desc)
            count += 1
            if count % 200 == 0:
                print(f"Processed {count} genes...")
        except Exception as e:
            print(f"Error parsing {filename}: {e}")
            
    print(f"Committing {count} gene descriptions (skipped {skipped})...")
    session.commit()
    print("Done migration!")
    session.close()

if __name__ == "__main__":
    migrate()
