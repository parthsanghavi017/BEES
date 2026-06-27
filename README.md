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
| **DOCX Report Export** | Structured clinical pathology report with split Tier 1/2 tables |
| **HIPAA / DPDP Act 2023 Compliant** | Audit logging, consent capture, secure deletion, token denylist, no identifiable storage |
| **Encrypted at rest** | Evidence database uses SQLCipher (AES-256) |

---

## Architecture

```
VCF Upload → Annotation (Jannovar*) → Filtering → Tiering (CIViC + Local DB*)
    └─→ Curation Grid (browser UI)
        └─→ Variant Confirmation → LLM Synthesis (Ollama local)
            └─→ DOCX Report Export
```

> \* Jannovar annotation databases and the local curated evidence database are not distributed in this repository. See [Setup](#setup) below.

---

## Requirements

- Linux / macOS
- [Miniconda](https://docs.conda.io/en/latest/miniconda.html) or Anaconda
- [Ollama](https://ollama.com/) with `medgemma:4b` pulled
- Python 3.10+

### Annotation Databases (not included)

BEES uses **Jannovar** for variant annotation. You will need to download the appropriate `.ser` transcript database for your reference genome (GRCh37 or GRCh38) from the [Jannovar releases page](https://github.com/charite/jannovar/releases).

For gnomAD allele frequency annotation, a bgzipped, tabix-indexed gnomAD VCF is required and configured in `vcfanno`.

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
# Edit .env and fill in:
#   SECRET_KEY         — generate with: python -c "import secrets; print(secrets.token_hex(32))"
#   SQLCIPHER_PASSPHRASE — strong passphrase for the evidence database
#   CLINICAL_DB_PASSPHRASE — strong passphrase for the case database
```

> [!IMPORTANT]
> Never commit your `.env` file. It is listed in `.gitignore`.

### 3. Run environment setup

```bash
chmod +x setup_env.sh
./setup_env.sh
```

This will:
- Detect GPU/Apple Silicon acceleration
- Verify Ollama is running and pull `medgemma:4b`
- Create the `clinical_pipeline` conda environment
- Install all Python dependencies

### 4. Start Ollama (if not running as a system service)

```bash
ollama serve
```

### 5. Create an admin user

```bash
conda run -n clinical_pipeline python create_admin.py
```

### 6. Start the application server

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
  ✓  API docs at   http://127.0.0.1:8000/docs
============================================================
```

Open `http://127.0.0.1:8000` in your browser.

> [!NOTE]
> For clinical deployment, run behind an nginx/caddy reverse proxy with TLS and set `secure=True` on the session cookie.

---

## Compliance Controls

BEES implements the following privacy and security controls:

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

BEES integrates evidence from two sources:

1. **CIViC** (`civicpy`) — open-access clinical interpretations of variants in cancer, fetched and cached locally.
2. **Local Evidence Database** (`Var_DB/clinical_evidence.db`) — a curated, institution-specific SQLCipher-encrypted precision oncology knowledge base. *Not distributed in this repository.*

Gene clinical descriptions are resolved from CIViC first, then from a local encrypted gene description table (16,376 genes). The fallback chain is fully local.

---

## Tiering Rules

| Tier | Criteria |
|---|---|
| **Tier 1A/1B** | Evidence level A or B matching the case's disease DOID |
| **Tier 2C** | Tier 1 evidence where disease indication does not match case DOID |
| **Tier 2D** | Level C evidence (remapped from C) for any indication |
| **Tier 3** | HIGH/MODERATE impact variant, gnomAD AF < 1%, no evidence (VUS) |
| **Tier 4** | All other variants |

---

## License

This project is released for research and educational use. For clinical deployment, ensure compliance with applicable regulations in your jurisdiction (HIPAA, DPDP Act 2023, GDPR, etc.).

---

## Contributing

Pull requests are welcome. Please open an issue first to discuss any significant changes. All contributions must maintain the sovereign, privacy-first design principles of this project.
