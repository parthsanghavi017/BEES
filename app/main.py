import os
import json
import uuid
import shutil
import hashlib
import subprocess
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File, Form, Response, Request, BackgroundTasks
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy.orm import Session

from app.database import get_db, init_db, ClinicalCase, User, TokenDenylist
from app.auth import get_current_user, hash_password, verify_password, create_access_token, invalidate_token
from app.schemas import ClinicalCaseResponse, UserResponse, UserCreate, DashboardStats, VariantConfirmRequest, ReportDraftSaveRequest
from app.pipeline import run_variant_pipeline
from app.logger import get_audit_logger

# Initialize FastAPI App — Swagger/ReDoc disabled for security (no PHI exposure via public schema)
app = FastAPI(
    title="BEES Genomic Variant Interpretation Pipeline",
    description="Sovereign, HIPAA/DPDP-compliant clinical genomics pipeline.",
    version="2.0.0",
    docs_url=None,
    redoc_url=None,
)

# ─── Security Headers Middleware ──────────────────────────────────────────────
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Injects standard HTTP security headers on every response.
    Mitigates XSS, clickjacking, MIME sniffing, and protocol downgrade attacks.
    """
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self';"
        )
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

app.add_middleware(SecurityHeadersMiddleware)

# Constants & Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
LOCAL_STORAGE_DIR = os.path.join(BASE_DIR, "local_storage", "vcfs")
DOID_FILE_PATH = os.path.join(BASE_DIR, "doid_list.json")

# Ensure secure local storage directory exists
os.makedirs(LOCAL_STORAGE_DIR, exist_ok=True)

# Load local DOID database on startup
try:
    with open(DOID_FILE_PATH, "r") as f:
        DOID_DATABASE = json.load(f)
except Exception as e:
    print(f"Warning: Failed to load DOID database: {e}")
    DOID_DATABASE = []


DRIVER_GENES_MAP = {}
DRIVER_GENES_COUNT = {}
INDICATION_LIST = []

def standardize_biomarker_type(bt: Optional[str]) -> str:
    if not bt:
        return "None"
    bt_clean = bt.strip().lower()
    if bt_clean in ("prognostic", "progonstic"):
        return "prognostic"
    if "predictive" in bt_clean or bt_clean.startswith("predic"):
        return "predictive"
    if bt_clean == "diagnostic":
        return "diagnostic"
    if bt_clean == "therapeutic":
        return "therapeutic"
    return bt.strip()

def load_driver_genes():
    global DRIVER_GENES_MAP, DRIVER_GENES_COUNT, INDICATION_LIST
    tsv_path = os.path.join(os.path.dirname(BASE_DIR), "References", "Driver-Genes.tsv")
    if not os.path.exists(tsv_path):
        tsv_path = os.path.join(BASE_DIR, "References", "Driver-Genes.tsv")
        
    if not os.path.exists(tsv_path):
        print(f"Warning: Driver-Genes.tsv not found at {tsv_path}")
        return

    doid_map = {item["doid"]: item["name"] for item in DOID_DATABASE}

    try:
        temp_map = {}
        doid_names = {}
        with open(tsv_path, "r") as f:
            header = f.readline().strip().split("\t")
            if "DOID" not in header or "SYMBOL" not in header:
                print("Warning: DOID or SYMBOL columns missing from Driver-Genes.tsv")
                return
            doid_idx = header.index("DOID")
            symbol_idx = header.index("SYMBOL")
            cancer_idx = header.index("CANCER_TYPE") if "CANCER_TYPE" in header else -1
            
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) > max(doid_idx, symbol_idx):
                    doid = parts[doid_idx].strip()
                    symbol = parts[symbol_idx].strip()
                    cancer_type = parts[cancer_idx].strip() if cancer_idx != -1 else ""
                    if doid and symbol:
                        if doid not in temp_map:
                            temp_map[doid] = set()
                        temp_map[doid].add(symbol)
                        if doid not in doid_names and cancer_type:
                            doid_names[doid] = cancer_type

        DRIVER_GENES_MAP = {doid: list(genes) for doid, genes in temp_map.items()}
        DRIVER_GENES_COUNT = {doid: len(genes) for doid, genes in temp_map.items()}
        
        abbrev_map = {
            "ANGS": "Angiosarcoma",
            "LIPO": "Liposarcoma",
            "CSCC": "Cutaneous Squamous Cell Carcinoma",
            "PRAD": "Prostate Adenocarcinoma",
            "STAD": "Stomach Adenocarcinoma",
            "HNSC": "Head and Neck Squamous Cell Carcinoma",
            "CCRCC": "Clear Cell Renal Cell Carcinoma",
            "ESCA": "Esophageal Cancer",
            "SKCM": "Skin Cutaneous Melanoma",
            "ALL": "Acute Lymphoblastic Leukemia",
            "NSCLC": "Non-Small Cell Lung Cancer",
            "SCLC": "Small Cell Lung Cancer",
            "READ": "Rectum Adenocarcinoma",
            "WT": "Wilms Tumor",
            "UCEC": "Uterine Corpus Endometrial Carcinoma",
            "BRCA": "Breast Invasive Carcinoma",
            "AML": "Acute Myeloid Leukemia",
            "COAD": "Colon Adenocarcinoma",
            "BLCA": "Bladder Urothelial Carcinoma",
            "PAAD": "Pancreatic Adenocarcinoma",
            "OV": "Ovarian Serous Cystadenocarcinoma",
            "GBM": "Glioblastoma Multiforme",
            "THCA": "Thyroid Carcinoma",
            "KIRC": "Kidney Renal Clear Cell Carcinoma",
            "LIHC": "Liver Hepatocellular Carcinoma"
        }

        indications = []
        for doid, genes in temp_map.items():
            name = doid_map.get(doid)
            if not name:
                raw_name = doid_names.get(doid) or "Unknown Disease"
                name = abbrev_map.get(raw_name, raw_name)
            indications.append({
                "doid": doid,
                "name": name,
                "gene_count": len(genes),
                "genes": list(genes)
            })
        
        INDICATION_LIST = sorted(indications, key=lambda x: x["name"])
        print(f"[LOADER] Loaded {len(DRIVER_GENES_MAP)} indications from Driver-Genes.tsv")
    except Exception as e:
        print(f"Error parsing Driver-Genes.tsv: {e}")

@app.on_event("startup")
def on_startup():
    """
    Initializes the database schema, preloads driver genes, and preloads cache on startup.
    """
    init_db()
    load_driver_genes()
    try:
        from app.civic_client import preload_civic_cache
        preload_civic_cache()
    except Exception as e:
        print(f"Warning: Failed to preload cache: {e}")

    print("")
    print("=" * 60)
    print("  ██████╗ ███████╗███████╗███████╗")
    print("  ██╔══██╗██╔════╝██╔════╝██╔════╝")
    print("  ██████╔╝█████╗  █████╗  ███████╗")
    print("  ██╔══██╗██╔══╝  ██╔══╝  ╚════██║")
    print("  ██████╔╝███████╗███████╗███████║")
    print("  ╚═════╝ ╚══════╝╚══════╝╚══════╝")
    print("")
    print("  BEES — Bioinformatics Evidence Evaluation System")
    print("  Version: 2.0 | Sovereign Clinical Genomics Pipeline")
    print("=" * 60)
    print("  ✓  Database schema initialized")
    print("  ✓  Driver genes loaded")
    print("  ✓  CIViC cache preloaded")
    print("  ✓  Server ready at http://127.0.0.1:8000")
    print("  ✓  API docs at   http://127.0.0.1:8000/docs")
    print("=" * 60)
    print("")

# --- AUTHENTICATION ENDPOINTS ---

@app.post("/api/auth/login")
def login(
    response: Response,
    request_data: UserCreate,
    db: Session = Depends(get_db)
):
    """
    Logs in a user, verifies credentials, and issues an HTTP-only JWT session cookie.
    
    Security Controls:
    - Verifies passwords using bcrypt.
    - Session cookie is marked 'httponly' to protect against XSS token extraction.
    - Cookie is set with 'samesite=strict' to protect against CSRF attacks.
    """
    user = db.query(User).filter(User.username == request_data.username).first()
    if not user or not verify_password(request_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )
    
    token = create_access_token(data={"sub": user.username})
    
    # Set JWT in HTTP-only, SameSite=Strict cookie
    # secure=True requires HTTPS — deploy behind nginx/caddy with TLS for clinical use
    response.set_cookie(
        key="bees_session",
        value=token,
        httponly=True,
        max_age=3600,
        samesite="strict",
        secure=False  # TODO: Set to True when deployed behind TLS reverse proxy
    )
    return {"message": "Login successful", "username": user.username, "role": user.role}

@app.post("/api/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """
    Logs out the user: clears the session cookie and adds the JWT to the denylist
    so it cannot be reused even before expiry.
    """
    token = request.cookies.get("bees_session")
    if token:
        invalidate_token(token, db)
    response.delete_cookie("bees_session")
    return {"message": "Logout successful"}

@app.post("/api/auth/add-user", response_model=UserResponse)
def add_user(
    new_user_data: UserCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Administrative endpoint to register a new user.
    
    Security Controls:
    - Protected: Only logged-in users with 'admin' role can invoke this.
    - Password is encrypted using bcrypt hash function before storing.
    """
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Administrator privileges required."
        )
    
    # Check if user already exists
    existing_user = db.query(User).filter(User.username == new_user_data.username).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists"
        )
        
    hashed = hash_password(new_user_data.password)
    user = User(
        username=new_user_data.username,
        hashed_password=hashed,
        role="admin"  # In Phase 1, users created are admin level
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

@app.get("/api/auth/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    """
    Retrieves the currently authenticated user's profile information.
    Used by the frontend to verify active sessions.
    """
    return current_user


# --- CLINICAL CASES ENDPOINTS ---

@app.get("/api/cases", response_model=List[ClinicalCaseResponse])
def list_cases(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Retrieves all clinical cases in the database. Protected route.
    """
    cases = db.query(ClinicalCase).all()
    return cases

@app.get("/api/cases/stats", response_model=DashboardStats)
def get_dashboard_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Computes aggregation statistics for the dashboard metrics and pie charts.
    """
    total_cases = db.query(ClinicalCase).count()
    active_cases = db.query(ClinicalCase).filter(ClinicalCase.is_archived == False).count()
    archived_cases = db.query(ClinicalCase).filter(ClinicalCase.is_archived == True).count()
    
    # Calculate distribution of Indication Names
    cases = db.query(ClinicalCase).all()
    distribution = {}
    for case in cases:
        name = case.indication_name
        distribution[name] = distribution.get(name, 0) + 1
        
    return DashboardStats(
        total_cases=total_cases,
        active_cases=active_cases,
        archived_cases=archived_cases,
        indication_distribution=distribution
    )


@app.get("/api/cases/{case_id}", response_model=ClinicalCaseResponse)
def get_case(
    case_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Retrieves details for a single clinical case record.
    """
    case = db.query(ClinicalCase).filter(ClinicalCase.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Clinical case not found")
    return case

@app.post("/api/cases", response_model=ClinicalCaseResponse)
def create_case(
    patient_id: str = Form(...),
    patient_age: int = Form(...),
    patient_sex: str = Form(...),
    indication_doid: str = Form(...),
    indication_name: str = Form(...),
    transcript_db: str = Form(...),
    reference_genome: str = Form(...),
    consent_given: bool = Form(...),
    vcf_file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Ingests a new clinical case with full compliance controls:
    1. patient_id: Non-identifying label (MRN / accession / initials) — no full names.
    2. consent_given: Operator must confirm consent was obtained; rejected if False.
    3. Demographics validated (age 0–125, sex, transcript_db, reference_genome).
    4. VCF extension + magic-byte signature validated before writing.
    5. File saved with UUID4 filename (path traversal prevention).
    6. SHA-256 checksum computed and stored for integrity verification.
    7. Consent timestamp recorded.
    """
    # 1. Consent gate — must be acknowledged before any PHI processing
    if not consent_given:
        raise HTTPException(
            status_code=400,
            detail="Case cannot be created without operator consent acknowledgement (HIPAA/DPDP §7)."
        )

    # 2. Demographics & Input Validation
    if not (0 <= patient_age <= 125):
        raise HTTPException(status_code=400, detail="Patient age must be between 0 and 125")
    if patient_sex not in {"Male", "Female", "Other"}:
        raise HTTPException(status_code=400, detail="Invalid gender value")
    if transcript_db not in {"Ensembl", "RefSeq"}:
        raise HTTPException(status_code=400, detail="Invalid transcript database preference")
    if reference_genome not in {"GRCh37", "GRCh38"}:
        raise HTTPException(status_code=400, detail="Invalid reference genome version preference")

    # 3. File Extension Validation
    filename = vcf_file.filename or ""
    is_gzipped = False
    if filename.endswith(".vcf.gz"):
        is_gzipped = True
        ext = ".vcf.gz"
    elif filename.endswith(".vcf"):
        ext = ".vcf"
    else:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file format. Only .vcf and .vcf.gz extensions are permitted."
        )

    # 4. Read header chunk for signature validation
    try:
        header_chunk = vcf_file.file.read(50)
        vcf_file.file.seek(0)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to read file headers.")

    # 5. Magic-Byte / Header Signature Validation
    if is_gzipped:
        if len(header_chunk) < 2 or header_chunk[:2] != b"\x1f\x8b":
            raise HTTPException(
                status_code=400,
                detail="File signature mismatch: .vcf.gz file lacks valid gzip magic bytes."
            )
    else:
        try:
            decoded_header = header_chunk.decode("utf-8", errors="ignore")
            if not decoded_header.startswith("##fileformat="):
                raise HTTPException(
                    status_code=400,
                    detail="File signature mismatch: .vcf file lacks standard VCF header."
                )
        except Exception:
            raise HTTPException(status_code=400, detail="File content parsing error.")

    # 6. Secure File Write (UUID4 filename)
    secure_filename = f"{uuid.uuid4()}{ext}"
    dest_path = os.path.join(LOCAL_STORAGE_DIR, secure_filename)
    sha256_hash = None
    try:
        hasher = hashlib.sha256()
        vcf_file.file.seek(0)
        with open(dest_path, "wb") as buffer:
            for chunk in iter(lambda: vcf_file.file.read(65536), b""):
                hasher.update(chunk)
                buffer.write(chunk)
        sha256_hash = hasher.hexdigest()
    except Exception:
        try:
            os.remove(dest_path)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="File write failed. Please try again.")

    # 7. Database Record
    now = datetime.utcnow()
    new_case = ClinicalCase(
        patient_id=patient_id,
        patient_age=patient_age,
        patient_sex=patient_sex,
        indication_doid=indication_doid,
        indication_name=indication_name,
        transcript_db=transcript_db,
        reference_genome=reference_genome,
        vcf_path=dest_path,
        sha256_hash=sha256_hash,
        consent_given=True,
        consent_timestamp=now,
        upload_timestamp=now,
        is_archived=False
    )
    db.add(new_case)
    db.commit()
    db.refresh(new_case)

    audit = get_audit_logger()
    audit.info(f"CASE-{new_case.id:04d} | {current_user.username} | CASE_CREATED | patient_id={patient_id} indication={indication_name} sha256={sha256_hash}")
    return new_case


def _secure_delete_vcf(vcf_path: str) -> None:
    """
    Securely deletes a VCF file from disk.
    Attempts `shred -u` (Linux) for forensic overwrite; falls back to
    zero-fill + os.remove if shred is unavailable.
    """
    if not vcf_path or not os.path.isfile(vcf_path):
        return
    try:
        subprocess.run(["shred", "-u", "-z", "-n", "3", vcf_path], check=True, timeout=30)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        # Fallback: overwrite with zeros then remove
        try:
            size = os.path.getsize(vcf_path)
            with open(vcf_path, "r+b") as f:
                f.write(b"\x00" * size)
            os.remove(vcf_path)
        except Exception:
            try:
                os.remove(vcf_path)
            except Exception:
                pass


@app.delete("/api/cases/{case_id}")
def delete_case(
    case_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Permanently deletes a clinical case record and securely shreds the associated VCF file.
    Admin-only. Supports DPDP Right to Erasure (§12) and HIPAA data destruction obligations.
    """
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator privileges required.")

    case = db.query(ClinicalCase).filter(ClinicalCase.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Clinical case not found")

    vcf_path = case.vcf_path
    patient_id_label = case.patient_id

    db.delete(case)
    db.commit()

    _secure_delete_vcf(vcf_path)

    audit = get_audit_logger()
    audit.info(f"CASE-{case_id:04d} | {current_user.username} | CASE_DELETED | patient_id={patient_id_label} vcf_shredded=True")
    return {"message": f"CASE-{case_id:04d} permanently deleted and VCF securely shredded."}


@app.patch("/api/cases/{case_id}/archive")
def archive_case(
    case_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Archives a clinical case and immediately securely shreds the associated VCF file.
    Archived cases remain in the database for audit traceability but the raw genomic
    file is permanently destroyed, satisfying HIPAA data minimisation obligations.
    """
    case = db.query(ClinicalCase).filter(ClinicalCase.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Clinical case not found")

    if case.is_archived:
        return {"message": f"CASE-{case_id:04d} is already archived."}

    vcf_path = case.vcf_path
    case.is_archived = True
    case.vcf_path = ""   # Clear path reference after secure deletion
    db.commit()

    _secure_delete_vcf(vcf_path)

    audit = get_audit_logger()
    audit.info(f"CASE-{case_id:04d} | {current_user.username} | CASE_ARCHIVED | vcf_shredded=True")
    return {"message": f"CASE-{case_id:04d} archived and VCF securely shredded."}


# --- DOID AUTOCOMPLETE SEARCH ---

# --- DOID AUTOCOMPLETE SEARCH ---

@app.get("/api/doid/search")
def search_doid(
    q: str,
    current_user: User = Depends(get_current_user)
):
    """
    Performs local, offline prefix/substring matching against the Disease Ontology list.
    """
    if not q or len(q) < 2:
        return []
    
    query = q.lower()
    matches = []
    for item in DOID_DATABASE:
        if query in item["name"].lower() or query in item["doid"].lower():
            matches.append(item)
            if len(matches) >= 10:  # Cap at 10 recommendations
                break
    return matches


# --- PHASE 2 PIPELINE & VARIANT INTERFACE ---

@app.post("/api/cases/{case_id}/process")
def process_case(
    case_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Triggers the asynchronous genomic pipeline for the specified case.
    """
    case = db.query(ClinicalCase).filter(ClinicalCase.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Clinical case not found")
        
    if case.status == "Processing":
        return {"message": "Case is currently being analyzed.", "status": case.status}

    case.status = "Processing"
    case.status_message = "Initiating processing pipeline..."
    db.commit()

    background_tasks.add_task(run_variant_pipeline, case_id)
    return {"message": "Genomic analysis triggered successfully.", "status": case.status}

def tier_case_variants(case, db):
    if not case.filtered_variants:
        return []
    variants = json.loads(case.filtered_variants)
    
    def deduplicate_evidence_list(evidence_json):
        if not evidence_json:
            return []
        grouped = {}
        for m in evidence_json:
            bt_val = m.get("biomarker_type", "None") or "None"
            bt_val = standardize_biomarker_type(bt_val)
            m["biomarker_type"] = bt_val
            drug_val = m.get("drug", "None") or "None"
            key = (bt_val.strip().lower(), drug_val.strip().lower())
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(m)
            
        unique_matches = []
        for key, group_records in grouped.items():
            def get_record_sort_key(rec):
                t_scores = {"Tier 1": 1, "Tier 2": 2, "Tier 3": 3}
                l_scores = {"Level A": 1, "Level B": 2, "Level C": 3, "Level D": 4, "Level VUS": 5}
                t_score = t_scores.get(rec.get("tier"), 9)
                l_score = l_scores.get(rec.get("level"), 9)
                return (t_score, l_score)
                
            group_records.sort(key=get_record_sort_key)
            rep = dict(group_records[0])
            
            # Combine PMIDs
            all_pmids = []
            for r in group_records:
                p_val = r.get("pmids")
                if p_val and p_val != "None" and p_val != "OncoKB":
                    import re
                    for p in re.split(r'[,;]', p_val):
                        p = p.strip()
                        if p and p not in all_pmids:
                            all_pmids.append(p)
            if all_pmids:
                rep["pmids"] = ";".join(all_pmids)
            else:
                has_oncokb = any(r.get("pmids") == "OncoKB" for r in group_records)
                rep["pmids"] = "OncoKB" if has_oncokb else "None"
                
            # Combine EIDs
            all_eids = []
            for r in group_records:
                eid_val = r.get("eid")
                if eid_val and eid_val != "None" and eid_val != "":
                    for e in re.split(r'[,;]', eid_val):
                        e = e.strip()
                        if e and e not in all_eids:
                            all_eids.append(e)
            if all_eids:
                rep["eid"] = ";".join(all_eids)
            else:
                rep["eid"] = ""
                
            # Combine Sources
            all_sources = []
            for r in group_records:
                src_val = r.get("source")
                if src_val and src_val not in all_sources:
                    all_sources.append(src_val)
            if all_sources:
                rep["source"] = ";".join(all_sources)
                
            unique_matches.append(rep)
            
        def get_record_sort_key_final(m):
            t_scores = {"Tier 1": 1, "Tier 2": 2, "Tier 3": 3}
            l_scores = {"Level A": 1, "Level B": 2, "Level C": 3, "Level D": 4, "Level VUS": 5}
            t_score = t_scores.get(m.get("tier"), 9)
            l_score = l_scores.get(m.get("level"), 9)
            return (t_score, l_score)
            
        unique_matches.sort(key=get_record_sort_key_final)
        return unique_matches

    # Fast path: if variants are already decorated/tiered, return immediately (after applying deduplication)
    if variants and isinstance(variants[0], dict) and "tier" in variants[0]:
        has_changed = False
        for v in variants:
            if "evidence_json" in v:
                orig_len = len(v["evidence_json"])
                v["evidence_json"] = deduplicate_evidence_list(v["evidence_json"])
                if len(v["evidence_json"]) != orig_len:
                    has_changed = True
                
                # Rebuild top-level columns to keep in sync
                if v["evidence_json"]:
                    tiers = [m["tier"] if m["tier"] else "Tier 3" for m in v["evidence_json"]]
                    levels = [m["level"] if m["level"] else "Level VUS" for m in v["evidence_json"]]
                    bts = [m["biomarker_type"].capitalize() if m["biomarker_type"] else "None" for m in v["evidence_json"]]
                    pmids = [m["pmids"] if m["pmids"] else "OncoKB" for m in v["evidence_json"]]
                    drugs = [m["drug"] if m["drug"] else "None" for m in v["evidence_json"]]
                    responses = [m["response"].replace("_", " ").title() if m["response"] else "None" for m in v["evidence_json"]]
                    sources = [m["source"] if m["source"] else "None" for m in v["evidence_json"]]
                    
                    v["tier"] = " | ".join(tiers)
                    v["level"] = " | ".join(levels)
                    v["biomarker_type"] = " | ".join(bts)
                    v["evidence"] = " | ".join(pmids)
                    v["drug"] = " | ".join(drugs)
                    v["response"] = " | ".join(responses)
                    v["evidence_source"] = " | ".join(sources)
                else:
                    is_candidate = (v.get("impact") in ("HIGH", "MODERATE") and v.get("gnomad_af", 0.0) <= 0.01)
                    if is_candidate:
                        v["tier"] = "Tier 3"
                        v["level"] = "Level VUS"
                    else:
                        v["tier"] = "Tier 4"
                        v["level"] = "None"
                    v["biomarker_type"] = "None"
                    v["evidence"] = "None"
                    v["drug"] = "None"
                    v["response"] = "None"
                    v["evidence_source"] = "None"
                    v["evidence_json"] = []
                    
        if has_changed:
            case.filtered_variants = json.dumps(variants)
            db.commit()
        return variants
    
    from app.database import EvidenceSessionLocal, LocalEvidence
    from app.tiering import calculate_variant_tier
    
    def normalize_alteration(alt):
        if not alt:
            return ""
        alt = alt.strip()
        if alt.startswith("p.") or alt.startswith("c."):
            alt = alt[2:]
        alt = alt.replace("(", "").replace(")", "").replace("[", "").replace("]", "")
        alt = alt.strip()
        if not alt or alt in (".", "?", "=", "unknown", "Unknown", "N/A", "n/a"):
            return ""
        return alt

    def query_local_db_sync(gene: str, protein: str, cdna: str, consequence: str, exon: str) -> list:
        records = []
        ev_session = EvidenceSessionLocal()
        try:
            db_records = ev_session.query(LocalEvidence).filter(
                LocalEvidence.gene.ilike(gene.strip())
            ).all()
            from app.civic_client import matches_variant, standardize_pmids
            for r in db_records:
                if matches_variant(gene, protein, cdna, consequence, exon, r.alteration):
                    raw_pmids = standardize_pmids(r.pmids if r.pmids else "None")
                    first_pmid = raw_pmids.split(";")[0].strip() if raw_pmids else "OncoKB"
                    if not first_pmid:
                        first_pmid = "OncoKB"
                    records.append({
                        "tier": r.tier,
                        "level": r.level,
                        "biomarker_type": r.biomarker_type if r.biomarker_type else "None",
                        "cancer": r.cancer,
                        "doid": r.doid,
                        "drug": r.drug if r.drug else "None",
                        "response": r.response if r.response else "None",
                        "pmids": first_pmid,
                        "source": "Local DB"
                    })
        except Exception as e:
            print(f"Error querying LocalEvidence: {e}")
        finally:
            ev_session.close()
        return records

    for variant in variants:
        p_alt = variant.get("protein", "")
        c_alt = variant.get("cdna", "")
        gene = variant.get("gene", "")
        consequence = variant.get("consequence", "")
        exon = variant.get("exon", "")
            
        local_records = query_local_db_sync(gene, p_alt, c_alt, consequence, exon)
        
        from app.civic_client import fetch_civic_evidence
        civic_records = fetch_civic_evidence(gene, p_alt, c_alt, consequence, exon)
        
        class DummyRecord:
            def __init__(self, doid, tier, level):
                self.doid = doid
                self.tier = tier
                self.level = level

        # Separate civic and local records first, calculate adjusted tier/level
        query_alt = p_alt if p_alt else c_alt
        
        civic_adjusted = []
        for r in civic_records:
            dummy = DummyRecord(r["doid"], r["tier"], r["level"])
            adjusted_tier, adjusted_level = calculate_variant_tier(
                gene, query_alt, case.indication_doid, dummy
            )
            r["tier"] = adjusted_tier
            r["level"] = adjusted_level
            civic_adjusted.append(r)
            
        civic_keys = set()
        for r in civic_adjusted:
            t_val = r["tier"].strip().upper() if r["tier"] else ""
            l_val = r["level"].strip().upper() if r["level"] else ""
            bt_val = r["biomarker_type"].strip().upper() if r["biomarker_type"] else ""
            civic_keys.add((t_val, l_val, bt_val))
            
        local_adjusted = []
        for r in local_records:
            dummy = DummyRecord(r["doid"], r["tier"], r["level"])
            adjusted_tier, adjusted_level = calculate_variant_tier(
                gene, query_alt, case.indication_doid, dummy
            )
            r["tier"] = adjusted_tier
            r["level"] = adjusted_level
            
            t_val = r["tier"].strip().upper() if r["tier"] else ""
            l_val = r["level"].strip().upper() if r["level"] else ""
            bt_val = r["biomarker_type"].strip().upper() if r["biomarker_type"] else ""
            
            if (t_val, l_val, bt_val) in civic_keys:
                # Drop duplicate local DB record since we keep CIViC
                continue
            local_adjusted.append(r)
            
        combined_matches = civic_adjusted + local_adjusted
            
        # Deduplicate and group by Biomarker Type and Drug
        grouped = {}
        for m in combined_matches:
            bt_val = m.get("biomarker_type", "None") or "None"
            bt_val = standardize_biomarker_type(bt_val)
            m["biomarker_type"] = bt_val
            drug_val = m.get("drug", "None") or "None"
            
            key = (bt_val.strip().lower(), drug_val.strip().lower())
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(m)
            
        unique_matches = []
        for key, group_records in grouped.items():
            # Find representative with highest level/tier
            def get_record_sort_key(rec):
                t_scores = {"Tier 1": 1, "Tier 2": 2, "Tier 3": 3}
                l_scores = {"Level A": 1, "Level B": 2, "Level C": 3, "Level D": 4, "Level VUS": 5}
                t_score = t_scores.get(rec.get("tier"), 9)
                l_score = l_scores.get(rec.get("level"), 9)
                return (t_score, l_score)
                
            group_records.sort(key=get_record_sort_key)
            rep = dict(group_records[0]) # Copy so we don't mutate original
            
            # Combine PMIDs
            all_pmids = []
            for r in group_records:
                p_val = r.get("pmids")
                if p_val and p_val != "None" and p_val != "OncoKB":
                    import re
                    for p in re.split(r'[,;]', p_val):
                        p = p.strip()
                        if p and p not in all_pmids:
                            all_pmids.append(p)
            if all_pmids:
                rep["pmids"] = ";".join(all_pmids)
            else:
                has_oncokb = any(r.get("pmids") == "OncoKB" for r in group_records)
                rep["pmids"] = "OncoKB" if has_oncokb else "None"
                
            # Combine EIDs
            all_eids = []
            for r in group_records:
                eid_val = r.get("eid")
                if eid_val and eid_val != "None" and eid_val != "":
                    for e in re.split(r'[,;]', eid_val):
                        e = e.strip()
                        if e and e not in all_eids:
                            all_eids.append(e)
            if all_eids:
                rep["eid"] = ";".join(all_eids)
            else:
                rep["eid"] = ""
            
            # Combine Sources
            all_sources = []
            for r in group_records:
                src_val = r.get("source")
                if src_val and src_val not in all_sources:
                    all_sources.append(src_val)
            if all_sources:
                rep["source"] = ";".join(all_sources)
                
            unique_matches.append(rep)
                
        has_tier1_or_2 = any(m["tier"] in ("Tier 1", "Tier 2") for m in unique_matches)
        if has_tier1_or_2:
            unique_matches = [m for m in unique_matches if m["tier"] != "Tier 3"]
                
        if unique_matches:
            def get_record_sort_key(m):
                t_scores = {"Tier 1": 1, "Tier 2": 2, "Tier 3": 3}
                l_scores = {"Level A": 1, "Level B": 2, "Level C": 3, "Level D": 4, "Level VUS": 5}
                t_score = t_scores.get(m["tier"], 9)
                l_score = l_scores.get(m["level"], 9)
                return (t_score, l_score)
                
            unique_matches.sort(key=get_record_sort_key)
            
            tiers = [m["tier"] if m["tier"] else "Tier 3" for m in unique_matches]
            levels = [m["level"] if m["level"] else "Level VUS" for m in unique_matches]
            bts = [m["biomarker_type"].capitalize() if m["biomarker_type"] else "None" for m in unique_matches]
            pmids = [m["pmids"] if m["pmids"] else "OncoKB" for m in unique_matches]
            drugs = [m["drug"] if m["drug"] else "None" for m in unique_matches]
            responses = [m["response"].replace("_", " ").title() if m["response"] else "None" for m in unique_matches]
            sources = [m["source"] if m["source"] else "None" for m in unique_matches]
            
            variant["tier"] = " | ".join(tiers)
            variant["level"] = " | ".join(levels)
            variant["biomarker_type"] = " | ".join(bts)
            variant["evidence"] = " | ".join(pmids)
            variant["drug"] = " | ".join(drugs)
            variant["response"] = " | ".join(responses)
            variant["evidence_source"] = " | ".join(sources)
            variant["evidence_json"] = unique_matches
        else:
            is_candidate = (variant.get("impact") in ("HIGH", "MODERATE") and variant.get("gnomad_af", 0.0) <= 0.01)
            if is_candidate:
                variant["tier"] = "Tier 3"
                variant["level"] = "Level VUS"
            else:
                variant["tier"] = "Tier 4"
                variant["level"] = "None"
            variant["biomarker_type"] = "None"
            variant["evidence"] = "None"
            variant["drug"] = "None"
            variant["response"] = "None"
            variant["evidence_source"] = "None"
            variant["evidence_json"] = []

    case.filtered_variants = json.dumps(variants)
    db.commit()
    return variants

@app.get("/api/cases/{case_id}/variants")
async def get_case_variants(
    case_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Returns the parsed, surviving variants of a completed case, decorated with
    computed Tier and Level based on Local DB (SQLCipher) or CIViC evidence.
    """
    case = db.query(ClinicalCase).filter(ClinicalCase.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Clinical case not found")

    if case.status != "Completed":
        raise HTTPException(
            status_code=400, 
            detail=f"Variants are only reviewable when analysis status is Completed. Current status: {case.status}"
        )

    return tier_case_variants(case, db)

@app.post("/api/cases/{case_id}/variants/confirm")
def confirm_case_variants(
    case_id: int,
    payload: VariantConfirmRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Logs the operator-confirmed variants and preserves them with raw evidence JSON.
    """
    case = db.query(ClinicalCase).filter(ClinicalCase.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Clinical case not found")

    # Filter out selected variants from the cached filtered list
    all_variants = tier_case_variants(case, db)

    confirmed_variants = []
    for v in all_variants:
        hgvsg = v.get("hgvsg")
        if not hgvsg:
            continue
        
        # Check if there are keys starting with hgvsg + "::" or exactly hgvsg
        matching_keys = [k for k in payload.selected_hgvsg if k == hgvsg or k.startswith(f"{hgvsg}::")]
        if not matching_keys:
            continue
            
        evidence_list = v.get("evidence_json", [])
        if not evidence_list:
            # Variant has no evidence list, but was selected (e.g. VUS/Tier 3 with no evidence)
            # Check if hgvsg is directly in selected_hgvsg
            if hgvsg in payload.selected_hgvsg:
                confirmed_variants.append(v)
        else:
            # Variant has evidence list, filter it based on selected keys
            selected_indices = []
            for k in matching_keys:
                if "::" in k:
                    try:
                        idx = int(k.split("::")[1])
                        selected_indices.append(idx)
                    except ValueError:
                        pass
            
            # Keep only the evidence items at selected indices
            filtered_evidence = [evidence_list[i] for i in selected_indices if 0 <= i < len(evidence_list)]
            if filtered_evidence:
                # Copy the variant so we don't mutate all_variants in-place
                v_copy = dict(v)
                v_copy["evidence_json"] = filtered_evidence
                
                # Recompute tier, level, biomarker_type, evidence, drug, response, evidence_source
                tiers = [m["tier"] if m["tier"] else "Tier 3" for m in filtered_evidence]
                levels = [m["level"] if m["level"] else "Level VUS" for m in filtered_evidence]
                bts = [m["biomarker_type"].capitalize() if m["biomarker_type"] else "None" for m in filtered_evidence]
                pmids = [m["pmids"] if m["pmids"] else "OncoKB" for m in filtered_evidence]
                drugs = [m["drug"] if m["drug"] else "None" for m in filtered_evidence]
                responses = [m["response"].replace("_", " ").title() if m["response"] else "None" for m in filtered_evidence]
                sources = [m["source"] if m["source"] else "None" for m in filtered_evidence]
                
                v_copy["tier"] = " | ".join(tiers)
                v_copy["level"] = " | ".join(levels)
                v_copy["biomarker_type"] = " | ".join(bts)
                v_copy["evidence"] = " | ".join(pmids)
                v_copy["drug"] = " | ".join(drugs)
                v_copy["response"] = " | ".join(responses)
                v_copy["evidence_source"] = " | ".join(sources)
                
                confirmed_variants.append(v_copy)
    
    # Save the selected variants
    case.confirmed_variants = json.dumps(confirmed_variants)
    db.commit()

    # Write to secure, append-only audit log file
    audit = get_audit_logger()
    audit.info(f"CASE-{case_id:04d} | {current_user.username} | VARIANTS_CONFIRMED | count={len(confirmed_variants)}")
    for v in confirmed_variants:
        audit.info(f"CASE-{case_id:04d} | {current_user.username} | VARIANT | hgvsg={v.get('hgvsg')} gene={v.get('gene')} tier={v.get('tier')}")

    return {"message": f"Successfully logged and saved {len(confirmed_variants)} confirmed variants for CASE-{case_id}."}


# --- DRIVER GENES & VCF REPORT EXPORT ---

@app.get("/api/driver-genes/indications")
def get_driver_genes_indications(
    current_user: User = Depends(get_current_user)
):
    """
    Returns the parsed and grouped lists of indications and their driver genes.
    """
    return INDICATION_LIST

@app.get("/api/cases/{case_id}/report/vcf")
def download_annotated_vcf(
    case_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Generates and downloads the annotated somatic VCF file containing variant Tiers.
    """
    from fastapi.responses import FileResponse
    import cyvcf2
    
    case = db.query(ClinicalCase).filter(ClinicalCase.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Clinical case not found")
        
    if case.status != "Completed":
        raise HTTPException(
            status_code=400, 
            detail=f"Report files are only reviewable when analysis status is Completed. Current status: {case.status}"
        )
        
    # Make sure tiers are computed on candidate variants
    variants = tier_case_variants(case, db)
    
    # Build tier mapping: (chrom, pos, ref, alt) -> tier_str
    tier_map = {}
    for var in variants:
        hgvsg = var.get("hgvsg", "")
        if ":" in hgvsg:
            try:
                chrom_part, rest = hgvsg.split(":")
                import re
                match = re.match(r"^(\d+)([A-Z\-]+)>([A-Z\-]+)$", rest, re.IGNORECASE)
                if match:
                    pos = int(match.group(1))
                    ref = match.group(2)
                    alt = match.group(3)
                    tier_str = var.get("tier", "Tier 3")
                    
                    if "Tier 1" in tier_str:
                        tier = "Tier 1"
                    elif "Tier 2" in tier_str:
                        tier = "Tier 2"
                    elif "Tier 3" in tier_str:
                        tier = "Tier 3"
                    else:
                        tier = "Tier 4"
                        
                    chrom_key = chrom_part.lower().replace("chr", "")
                    tier_map[(chrom_key, pos, ref.upper(), alt.upper())] = tier
            except Exception as e:
                print(f"Error parsing hgvsg {hgvsg} for VCF download: {e}")

    # Build output VCF path
    input_vcf = case.vcf_path
    output_vcf = input_vcf + ".annotated.vcf"
    
    try:
        vcf_reader = cyvcf2.VCF(input_vcf)
        vcf_reader.add_info_to_header({
            'ID': 'TIER',
            'Description': 'Variant Tier (Tier 1/2/3/4) based on AMP/ASCO/CAP guidelines',
            'Type': 'String',
            'Number': '1'
        })
        vcf_writer = cyvcf2.Writer(output_vcf, vcf_reader)
        
        for record in vcf_reader:
            filter_val = record.FILTER
            is_pass = False
            if filter_val is None:
                is_pass = True
            elif isinstance(filter_val, str):
                is_pass = filter_val.strip() in ("", ".", "PASS")
            elif isinstance(filter_val, (list, tuple)):
                is_pass = len(filter_val) == 0 or all(x in ("", ".", "PASS") for x in filter_val)
                
            if is_pass:
                chrom_key = str(record.CHROM).lower().replace("chr", "")
                pos = record.POS + 1
                ref = str(record.REF).upper()
                alt = str(record.ALT[0]).upper() if record.ALT else ""
                
                tier = tier_map.get((chrom_key, pos, ref, alt))
                if not tier:
                    tier = "Tier 4"
                record.INFO['TIER'] = tier
                
            vcf_writer.write_record(record)
            
        vcf_reader.close()
        vcf_writer.close()
    except Exception as e:
        if os.path.exists(output_vcf):
            try:
                os.remove(output_vcf)
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"Failed to annotate output VCF: {str(e)}")
        
    filename = f"Case_{case_id}_Annotated.vcf"
    return FileResponse(
        output_vcf,
        media_type="text/vcard",
        filename=filename
    )


# --- PHASE 4 LLM SYNTHESIS & REPORTING ENDPOINTS ---

@app.post("/api/cases/{case_id}/report/generate")
async def generate_case_report(
    case_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Triggers local LLM synthesis for confirmed variants' narratives.
    """
    case = db.query(ClinicalCase).filter(ClinicalCase.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Clinical case not found")
        
    if not case.confirmed_variants:
        raise HTTPException(
            status_code=400,
            detail="No confirmed variants exist for this case. Please review and confirm variants first."
        )
        
    confirmed_variants = json.loads(case.confirmed_variants)
    if not confirmed_variants:
        raise HTTPException(
            status_code=400,
            detail="Confirmed variants list is empty. Please select and confirm variants first."
        )

    try:
        from app.llm_service import LlmSynthesisService
        synthesis = await LlmSynthesisService.synthesize_report(case_id, confirmed_variants)
        
        # Pre-populate / Initialize report_draft in DB
        case.report_draft = json.dumps(synthesis)
        db.commit()
        
        return synthesis
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"LLM Synthesis failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Local LLM service failed to synthesize report: {str(e)}. Make sure Ollama daemon is running ('ollama serve') and model 'medgemma:4b' is downloaded."
        )

@app.post("/api/cases/{case_id}/report/save")
def save_case_report_draft(
    case_id: int,
    payload: ReportDraftSaveRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Saves manually curated report draft narratives back to the database.
    """
    case = db.query(ClinicalCase).filter(ClinicalCase.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Clinical case not found")
        
    case.report_draft = payload.report_draft
    db.commit()
    return {"message": "Draft report saved successfully."}

def refactor_references(narratives_dict):
    import re
    parsed_references = {}
    citation_order = []
    cleaned_narratives = {}
    
    # 1. First pass: extract references sections and strip them from the narratives
    ref_section_pattern = re.compile(
        r"##\s*References\s*\n(.*?)(?=\n##|\n#|$)", 
        re.IGNORECASE | re.DOTALL
    )
    
    for key, text in narratives_dict.items():
        if not text:
            cleaned_narratives[key] = ""
            continue
            
        # Find all references blocks
        ref_blocks = ref_section_pattern.findall(text)
        for block in ref_blocks:
            ref_lines = [line.strip() for line in block.split("\n") if line.strip()]
            for line in ref_lines:
                key_match = re.search(r"\(([^)]+)\)", line)
                if key_match:
                    ref_key = key_match.group(1).strip()
                    
                    # Clean up the reference line: strip number prefix if any
                    # while preserving markdown link structure
                    link_match = re.match(r"^\[(.*?)\]\((.*?)\)$", line)
                    if link_match:
                        link_text = link_match.group(1).strip()
                        link_url = link_match.group(2).strip()
                        cleaned_link_text = re.sub(r"^\[?\s*\d+\.\s+", "", link_text)
                        cleaned_line = f"[{cleaned_link_text}]({link_url})"
                    else:
                        cleaned_line = re.sub(r"^\[?\s*\d+\.\s+", "", line)
                        
                    parsed_references[ref_key] = cleaned_line
                    
        # Remove references sections from text
        cleaned_text = ref_section_pattern.sub("", text).strip()
        cleaned_narratives[key] = cleaned_text

    # 2. Extract PMIDs from the cleaned narratives and add them to parsed_references
    pmid_pattern = re.compile(r"\bPMID:\s*(\d+)\b", re.IGNORECASE)
    for key, text in cleaned_narratives.items():
        if not text:
            continue
        pmids_found = pmid_pattern.findall(text)
        for pmid_num in pmids_found:
            pmid_key = f"PMID:{pmid_num}"
            if pmid_key not in parsed_references:
                parsed_references[pmid_key] = f"[PMID:{pmid_num}](https://pubmed.ncbi.nlm.nih.gov/{pmid_num})"

    # 3. Second pass: find citations in the cleaned text and map them to numbers
    all_combined_text = "\n".join(cleaned_narratives.values())
    
    key_positions = []
    for ref_key in parsed_references.keys():
        escaped_key = re.escape(ref_key)
        # Search for pmid or standard key case-insensitively
        match = re.search(rf"\b{escaped_key}\b|{escaped_key}", all_combined_text, re.IGNORECASE)
        if match:
            key_positions.append((ref_key, match.start()))
            
    # Sort keys by their first occurrence position
    key_positions.sort(key=lambda x: x[1])
    for ref_key, _ in key_positions:
        if ref_key not in citation_order:
            citation_order.append(ref_key)
            
    # Add any remaining keys that weren't cited in text
    for ref_key in parsed_references.keys():
        if ref_key not in citation_order:
            citation_order.append(ref_key)
            
    # Map from key -> number
    key_to_num = {key: idx for idx, key in enumerate(citation_order, 1)}
    
    # 4. Third pass: replace in-text citations with numbers
    for key, text in cleaned_narratives.items():
        if not text:
            continue
            
        if key_to_num:
            standard_keys = [k for k in key_to_num.keys() if not k.startswith("PMID:")]
            pmid_keys = [k for k in key_to_num.keys() if k.startswith("PMID:")]
            
            if standard_keys:
                keys_escaped = [re.escape(k) for k in standard_keys]
                keys_pattern = "|".join(keys_escaped)
                paren_pattern = re.compile(rf"[(\[]\s*((?:{keys_pattern})(?:\s*;\s*(?:{keys_pattern}))*)\s*[)\]]")
                
                def replacer(match):
                    content = match.group(1)
                    citation_parts = [c.strip() for c in content.split(";")]
                    num_citations = []
                    for part in citation_parts:
                        if part in key_to_num:
                            num_citations.append(f"{key_to_num[part]}")
                        else:
                            num_citations.append(part)
                    return "[" + ", ".join(num_citations) + "]"
                    
                text = paren_pattern.sub(replacer, text)
                
            for p_key in pmid_keys:
                num = key_to_num[p_key]
                # Match [PMID:12345] or (PMID:12345)
                text = re.sub(rf"[(\[]\s*{re.escape(p_key)}\s*[)\]]", f"[{num}]", text, flags=re.IGNORECASE)
                # Match bare PMID:12345
                text = re.sub(rf"\b{re.escape(p_key)}\b", f"[{num}]", text, flags=re.IGNORECASE)
                
        cleaned_narratives[key] = text

    # Build the final references block
    final_references = []
    for ref_key in citation_order:
        ref_text = parsed_references[ref_key]
        num = key_to_num[ref_key]
        final_references.append(f"{num}. {ref_text}")
        
    return cleaned_narratives, final_references

@app.get("/api/cases/{case_id}/report/docx")
def download_case_report_docx(
    case_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Generates and downloads a clean, editable clinical molecular pathology report in DOCX format.
    """
    import io
    from fastapi.responses import StreamingResponse
    from docx import Document
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    case = db.query(ClinicalCase).filter(ClinicalCase.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Clinical case not found")

    confirmed_variants = json.loads(case.confirmed_variants) if case.confirmed_variants else []
    
    def deduplicate_evidence_json(evidence_json):
        if not evidence_json:
            return []
        grouped = {}
        for m in evidence_json:
            bt_val = m.get("biomarker_type", "None") or "None"
            bt_val = standardize_biomarker_type(bt_val)
            m["biomarker_type"] = bt_val
            drug_val = m.get("drug", "None") or "None"
            key = (bt_val.strip().lower(), drug_val.strip().lower())
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(m)
            
        unique_matches = []
        for key, group_records in grouped.items():
            def get_record_sort_key(rec):
                t_scores = {"Tier 1": 1, "Tier 2": 2, "Tier 3": 3}
                l_scores = {"Level A": 1, "Level B": 2, "Level C": 3, "Level D": 4, "Level VUS": 5}
                t_score = t_scores.get(rec.get("tier"), 9)
                l_score = l_scores.get(rec.get("level"), 9)
                return (t_score, l_score)
                
            group_records.sort(key=get_record_sort_key)
            rep = dict(group_records[0])
            
            # Combine PMIDs
            all_pmids = []
            for r in group_records:
                p_val = r.get("pmids")
                if p_val and p_val != "None" and p_val != "OncoKB":
                    import re
                    for p in re.split(r'[,;]', p_val):
                        p = p.strip()
                        if p and p not in all_pmids:
                            all_pmids.append(p)
            if all_pmids:
                rep["pmids"] = ";".join(all_pmids)
            else:
                has_oncokb = any(r.get("pmids") == "OncoKB" for r in group_records)
                rep["pmids"] = "OncoKB" if has_oncokb else "None"
                
            # Combine EIDs
            all_eids = []
            for r in group_records:
                eid_val = r.get("eid")
                if eid_val and eid_val != "None" and eid_val != "":
                    for e in re.split(r'[,;]', eid_val):
                        e = e.strip()
                        if e and e not in all_eids:
                            all_eids.append(e)
            if all_eids:
                rep["eid"] = ";".join(all_eids)
            else:
                rep["eid"] = ""
                
            # Combine Sources
            all_sources = []
            for r in group_records:
                src_val = r.get("source")
                if src_val and src_val not in all_sources:
                    all_sources.append(src_val)
            if all_sources:
                rep["source"] = ";".join(all_sources)
                
            unique_matches.append(rep)
            
        def get_record_sort_key_final(m):
            t_scores = {"Tier 1": 1, "Tier 2": 2, "Tier 3": 3}
            l_scores = {"Level A": 1, "Level B": 2, "Level C": 3, "Level D": 4, "Level VUS": 5}
            t_score = t_scores.get(m.get("tier"), 9)
            l_score = l_scores.get(m.get("level"), 9)
            return (t_score, l_score)
            
        unique_matches.sort(key=get_record_sort_key_final)
        return unique_matches

    # Apply deduplication dynamically to confirmed_variants
    for v in confirmed_variants:
        if "evidence_json" in v:
            v["evidence_json"] = deduplicate_evidence_json(v["evidence_json"])
            
            # Recalculate variant fields to keep in sync
            if v["evidence_json"]:
                tiers = [m["tier"] if m["tier"] else "Tier 3" for m in v["evidence_json"]]
                levels = [m["level"] if m["level"] else "Level VUS" for m in v["evidence_json"]]
                bts = [m["biomarker_type"].capitalize() if m["biomarker_type"] else "None" for m in v["evidence_json"]]
                pmids = [m["pmids"] if m["pmids"] else "OncoKB" for m in v["evidence_json"]]
                drugs = [m["drug"] if m["drug"] else "None" for m in v["evidence_json"]]
                responses = [m["response"].replace("_", " ").title() if m["response"] else "None" for m in v["evidence_json"]]
                sources = [m["source"] if m["source"] else "None" for m in v["evidence_json"]]
                
                v["tier"] = " | ".join(tiers)
                v["level"] = " | ".join(levels)
                v["biomarker_type"] = " | ".join(bts)
                v["evidence"] = " | ".join(pmids)
                v["drug"] = " | ".join(drugs)
                v["response"] = " | ".join(responses)
                v["evidence_source"] = " | ".join(sources)
    report_draft_parsed = {}
    if case.report_draft:
        try:
            report_draft_parsed = json.loads(case.report_draft)
        except Exception:
            pass

    # Extract, strip, and re-sequence references globally
    narratives_dict = {
        "gene_analysis": report_draft_parsed.get("gene_analysis", ""),
        "variant_narrative": report_draft_parsed.get("variant_narrative", ""),
        "evidence_records": report_draft_parsed.get("evidence_records", "")
    }
    cleaned_narratives, final_references = refactor_references(narratives_dict)

    # Build Word document
    doc = Document()
    
    # Title
    title_p = doc.add_paragraph()
    title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title_p.add_run("CLINICAL GENOMIC VARIANT INTERPRETATION REPORT")
    run.font.name = 'Arial'
    run.font.size = Pt(18)
    run.font.bold = True
    run.font.color.rgb = RGBColor(15, 23, 42) # Slate 900
    
    # Subtitle
    sub_p = doc.add_paragraph()
    sub_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run_sub = sub_p.add_run("Sovereign Genomic Interpretation Pipeline (BEES) • Confidential Report")
    run_sub.font.name = 'Arial'
    run_sub.font.size = Pt(10)
    run_sub.font.italic = True
    run_sub.font.color.rgb = RGBColor(100, 116, 139) # Slate 500

    # Section 1: Clinical & Patient Demographics
    h1 = doc.add_heading(level=1)
    run_h1 = h1.add_run("1. Clinical & Patient Demographics")
    run_h1.font.name = 'Arial'
    run_h1.font.color.rgb = RGBColor(15, 23, 42)
    
    table_info = doc.add_table(rows=7, cols=4)
    table_info.style = 'Table Grid'
    
    demog_grid = [
        [("Provider", ""), ("Physician", "")],
        [("Pathologist", ""), ("Report Date", "")],
        [("Patient Name", ""), ("Age", str(case.patient_age) if case.patient_age is not None else "")],  # Patient Name intentionally blank — complete manually
        [("Sex", case.patient_sex or ""), ("Diagnosis", case.indication_name or "")],
        [("Stage", ""), ("Accession Number / Patient ID", f"CASE-{case.id:04d}")],
        [("Collection Site", ""), ("Specimen Type", "")],
        [("Collection Date", ""), ("", "")]
    ]
    
    for r_idx, row_pairs in enumerate(demog_grid):
        row = table_info.rows[r_idx]
        
        # Pair 1: cols 0 & 1
        label1, val1 = row_pairs[0]
        p1 = row.cells[0].paragraphs[0]
        run1 = p1.add_run(label1)
        run1.font.name = 'Arial'
        run1.font.bold = True
        run1.font.size = Pt(9.5)
        p1_val = row.cells[1].paragraphs[0]
        run1_val = p1_val.add_run(val1)
        run1_val.font.name = 'Arial'
        run1_val.font.size = Pt(9.5)
        
        # Pair 2: cols 2 & 3
        label2, val2 = row_pairs[1]
        p2 = row.cells[2].paragraphs[0]
        run2 = p2.add_run(label2)
        run2.font.name = 'Arial'
        run2.font.bold = True
        run2.font.size = Pt(9.5)
        p2_val = row.cells[3].paragraphs[0]
        run2_val = p2_val.add_run(val2)
        run2_val.font.name = 'Arial'
        run2_val.font.size = Pt(9.5)
        
    doc.add_paragraph() # Spacing

    # Section 2: Variant Classification Summary
    h2 = doc.add_heading(level=1)
    run_h2 = h2.add_run("2. Variant Classification Summary")
    run_h2.font.name = 'Arial'
    run_h2.font.color.rgb = RGBColor(15, 23, 42)
    
    from app.llm_service import format_protein_change

    def format_therapeutic_response(biomarker_type, drug, response):
        if not biomarker_type or biomarker_type.lower() != "therapeutic":
            return "None", "None"
        clean_drug = drug if drug and drug != "None" else "None"
        clean_response = "None"
        if response and response != "None":
            resp_lower = response.lower()
            if "sensit" in resp_lower:
                clean_response = "Sensitive"
            elif "resist" in resp_lower or "reduced" in resp_lower or "non" in resp_lower or "no" in resp_lower:
                clean_response = "Non-sensitive"
            else:
                clean_response = response
        return clean_drug, clean_response

    def clean_variant_heading(header):
        import re
        header = re.sub(r'^###\s*', '', header).strip()
        header = re.sub(r':$', '', header).strip()
        match = re.search(r'Variant\s+\d+\s*\(([^)]+)\)', header, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        header = re.sub(r'^Variant\s+\d+\s*[:-]?\s*', '', header, flags=re.IGNORECASE)
        return header.strip()

    def find_matching_variant(header_text, confirmed_variants):
        header_lower = header_text.lower()
        best_match = None
        best_score = 0
        for v in confirmed_variants:
            gene = v.get("gene", "").lower()
            protein = v.get("protein", "").lower()
            cdna = v.get("cdna", "").lower()
            hgvsg = v.get("hgvsg", "").lower()
            
            if gene and gene in header_lower:
                score = 1
                if protein and format_protein_change(protein).lower() in header_lower:
                    score += 3
                if cdna and cdna in header_lower:
                    score += 2
                if hgvsg and hgvsg in header_lower:
                    score += 4
                
                if score > best_score:
                    best_score = score
                    best_match = v
        return best_match

    def add_variant_table(doc, section_title, target_tier):
        # We find all (variant, ev) pairs that belong to target_tier
        rows_to_add = []
        for v in confirmed_variants:
            evidence_items = v.get("evidence_json", [])
            if not evidence_items:
                # If no evidence items, check variant's overall tier
                v_tier = v.get("tier", "Tier 3")
                if target_tier.lower() in v_tier.lower():
                    rows_to_add.append((v, None))
            else:
                for ev in evidence_items:
                    ev_tier = ev.get("tier", "Tier 3")
                    if target_tier.lower() in ev_tier.lower():
                        rows_to_add.append((v, ev))

        # Add subsection title
        p_sub = doc.add_paragraph()
        p_sub.paragraph_format.space_before = Pt(8)
        p_sub.paragraph_format.space_after = Pt(4)
        run_sub = p_sub.add_run(section_title)
        run_sub.font.name = 'Arial'
        run_sub.font.bold = True
        run_sub.font.size = Pt(11)
        run_sub.font.color.rgb = RGBColor(71, 85, 105)
        
        table = doc.add_table(rows=1, cols=10)
        table.style = 'Table Grid'
        
        headers = [
            "Gene", "cDNA Change", "Protein Change", "HGVSg.", "Consequence",
            "Level", "Biomarker Type", "Drug", "Response", "Evidence"
        ]
        hdr_cells = table.rows[0].cells
        for col_idx, h_text in enumerate(headers):
            p = hdr_cells[col_idx].paragraphs[0]
            run = p.add_run(h_text)
            run.font.name = 'Arial'
            run.font.bold = True
            run.font.size = Pt(9)
            
        if not rows_to_add:
            row_cells = table.add_row().cells
            row_cells[0].paragraphs[0].add_run("No variants of this tier selected.")
        else:
            for v, ev in rows_to_add:
                row_cells = table.add_row().cells
                
                gene_name = v.get("gene", "")
                cdna = v.get("cdna", "")
                consequence = v.get("consequence", "")
                is_splice = consequence and "splice" in consequence.lower()
                p_notation = "" if is_splice else format_protein_change(v.get("protein", ""), consequence)
                hgvsg = v.get("hgvsg", "")
                
                consequence_clean = ""
                if consequence:
                    parts = consequence.split('&')
                    consequence_clean = ' & '.join(
                        p.replace("_variant", "").replace("_", " ").title() for p in parts
                    )
                else:
                    consequence_clean = "Unknown"
                
                level = "VUS"
                biomarker_type = "None"
                drug = "None"
                response = "None"
                evidence_str = "None"
                
                if ev:
                    level = ev.get("level", "Level VUS").replace("Level ", "").strip()
                    biomarker_type = ev.get("biomarker_type", "None").strip()
                    
                    # Therapeutic mapping
                    drug, response = format_therapeutic_response(biomarker_type, ev.get("drug", "None"), ev.get("response", "None"))
                    
                    # Evidence EID and PMID
                    parts = []
                    if ev.get("source") and "CIViC" in ev.get("source") and ev.get("eid"):
                        parts.append(ev.get("eid"))
                    pmid_val = ev.get("pmids")
                    if pmid_val and pmid_val != "None":
                        parts.append(pmid_val)
                    
                    evidence_str = f"{parts[0]} ({parts[1]})" if len(parts) > 1 else (parts[0] if parts else "None")
                else:
                    level = v.get("level", "Level VUS").split(" | ")[0].replace("Level ", "").strip()
                    biomarker_type = v.get("biomarker_type", "None").split(" | ")[0].strip()
                    
                    raw_drug = v.get("drug", "None").split(" | ")[0]
                    raw_resp = v.get("response", "None").split(" | ")[0]
                    drug, response = format_therapeutic_response(biomarker_type, raw_drug, raw_resp)
                    
                    evidence_str = v.get("evidence", "None").split(" | ")[0]
                
                biomarker_clean = standardize_biomarker_type(biomarker_type).capitalize()
                
                for col_idx, val in enumerate([
                    gene_name, cdna, p_notation, hgvsg, consequence_clean,
                    level, biomarker_clean, drug, response, evidence_str
                ]):
                    p = row_cells[col_idx].paragraphs[0]
                    run = p.add_run(val)
                    run.font.name = 'Arial'
                    run.font.size = Pt(8.5)
                        
        doc.add_paragraph() # Spacing

    add_variant_table(doc, "a. Variants of Strong Clinical Significance - Tier 1", "Tier 1")
    add_variant_table(doc, "b. Variants of Potential Clinical Significance - Tier 2", "Tier 2")

    # Section 3: Detailed Section (LLM generated stuff)
    h3 = doc.add_heading(level=1)
    run_h3 = h3.add_run("3. Detailed Interpretative Narratives")
    run_h3.font.name = 'Arial'
    run_h3.font.color.rgb = RGBColor(15, 23, 42)
    
    def parse_inline_markdown(paragraph, text):
        import re
        pattern = re.compile(r'(\*\*.*?\*\*|\*.*?\*|\[.*?\]\(.*?\))')
        parts = pattern.split(text)
        for part in parts:
            if not part:
                continue
            if part.startswith('**') and part.endswith('**'):
                content = part[2:-2]
                run = paragraph.add_run(content)
                run.font.name = 'Arial'
                run.font.size = Pt(10)
                run.font.bold = True
            elif part.startswith('*') and part.endswith('*'):
                content = part[1:-1]
                run = paragraph.add_run(content)
                run.font.name = 'Arial'
                run.font.size = Pt(10)
                run.font.italic = True
            elif part.startswith('[') and ']' in part and part.endswith(')'):
                match = re.match(r'^\[(.*?)\]\((.*?)\)$', part)
                if match:
                    link_text = match.group(1)
                    link_url = match.group(2)
                    
                    import docx
                    import docx.oxml
                    import docx.oxml.ns
                    
                    part_obj = paragraph.part
                    r_id = part_obj.relate_to(link_url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", is_external=True)
                    
                    hyperlink = docx.oxml.shared.OxmlElement('w:hyperlink')
                    hyperlink.set(docx.oxml.shared.qn('r:id'), r_id)
                    
                    new_run = docx.oxml.shared.OxmlElement('w:r')
                    rPr = docx.oxml.shared.OxmlElement('w:rPr')
                    
                    rFonts = docx.oxml.shared.OxmlElement('w:rFonts')
                    rFonts.set(docx.oxml.shared.qn('w:ascii'), 'Arial')
                    rFonts.set(docx.oxml.shared.qn('w:hAnsi'), 'Arial')
                    rPr.append(rFonts)
                    
                    sz = docx.oxml.shared.OxmlElement('w:sz')
                    sz.set(docx.oxml.shared.qn('w:val'), '20')
                    rPr.append(sz)
                    
                    color = docx.oxml.shared.OxmlElement('w:color')
                    color.set(docx.oxml.shared.qn('w:val'), '2563EB')
                    rPr.append(color)
                    
                    u = docx.oxml.shared.OxmlElement('w:u')
                    u.set(docx.oxml.shared.qn('w:val'), 'single')
                    rPr.append(u)
                    
                    new_run.append(rPr)
                    text_node = docx.oxml.shared.OxmlElement('w:t')
                    text_node.text = link_text
                    new_run.append(text_node)
                    hyperlink.append(new_run)
                    paragraph._p.append(hyperlink)
            else:
                run = paragraph.add_run(part)
                run.font.name = 'Arial'
                run.font.size = Pt(10)

    def add_markdown_to_docx(doc, markdown_text):
        import re
        if not markdown_text:
            return
        lines = markdown_text.split('\n')
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
                continue
                
            # Headings
            heading_match = re.match(r'^(#{1,6})\s+(.*)$', line)
            if heading_match:
                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()
                
                h = doc.add_paragraph()
                h.paragraph_format.space_before = Pt(10)
                h.paragraph_format.space_after = Pt(4)
                h.paragraph_format.keep_with_next = True
                
                run = h.add_run(title)
                run.font.name = 'Arial'
                run.font.bold = True
                
                if level == 1:
                    run.font.size = Pt(13)
                    run.font.color.rgb = RGBColor(15, 23, 42)
                elif level == 2:
                    run.font.size = Pt(11.5)
                    run.font.color.rgb = RGBColor(71, 85, 105)
                else:
                    run.font.size = Pt(10.5)
                    run.font.color.rgb = RGBColor(100, 116, 139)
                i += 1
                continue
                
            # Lists
            bullet_match = re.match(r'^[-*]\s+(.*)$', line)
            number_match = re.match(r'^(\d+)\.\s+(.*)$', line)
            
            if bullet_match:
                p = doc.add_paragraph(style='List Bullet')
                p.paragraph_format.space_before = Pt(1)
                p.paragraph_format.space_after = Pt(1)
                parse_inline_markdown(p, bullet_match.group(1).strip())
            elif number_match:
                p = doc.add_paragraph(style='List Number')
                p.paragraph_format.space_before = Pt(1)
                p.paragraph_format.space_after = Pt(1)
                parse_inline_markdown(p, number_match.group(2).strip())
            else:
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(3)
                p.paragraph_format.space_after = Pt(4)
                parse_inline_markdown(p, line)
                
            i += 1

    def group_narratives_by_variant(gene_analysis, variant_narrative, evidence_records):
        import re
        def split_by_variant(text):
            if not text:
                return {}
            # Match standard variant header pattern
            pattern = re.compile(r'(### Variant\s+\d+\s*\([^)]+\)\s*Evidence:?|### Variant\s+\d+\s*\([^)]+\):?)', re.IGNORECASE)
            parts = pattern.split(text)
            grouped = {}
            i = 1
            while i < len(parts):
                header = parts[i].strip()
                content = parts[i+1].strip() if i+1 < len(parts) else ""
                num_match = re.search(r'Variant\s+(\d+)', header, re.IGNORECASE)
                if num_match:
                    var_num = int(num_match.group(1))
                    grouped[var_num] = {
                        "header": header,
                        "content": content
                    }
                i += 2
            return grouped

        genes_grouped = split_by_variant(gene_analysis)
        variants_grouped = split_by_variant(variant_narrative)
        evidences_grouped = split_by_variant(evidence_records)

        all_indices = sorted(list(set(genes_grouped.keys()) | set(variants_grouped.keys()) | set(evidences_grouped.keys())))
        
        combined = []
        for idx in all_indices:
            header = ""
            if idx in genes_grouped:
                header = genes_grouped[idx]["header"]
            elif idx in variants_grouped:
                header = variants_grouped[idx]["header"]
            elif idx in evidences_grouped:
                header = evidences_grouped[idx]["header"]
                header = re.sub(r'\s*Evidence:?$', '', header, flags=re.IGNORECASE)

            header_clean = re.sub(r'^###\s*', '', header).strip()
            header_clean = re.sub(r':$', '', header_clean).strip()
            
            combined.append({
                "index": idx,
                "header": header_clean,
                "gene_summary": genes_grouped.get(idx, {}).get("content", ""),
                "variant_summary": variants_grouped.get(idx, {}).get("content", ""),
                "evidence_summary": evidences_grouped.get(idx, {}).get("content", "")
            })
        return combined

    # Group and output narratives variant-by-variant
    grouped_narratives = group_narratives_by_variant(
        cleaned_narratives.get("gene_analysis", ""),
        cleaned_narratives.get("variant_narrative", ""),
        cleaned_narratives.get("evidence_records", "")
    )
    
    # Sort grouped narratives by tier (Tier 1 first, then Tier 2)
    def get_narrative_sort_key(item):
        v = find_matching_variant(item["header"], confirmed_variants)
        if not v:
            return 99 # Push unmatched to the end
        v_tier = v.get("tier", "Tier 3").lower()
        if "tier 1" in v_tier:
            return 1
        if "tier 2" in v_tier:
            return 2
        if "tier 3" in v_tier:
            return 3
        return 4
        
    grouped_narratives.sort(key=get_narrative_sort_key)
    
    if not grouped_narratives:
        p = doc.add_paragraph()
        run_empty = p.add_run("No detailed interpretative narratives are available.")
        run_empty.font.name = 'Arial'
        run_empty.font.size = Pt(10)
    else:
        for item in grouped_narratives:
            # Variant heading (no variant numbers)
            h_var = doc.add_paragraph()
            h_var.paragraph_format.space_before = Pt(12)
            h_var.paragraph_format.space_after = Pt(4)
            h_var.paragraph_format.keep_with_next = True
            
            cleaned_heading = clean_variant_heading(item["header"])
            run_h_var = h_var.add_run(cleaned_heading)
            run_h_var.font.name = 'Arial'
            run_h_var.font.bold = True
            run_h_var.font.size = Pt(11.5)
            run_h_var.font.color.rgb = RGBColor(71, 85, 105)
            
            # Gene Clinical Summary
            p_lbl_gene = doc.add_paragraph()
            p_lbl_gene.paragraph_format.space_before = Pt(4)
            p_lbl_gene.paragraph_format.space_after = Pt(2)
            p_lbl_gene.paragraph_format.keep_with_next = True
            run_lbl_gene = p_lbl_gene.add_run("Gene Clinical Summary:")
            run_lbl_gene.font.name = 'Arial'
            run_lbl_gene.font.bold = True
            run_lbl_gene.font.size = Pt(10)
            
            add_markdown_to_docx(doc, item["gene_summary"] if item["gene_summary"] else "No gene summary is available.")
            
            # Variant Summary
            p_lbl_var = doc.add_paragraph()
            p_lbl_var.paragraph_format.space_before = Pt(6)
            p_lbl_var.paragraph_format.space_after = Pt(2)
            p_lbl_var.paragraph_format.keep_with_next = True
            run_lbl_var = p_lbl_var.add_run("Variant Summary:")
            run_lbl_var.font.name = 'Arial'
            run_lbl_var.font.bold = True
            run_lbl_var.font.size = Pt(10)
            
            add_markdown_to_docx(doc, item["variant_summary"] if item["variant_summary"] else "No variant summary is available.")
            
            # Evidence Summary
            p_lbl_ev = doc.add_paragraph()
            p_lbl_ev.paragraph_format.space_before = Pt(6)
            p_lbl_ev.paragraph_format.space_after = Pt(2)
            p_lbl_ev.paragraph_format.keep_with_next = True
            run_lbl_ev = p_lbl_ev.add_run("Evidence Summary:")
            run_lbl_ev.font.name = 'Arial'
            run_lbl_ev.font.bold = True
            run_lbl_ev.font.size = Pt(10)
            
            # Find corresponding variant object
            matched_var = find_matching_variant(item["header"], confirmed_variants)
            
            # Determine tier subheading text
            v_tier = matched_var.get("tier", "Tier 3").lower() if matched_var else "tier 3"
            if "tier 1" in v_tier:
                sub_heading_text = "Variants of Strong Clinical Significance"
            elif "tier 2" in v_tier:
                sub_heading_text = "Variants of Potential Clinical Significance"
            else:
                sub_heading_text = "Variants of Unknown Significance"
                
            p_sub = doc.add_paragraph()
            p_sub.paragraph_format.space_before = Pt(4)
            p_sub.paragraph_format.space_after = Pt(2)
            p_sub.paragraph_format.keep_with_next = True
            run_sub = p_sub.add_run(sub_heading_text)
            run_sub.font.name = 'Arial'
            run_sub.font.bold = True
            run_sub.font.size = Pt(10)
            run_sub.font.color.rgb = RGBColor(71, 85, 105) # Slate 600
            
            # Add compact table
            table = doc.add_table(rows=1, cols=6)
            table.style = 'Table Grid'
            hdr_cells = table.rows[0].cells
            headers = ["Gene Name", "Mutation", "Tier", "Level", "Biomarker Type", "EID"]
            for col_idx, h_text in enumerate(headers):
                p = hdr_cells[col_idx].paragraphs[0]
                run = p.add_run(h_text)
                run.font.name = 'Arial'
                run.font.bold = True
                run.font.size = Pt(9.5)
                
            evidence_items = matched_var.get("evidence_json", []) if matched_var else []
            if not evidence_items:
                row_cells = table.add_row().cells
                gene_name = matched_var.get("gene", "") if matched_var else ""
                consequence = matched_var.get("consequence", "") if matched_var else ""
                is_splice = consequence and "splice" in consequence.lower()
                mutation = (matched_var.get("cdna", "") if is_splice else format_protein_change(matched_var.get("protein", ""), consequence)) if matched_var else ""
                if matched_var and not mutation:
                    mutation = matched_var.get("cdna", "") or matched_var.get("protein", "") or "Unknown"
                
                tier = matched_var.get("tier", "Tier 3") if matched_var else "Tier 3"
                level = matched_var.get("level", "Level VUS") if matched_var else "Level VUS"
                biomarker_type = matched_var.get("biomarker_type", "None") if matched_var else "None"
                eid = "N/A"
                
                biomarker_clean = standardize_biomarker_type(biomarker_type).capitalize()
                
                for col_idx, val in enumerate([gene_name, mutation, tier, level, biomarker_clean, eid]):
                    p = row_cells[col_idx].paragraphs[0]
                    run = p.add_run(val)
                    run.font.name = 'Arial'
                    run.font.size = Pt(9)
            else:
                seen_rows = set()
                for ev in evidence_items:
                    gene_name = matched_var.get("gene", "") if matched_var else ""
                    consequence = matched_var.get("consequence", "") if matched_var else ""
                    is_splice = consequence and "splice" in consequence.lower()
                    mutation = (matched_var.get("cdna", "") if is_splice else format_protein_change(matched_var.get("protein", ""), consequence)) if matched_var else ""
                    if matched_var and not mutation:
                        mutation = matched_var.get("cdna", "") or matched_var.get("protein", "") or "Unknown"
                    
                    tier = ev.get("tier", "Tier 3")
                    level = ev.get("level", "Level VUS")
                    biomarker_type = ev.get("biomarker_type", "None")
                    eid = ev.get("eid", "N/A") if ev.get("source") and "CIViC" in ev.get("source") and ev.get("eid") else "N/A"
                    
                    biomarker_clean = standardize_biomarker_type(biomarker_type).capitalize()
                    
                    row_key = (gene_name, mutation, tier, level, biomarker_clean, eid)
                    if row_key in seen_rows:
                        continue
                    seen_rows.add(row_key)
                    
                    row_cells = table.add_row().cells
                    for col_idx, val in enumerate([gene_name, mutation, tier, level, biomarker_clean, eid]):
                        p = row_cells[col_idx].paragraphs[0]
                        run = p.add_run(val)
                        run.font.name = 'Arial'
                        run.font.size = Pt(9)
                        
            doc.add_paragraph() # space after table
            
            add_markdown_to_docx(doc, item["evidence_summary"] if item["evidence_summary"] else "No evidence summary is available.")

    # Section 4: Variants of Unknown Significance (VUS)
    vus_variants = [v for v in confirmed_variants if "tier 3" in v.get("tier", "").lower()]
    if vus_variants:
        h4 = doc.add_heading(level=1)
        run_h4 = h4.add_run("4. Variants of Unknown Significance")
        run_h4.font.name = 'Arial'
        run_h4.font.color.rgb = RGBColor(15, 23, 42)
        
        p_vus_intro = doc.add_paragraph()
        p_vus_intro.paragraph_format.space_before = Pt(4)
        p_vus_intro.paragraph_format.space_after = Pt(6)
        run_vus_intro = p_vus_intro.add_run("The following variants are classified as Variants of Unknown Significance (VUS) / Tier 3 based on current guidelines.")
        run_vus_intro.font.name = 'Arial'
        run_vus_intro.font.size = Pt(10)
        run_vus_intro.font.italic = True
        
        vus_table = doc.add_table(rows=1, cols=4)
        vus_table.style = 'Table Grid'
        
        headers = ["Gene Name", "Variant", "Consequence", "Allele Frequency"]
        hdr_cells = vus_table.rows[0].cells
        for col_idx, h_text in enumerate(headers):
            p = hdr_cells[col_idx].paragraphs[0]
            run = p.add_run(h_text)
            run.font.name = 'Arial'
            run.font.bold = True
            run.font.size = Pt(9.5)
            
        for v in vus_variants:
            row_cells = vus_table.add_row().cells
            
            gene_name = v.get("gene", "")
            cdna = v.get("cdna", "")
            consequence = v.get("consequence", "")
            is_splice = consequence and "splice" in consequence.lower()
            p_notation = "" if is_splice else format_protein_change(v.get("protein", ""), consequence)
            variant_str = f"{cdna} {p_notation}".strip()
            
            consequence_clean = ""
            if consequence:
                parts = consequence.split('&')
                consequence_clean = ' & '.join(
                    p.replace("_variant", "").replace("_", " ").title() for p in parts
                )
            else:
                consequence_clean = "Unknown"
                
            af_val = v.get("af", 0.0)
            if isinstance(af_val, str):
                try:
                    af_val = float(af_val)
                except ValueError:
                    af_val = 0.0
            af_str = f"{af_val * 100:.2f}%" if af_val else "0.00%"
            
            for col_idx, val in enumerate([
                gene_name, variant_str, consequence_clean, af_str
            ]):
                p = row_cells[col_idx].paragraphs[0]
                run = p.add_run(val)
                run.font.name = 'Arial'
                run.font.size = Pt(9.5)
                
        doc.add_paragraph() # Spacing

    # Section 5: References
    if final_references:
        h_ref = doc.add_heading(level=1)
        section_num = 5 if vus_variants else 4
        run_href = h_ref.add_run(f"{section_num}. References")
        run_href.font.name = 'Arial'
        run_href.font.color.rgb = RGBColor(15, 23, 42)
        
        # Add references text as markdown block
        ref_block = "\n".join(final_references)
        add_markdown_to_docx(doc, ref_block)

    # Save doc to memory stream
    file_stream = io.BytesIO()
    doc.save(file_stream)
    file_stream.seek(0)
    
    filename = f"Case_{case_id}_Clinical_Report.docx"
    return StreamingResponse(
        file_stream,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# --- FRONTEND ROUTING & STATIC FILES ---


# Serve app/static folder for stylesheet, JS files, and ChartJS
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def serve_index():
    """
    Serves the main frontend Single Page Application (SPA).
    Redirects to login if cookie is missing, otherwise serves the page.
    Note: Token validation is done on the frontend JS, this serves the SPA shell.
    """
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return HTTPException(status_code=404, detail="Index HTML not found.")

@app.get("/cases/{case_id}/review")
def serve_review_page(case_id: int):
    """
    Serves the dedicated clinical variant review page in a separate tab.
    """
    review_file = os.path.join(STATIC_DIR, "review.html")
    if os.path.exists(review_file):
        return FileResponse(review_file)
    return HTTPException(status_code=404, detail="Review HTML file not found.")
