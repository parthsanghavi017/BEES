import os
import sys
import json
import traceback
from sqlalchemy.orm import Session
import cyvcf2

# Add the bin/ directory to the import path so the annotation wrapper is importable
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
from bees_annotator import annotate_vcf as _run_annotation

from app.database import SessionLocal, ClinicalCase

IMPACT_SCORES = {
    "HIGH": 4,
    "MODERATE": 3,
    "LOW": 2,
    "MODIFIER": 1
}

def load_driver_genes_set():
    """
    Loads unique gene symbols from References/Driver-Genes.tsv
    """
    driver_genes = set()
    base_dir = os.path.dirname(os.path.abspath(__file__))
    tsv_path = os.path.join(os.path.dirname(base_dir), "References", "Driver-Genes.tsv")
    if os.path.exists(tsv_path):
        try:
            with open(tsv_path, "r") as f:
                header = f.readline()
                for line in f:
                    parts = line.strip().split("\t")
                    if parts and parts[0]:
                        driver_genes.add(parts[0].strip().upper())
        except Exception as e:
            print(f"Error loading driver genes in pipeline: {e}")
    return driver_genes

def get_depth_and_af(record, sample_idx=0):
    """
    Extracts Depth (DP) and Allele Frequency (AF) for the given sample index,
    supporting Mutect2, Sentieon, and Illumina formats.
    """
    dp = None
    fmt_fields = record.FORMAT
    if fmt_fields is not None:
        if 'DP' in fmt_fields:
            dp_val = record.format('DP')
            if dp_val is not None and len(dp_val) > sample_idx:
                dp = int(dp_val[sample_idx][0])
        elif 'DPT' in fmt_fields:
            dp_val = record.format('DPT')
            if dp_val is not None and len(dp_val) > sample_idx:
                dp = int(dp_val[sample_idx][0])
        elif 'DPC' in fmt_fields:
            dp_val = record.format('DPC')
            if dp_val is not None and len(dp_val) > sample_idx:
                dp = int(dp_val[sample_idx][0])

    af = None
    if fmt_fields is not None:
        if 'AF' in fmt_fields:
            af_val = record.format('AF')
            if af_val is not None and len(af_val) > sample_idx:
                val = af_val[sample_idx]
                if hasattr(val, '__len__') or isinstance(val, (list, tuple)):
                    af = float(val[0])
                else:
                    af = float(val)
        elif 'VF' in fmt_fields:
            vf_val = record.format('VF')
            if vf_val is not None and len(vf_val) > sample_idx:
                val = vf_val[sample_idx]
                if hasattr(val, '__len__') or isinstance(val, (list, tuple)):
                    af = float(val[0])
                else:
                    af = float(val)

        # If AF is not directly defined, compute it using AD/ADT/ADC
        if af is None:
            ad_vals = None
            if 'AD' in fmt_fields:
                ad_vals = record.format('AD')
            elif 'ADT' in fmt_fields:
                ad_vals = record.format('ADT')
            elif 'ADC' in fmt_fields:
                ad_vals = record.format('ADC')

            if ad_vals is not None and len(ad_vals) > sample_idx:
                val = ad_vals[sample_idx]
                if isinstance(val, str):
                    parts = [p.strip() for p in val.split(',')]
                    try:
                        ints = [int(p) for p in parts if p != '.' and p != '']
                    except ValueError:
                        ints = []
                elif isinstance(val, (list, tuple)) or hasattr(val, '__iter__'):
                    ints = []
                    for p in val:
                        try:
                            if isinstance(p, str):
                                ints.extend([int(x) for x in p.split(',') if x != '.' and x != ''])
                            else:
                                ints.append(int(p))
                        except (ValueError, TypeError):
                            pass
                else:
                    ints = []

                if len(ints) >= 2:
                    ref_count = ints[0]
                    alt_count = ints[1]
                    total_ad = ref_count + alt_count
                    if total_ad > 0:
                        af = float(alt_count) / float(total_ad)
                        if dp is None:
                            dp = total_ad

    # Global Fallbacks
    if dp is None:
        dp_info = record.INFO.get('DP')
        if dp_info is not None:
            dp = int(dp_info)
        else:
            dp = 0
    if af is None:
        af = 0.0

    return dp, af

def parse_highest_impact_ann(ann_field_str):
    """
    Parses the ANN field string from the annotation engine and returns the details of the 
    most deleterious transcript annotation based on the IMPACT score.
    
    ANN Format: Allele|Annotation|Impact|GeneName|GeneID|FeatureType|FeatureID|TranscriptBiotype|ExonRank/Total|...
    Returns: (impact_score, impact_str, consequence_str, gene_name, cdna_change, protein_change, transcript_id, exon_rank)
    """
    if not ann_field_str:
        return (0, "MODIFIER", ".", "Unknown", ".", ".", ".", ".")
        
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
                "consequence": parts[1].strip() if parts[1].strip() else ".",
                "gene": parts[3].strip(),
                "cdna": parts[9].strip() if parts[9].strip() else ".",
                "protein": parts[10].strip() if parts[10].strip() else ".",
                "transcript_id": parts[6].strip() if parts[6].strip() else ".",
                "exon": parts[8].strip() if parts[8].strip() else "."
            }
            
    if best_ann:
        return (highest_score, best_ann["impact"], best_ann["consequence"], best_ann["gene"], best_ann["cdna"], best_ann["protein"], best_ann["transcript_id"], best_ann["exon"])
    return (0, "MODIFIER", ".", "Unknown", ".", ".", ".", ".")

