from datetime import datetime
from pydantic import BaseModel, Field, constr, validator
from typing import Dict, List, Optional

class UserCreate(BaseModel):
    username: constr(min_length=3, max_length=50, pattern="^[a-zA-Z0-9_-]+$") = Field(
        ..., description="Alphanumeric username with underscores or hyphens."
    )
    password: constr(min_length=8) = Field(
        ..., description="Password must be at least 8 characters long."
    )

class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    created_at: datetime

    class Config:
        orm_mode = True

class Token(BaseModel):
    access_token: str
    token_type: str

class ClinicalCaseCreate(BaseModel):
    patient_name: constr(min_length=1, max_length=100) = Field(
        ..., description="Patient name or identifier (non-empty)"
    )
    patient_age: int = Field(
        ..., ge=0, le=125, description="Patient age must be an integer between 0 and 125"
    )
    patient_sex: str = Field(
        ..., description="Patient sex must be one of: Male, Female, Other"
    )
    indication_doid: str = Field(
        ..., description="Standardized Disease Ontology ID (e.g. DOID:162)"
    )
    indication_name: str = Field(
        ..., description="Standardized Disease Ontology Name (e.g. Breast Cancer)"
    )
    transcript_db: str = Field(
        ..., description="Preferred transcript database: Ensembl or RefSeq"
    )
    reference_genome: str = Field(
        ..., description="Reference genome: GRCh37 or GRCh38"
    )

    @validator("patient_sex")
    def validate_sex(cls, v):
        allowed = {"Male", "Female", "Other"}
        if v not in allowed:
            raise ValueError(f"patient_sex must be one of {allowed}")
        return v

    @validator("transcript_db")
    def validate_transcript_db(cls, v):
        allowed = {"Ensembl", "RefSeq"}
        if v not in allowed:
            raise ValueError(f"transcript_db must be one of {allowed}")
        return v

    @validator("reference_genome")
    def validate_reference_genome(cls, v):
        allowed = {"GRCh37", "GRCh38"}
        if v not in allowed:
            raise ValueError(f"reference_genome must be one of {allowed}")
        return v

class ClinicalCaseResponse(BaseModel):
    id: int
    patient_name: str
    patient_age: int
    patient_sex: str
    indication_doid: str
    indication_name: str
    transcript_db: str
    reference_genome: str
    vcf_path: str
    upload_timestamp: datetime
    is_archived: bool
    status: str
    status_message: Optional[str] = None
    total_input_variants: Optional[int] = None
    passed_impact_variants: Optional[int] = None
    passed_af_variants: Optional[int] = None
    report_draft: Optional[str] = None

    class Config:
        orm_mode = True

class DashboardStats(BaseModel):
    total_cases: int
    active_cases: int
    archived_cases: int
    indication_distribution: Dict[str, int]

class VariantConfirmRequest(BaseModel):
    selected_hgvsg: List[str]

class ReportDraftSaveRequest(BaseModel):
    report_draft: str
