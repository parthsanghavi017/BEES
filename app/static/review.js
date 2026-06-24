// Review Page State
let caseId = null;
let caseData = null;
let variantsData = [];
let selectedSet = new Set();
let currentSortCol = null;
let currentSortDir = 'asc'; // 'asc' or 'desc'

document.addEventListener("DOMContentLoaded", () => {
    extractCaseId();
    if (caseId) {
        loadReviewData();
        setupListeners();
    } else {
        showError("Invalid Case ID in URL pathway.");
    }
});

function extractCaseId() {
    const pathParts = window.location.pathname.split('/');
    // Expected path: /cases/{id}/review
    const idIdx = pathParts.indexOf("cases") + 1;
    if (idIdx > 0 && idIdx < pathParts.length) {
        caseId = parseInt(pathParts[idIdx]);
    }
}

async function loadReviewData() {
    try {
        // 1. Fetch Case details
        const caseResp = await fetch(`/api/cases/${caseId}`);
        if (caseResp.status === 401) {
            // Session expired, send back to login
            window.location.href = "/";
            return;
        }
        
        if (!caseResp.ok) {
            showError("Failed to retrieve clinical case metadata details.");
            return;
        }
        
        caseData = await caseResp.json();
        renderCaseMetadata();

        // 2. Fetch variants
        const varResp = await fetch(`/api/cases/${caseId}/variants`);
        if (!varResp.ok) {
            const err = await varResp.json();
            showError(err.detail || "Failed to load variants checklist.");
            return;
        }
        
        variantsData = await varResp.json();
        
        // Render Funnel with DB statistics
        renderFunnel();
        
        // Initial Table Render
        renderVariantsTable();
        
    } catch (err) {
        showError("Network connection error loading variant review board.");
        console.error(err);
    }
}

function renderCaseMetadata() {
    document.getElementById("case-id-display").textContent = `CASE-${caseId.toString().padStart(4, '0')}`;
    document.getElementById("patient-name-display").textContent = caseData.patient_name;
    document.getElementById("demog-display").textContent = `Age ${caseData.patient_age} • ${caseData.patient_sex}`;
    document.getElementById("indication-display").textContent = `${caseData.indication_name} (${caseData.indication_doid})`;
    
    const transcriptEl = document.getElementById("transcript-display");
    transcriptEl.textContent = caseData.transcript_db;
    
    const genomeEl = document.getElementById("genome-display");
    genomeEl.textContent = caseData.reference_genome;
}

function renderFunnel() {
    const total = caseData.total_input_variants || 0;
    const impact = caseData.passed_impact_variants || 0;
    const af = caseData.passed_af_variants || 0;

    // Display counts
    document.getElementById("funnel-raw-count").textContent = total.toLocaleString();
    document.getElementById("funnel-impact-count").textContent = impact.toLocaleString();
    document.getElementById("funnel-af-count").textContent = af.toLocaleString();

    // Discard calculations
    const impactDiscard = Math.max(0, total - impact);
    const afDiscard = Math.max(0, impact - af);

    document.getElementById("funnel-impact-discard").textContent = `Filtered out: ${impactDiscard.toLocaleString()} (Low/Modif)`;
    document.getElementById("funnel-af-discard").textContent = `Filtered out: ${afDiscard.toLocaleString()} (AF > 0.01)`;

    // Width percentages representing visual funnel shrink (bounded minimum 5% width for visibility)
    if (total > 0) {
        const impactPct = Math.max(8, Math.min(100, (impact / total) * 100));
        const afPct = Math.max(5, Math.min(100, (af / total) * 100));
        
        document.getElementById("funnel-impact-bar").style.width = `${impactPct}%`;
        document.getElementById("funnel-af-bar").style.width = `${afPct}%`;
    } else {
        document.getElementById("funnel-impact-bar").style.width = "0%";
        document.getElementById("funnel-af-bar").style.width = "0%";
    }
}

