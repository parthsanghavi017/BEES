import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Database URL can be overridden by environment variable for PostgreSQL support
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./clinical_pipeline.db")

# For SQLite, we need to allow access from multiple threads
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class User(Base):
    """
    User model representing authorized clinical bioinformatics pipeline operators.
    Passwords must always be hashed securely with bcrypt.
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="admin", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class ClinicalCase(Base):
    """
    ClinicalCase model representing genomic variant interpretation cases.
    Stores patient demographics, indication with Disease Ontology IDs (DOID),
    and local file path to the securely uploaded VCF file.
    """
    __tablename__ = "clinical_cases"

    id = Column(Integer, primary_key=True, index=True)
    patient_name = Column(String, nullable=False)
    patient_age = Column(Integer, nullable=False)
    patient_sex = Column(String, nullable=False)  # Male, Female, Other
    indication_doid = Column(String, nullable=False)  # e.g., DOID:162
    indication_name = Column(String, nullable=False)  # e.g., Cancer
    transcript_db = Column(String, nullable=False)  # Ensembl, RefSeq
    reference_genome = Column(String, nullable=False)  # GRCh37, GRCh38
    vcf_path = Column(String, nullable=False)  # Local storage path to ingested VCF
    upload_timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    is_archived = Column(Boolean, default=False, nullable=False)
    status = Column(String, default="Pending", nullable=False)  # Pending, Processing, Completed, Failed
    status_message = Column(String, nullable=True)  # Error details if failed
    filtered_variants = Column(String, nullable=True)  # Serialized JSON of surviving variants
    confirmed_variants = Column(String, nullable=True)  # Serialized JSON of curated/confirmed variants (with evidence)
    total_input_variants = Column(Integer, nullable=True)
    passed_impact_variants = Column(Integer, nullable=True)
    passed_af_variants = Column(Integer, nullable=True)
    report_draft = Column(String, nullable=True)  # JSON string containing Gene Analysis, Variant Narrative, and Evidence blocks

# Encrypted SQLite database via SQLCipher setup
EvidenceBase = declarative_base()

class LocalEvidence(EvidenceBase):
    """
    LocalEvidence model representing curated precision oncology variant evidence.
    Encrypted at rest using SQLCipher and SQLAlchemy.
    """
    __tablename__ = "local_evidence"

    id = Column(Integer, primary_key=True, index=True)
    gene = Column(String, index=True, nullable=False)
    alteration = Column(String, index=True, nullable=False)
    cancer = Column(String, nullable=False)
    doid = Column(String, index=True, nullable=False)
    drug = Column(String, nullable=True)
    biomarker_type = Column(String, nullable=True)
    response = Column(String, nullable=True)
    tier = Column(String, nullable=False)
    level = Column(String, nullable=False)
    pmids = Column(String, nullable=True)

EVIDENCE_PASSPHRASE = os.getenv("SQLCIPHER_PASSPHRASE", "bees_secure_evidence_cipher_key_2026")
db_dir = os.path.dirname(os.path.abspath(__file__))
evidence_db_path = os.path.join(os.path.dirname(db_dir), "Var_DB", "clinical_evidence.db")
os.makedirs(os.path.dirname(evidence_db_path), exist_ok=True)
EVIDENCE_DATABASE_URL = f"sqlite+pysqlcipher://:{EVIDENCE_PASSPHRASE}@/{evidence_db_path}"

evidence_engine = create_engine(EVIDENCE_DATABASE_URL)
EvidenceSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=evidence_engine)

def init_db():
    """
    Creates all database tables defined in the schema.
    Also dynamically handles schema upgrades for SQLite (adding report_draft column).
    """
    Base.metadata.create_all(bind=engine)
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            res = conn.execute(text("PRAGMA table_info(clinical_cases)"))
            columns = [row[1] for row in res]
            if "report_draft" not in columns:
                conn.execute(text("ALTER TABLE clinical_cases ADD COLUMN report_draft TEXT"))
                # Commit if necessary depending on transactional context, SQLite usually autocommits DDL
                print("[DATABASE] Successfully added report_draft column to clinical_cases table.")
    except Exception as e:
        print(f"[DATABASE] Schema upgrade warning: {e}")

def init_evidence_db():
    """
    Creates the encrypted local evidence table.
    """
    EvidenceBase.metadata.create_all(bind=evidence_engine)

def get_db():
    """
    FastAPI dependency that provides a transactional database session scope.
    Ensures the session is closed after the request completes.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_evidence_db():
    """
    FastAPI dependency that provides a transactional session scope for the evidence DB.
    """
    db = EvidenceSessionLocal()
    try:
        yield db
    finally:
        db.close()
