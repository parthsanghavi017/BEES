import os
import json
import traceback
import subprocess
from sqlalchemy.orm import Session
import cyvcf2

from app.database import SessionLocal, ClinicalCase

IMPACT_SCORES = {
    "HIGH": 4,
    "MODERATE": 3,
    "LOW": 2,
    "MODIFIER": 1
}

def parse_highest_impact_ann(ann_field_str):
    """
    Parses the ANN field string from Jannovar and returns the details of the 
    most deleterious transcript annotation based on the IMPACT score.
    
    ANN Format: Allele|Annotation|Impact|GeneName|GeneID|FeatureType|FeatureID|...
    Returns: (impact_score, impact_str, gene_name, cdna_change, protein_change, transcript_id)
    """
    if not ann_field_str:
        return (0, "MODIFIER", "Unknown", ".", ".", ".")
        
    highest_score = -1
    best_ann = None
    
    # ANN can have multiple transcript effects separated by commas
    for ann in ann_field_str.split(","):
        parts = ann.split("|")
        if len(parts) < 11:
            continue
            
        impact = parts[2].strip().upper()
        score = IMPACT_SCORES.get(impact, 0)
        
        if score > highest_score:
            highest_score = score
            best_ann = {
                "impact": impact,
                "gene": parts[3].strip(),
                "cdna": parts[9].strip() if parts[9].strip() else ".",
                "protein": parts[10].strip() if parts[10].strip() else ".",
                "transcript_id": parts[6].strip() if parts[6].strip() else "."
            }
            
    if best_ann:
        return (highest_score, best_ann["impact"], best_ann["gene"], best_ann["cdna"], best_ann["protein"], best_ann["transcript_id"])
    return (0, "MODIFIER", "Unknown", ".", ".", ".")

def run_variant_pipeline(case_id: int):
    """
    Asynchronous genomic processing task. Executes Jannovar annotation, impact filtering,
    vcfanno frequency annotation, and frequency filtering. Saves results to the DB case.
    
    Security Measures:
    - Runs subprocesses with strict list arguments (no shell=True).
    - Writes intermediate files to a secure localized storage directory.
    - Cleans up intermediate VCF files after completion to prevent data egress and clutter.
    """
    db: Session = SessionLocal()
    case = db.query(ClinicalCase).filter(ClinicalCase.id == case_id).first()
    if not case:
        db.close()
        return

    # Update state to Processing
    case.status = "Processing"
    case.status_message = None
    db.commit()

    vcf_in_path = case.vcf_path
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Intermediate VCF files
    jannovar_out = vcf_in_path + ".jannovar.vcf"
    filtered_impact_out = vcf_in_path + ".impact.vcf"
    vcfanno_out = vcf_in_path + ".vcfanno.vcf"

    try:
        # 1. Jannovar Annotation Subprocess
        # Map transcript database preference to the serialized database file
        db_ser = "References/ensembl_91_hg38.ser" if case.transcript_db == "Ensembl" else "References/refseq_109_hg38.ser"
        db_ser_path = os.path.join(os.path.dirname(base_dir), db_ser)
        jannovar_jar = os.path.join(os.path.dirname(base_dir), "jannovar-cli-0.36.jar")
        
        if not os.path.exists(db_ser_path):
            raise FileNotFoundError(f"Jannovar serialized database not found: {db_ser_path}")
        if not os.path.exists(jannovar_jar):
            raise FileNotFoundError(f"Jannovar CLI jar not found: {jannovar_jar}")

        jannovar_cmd = [
            "java", "-jar", jannovar_jar, "annotate-vcf",
            "-i", vcf_in_path,
            "-o", jannovar_out,
            "-d", db_ser_path,
            "--report-no-progress"
        ]

        # Secure subprocess execution
        subprocess.run(jannovar_cmd, capture_output=True, text=True, check=True)

        if not os.path.exists(jannovar_out):
            raise FileNotFoundError("Jannovar execution completed but output VCF was not created.")

        # 2. Parse & Impact Filtering (HIGH/MODERATE) using cyvcf2
        # Discards LOW/MODIFIER variant impacts and outputs to filtered_impact_out VCF
        vcf_reader = cyvcf2.VCF(jannovar_out)
        
        # We must add the ANN info field header if writing output
        vcf_writer = cyvcf2.Writer(filtered_impact_out, vcf_reader)
        
        passing_impact_count = 0
        for record in vcf_reader:
            ann_field = record.INFO.get("ANN")
            score, impact, _, _, _, _ = parse_highest_impact_ann(ann_field)
            
            # Filter criteria: keep only HIGH (score 4) and MODERATE (score 3)
            if score >= 3:
                vcf_writer.write_record(record)
                passing_impact_count += 1
                
        vcf_reader.close()
        vcf_writer.close()

        # If no variants passed the impact filter, we can skip vcfanno
        surviving_variants = []
        if passing_impact_count > 0:
            # 3. vcfanno Annotation Subprocess (gnomAD AF)
            vcfanno_config = os.path.join(base_dir, "vcfanno_config.toml")
            # In Conda env, vcfanno is in bin directory. We invoke it directly
            vcfanno_cmd = [
                "vcfanno",
                "-p", "1",
                vcfanno_config,
                filtered_impact_out
            ]

            with open(vcfanno_out, "w") as f_out:
                subprocess.run(
                    vcfanno_cmd,
                    stdout=f_out,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=True
                )

            # 4. Allele Frequency Filtering (gnomad_af <= 0.01)
            vcf_anno_reader = cyvcf2.VCF(vcfanno_out)
            
            for record in vcf_anno_reader:
                # Extract gnomad_af value
                gnomad_af = record.INFO.get("gnomad_af")
                
                # Check allele frequency bounds
                if gnomad_af is not None:
                    # gnomad_af can be returned as float or a list/tuple for multi-allelic sites
                    if isinstance(gnomad_af, (list, tuple)):
                        af_val = float(gnomad_af[0])
                    else:
                        af_val = float(gnomad_af)
                        
                    if af_val > 0.01:
                        continue  # Discard common variants
                else:
                    af_val = 0.0  # Novel variant

                # Parse annotation details for surviving variants
                ann_field = record.INFO.get("ANN")
                _, impact, gene, cdna, protein, transcript_id = parse_highest_impact_ann(ann_field)
                
                # Format HGVSg: Chr:PosRef>Alt (VCF is 1-based, cyvcf2 POS is 0-based)
                # Take the first ALT allele
                alt_allele = record.ALT[0] if record.ALT else "."
                hgvsg = f"{record.CHROM}:{record.POS + 1}{record.REF}>{alt_allele}"
                
                surviving_variants.append({
                    "hgvsg": hgvsg,
                    "gene": gene,
                    "cdna": cdna,
                    "protein": protein,
                    "transcript_type": case.transcript_db,
                    "transcript_id": transcript_id,
                    "impact": impact,
                    "gnomad_af": af_val
                })
                
            vcf_anno_reader.close()

        # Save results back to database
        case.filtered_variants = json.dumps(surviving_variants)
        case.status = "Completed"
        case.status_message = f"Analysis completed successfully. Found {len(surviving_variants)} candidate variants."
        db.commit()

    except Exception as e:
        print(f"Pipeline error for case {case_id}: {str(e)}")
        traceback.print_exc()
        case.status = "Failed"
        case.status_message = f"Error during pipeline execution: {str(e)}"
        db.commit()

    finally:
        # Clean up temporary VCF files for security and storage conservation
        for filepath in [jannovar_out, filtered_impact_out, vcfanno_out]:
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception:
                    pass
        db.close()