function renderVariantsTable() {
    const tableBody = document.getElementById("variants-table-body");
    tableBody.innerHTML = "";

    if (variantsData.length === 0) {
        tableBody.innerHTML = `
            <tr>
                <td colspan="9" class="table-placeholder">Zero genomic variants survived the hard-filtering checks for this clinical case.</td>
            </tr>`;
        return;
    }

    variantsData.forEach((v) => {
        const tr = document.createElement("tr");
        
        const isChecked = selectedSet.has(v.hgvsg) ? "checked" : "";
        const afStr = v.gnomad_af === 0 ? "Novel (0.0000)" : v.gnomad_af.toFixed(5);
        const impactClass = v.impact.toLowerCase() === "high" ? "impact-high" : "impact-moderate";

        tr.innerHTML = `
            <td style="text-align: center;">
                <input type="checkbox" class="variant-select" data-hgvsg="${v.hgvsg}" ${isChecked}>
            </td>
            <td><strong class="transcript-ref" style="background-color:rgba(6, 182, 212, 0.07); color:var(--color-secondary); border:1px solid rgba(6, 182, 212, 0.15);">${v.hgvsg}</strong></td>
            <td><strong>${escapeHtml(v.gene)}</strong></td>
            <td><span style="font-family: monospace;">${escapeHtml(v.cdna)}</span></td>
            <td><span style="font-family: monospace; font-weight: bold; color: #f1f5f9;">${escapeHtml(v.protein)}</span></td>
            <td><span class="transcript-ref">${v.transcript_type}</span></td>
            <td><span style="font-family: monospace;">${escapeHtml(v.transcript_id)}</span></td>
            <td><span class="impact-badge ${impactClass}">${v.impact}</span></td>
            <td><span style="font-family: monospace; font-weight: 600; color: ${v.gnomad_af === 0 ? '#10b981' : '#94a3b8'}">${afStr}</span></td>
        `;
        tableBody.appendChild(tr);
    });

    // Add checkboxes click handlers
    const checkBoxes = tableBody.querySelectorAll(".variant-select");
    checkBoxes.forEach(cb => {
        cb.addEventListener("change", (e) => {
            const hgvsg = e.target.getAttribute("data-hgvsg");
            if (e.target.checked) {
                selectedSet.add(hgvsg);
            } else {
                selectedSet.delete(hgvsg);
            }
            
            // Keep "Select All" checkbox state in sync
            const allChecked = Array.from(checkBoxes).every(box => box.checked);
            document.getElementById("select-all-variants").checked = allChecked;
            
            updateProceedButton();
        });
    });
    
    // Sync the header checkbox
    const allChecked = checkBoxes.length > 0 && Array.from(checkBoxes).every(box => box.checked);
    document.getElementById("select-all-variants").checked = allChecked;
}

function updateProceedButton() {
    const proceedBtn = document.getElementById("proceed-variants-button");
    const count = selectedSet.size;
    proceedBtn.textContent = `Proceed with ${count} variant${count === 1 ? '' : 's'}`;
    
    if (count > 0) {
        proceedBtn.removeAttribute("disabled");
        proceedBtn.style.opacity = "1";
        proceedBtn.style.cursor = "pointer";
    } else {
        proceedBtn.setAttribute("disabled", "true");
        proceedBtn.style.opacity = "0.6";
        proceedBtn.style.cursor = "not-allowed";
    }
}

// --- CLIENT-SIDE SORTING ENGINE ---

function handleSort(column) {
    if (currentSortCol === column) {
        // Toggle direction
        currentSortDir = currentSortDir === 'asc' ? 'desc' : 'asc';
    } else {
        currentSortCol = column;
        currentSortDir = 'asc';
    }

    // Sort variants array
    variantsData.sort((a, b) => {
        let valA = a[column];
        let valB = b[column];

        // Custom sort for HGVSg Chr:PosRef>Alt
        if (column === 'hgvsg') {
            return compareHgvsg(valA, valB) * (currentSortDir === 'asc' ? 1 : -1);
        }

        // Standard comparisons
        if (typeof valA === 'string') {
            return valA.localeCompare(valB) * (currentSortDir === 'asc' ? 1 : -1);
        } else {
            // numbers (e.g. allele freq)
            return (valA - valB) * (currentSortDir === 'asc' ? 1 : -1);
        }
    });

    // Update sorting arrow symbols in headers
    updateSortHeaders();

    // Re-render
    renderVariantsTable();
}

