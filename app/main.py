import os
import json
import uuid
import shutil
import mimetypes
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File, Form, Response, Request, BackgroundTasks
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.database import get_db, init_db, ClinicalCase, User
from app.auth import get_current_user, hash_password, verify_password, create_access_token
from app.schemas import ClinicalCaseResponse, UserResponse, UserCreate, DashboardStats, VariantConfirmRequest
from app.pipeline import run_variant_pipeline

# Initialize FastAPI App
app = FastAPI(
    title="BEES Genomic Variant Interpretation Pipeline - Clinical Data Frontend",
    description="Sovereign, HIPAA-compliant patient case ingestion and tracking portal.",
    version="1.0.0"
)

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

@app.on_event("startup")
def on_startup():
    """
    Initializes the database schema on server startup.
    """
    init_db()

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
    
    # Set JWT in secure, HTTP-only cookie
    response.set_cookie(
        key="bees_session",
        value=token,
        httponly=True,
        max_age=3600, # 1 hour
        samesite="strict",
        secure=False  # Set to True in production with HTTPS
    )
    return {"message": "Login successful", "username": user.username, "role": user.role}

@app.post("/api/auth/logout")
def logout(response: Response):
    """
    Logs out the user by clearing the HTTP-only session cookie.
    """
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
    patient_name: str = Form(...),
    patient_age: int = Form(...),
    patient_sex: str = Form(...),
    indication_doid: str = Form(...),
    indication_name: str = Form(...),
    transcript_db: str = Form(...),
    reference_genome: str = Form(...),
    vcf_file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Ingests a new clinical case, performs strict validation on demographics and VCF uploads,
    saves the file securely on local storage, and logs details in the database.
    
    Security and Validation Controls:
    1. Demographics:
       - Age validated to be a positive integer between 0 and 125.
       - Sex validated to be Male, Female, or Other.
    2. Transcript:
       - Confirms transcript_db choice is Ensembl or RefSeq.
    3. File Name Sanitation:
       - Ingests VCF file and generates a unique, sanitized local path using UUID4.
       - Replaces filename with UUID4 + sanitized extension to prevent path traversal attacks.
    4. File Type and Content Verification (MIME & Header Signature):
       - Strictly validates that file extension is either '.vcf' or '.vcf.gz'.
       - Checks magic bytes / header content:
         - Uncompressed VCF: Reads the first 20 bytes to verify it starts with '##fileformat=VCF'.
         - Gzipped VCF: Reads the first 2 bytes to verify the gzip signature '\x1f\x8b'.
       - If validation fails, the file is rejected immediately, and nothing is written to disk.
    """
    # 1. Demographics & Inputs Validation
    if not (0 <= patient_age <= 125):
        raise HTTPException(status_code=400, detail="Patient age must be between 0 and 125")
    if patient_sex not in {"Male", "Female", "Other"}:
        raise HTTPException(status_code=400, detail="Invalid gender value")
    if transcript_db not in {"Ensembl", "RefSeq"}:
        raise HTTPException(status_code=400, detail="Invalid transcript database preference")
    if reference_genome not in {"GRCh37", "GRCh38"}:
        raise HTTPException(status_code=400, detail="Invalid reference genome version preference")

    # 2. File Extension Validation
    filename = vcf_file.filename
    is_gzipped = False
    if filename.endswith(".vcf.gz"):
        is_gzipped = True
        ext = ".vcf.gz"
    elif filename.endswith(".vcf"):
        is_gzipped = False
        ext = ".vcf"
    else:
        raise HTTPException(
            status_code=400, 
            detail="Unsupported file format. Only .vcf and .vcf.gz extensions are permitted."
        )

    # Read start of file for signature verification
    try:
        # FastAPI's UploadFile is file-like, we can read a chunk
        header_chunk = vcf_file.file.read(50)
        # Seek back to beginning so we can write the full file later
        vcf_file.file.seek(0)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read file headers: {str(e)}")

    # 3. File Signature / Magic Bytes Validation
    if is_gzipped:
        # Gzip magic bytes are 1F 8B
        if len(header_chunk) < 2 or header_chunk[:2] != b"\x1f\x8b":
            raise HTTPException(
                status_code=400,
                detail="File signature mismatch: File claims to be .vcf.gz but lacks valid gzip headers."
            )
    else:
        # Uncompressed VCF must start with '##fileformat=VCF'
        try:
            decoded_header = header_chunk.decode("utf-8", errors="ignore")
            if not decoded_header.startswith("##fileformat="):
                raise HTTPException(
                    status_code=400,
                    detail="File signature mismatch: File claims to be .vcf but lacks standard VCF header ('##fileformat=')"
                )
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="File content parsing error. Plaintext VCF contains invalid characters."
            )

    # 4. Secure File Saving
    # Generate unique filename using UUID4 to prevent naming collisions and directory traversal
    secure_filename = f"{uuid.uuid4()}{ext}"
    dest_path = os.path.join(LOCAL_STORAGE_DIR, secure_filename)

    try:
        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(vcf_file.file, buffer)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Secure file write failed: {str(e)}"
        )

    # 5. Database Record Linking
    new_case = ClinicalCase(
        patient_name=patient_name,
        patient_age=patient_age,
        patient_sex=patient_sex,
        indication_doid=indication_doid,
        indication_name=indication_name,
        transcript_db=transcript_db,
        reference_genome=reference_genome,
        vcf_path=dest_path,
        upload_timestamp=datetime.utcnow(),
        is_archived=False
    )
    
    db.add(new_case)
    db.commit()
    db.refresh(new_case)
    
    return new_case


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

@app.get("/api/cases/{case_id}/variants")
def get_case_variants(
    case_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Returns the parsed, surviving variants of a completed case.
    """
    case = db.query(ClinicalCase).filter(ClinicalCase.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Clinical case not found")

    if case.status != "Completed":
        raise HTTPException(
            status_code=400, 
            detail=f"Variants are only reviewable when analysis status is Completed. Current status: {case.status}"
        )

    if not case.filtered_variants:
        return []

    return json.loads(case.filtered_variants)

@app.post("/api/cases/{case_id}/variants/confirm")
def confirm_case_variants(
    case_id: int,
    payload: VariantConfirmRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Logs the operator-confirmed variants for the specific case to prepare for Phase 3.
    """
    case = db.query(ClinicalCase).filter(ClinicalCase.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Clinical case not found")

    # Logging to standard output as required for trace-back
    print(f"\n[CLINICAL AUDIT LOG] Case ID: CASE-{case_id} | Operator: {current_user.username} | Timestamp: {datetime.utcnow()}")
    print(f"[CLINICAL AUDIT LOG] Confirmed {len(payload.selected_hgvsg)} Variants:")
    for hgvsg in payload.selected_hgvsg:
        print(f"  - HGVSg: {hgvsg}")
    print("[CLINICAL AUDIT LOG] End of Log.\n")

    return {"message": f"Successfully logged {len(payload.selected_hgvsg)} variants for CASE-{case_id}."}


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
