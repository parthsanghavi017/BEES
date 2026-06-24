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

def init_db():
    """
    Creates all database tables defined in the schema.
    """
    Base.metadata.create_all(bind=engine)

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