def run_variant_pipeline(case_id: int):
    """
    Asynchronous genomic processing task. Executes variant annotation, impact filtering,
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
    case.status_message = "Initiating variant curation pipeline..."
    db.commit()

    vcf_in_path = case.vcf_path
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Intermediate VCF files
    annotation_out = vcf_in_path + ".annotated_temp.vcf"
    filtered_impact_out = vcf_in_path + ".impact.vcf"
    vcfanno_out = vcf_in_path + ".vcfanno.vcf"

    try:
        # Load driver genes set
        driver_genes_set = load_driver_genes_set()

        # 1. Variant Annotation
        # Map transcript database preference to the serialized annotation database
        db_ser = "References/bees_ensembl_hg38.ser" if case.transcript_db == "Ensembl" else "References/bees_refseq_hg38.ser"
        db_ser_path = os.path.join(os.path.dirname(base_dir), db_ser)

        if not os.path.exists(db_ser_path):
            raise FileNotFoundError(f"Annotation database not found: {db_ser}")

        # Delegate entirely to the annotation wrapper — implementation is internal
        _run_annotation(vcf_in_path, annotation_out, db_ser_path)

        if not os.path.exists(annotation_out):
            raise FileNotFoundError("Annotation execution completed but output VCF was not created.")

        # 2. Parse & Quality/Impact Filtering using cyvcf2
        vcf_reader = cyvcf2.VCF(annotation_out)
        vcf_writer = cyvcf2.Writer(filtered_impact_out, vcf_reader)
        
        total_count = 0
        passing_impact_count = 0
        for record in vcf_reader:
            total_count += 1
            
            # Enforce filter constraint: only PASS or .
            filter_val = record.FILTER
            is_pass = False
            if filter_val is None:
                is_pass = True
            elif isinstance(filter_val, str):
                is_pass = filter_val.strip() in ("", ".", "PASS")
            elif isinstance(filter_val, (list, tuple)):
                is_pass = len(filter_val) == 0 or all(x in ("", ".", "PASS") for x in filter_val)
                
            if not is_pass:
                continue

            ann_field = record.INFO.get("ANN")
            score, impact, _, gene, _, _, _, _ = parse_highest_impact_ann(ann_field)
            
            # Filter criteria: keep only HIGH (score 4) and MODERATE (score 3)
            if score >= 3:
                vcf_writer.write_record(record)
                passing_impact_count += 1
                
        vcf_reader.close()
        vcf_writer.close()

        # If no variants passed the filter, we can skip vcfanno
        surviving_variants = []
        if passing_impact_count > 0:
            # 3. vcfanno Annotation Subprocess (gnomAD AF)
            vcfanno_config = os.path.join(base_dir, "vcfanno_config.toml")
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
            
            # Determine tumor sample index dynamically
            samples = vcf_anno_reader.samples
            tumor_idx = 0
            if len(samples) > 1:
                for idx, s in enumerate(samples):
                    if 'tumor' in s.lower() or 'tumour' in s.lower():
                        tumor_idx = idx
                        break
                else:
                    for idx, s in enumerate(samples):
                        if 'normal' in s.lower() or 'germline' in s.lower() or 'control' in s.lower():
                            tumor_idx = 1 - idx
                            break
            
            for record in vcf_anno_reader:
                # Extract gnomad_af value
                gnomad_af = record.INFO.get("gnomad_af")
                
                # Check allele frequency bounds
                if gnomad_af is not None:
                    if isinstance(gnomad_af, (list, tuple)):
                        af_val = float(gnomad_af[0])
                    else:
                        af_val = float(gnomad_af)
                else:
                    af_val = 0.0  # Novel variant

                # Parse annotation details for surviving variants
                ann_field = record.INFO.get("ANN")
                _, impact, consequence, gene, cdna, protein, transcript_id, exon = parse_highest_impact_ann(ann_field)
                
                # Filter out common variants (0.01 and above)
                if af_val >= 0.01:
                    continue
                
                # Format HGVSg: Chr:PosRef>Alt (VCF is 1-based, cyvcf2 POS is 0-based)
                alt_allele = record.ALT[0] if record.ALT else "."
                hgvsg = f"{record.CHROM}:{record.POS + 1}{record.REF}>{alt_allele}"
                
                # Extract sample depth and AF
                depth_val, af_sample_val = get_depth_and_af(record, sample_idx=tumor_idx)
                
                surviving_variants.append({
                    "hgvsg": hgvsg,
                    "gene": gene,
                    "cdna": cdna,
                    "protein": protein,
                    "transcript_type": case.transcript_db,
                    "transcript_id": transcript_id,
                    "impact": impact,
                    "consequence": consequence,
                    "gnomad_af": af_val,
                    "exon": exon,
                    "depth": depth_val,
                    "af": af_sample_val
                })
                
            vcf_anno_reader.close()

        # Save results back to database
        case.filtered_variants = json.dumps(surviving_variants)
        case.total_input_variants = total_count
        case.passed_impact_variants = passing_impact_count
        case.passed_af_variants = len(surviving_variants)
        db.commit()

        # Pre-calculate Tiers and Levels in the background to make loading variants instant
        try:
            from app.main import tier_case_variants
            tier_case_variants(case, db)
        except Exception as te:
            print(f"Error pre-tiering variants in background: {te}")

        case.status = "Completed"
        case.status_message = f"Curation completed successfully. Found {len(surviving_variants)} candidate variants."
        db.commit()

    except Exception as e:
        print(f"Pipeline error for case {case_id}: {str(e)}")
        traceback.print_exc()
        case.status = "Failed"
        case.status_message = f"Error during pipeline execution: {str(e)}"
        db.commit()

    finally:
        # Clean up temporary VCF files for security and storage conservation
        for filepath in [annotation_out, filtered_impact_out, vcfanno_out]:
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception:
                    pass
        db.close()

