// Review Page State
let caseId = null;
let caseData = null;
let variantsData = [];
let selectedSet = new Set();
let currentSortCol = null;
let currentSortDir = 'asc'; // 'asc' or 'desc'
let indicationsList = [];
let filteredVariantsList = [];

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

function formatReferenceLinks(evidence_str) {
    if (!evidence_str || evidence_str === "None") return "None";
    return evidence_str.split(" | ").map(rowItem => {
        return rowItem.split(";").map(subItem => {
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
    }).join("<span style='color:var(--text-muted); margin:0 4px;'> | </span>");
}

function formatProteinChange(protein, keepPrefix = true) {
    if (!protein) return "";
    let clean = protein.trim();
    let hasPrefix = clean.toLowerCase().startsWith("p.");
    if (hasPrefix) {
        clean = clean.substring(2);
    }
    clean = clean.replace(/[\(\)\[\]]/g, "");
    clean = clean.trim();
    if (clean === "?" || clean === "" || clean.toLowerCase() === "unknown" || clean.includes("?")) {
        return "";
    }
    return (hasPrefix || keepPrefix) ? "p." + clean : clean;
}


document.addEventListener("DOMContentLoaded", async () => {
    extractCaseId();
    if (caseId) {
        await loadIndications();
        await loadReviewData();
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
        
        // Initial Table Render through filters logic
        applyFilters();

        // Restore previously confirmed variants if they exist
        if (caseData.confirmed_variants) {
            try {
                const confirmed = JSON.parse(caseData.confirmed_variants);
                confirmed.forEach(cv => {
                    const orig = variantsData.find(v => v.hgvsg === cv.hgvsg);
                    if (orig && orig.evidence_json && orig.evidence_json.length > 0) {
                        cv.evidence_json.forEach(cvEv => {
                            const idx = orig.evidence_json.findIndex(origEv => {
                                const origDrug = (origEv.drug || "None").trim().toLowerCase();
                                const cvDrug = (cvEv.drug || "None").trim().toLowerCase();
                                const origBt = (origEv.biomarker_type || "None").trim().toLowerCase();
                                const cvBt = (cvEv.biomarker_type || "None").trim().toLowerCase();
                                return origDrug === cvDrug && origBt === cvBt;
                            });
                            if (idx !== -1) {
                                selectedSet.add(`${cv.hgvsg}::${idx}`);
                            }
                        });
                    } else {
                        selectedSet.add(cv.hgvsg);
                    }
                });
                applyFilters(); // Re-render to check correct rows
                updateProceedButton();
            } catch (e) {
                console.error("Failed to restore confirmed variants:", e);
            }
        }
        
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

function isVariantSelected(v) {
    if (selectedSet.has(v.hgvsg)) {
        return true;
    }
    for (const key of selectedSet) {
        if (key.startsWith(v.hgvsg + "::")) {
            return true;
        }
    }
    return false;
}

function renderVariantsTable(customData) {
    const tableBody = document.getElementById("variants-table-body");
    tableBody.innerHTML = "";

    const data = customData || filteredVariantsList;

    if (data.length === 0) {
        tableBody.innerHTML = `
            <tr>
                <td colspan="18" class="table-placeholder">Zero genomic variants match the active filters for this clinical case.</td>
            </tr>`;
        return;
    }

    data.forEach((v) => {
        const tr = document.createElement("tr");
        
        const isChecked = isVariantSelected(v) ? "checked" : "";
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

        const isSplice = v.consequence && v.consequence.toLowerCase().includes("splice");
        const displayProtein = isSplice ? v.cdna : formatProteinChange(v.protein, true);
        const afSampleStr = v.af !== undefined && v.af !== null ? `${(v.af * 100).toFixed(2)}%` : "0.00%";

        tr.innerHTML = `
            <td style="text-align: center;">
                <input type="checkbox" class="variant-select" data-hgvsg="${v.hgvsg}" ${isChecked}>
            </td>
            <td><strong class="transcript-ref" style="background-color:rgba(6, 182, 212, 0.07); color:var(--color-secondary); border:1px solid rgba(6, 182, 212, 0.15);">${v.hgvsg}</strong></td>
            <td>${geneCellContent}</td>
            <td><span style="font-family: monospace;">${escapeHtml(v.cdna)}</span></td>
            <td><span style="font-family: monospace; font-weight: bold; color: #f1f5f9;">${escapeHtml(displayProtein)}</span></td>
            <td><span style="font-family: monospace;">${afSampleStr}</span></td>
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
            const v = variantsData.find(vd => vd.hgvsg === hgvsg);
            if (e.target.checked) {
                if (v && v.evidence_json && v.evidence_json.length > 0) {
                    v.evidence_json.forEach((ev, idx) => {
                        selectedSet.add(`${hgvsg}::${idx}`);
                    });
                } else {
                    selectedSet.add(hgvsg);
                }
            } else {
                selectedSet.delete(hgvsg);
                Array.from(selectedSet).forEach(item => {
                    if (item.startsWith(hgvsg + "::")) {
                        selectedSet.delete(item);
                    }
                });
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
    const columns = ['hgvsg', 'gene', 'cdna', 'protein', 'af', 'transcript_id', 'consequence', 'gnomad_af', 'tier', 'level', 'biomarker_type', 'evidence', 'drug', 'response', 'evidence_source'];
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
            const v = variantsData.find(vd => vd.hgvsg === hgvsg);
            if (e.target.checked) {
                if (v && v.evidence_json && v.evidence_json.length > 0) {
                    v.evidence_json.forEach((ev, idx) => {
                        selectedSet.add(`${hgvsg}::${idx}`);
                    });
                } else {
                    selectedSet.add(hgvsg);
                }
            } else {
                selectedSet.delete(hgvsg);
                Array.from(selectedSet).forEach(item => {
                    if (item.startsWith(hgvsg + "::")) {
                        selectedSet.delete(item);
                    }
                });
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
                
                // Refresh caseData so it has the confirmed list
                const updatedCaseResp = await fetch(`/api/cases/${caseId}`);
                if (updatedCaseResp.ok) {
                    caseData = await updatedCaseResp.json();
                }
                
                setTimeout(() => {
                    successDiv.classList.add("hidden");
                    showReportView();
                }, 1000);
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

    // Download Annotated VCF handler
    const downloadVcfBtn = document.getElementById("download-vcf-button");
    if (downloadVcfBtn) {
        downloadVcfBtn.addEventListener("click", () => {
            window.open(`/api/cases/${caseId}/report/vcf`, '_blank');
        });
    }

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
                
                const isSplice = consequence.toLowerCase().includes("splice");
                const displayProtein = isSplice ? cdna : formatProteinChange(protein, true);
                
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
                        hgvsg, gene, cdna, displayProtein, 
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
            link.setAttribute("target", "_blank");
            
            // File naming: Case_[ID]_exploded_variants.tsv
            const fileName = `Case_${caseId}_exploded_variants.tsv`;
            link.setAttribute("download", fileName);
            link.style.visibility = 'hidden';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        });
    }
    
    // Wire up report view listeners
    setupReportListeners();
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

// --- PHASE 4 REPORT GENERATOR WORKSPACE METHODS ---

function showReportView() {
    document.getElementById("grid-view-section").classList.add("hidden");
    document.getElementById("dashboard-section").classList.add("hidden");
    document.getElementById("report-view-section").classList.remove("hidden");
    populateReportInfo();
}

function showGridView() {
    document.getElementById("report-view-section").classList.add("hidden");
    document.getElementById("grid-view-section").classList.remove("hidden");
    document.getElementById("dashboard-section").classList.remove("hidden");
}

function formatTherapeuticResponse(biomarkerType, drug, response) {
    if (!biomarkerType || biomarkerType.toLowerCase() !== "therapeutic") {
        return { drug: "None", response: "None" };
    }
    const cleanDrug = drug && drug !== "None" ? drug : "None";
    let cleanResponse = "None";
    if (response && response !== "None") {
        const respLower = response.toLowerCase();
        if (respLower.includes("sensit")) {
            cleanResponse = "Sensitive";
        } else if (respLower.includes("resist") || respLower.includes("reduced") || respLower.includes("non") || respLower.includes("no")) {
            cleanResponse = "Non-sensitive";
        } else {
            cleanResponse = response;
        }
    }
    return { drug: cleanDrug, response: cleanResponse };
}

function populateReportInfo() {
    // 1. Demographics (13 fields, empty where not in database)
    document.getElementById("rep-dem-provider").textContent = "";
    document.getElementById("rep-dem-physician").textContent = "";
    document.getElementById("rep-dem-pathologist").textContent = "";
    document.getElementById("rep-dem-report-date").textContent = "";
    document.getElementById("rep-dem-name").textContent = caseData.patient_name || "";
    document.getElementById("rep-dem-age").textContent = caseData.patient_age !== undefined ? caseData.patient_age : "";
    document.getElementById("rep-dem-sex").textContent = caseData.patient_sex || "";
    document.getElementById("rep-dem-diagnosis").textContent = caseData.indication_name || "";
    document.getElementById("rep-dem-stage").textContent = "";
    document.getElementById("rep-dem-patient-id").textContent = `CASE-${String(caseData.id).padStart(4, '0')}`;
    document.getElementById("rep-dem-collection-site").textContent = "";
    document.getElementById("rep-dem-type").textContent = "";
    document.getElementById("rep-dem-collection-date").textContent = "";

    // 2. Summary Tables of Selected Variants (Tier 1 vs Tier 2)
    const tier1Body = document.getElementById("report-variants-tier1-body");
    const tier2Body = document.getElementById("report-variants-tier2-body");
    tier1Body.innerHTML = "";
    tier2Body.innerHTML = "";
    
    const vusContainer = document.getElementById("report-vus-container");
    const vusBody = document.getElementById("report-variants-vus-body");
    if (vusBody) vusBody.innerHTML = "";

    const cols = 11;
    
    // Build flat list of all selected evidence items / rows
    const selectedRows = [];
    variantsData.forEach(v => {
        const evidenceList = v.evidence_json || [];
        if (evidenceList.length === 0) {
            if (selectedSet.has(v.hgvsg)) {
                selectedRows.push({
                    variant: v,
                    evidence: null,
                    key: v.hgvsg,
                    tier: v.tier || "Tier 3",
                    level: v.level || "Level VUS"
                });
            }
        } else {
            evidenceList.forEach((ev, idx) => {
                const key = `${v.hgvsg}::${idx}`;
                if (selectedSet.has(key)) {
                    selectedRows.push({
                        variant: v,
                        evidence: ev,
                        key: key,
                        tier: ev.tier || "Tier 3",
                        level: ev.level || "Level VUS"
                    });
                }
            });
        }
    });

    const getRowHtml = (row) => {
        const v = row.variant;
        const ev = row.evidence;
        const gene = v.gene || "";
        const cdna = v.cdna || "";
        const isSplice = v.consequence && v.consequence.toLowerCase().includes("splice");
        const protein = isSplice ? "" : formatProteinChange(v.protein, true);
        const hgvsg = v.hgvsg || "";
        const consequence = v.consequence ? formatConsequence(v.consequence) : "Unknown";
        
        let level = "VUS";
        let biomarkerType = "None";
        let drug = "None";
        let response = "None";
        let evidenceStr = "None";
        
        if (ev) {
            level = ev.level ? ev.level.replace(/level\s*/i, "").trim() : "VUS";
            biomarkerType = ev.biomarker_type ? ev.biomarker_type.trim() : "None";
            
            // Therapeutic mapping
            const tf = formatTherapeuticResponse(biomarkerType, ev.drug, ev.response);
            drug = tf.drug;
            response = tf.response;
            
            // QC EID and PMID
            let parts = [];
            if (ev.source && ev.source.includes("CIViC") && ev.eid) {
                parts.push(ev.eid);
            }
            if (ev.pmids && ev.pmids !== "None") {
                parts.push(ev.pmids);
            }
            evidenceStr = parts.length > 0 ? parts.join(" (") + (parts.length > 1 ? ")" : "") : "None";
        } else {
            level = v.level ? v.level.split(" | ")[0].replace(/level\s*/i, "").trim() : "VUS";
            biomarkerType = v.biomarker_type ? v.biomarker_type.split(" | ")[0].trim() : "None";
            
            const tf = formatTherapeuticResponse(biomarkerType, v.drug ? v.drug.split(" | ")[0] : "None", v.response ? v.response.split(" | ")[0] : "None");
            drug = tf.drug;
            response = tf.response;
            
            evidenceStr = v.evidence ? v.evidence.split(" | ")[0] : "None";
        }
        
        const levelClean = level.toUpperCase() === "VUS" ? "vus" : level.toLowerCase().replace(/\s+/g, '');
        const levelBadge = `<span class="level-badge level-${levelClean}">${escapeHtml(level)}</span>`;
        const biomarkerClean = biomarkerType.charAt(0).toUpperCase() + biomarkerType.slice(1).toLowerCase();
        
        return `
            <td><strong>${escapeHtml(gene)}</strong></td>
            <td><span style="font-family: monospace;">${escapeHtml(cdna)}</span></td>
            <td><span style="font-family: monospace;">${escapeHtml(protein)}</span></td>
            <td><span style="font-family: monospace;" class="transcript-ref">${escapeHtml(hgvsg)}</span></td>
            <td>${escapeHtml(consequence)}</td>
            <td>${levelBadge}</td>
            <td>${escapeHtml(biomarkerClean)}</td>
            <td>${escapeHtml(drug)}</td>
            <td>${escapeHtml(response)}</td>
            <td>${escapeHtml(evidenceStr)}</td>
            <td style="text-align: center;">
                <button type="button" class="btn btn-danger btn-xs btn-drop-variant" data-key="${row.key}" style="background:#ef4444; border:none; color:#fff; padding:4px 8px; border-radius:4px; font-size:0.75rem; cursor:pointer;">Drop</button>
            </td>
        `;
    };

    const tier1Rows = selectedRows.filter(r => r.tier.toLowerCase().includes("1"));
    const tier2Rows = selectedRows.filter(r => r.tier.toLowerCase().includes("2"));
    const vusRows = selectedRows.filter(r => r.tier.toLowerCase().includes("3"));

    if (tier1Rows.length === 0) {
        tier1Body.innerHTML = `<tr><td colspan="${cols}" class="table-placeholder">No Tier 1 variants selected.</td></tr>`;
    } else {
        tier1Rows.forEach(row => {
            const tr = document.createElement("tr");
            tr.innerHTML = getRowHtml(row);
            tier1Body.appendChild(tr);
        });
    }
    
    if (tier2Rows.length === 0) {
        tier2Body.innerHTML = `<tr><td colspan="${cols}" class="table-placeholder">No Tier 2 variants selected.</td></tr>`;
    } else {
        tier2Rows.forEach(row => {
            const tr = document.createElement("tr");
            tr.innerHTML = getRowHtml(row);
            tier2Body.appendChild(tr);
        });
    }

    if (vusContainer && vusBody) {
        if (vusRows.length > 0) {
            vusContainer.classList.remove("hidden");
            vusRows.forEach(row => {
                const tr = document.createElement("tr");
                const v = row.variant;
                const isSplice = v.consequence && v.consequence.toLowerCase().includes("splice");
                const p_notation = isSplice ? "" : formatProteinChange(v.protein, true);
                const variant_str = `${v.cdna || ""} ${p_notation}`.trim();
                const consequence_clean = v.consequence ? formatConsequence(v.consequence) : "Unknown";
                const af_pct = v.af !== undefined && v.af !== null ? `${(parseFloat(v.af) * 100).toFixed(2)}%` : "0.00%";
                
                tr.innerHTML = `
                    <td><strong>${escapeHtml(v.gene)}</strong></td>
                    <td><span style="font-family: monospace;">${escapeHtml(variant_str)}</span></td>
                    <td>${escapeHtml(consequence_clean)}</td>
                    <td><span style="font-family: monospace;">${af_pct}</span></td>
                    <td style="text-align: center;">
                        <button type="button" class="btn btn-danger btn-xs btn-drop-variant" data-key="${row.key}" style="background:#ef4444; border:none; color:#fff; padding:4px 8px; border-radius:4px; font-size:0.75rem; cursor:pointer;">Drop</button>
                    </td>
                `;
                vusBody.appendChild(tr);
            });
        } else {
            vusContainer.classList.add("hidden");
        }
    }

    // 3. Draft Narrative Blocks & button text
    const btnText = document.getElementById("btn-generate-text");
    if (caseData.report_draft) {
        if (btnText) btnText.textContent = "Regenerate Draft (local LLM)";
        try {
            const parsed = JSON.parse(caseData.report_draft);
            document.getElementById("report-textarea-gene").value = parsed.gene_analysis || "";
            document.getElementById("report-textarea-variant").value = parsed.variant_narrative || "";
            document.getElementById("report-textarea-evidence").value = parsed.evidence_records || "";
        } catch (e) {
            console.error("Failed to parse report draft JSON:", e);
        }
    } else {
        if (btnText) btnText.textContent = "Generate Draft (local LLM)";
        document.getElementById("report-textarea-gene").value = "";
        document.getElementById("report-textarea-variant").value = "";
        document.getElementById("report-textarea-evidence").value = "";
    }
}

function setupReportListeners() {
    const backBtn = document.getElementById("back-to-grid-btn");
    if (backBtn) {
        backBtn.addEventListener("click", showGridView);
    }
    
    // Save report draft
    const saveBtn = document.getElementById("btn-save-report");
    if (saveBtn) {
        saveBtn.addEventListener("click", async () => {
            const errorEl = document.getElementById("report-error");
            const successEl = document.getElementById("report-success");
            errorEl.classList.add("hidden");
            successEl.classList.add("hidden");

            const data = {
                gene_analysis: document.getElementById("report-textarea-gene").value,
                variant_narrative: document.getElementById("report-textarea-variant").value,
                evidence_records: document.getElementById("report-textarea-evidence").value
            };

            try {
                const response = await fetch(`/api/cases/${caseId}/report/save`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ report_draft: JSON.stringify(data) })
                });

                const respData = await response.json();
                if (response.ok) {
                    // Update local caseData in memory
                    caseData.report_draft = JSON.stringify(data);
                    successEl.textContent = respData.message || "Report draft saved successfully.";
                    successEl.classList.remove("hidden");
                    setTimeout(() => successEl.classList.add("hidden"), 3000);
                    
                    // Sync regenerate button state
                    const btnText = document.getElementById("btn-generate-text");
                    if (btnText) btnText.textContent = "Regenerate Draft (local LLM)";
                } else {
                    errorEl.textContent = respData.detail || "Failed to save report draft.";
                    errorEl.classList.remove("hidden");
                }
            } catch (err) {
                errorEl.textContent = "Network error saving report draft.";
                errorEl.classList.remove("hidden");
            }
        });
    }

    // Generate report
    const generateBtn = document.getElementById("btn-generate-report");
    if (generateBtn) {
        generateBtn.addEventListener("click", async () => {
            const errorEl = document.getElementById("report-error");
            const successEl = document.getElementById("report-success");
            const spinner = document.getElementById("generate-spinner");
            const btnText = document.getElementById("btn-generate-text");
            const isRegenerate = btnText && btnText.textContent.includes("Regenerate");
            
            errorEl.classList.add("hidden");
            successEl.classList.add("hidden");
            
            if (spinner) spinner.classList.remove("hidden");
            generateBtn.disabled = true;
            if (btnText) btnText.textContent = "Generating...";

            try {
                const response = await fetch(`/api/cases/${caseId}/report/generate`, {
                    method: "POST"
                });

                const respData = await response.json();
                if (response.ok) {
                    document.getElementById("report-textarea-gene").value = respData.gene_analysis || "";
                    document.getElementById("report-textarea-variant").value = respData.variant_narrative || "";
                    document.getElementById("report-textarea-evidence").value = respData.evidence_records || "";
                    
                    // Update in-memory caseData
                    caseData.report_draft = JSON.stringify(respData);
                    
                    successEl.textContent = "Report draft synthesized successfully via local LLM.";
                    successEl.classList.remove("hidden");
                    setTimeout(() => successEl.classList.add("hidden"), 3000);
                } else {
                    errorEl.textContent = respData.detail || "LLM report generation failed.";
                    errorEl.classList.remove("hidden");
                }
            } catch (err) {
                errorEl.textContent = "Network error communicating with local LLM service.";
                errorEl.classList.remove("hidden");
            } finally {
                if (spinner) spinner.classList.add("hidden");
                generateBtn.disabled = false;
                if (btnText) btnText.textContent = "Regenerate Draft (local LLM)";
            }
        });
    }

    // Download DOCX
    const docxBtn = document.getElementById("btn-download-docx");
    if (docxBtn) {
        docxBtn.addEventListener("click", () => {
            window.open(`/api/cases/${caseId}/report/docx`, '_blank');
        });
    }

    // Drop variant event delegation in report workspace
    const reportWorkspace = document.querySelector(".report-workspace");
    if (reportWorkspace) {
        if (!reportWorkspace.dataset.hasDropListener) {
            reportWorkspace.dataset.hasDropListener = "true";
            reportWorkspace.addEventListener("click", async (e) => {
                if (e.target.classList.contains("btn-drop-variant")) {
                    const key = e.target.getAttribute("data-key");
                    if (confirm(`Are you sure you want to drop this item from the report?`)) {
                        selectedSet.delete(key);
                        e.target.disabled = true;
                        e.target.textContent = "Dropping...";
                        
                        try {
                            const response = await fetch(`/api/cases/${caseId}/variants/confirm`, {
                                method: "POST",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({ selected_hgvsg: Array.from(selectedSet) })
                            });
                            
                            if (response.ok) {
                                const caseResp = await fetch(`/api/cases/${caseId}`);
                                if (caseResp.ok) {
                                    caseData = await caseResp.json();
                                }
                                populateReportInfo();
                                applyFilters();
                                updateProceedButton();
                            } else {
                                alert("Failed to update database variant confirmation state.");
                                e.target.disabled = false;
                                e.target.textContent = "Drop";
                            }
                        } catch (err) {
                            console.error("Network error dropping variant:", err);
                            alert("Network connection error communicating with BEES server.");
                            e.target.disabled = false;
                            e.target.textContent = "Drop";
                        }
                    }
                }
            });
        }
    }
}

// Collapsible Filters Sidebar & Driver Gene Filter helper methods
function toggleSidebar() {
    const sidebar = document.getElementById("filter-sidebar");
    const openBtn = document.getElementById("open-sidebar-btn");
    if (sidebar) {
        sidebar.classList.toggle("collapsed");
        if (sidebar.classList.contains("collapsed")) {
            if (openBtn) openBtn.classList.remove("hidden");
        } else {
            if (openBtn) openBtn.classList.add("hidden");
        }
    }
}

async function loadIndications() {
    try {
        const resp = await fetch("/api/driver-genes/indications");
        if (resp.ok) {
            indicationsList = await resp.json();
            populateIndicationDropdown();
        }
    } catch (e) {
        console.error("Failed to load driver gene indications:", e);
    }
}

function populateIndicationDropdown() {
    const select = document.getElementById("sidebar-indication-filter");
    if (!select) return;
    select.innerHTML = '<option value="">-- Select Indication --</option>';
    
    indicationsList.forEach(ind => {
        const option = document.createElement("option");
        option.value = ind.doid;
        option.textContent = `${ind.name} (${ind.doid}) - ${ind.gene_count} genes`;
        select.appendChild(option);
    });
}

function applyFilters() {
    const geneSearch = document.getElementById("sidebar-gene-search") ? document.getElementById("sidebar-gene-search").value.trim().toLowerCase() : "";
    const minAfVal = document.getElementById("sidebar-af-range") ? parseFloat(document.getElementById("sidebar-af-range").value) : 0;
    
    const t1Checked = document.getElementById("filter-tier1") ? document.getElementById("filter-tier1").checked : true;
    const t2Checked = document.getElementById("filter-tier2") ? document.getElementById("filter-tier2").checked : true;
    const t3Checked = document.getElementById("filter-tier3") ? document.getElementById("filter-tier3").checked : true;
    
    const selectedDoid = document.getElementById("sidebar-indication-filter") ? document.getElementById("sidebar-indication-filter").value : "";
    
    let driverGenes = [];
    if (selectedDoid) {
        const ind = indicationsList.find(i => i.doid === selectedDoid);
        if (ind && ind.genes) {
            driverGenes = ind.genes.map(g => g.toUpperCase());
        }
    }
    
    function getVariantHighestTier(v) {
        const tStr = (v.tier || "Tier 3").toUpperCase();
        if (tStr.includes("TIER 1")) return "Tier 1";
        if (tStr.includes("TIER 2")) return "Tier 2";
        if (tStr.includes("TIER 3")) return "Tier 3";
        return "Tier 4";
    }

    filteredVariantsList = variantsData.filter(v => {
        // Selected variants bypass all filtration to remain visible and checkable
        if (isVariantSelected(v)) {
            return true;
        }

        // Gene search filter
        if (geneSearch && !v.gene.toLowerCase().includes(geneSearch)) {
            return false;
        }
        
        // Somatic AF filter (Altered allelic frequency)
        const variantAf = (v.af !== undefined && v.af !== null) ? parseFloat(v.af) * 100 : 0;
        if (variantAf < minAfVal) {
            return false;
        }
        
        // Indication driver gene match (bypasses tier filters)
        const isDriver = selectedDoid && driverGenes.includes(v.gene.toUpperCase());
        if (selectedDoid && isDriver) {
            return true;
        }
        
        // If an indication is selected and it's NOT a driver gene, we filter it out
        if (selectedDoid && !isDriver) {
            return false;
        }
        
        // Tier checkbox filter
        const tier = getVariantHighestTier(v);
        if (tier === "Tier 1" && !t1Checked) return false;
        if (tier === "Tier 2" && !t2Checked) return false;
        if (tier === "Tier 3" && !t3Checked) return false;
        if (tier === "Tier 4") return false;
        
        return true;
    });
    
    renderVariantsTable();
}



