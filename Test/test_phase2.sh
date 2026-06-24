#!/usr/bin/env bash
set -e

echo "=== STARTING PHASE 2 INTERPRETATION PIPELINE TEST ==="

COOKIE_FILE="test_cookies.txt"

# 1. Login
echo "Logging in operator..."
curl -s -c "${COOKIE_FILE}" \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin12345"}' \
  http://127.0.0.1:8000/api/auth/login > /dev/null

# 2. Ingest Case
echo "Ingesting T001-BRCA.grch38.vcf.gz case..."
INGEST_RESP=$(curl -s -b "${COOKIE_FILE}" \
  -F "patient_name=T001" \
  -F "patient_age=60" \
  -F "patient_sex=Female" \
  -F "transcript_db=RefSeq" \
  -F "reference_genome=GRCh38" \
  -F "indication_doid=DOID:1612" \
  -F "indication_name=Breast Cancer" \
  -F "vcf_file=@Test/T001-BRCA.grch38.vcf.gz" \
  http://127.0.0.1:8000/api/cases)

CASE_ID=$(echo "${INGEST_RESP}" | grep -o '"id":[0-9]*' | head -n 1 | cut -d':' -f2)
echo "Case successfully ingested. Assigned ID: ${CASE_ID}"

# 3. Trigger Processing
echo "Triggering background analysis pipeline for case ${CASE_ID}..."
curl -s -b "${COOKIE_FILE}" -X POST "http://127.0.0.1:8000/api/cases/${CASE_ID}/process" | grep -o '"status":"[^"]*"'

# 4. Poll status
echo "Polling analysis status..."
STATUS="Processing"
while [ "${STATUS}" = "Processing" ] || [ "${STATUS}" = "Pending" ]; do
    sleep 2
    CASE_STATUS_RESP=$(curl -s -b "${COOKIE_FILE}" "http://127.0.0.1:8000/api/cases")
    # Extract status for our specific case
    # Format of response is list of cases. We find our case status:
    STATUS=$(echo "${CASE_STATUS_RESP}" | grep -o '"id":'"${CASE_ID}"',[^}]*' | grep -o '"status":"[^"]*"' | cut -d'"' -f4)
    STATUS_MSG=$(echo "${CASE_STATUS_RESP}" | grep -o '"id":'"${CASE_ID}"',[^}]*' | grep -o '"status_message":"[^"]*"' | cut -d'"' -f4)
    echo "  -> Current Status: ${STATUS} (${STATUS_MSG})"
done

if [ "${STATUS}" = "Failed" ]; then
    echo "ERROR: Pipeline failed!"
    rm -f "${COOKIE_FILE}"
    exit 1
fi

# 5. Query variants
echo "Pipeline completed! Fetching filtered variants..."
VARIANTS_RESP=$(curl -s -b "${COOKIE_FILE}" "http://127.0.0.1:8000/api/cases/${CASE_ID}/variants")

# Parse list of variants (extract gene names and HGVSg values)
VAR_COUNT=$(echo "${VARIANTS_RESP}" | grep -o '"hgvsg":"[^"]*"' | wc -l)
echo "Found ${VAR_COUNT} variants that passed impact and AF filters."
echo "Surviving variants:"
echo "${VARIANTS_RESP}" | grep -o '"hgvsg":"[^"]*"' | cut -d'"' -f4 | sed 's/^/  - /'

# Get first HGVSg value to test confirmation
FIRST_HGVSG=$(echo "${VARIANTS_RESP}" | grep -o '"hgvsg":"[^"]*"' | head -n 1 | cut -d'"' -f4)

if [ -n "${FIRST_HGVSG}" ]; then
    # 6. Confirm variant selection
    echo "Confirming selection of variant: ${FIRST_HGVSG}..."
    CONFIRM_RESP=$(curl -s -b "${COOKIE_FILE}" \
      -H "Content-Type: application/json" \
      -d '{"selected_hgvsg": ["'"${FIRST_HGVSG}"'"]}' \
      "http://127.0.0.1:8000/api/cases/${CASE_ID}/variants/confirm")
    echo "Response: ${CONFIRM_RESP}"
else
    echo "No variants to confirm."
fi

# Cleanup
rm -f "${COOKIE_FILE}"
echo "=== PIPELINE TEST COMPLETED SUCCESSFULLY ==="
