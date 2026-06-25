// Review Page State
let caseId = null;
let caseData = null;
let variantsData = [];
let selectedSet = new Set();
let currentSortCol = null;
let currentSortDir = 'asc'; // 'asc' or 'desc'

function formatBadgeList(str, type) {
    if (!str || str === "None" || str === "none") {
        if (type === 'tier') return `<span class="tier-badge tier-3">3</span>`;
        if (type === 'level') return `<span class="level-badge level-vus">VUS</span>`;
        if (type === 'source') return `<span class="source-badge source-none">None</span>`;
        return `<span style="color: var(--text-muted);">None</span>`;
    }
    
    return str.split(" | ").map(item => {
        const val = item.trim();
        if (type === 'tier') {
            let cls = val.toLowerCase().replace(" ", "-");
            let displayText = val.replace(/tier\s*/i, "").trim();
            if (displayText.toUpperCase() === "VUS" || cls === "vus" || cls === "tier-vus") {
                displayText = "3";
                cls = "tier-3";
            }
            return `<span class="tier-badge ${cls}">${displayText}</span>`;
        }
        if (type === 'source') {
            const cls = "source-" + val.toLowerCase().replace(" ", "-");
            return `<span class="source-badge ${cls}">${val}</span>`;
        }
        if (type === 'level') {
            const cls = val.toLowerCase().replace(" ", "-");
            const displayText = val.replace(/level\s*/i, "").trim();
            return `<span class="level-badge ${cls}">${displayText}</span>`;
        }
        if (type === 'biomarker') {
            return `<span class="transcript-ref" style="background-color:rgba(255,255,255,0.03); color:#e2e8f0; border:1px solid rgba(255,255,255,0.08);">${val}</span>`;
        }
        if (type === 'evidence') {
            return val.split(";").map(subItem => {
                const trimmed = subItem.trim();
                if (trimmed.toUpperCase().startsWith("PMID:")) {
                    const id = trimmed.replace(/pmid:/i, "");
                    return `<a href="https://pubmed.ncbi.nlm.nih.gov/${id}" target="_blank" class="transcript-ref" style="color:var(--color-secondary); text-decoration:none; border-bottom: 1px dashed var(--color-secondary); padding: 1px 0;">${trimmed}</a>`;
                }
                if (trimmed.toUpperCase() === "ONCOKB") {
                    return `<a href="https://www.oncokb.org/" target="_blank" class="transcript-ref" style="color:var(--color-secondary); text-decoration:none; border-bottom: 1px dashed var(--color-secondary); padding: 1px 0;">OncoKB</a>`;
                }
                return `<span style="font-family: monospace;">${trimmed}</span>`;
            }).join("<span style='color:var(--text-muted); margin:0 3px;'>;</span>");
        }
        return `<strong>${val}</strong>`;
    }).join("<span style='color:var(--text-muted); margin:0 4px;'>|</span>");
}

