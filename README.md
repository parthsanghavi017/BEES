# BEES — Bioinformatics Evidence Evaluation System

**BEES** is a sovereign, privacy-first clinical genomics variant curation and interpretation pipeline. It processes somatic VCF files, tier-classifies variants under **AMP/ASCO/CAP** guidelines, and synthesizes draft pathology reports using a fully **local** LLM — no patient data ever leaves the machine.

> Built for clinical bioinformaticians and molecular pathologists who need a compliant, auditable, self-hosted alternative to cloud-based genomics platforms.

---

## Key Features

| Feature | Detail |
|---|---|
| **Sovereign execution** | All inference via local Ollama (`medgemma:4b`) — zero cloud calls with patient data |
| **AMP/ASCO/CAP Tiering** | Automated Tier 1–4 classification with disease-specific DOID matching |
| **CIViC Evidence Integration** | Live + cached evidence via `civicpy` |
| **Curated Local Evidence** | Encrypted precision oncology knowledge base (included, AES-256) |
| **DOCX Report Export** | Structured clinical pathology report with split Tier 1/2 tables |
| **HIPAA / DPDP Act 2023 Compliant** | Audit logging, consent capture, secure deletion, token denylist |
| **Encrypted at rest** | Evidence database uses SQLCipher (AES-256) |

---

## Architecture

```
VCF Upload → Variant Annotation → Impact & gnomAD AF Filtering
    └─→ Tiering Engine (CIViC + Local Curated DB)
        └─→ Curation Grid (browser UI)
            └─→ Variant Confirmation → LLM Synthesis (Ollama local)
                └─→ DOCX Report Export
```

---

## Requirements

- Linux / macOS
- [Miniconda](https://docs.conda.io/en/latest/miniconda.html) or Anaconda
- [Ollama](https://ollama.com/) with `medgemma:4b` pulled
- Python 3.10+
- Java 11+ (for the bundled annotation engine)

---

## What's Included

```
BEES_v2/
├── app/                        ← FastAPI application (full source)
├── References/
│   ├── bees_ensembl_hg38.ser   ← Ensembl transcript annotation database (GRCh38)
│   ├── bees_refseq_hg38.ser    ← RefSeq transcript annotation database (GRCh38)
│   ├── Driver-Genes.tsv        ← Cancer driver gene panel
│   └── af-only-gnomad.hg38.vcf.gz  ← gnomAD population AF reference, this can be downlaoded from https://www.bcgsc.ca/downloads/morinlab/reference/af-only-gnomad.hg38.vcf.gz (Get the .tbi as well)
├── Var_DB/
│   └── clinical_evidence.db    ← AES-256 encrypted curated oncology evidence
├── bees-annotator.jar          ← Bundled variant annotation engine (Java)
├── setup_env.sh                ← Environment setup script
├── create_admin.py             ← Admin user creation utility
└── .env.example                ← Environment variable template
```

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/<your-org>/BEES_v2.git
cd BEES_v2
```

### 2. Configure environment secrets

```bash
cp .env.example .env
# Edit .env — fill in the three required values:
#   SECRET_KEY             — generate: python -c "import secrets; print(secrets.token_hex(32))"
#   SQLCIPHER_PASSPHRASE   — passphrase for Var_DB/clinical_evidence.db
#   CLINICAL_DB_PASSPHRASE — passphrase for the case database
```

Contact the maintainers for the database passphrases if you are setting up an institutional deployment.

> [!IMPORTANT]
> Never commit your `.env` file — it is listed in `.gitignore`.

### 3. Verify Java is available

```bash
java -version   # Must be Java 11 or higher
```

### 4. Run environment setup

```bash
chmod +x setup_env.sh
./setup_env.sh
```

This will:
- Detect GPU/Apple Silicon acceleration
- Verify Ollama is running and pull `medgemma:4b`
- Create the `clinical_pipeline` conda environment and install all Python dependencies

### 5. Start Ollama (if not running as a system service)

```bash
ollama serve
```

### 6. Create an admin user

```bash
conda run -n clinical_pipeline python create_admin.py
```

### 7. Start the application server

```bash
conda run -n clinical_pipeline uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

On startup you will see:

```
============================================================
  BEES — Bioinformatics Evidence Evaluation System
  Version: 2.0 | Sovereign Clinical Genomics Pipeline
============================================================
  ✓  Database schema initialized
  ✓  Driver genes loaded
  ✓  CIViC cache preloaded
  ✓  Server ready at http://127.0.0.1:8000
============================================================
```

Open `http://127.0.0.1:8000` in your browser.

> [!NOTE]
> For clinical deployment, run behind an nginx/caddy reverse proxy with TLS enabled.

---

## Compliance Controls

| Control | Implementation |
|---|---|
| No full patient names stored | `patient_id` field only (MRN / accession / initials) |
| Operator consent capture | Required checkbox at case ingestion (DPDP §7 / HIPAA) |
| VCF integrity check | SHA-256 hash computed and stored at upload |
| Secure deletion | `shred -u` on archive/delete (HIPAA data destruction) |
| Audit trail | Append-only rotating file log (`/var/log/bees/` or `./logs/`) |
| Encrypted evidence DB | SQLCipher AES-256 |
| Token revocation | JWT denylist on explicit logout |
| Security headers | CSP, X-Frame-Options, HSTS on all responses |
| Session management | HTTP-only, SameSite=Strict cookie, 1-hour expiry |

---

## Evidence Sources

BEES integrates clinical evidence from two sources:

1. **CIViC** (`civicpy`) — open-access clinical interpretations of variants in cancer, fetched and cached locally.
2. **Curated Local Evidence DB** (`Var_DB/clinical_evidence.db`) — an institution-curated, AES-256 encrypted precision oncology knowledge base included with this release.

Gene clinical descriptions are resolved from CIViC first, then from a local encrypted gene description table (16,376 genes). The full fallback chain is local.

---

## Tiering Rules

| Tier | Criteria |
|---|---|
| **Tier 1A/1B** | Level A or B evidence matching the case's disease DOID |
| **Tier 2C** | Tier 1 evidence where disease indication does not match case DOID |
| **Tier 2D** | Level C evidence (remapped from C) for any indication |
| **Tier 3** | HIGH/MODERATE impact variant, gnomAD AF < 1%, no evidence (VUS) |
| **Tier 4** | All other variants |

---

## License

This project is released for research and educational use. For clinical deployment, ensure compliance with applicable regulations in your jurisdiction (HIPAA, DPDP Act 2023, GDPR, etc.).

---

## Contributing

Pull requests are welcome. Please open an issue first to discuss significant changes. All contributions must maintain the privacy-first design principles of this project.