function compareHgvsg(a, b) {
    // HGVSg format: Chr:PosRef>Alt (e.g., 17:7673803G>A or chr17:7673803G>A)
    const splitA = a.split(':');
    const splitB = b.split(':');
    if (splitA.length < 2 || splitB.length < 2) return a.localeCompare(b);

    // 1. Extract Chromosome
    const chrA = splitA[0].replace(/chr/i, "").trim();
    const chrB = splitB[0].replace(/chr/i, "").trim();

    // Convert sex chromosomes to numeric values for sorting order
    const getChrRank = (chr) => {
        if (chr.toUpperCase() === 'X') return 23;
        if (chr.toUpperCase() === 'Y') return 24;
        if (chr.toUpperCase() === 'MT' || chr.toUpperCase() === 'M') return 25;
        const val = parseInt(chr);
        return isNaN(val) ? 99 : val;
    };

    const rankA = getChrRank(chrA);
    const rankB = getChrRank(chrB);

    if (rankA !== rankB) {
        return rankA - rankB;
    }

    // 2. Extract numeric position
    const getPos = (str) => {
        const matches = str.match(/^\d+/);
        return matches ? parseInt(matches[0]) : 0;
    };

    const posA = getPos(splitA[1]);
    const posB = getPos(splitB[1]);

    return posA - posB;
}

function updateSortHeaders() {
    const columns = ['hgvsg', 'gene', 'cdna', 'protein', 'transcript_id', 'impact', 'gnomad_af'];
    columns.forEach(col => {
        const span = document.getElementById(`sort-${col}`);
        if (span) {
            if (col === currentSortCol) {
                span.textContent = currentSortDir === 'asc' ? '▲' : '▼';
                span.classList.add("active");
            } else {
                span.textContent = '↕';
                span.classList.remove("active");
            }
        }
    });
}

// --- EVENT LISTENERS ---

function setupListeners() {
    // Select All Checkbox Handler
    const selectAll = document.getElementById("select-all-variants");
    selectAll.addEventListener("change", (e) => {
        const checkBoxes = document.querySelectorAll("#variants-table-body .variant-select");
        checkBoxes.forEach(cb => {
            cb.checked = e.target.checked;
            const hgvsg = cb.getAttribute("data-hgvsg");
            if (e.target.checked) {
                selectedSet.add(hgvsg);
            } else {
                selectedSet.delete(hgvsg);
            }
        });
        updateProceedButton();
    });

    // Proceed Confirmation handler
    const proceedBtn = document.getElementById("proceed-variants-button");
    const errorDiv = document.getElementById("review-error");
    const successDiv = document.getElementById("review-success");

    proceedBtn.addEventListener("click", async () => {
        errorDiv.classList.add("hidden");
        successDiv.classList.add("hidden");

        const selectedList = Array.from(selectedSet);
        if (selectedList.length === 0) return;

        proceedBtn.disabled = true;
        proceedBtn.textContent = "Confirming Review...";

        try {
            const response = await fetch(`/api/cases/${caseId}/variants/confirm`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ selected_hgvsg: selectedList })
            });

            const data = await response.json();
            if (response.ok) {
                successDiv.textContent = data.message || "Variants successfully confirmed.";
                successDiv.classList.remove("hidden");
                
                console.log(`[CLINICAL VARIANTS CONFIRMED] Case ID: CASE-${caseId} | Selected:`, selectedList);
                
                setTimeout(() => {
                    successDiv.classList.add("hidden");
                    // Optionally close tab on successful confirmation
                    window.close();
                }, 1500);
            } else {
                errorDiv.textContent = data.detail || "Database validation failed.";
                errorDiv.classList.remove("hidden");
            }
        } catch (err) {
            errorDiv.textContent = "Network connection failed transmitting variant logs.";
            errorDiv.classList.remove("hidden");
        } finally {
            proceedBtn.disabled = false;
            updateProceedButton();
        }
    });
}

function showError(msg) {
    const errorDiv = document.getElementById("review-error");
    errorDiv.textContent = msg;
    errorDiv.classList.remove("hidden");
}

function escapeHtml(str) {
    if (!str) return '';
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}