function formatProteinChange(protein) {
    if (!protein) return "";
    let clean = protein.trim();
    if (clean.toLowerCase().startsWith("p.")) {
        clean = clean.substring(2);
    }
    clean = clean.replace(/[\(\)\[\]]/g, "");
    clean = clean.trim();
    if (clean === "?" || clean === "" || clean.toLowerCase() === "unknown") {
        return "";
    }
    return clean;
}


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
        
        // Sort by Tier by default (Tier 1 > Tier 2 > Tier 3)
        currentSortCol = 'tier';
        currentSortDir = 'asc';
        sortVariantsData('tier');
        
        // Render Funnel with DB statistics
        renderFunnel();
        
        // Render Match Statistics
        renderStatistics();
        
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
                <td colspan="16" class="table-placeholder">Zero genomic variants survived the hard-filtering checks for this clinical case.</td>
            </tr>`;
        return;
    }

    variantsData.forEach((v) => {
        const tr = document.createElement("tr");
        
        const isChecked = selectedSet.has(v.hgvsg) ? "checked" : "";
        const afStr = v.gnomad_af === 0 ? "Novel (0.0000)" : v.gnomad_af.toFixed(5);
        const impactClass = v.impact && v.impact.toLowerCase() === "high" ? "impact-high" : "impact-moderate";
        const consequenceText = v.consequence ? formatConsequence(v.consequence) : (v.impact || "Unknown");

        const matchCount = v.tier ? v.tier.split(" | ").length : 0;
        let geneCellContent = `<strong>${escapeHtml(v.gene)}</strong>`;
        if (matchCount > 3) {
            tr.classList.add("expandable-row");
            geneCellContent += `<div class="matches-indicator"><span class="chevron-icon">▼</span> ${matchCount} matches</div>`;
        }

        if (matchCount > 3) {
            tr.addEventListener("click", (e) => {
                // Ignore clicks on checkbox, links, buttons, or inputs
                if (e.target.tagName === 'INPUT' || e.target.tagName === 'A' || e.target.tagName === 'BUTTON' || e.target.closest('a') || e.target.closest('input') || e.target.closest('button')) {
                    return;
                }
                tr.classList.toggle("row-expanded");
            });
        }

        tr.innerHTML = `
            <td style="text-align: center;">
                <input type="checkbox" class="variant-select" data-hgvsg="${v.hgvsg}" ${isChecked}>
            </td>
            <td><strong class="transcript-ref" style="background-color:rgba(6, 182, 212, 0.07); color:var(--color-secondary); border:1px solid rgba(6, 182, 212, 0.15);">${v.hgvsg}</strong></td>
            <td>${geneCellContent}</td>
            <td><span style="font-family: monospace;">${escapeHtml(v.cdna)}</span></td>
            <td><span style="font-family: monospace; font-weight: bold; color: #f1f5f9;">${escapeHtml(formatProteinChange(v.protein))}</span></td>
            <td><span class="transcript-ref">${v.transcript_type}</span></td>
            <td><span style="font-family: monospace;">${escapeHtml(v.transcript_id)}</span></td>
            <td><span class="impact-badge ${impactClass}">${consequenceText}</span></td>
            <td><span style="font-family: monospace; font-weight: 600; color: ${v.gnomad_af === 0 ? '#10b981' : '#94a3b8'}">${afStr}</span></td>
            <td><div class="clinical-container">${formatBadgeList(v.tier, 'tier')}</div></td>
            <td><div class="clinical-container">${formatBadgeList(v.level, 'level')}</div></td>
            <td><div class="clinical-container">${formatBadgeList(v.biomarker_type, 'biomarker')}</div></td>
            <td><div class="clinical-container">${formatBadgeList(v.evidence, 'evidence')}</div></td>
            <td><div class="clinical-container">${formatBadgeList(v.drug, 'drug')}</div></td>
            <td><div class="clinical-container">${formatBadgeList(v.response, 'response')}</div></td>
            <td><div class="clinical-container">${formatBadgeList(v.evidence_source, 'source')}</div></td>
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

function sortVariantsData(column) {
    variantsData.sort((a, b) => {
        let valA = a[column];
        let valB = b[column];

        // Custom sort for HGVSg Chr:PosRef>Alt
        if (column === 'hgvsg') {
            return compareHgvsg(valA, valB) * (currentSortDir === 'asc' ? 1 : -1);
        }

        if (column === 'consequence') {
            valA = valA ? formatConsequence(valA) : (a.impact || "");
            valB = valB ? formatConsequence(valB) : (b.impact || "");
        }

        if (column === 'tier') {
            const getTierRank = (tierStr) => {
                if (!tierStr) return 99;
                const ranks = tierStr.split(" | ").map(t => {
                    const cleanT = t.toString().toUpperCase();
                    if (cleanT.includes("1")) return 1;
                    if (cleanT.includes("2")) return 2;
                    if (cleanT.includes("3")) return 3;
                    return 4;
                });
                return Math.min(...ranks);
            };
            return (getTierRank(valA) - getTierRank(valB)) * (currentSortDir === 'asc' ? 1 : -1);
        }

        if (column === 'level') {
            const getLevelRank = (levelStr) => {
                if (!levelStr) return 99;
                const ranks = levelStr.split(" | ").map(l => {
                    const cleanL = l.toString().toUpperCase();
                    if (cleanL.includes("LEVEL A")) return 1;
                    if (cleanL.includes("LEVEL B")) return 2;
                    if (cleanL.includes("LEVEL C")) return 3;
                    if (cleanL.includes("LEVEL D")) return 4;
                    if (cleanL.includes("LEVEL VUS")) return 5;
                    return 6;
                });
                return Math.min(...ranks);
            };
            return (getLevelRank(valA) - getLevelRank(valB)) * (currentSortDir === 'asc' ? 1 : -1);
        }

        // Standard comparisons
        if (typeof valA === 'string') {
            const strA = valA || "";
            const strB = valB || "";
            return strA.localeCompare(strB) * (currentSortDir === 'asc' ? 1 : -1);
        } else {
            // numbers (e.g. allele freq)
            const numA = typeof valA === 'number' ? valA : 0;
            const numB = typeof valB === 'number' ? valB : 0;
            return (numA - numB) * (currentSortDir === 'asc' ? 1 : -1);
        }
    });
}

function handleSort(column) {
    if (currentSortCol === column) {
        // Toggle direction
        currentSortDir = currentSortDir === 'asc' ? 'desc' : 'asc';
    } else {
        currentSortCol = column;
        currentSortDir = 'asc';
    }

    sortVariantsData(column);

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
    const columns = ['hgvsg', 'gene', 'cdna', 'protein', 'transcript_id', 'consequence', 'gnomad_af', 'tier', 'level', 'biomarker_type', 'evidence', 'drug', 'response', 'evidence_source'];
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

function renderStatistics() {
    const total = variantsData.length;
    let localCount = 0;
    let civicCount = 0;
    let unmatchedCount = 0;

    variantsData.forEach(v => {
        const src = v.evidence_source || "";
        if (src.includes("Local DB")) {
            localCount++;
        }
        if (src.includes("CIViC")) {
            civicCount++;
        }
        if (!src.includes("Local DB") && !src.includes("CIViC")) {
            unmatchedCount++;
        }
    });

    document.getElementById("stats-total-variants").textContent = total.toLocaleString();
    document.getElementById("stats-local-matches").textContent = localCount.toLocaleString();
    document.getElementById("stats-civic-matches").textContent = civicCount.toLocaleString();
    document.getElementById("stats-unmatched").textContent = unmatchedCount.toLocaleString();
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

    // Download Exploded TSV handler
    const downloadTsvBtn = document.getElementById("download-tsv-button");
    if (downloadTsvBtn) {
        downloadTsvBtn.addEventListener("click", () => {
            if (variantsData.length === 0) {
                alert("No variant data available to download.");
                return;
            }
            
            // TSV Header columns
            const headers = [
                "HGVSg", "Gene", "cDNA Change", "Protein Change", 
                "Transcript Database", "Transcript ID", "Consequence", 
                "Allele Frequency (gnomAD)", "Tier", "Level", 
                "Biomarker Type", "Evidence (PMID/OncoKB)", "Drug", 
                "Response", "Source"
            ];
            
            let rows = [headers.join("\t")];
            
            variantsData.forEach(v => {
                const cleanStr = (s) => (s === null || s === undefined) ? "" : s.toString().trim();
                
                const hgvsg = cleanStr(v.hgvsg);
                const gene = cleanStr(v.gene);
                const cdna = cleanStr(v.cdna);
                const protein = cleanStr(v.protein);
                const tx_type = cleanStr(v.transcript_type);
                const tx_id = cleanStr(v.transcript_id);
                const consequence = cleanStr(v.consequence);
                const af = cleanStr(v.gnomad_af);
                
                // Split pipe-separated columns
                const tiers = cleanStr(v.tier).split(" | ");
                const levels = cleanStr(v.level).split(" | ");
                const bts = cleanStr(v.biomarker_type).split(" | ");
                const evs = cleanStr(v.evidence).split(" | ");
                const drugs = cleanStr(v.drug).split(" | ");
                const responses = cleanStr(v.response).split(" | ");
                const sources = cleanStr(v.evidence_source).split(" | ");
                
                // Determine the number of exploded rows (maximum list length)
                const N = Math.max(
                    tiers.length, levels.length, bts.length, 
                    evs.length, drugs.length, responses.length, sources.length
                );
                
                for (let i = 0; i < N; i++) {
                    const rowTier = cleanStr(tiers[i] || "");
                    const rowLevel = cleanStr(levels[i] || "");
                    const rowBt = cleanStr(bts[i] || "");
                    const rowEv = cleanStr(evs[i] || "");
                    const rowDrug = cleanStr(drugs[i] || "");
                    const rowResp = cleanStr(responses[i] || "");
                    const rowSrc = cleanStr(sources[i] || "");
                    
                    const rowData = [
                        hgvsg, gene, cdna, protein, 
                        tx_type, tx_id, consequence, af,
                        rowTier, rowLevel, rowBt, rowEv, 
                        rowDrug, rowResp, rowSrc
                    ];
                    
                    rows.push(rowData.join("\t"));
                }
            });
            
            // Build TSV blob and trigger download
            const tsvContent = rows.join("\n");
            const blob = new Blob([tsvContent], { type: "text/tab-separated-values;charset=utf-8;" });
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.setAttribute("href", url);
            
            // File naming: Case_[ID]_exploded_variants.tsv
            const fileName = `Case_${caseId}_exploded_variants.tsv`;
            link.setAttribute("download", fileName);
            link.style.visibility = 'hidden';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        });
    }
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

function formatConsequence(consequence) {
    if (!consequence) return "Unknown";
    return consequence.split('&').map(part => {
        let term = part.replace(/_variant$/i, "");
        term = term.replace(/_/g, " ");
        // Title case
        return term.replace(/\b\w/g, c => c.toUpperCase());
    }).join(' & ');
}



