// State Management
let currentUser = null;
let selectedVcfFile = null;
let dashboardStats = null;
let pollingInterval = null;
let activeReviewCaseId = null;
let selectedVariantsSet = new Set();
let currentCaseData = null;

// HSL Color Generator for Pie Chart slices
const colors = [
    '#10b981', // Emerald
    '#06b6d4', // Cyan
    '#3b82f6', // Blue
    '#8b5cf6', // Violet
    '#ec4899', // Pink
    '#f59e0b', // Amber
    '#ef4444', // Red
    '#a855f7', // Purple
    '#14b8a6', // Teal
    '#6366f1'  // Indigo
];

// --- EVENT LISTENERS & INITIALIZATION ---

document.addEventListener("DOMContentLoaded", () => {
    checkSession();
    setupAuthListeners();
    setupModalListeners();
    setupAutocomplete();
    setupDragAndDrop();
    setupCaseSubmission();
    setupVariantReviewListeners();
});

// --- SESSION & AUTHENTICATION ---

async function checkSession() {
    try {
        const response = await fetch("/api/auth/me");
        if (response.ok) {
            currentUser = await response.ok ? await response.json() : null;
            if (currentUser) {
                showDashboard(currentUser);
            } else {
                showLogin();
            }
        } else {
            showLogin();
        }
    } catch (e) {
        showLogin();
    }
}

function showLogin() {
    document.getElementById("login-container").classList.remove("hidden");
    document.getElementById("dashboard-container").classList.add("hidden");
    currentUser = null;
}

function showDashboard(user) {
    document.getElementById("login-container").classList.add("hidden");
    document.getElementById("dashboard-container").classList.remove("hidden");
    document.getElementById("current-username").textContent = user.username;
    initDashboard();
}

function setupAuthListeners() {
    const loginForm = document.getElementById("login-form");
    const loginError = document.getElementById("login-error");
    
    loginForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        loginError.classList.add("hidden");
        
        const username = document.getElementById("login-username").value.trim();
        const password = document.getElementById("login-password").value;
        
        try {
            const response = await fetch("/api/auth/login", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ username, password })
            });
            
            const data = await response.json();
            if (response.ok) {
                currentUser = { username: data.username, role: data.role };
                showDashboard(currentUser);
                loginForm.reset();
            } else {
                loginError.textContent = data.detail || "Authentication failed.";
                loginError.classList.remove("hidden");
            }
        } catch (err) {
            loginError.textContent = "Server connection failed. Verify server is running.";
            loginError.classList.remove("hidden");
        }
    });

    // Logout
    document.getElementById("logout-button").addEventListener("click", async () => {
        try {
            await fetch("/api/auth/logout", { method: "POST" });
        } catch (e) {}
        showLogin();
    });

    // Register Operator User Form
    const createUserForm = document.getElementById("create-user-form");
    const userError = document.getElementById("user-error");
    const userSuccess = document.getElementById("user-success");

    createUserForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        userError.classList.add("hidden");
        userSuccess.classList.add("hidden");

        const username = document.getElementById("user-username").value.trim();
        const password = document.getElementById("user-password").value;

        try {
            const response = await fetch("/api/auth/add-user", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ username, password })
            });

            const data = await response.json();
            if (response.ok) {
                userSuccess.classList.remove("hidden");
                createUserForm.reset();
                setTimeout(() => {
                    closeModal("user-modal");
                    userSuccess.classList.add("hidden");
                }, 1500);
            } else {
                userError.textContent = data.detail || "Failed to create user.";
                userError.classList.remove("hidden");
            }
        } catch (err) {
            userError.textContent = "Network error. Operator registration failed.";
            userError.classList.remove("hidden");
        }
    });
}

// --- MODAL UTILITIES ---

function setupModalListeners() {
    // Open Case modal
    document.getElementById("create-case-trigger").addEventListener("click", () => {
        openModal("case-modal");
    });

    // Open User registration modal
    document.getElementById("add-user-trigger").addEventListener("click", () => {
        openModal("user-modal");
    });

    // Close buttons on headers and cancel buttons
    document.querySelectorAll(".modal-close, .modal-close-btn").forEach(btn => {
        btn.addEventListener("click", (e) => {
            const overlay = e.target.closest(".modal-overlay");
            if (overlay) {
                closeModal(overlay.id);
            }
        });
    });

    // Click outside to close
    window.addEventListener("click", (e) => {
        if (e.target.classList.contains("modal-overlay")) {
            closeModal(e.target.id);
        }
    });
}

function openModal(modalId) {
    document.getElementById(modalId).classList.remove("hidden");
}

function closeModal(modalId) {
    document.getElementById(modalId).classList.add("hidden");
    if (modalId === "case-modal") {
        document.getElementById("create-case-form").reset();
        clearDoidSelection();
        clearSelectedFile();
        document.getElementById("case-error").classList.add("hidden");
        document.getElementById("case-success").classList.add("hidden");
    } else if (modalId === "user-modal") {
        document.getElementById("create-user-form").reset();
        document.getElementById("user-error").classList.add("hidden");
        document.getElementById("user-success").classList.add("hidden");
    }
}

// --- DASHBOARD LOADER ---

async function initDashboard() {
    await fetchDashboardStats();
    await fetchCases();
}

async function fetchDashboardStats() {
    try {
        const response = await fetch("/api/cases/stats");
        if (response.ok) {
            dashboardStats = await response.json();
            
            document.getElementById("stat-total-cases").textContent = dashboardStats.total_cases;
            document.getElementById("stat-active-cases").textContent = dashboardStats.active_cases;
            document.getElementById("stat-archived-cases").textContent = dashboardStats.archived_cases;
            
            renderPieChart(dashboardStats.indication_distribution);
        }
    } catch (e) {
        console.error("Error loading metrics stats:", e);
    }
}

async function fetchCases() {
    const tableBody = document.getElementById("cases-table-body");
    try {
        const response = await fetch("/api/cases");
        if (response.ok) {
            const cases = await response.json();
            tableBody.innerHTML = "";
            
            if (cases.length === 0) {
                tableBody.innerHTML = `
                    <tr>
                        <td colspan="9" class="table-placeholder">No clinical cases ingested yet. Use the intake form to register the first patient.</td>
                    </tr>`;
                return;
            }
            
            // Sort cases by upload timestamp descending (newest first)
            cases.sort((a, b) => new Date(b.upload_timestamp) - new Date(a.upload_timestamp));

            let hasActiveProcessing = false;

            cases.forEach(c => {
                if (c.status === "Processing") {
                    hasActiveProcessing = true;
                }

                const tr = document.createElement("tr");
                if (c.is_archived) tr.classList.add("row-archived");

                const formattedDate = new Date(c.upload_timestamp).toLocaleString();
                
                // Get filename from path
                const parts = c.vcf_path.split("/");
                const filename = parts[parts.length - 1];

                // Action buttons based on status
                let actionButton = "";
                if (c.status === "Pending" || c.status === "Failed") {
                    actionButton = `<button onclick="processCase(${c.id})" class="btn btn-primary btn-sm">Process Case</button>`;
                } else if (c.status === "Processing") {
                    actionButton = `<button disabled class="btn btn-secondary btn-sm" style="cursor: not-allowed; opacity: 0.7;">Processing...</button>`;
                } else if (c.status === "Completed") {
                    actionButton = `<button onclick="viewVariants(${c.id})" class="btn btn-secondary btn-sm" style="border-color: var(--color-primary); color: var(--color-primary);">Review Variants</button>`;
                }

                const statusTitle = c.status_message ? escapeHtml(c.status_message) : c.status;

                tr.innerHTML = `
                    <td><span class="case-id">CASE-${c.id.toString().padStart(4, '0')}</span></td>
                    <td>
                        <div class="case-demog">
                            <strong>${escapeHtml(c.patient_name)}</strong>
                            <span class="case-demog-sub">Age ${c.patient_age} • ${c.patient_sex}</span>
                        </div>
                    </td>
                    <td>
                        <div class="case-demog">
                            <strong>${escapeHtml(c.indication_name)}</strong>
                            <span class="case-demog-sub">${escapeHtml(c.indication_doid)}</span>
                        </div>
                    </td>
                    <td><span class="transcript-ref">${c.transcript_db}</span></td>
                    <td><span class="genome-ref-badge">${escapeHtml(c.reference_genome)}</span></td>
                    <td>
                        <div class="case-path" title="${escapeHtml(c.vcf_path)}">
                            ${escapeHtml(filename)}
                        </div>
                    </td>
                    <td>${formattedDate}</td>
                    <td>
                        <span class="status-badge status-${c.status.toLowerCase()}" title="${statusTitle}">
                            ${c.status}
                        </span>
                    </td>
                    <td>${actionButton}</td>
                `;
                tableBody.appendChild(tr);
            });

            if (hasActiveProcessing) {
                startPolling();
            } else {
                stopPolling();
            }
        }
    } catch (e) {
        tableBody.innerHTML = `<tr><td colspan="9" class="table-placeholder alert-danger">Error loading case records from pipeline server.</td></tr>`;
    }
}

// --- INTERACTIVE SVG PIE/DONUT CHART ---

function renderPieChart(distribution) {
    const container = document.getElementById("pie-chart-container");
    const legend = document.getElementById("chart-legend");
    container.innerHTML = "";
    legend.innerHTML = "";

    const entries = Object.entries(distribution);
    const total = entries.reduce((sum, [_, count]) => sum + count, 0);

    if (total === 0) {
        // Render Empty Chart State
        container.innerHTML = `
            <svg id="dynamic-pie-chart" viewBox="0 0 200 200" width="100%" height="100%">
                <circle cx="100" cy="100" r="70" fill="transparent" stroke="#1b253b" stroke-width="30" />
                <text x="100" y="105" text-anchor="middle" fill="#64748b" font-size="11" font-weight="600">NO CASES</text>
            </svg>`;
        legend.innerHTML = `<div class="table-placeholder" style="padding:10px; font-size:0.8rem;">No indications to display</div>`;
        return;
    }

    // Create dynamic SVG
    const svgNamespace = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(svgNamespace, "svg");
    svg.setAttribute("viewBox", "0 0 200 200");
    svg.setAttribute("width", "100%");
    svg.setAttribute("height", "100%");

    // Middle hollow text element
    const centerTextValue = document.createElementNS(svgNamespace, "text");
    centerTextValue.setAttribute("x", "100");
    centerTextValue.setAttribute("y", "98");
    centerTextValue.setAttribute("text-anchor", "middle");
    centerTextValue.setAttribute("fill", "#ffffff");
    centerTextValue.setAttribute("font-size", "18");
    centerTextValue.setAttribute("font-weight", "bold");
    centerTextValue.textContent = total;

    const centerTextLabel = document.createElementNS(svgNamespace, "text");
    centerTextLabel.setAttribute("x", "100");
    centerTextLabel.setAttribute("y", "116");
    centerTextLabel.setAttribute("text-anchor", "middle");
    centerTextLabel.setAttribute("fill", "#94a3b8");
    centerTextLabel.setAttribute("font-size", "9");
    centerTextLabel.setAttribute("font-weight", "600");
    centerTextLabel.textContent = "TOTAL CASES";
    
    svg.appendChild(centerTextValue);
    svg.appendChild(centerTextLabel);

    let accumulatedAngle = -Math.PI / 2; // Start from top 12 o'clock

    entries.forEach(([name, count], index) => {
        const percentage = count / total;
        const sliceAngle = percentage * Math.PI * 2;
        const color = colors[index % colors.length];

        // Coordinate calculations for pie slices (Radius 70)
        const r = 70;
        const cx = 100;
        const cy = 100;

        const x1 = cx + r * Math.cos(accumulatedAngle);
        const y1 = cy + r * Math.sin(accumulatedAngle);
        
        accumulatedAngle += sliceAngle;
        
        const x2 = cx + r * Math.cos(accumulatedAngle);
        const y2 = cy + r * Math.sin(accumulatedAngle);

        const largeArcFlag = sliceAngle > Math.PI ? 1 : 0;

        // Donut slice: drawn by setting thin fills, but standard way is a path.
        // We'll draw path segments using: M x_center y_center L x1 y1 A r r 0 largeArcFlag 1 x2 y2 Z
        // To make it a donut slice rather than pie, we can make it a simple thick stroke arc with no fill!
        // For a donut arc: radius 70, stroke-width 24, transparent fill.
        // SVG Arc format: M x1 y1 A r r 0 largeArcFlag 1 x2 y2
        let pathData = "";
        
        // Single item edge case (full circle)
        if (percentage === 1) {
            pathData = `M 100 30 A 70 70 0 1 1 99.9 30 Z`;
        } else {
            pathData = `M ${x1} ${y1} A ${r} ${r} 0 ${largeArcFlag} 1 ${x2} ${y2}`;
        }

        const path = document.createElementNS(svgNamespace, "path");
        path.setAttribute("d", pathData);
        path.setAttribute("fill", "none");
        path.setAttribute("stroke", color);
        path.setAttribute("stroke-width", "22");
        path.setAttribute("style", "cursor: pointer; transition: stroke-width 0.2s ease, opacity 0.2s ease;");
        
        // Hover interactions
        path.addEventListener("mouseover", () => {
            path.setAttribute("stroke-width", "28");
            centerTextValue.textContent = count;
            centerTextLabel.textContent = name.substring(0, 16).toUpperCase();
            highlightLegendItem(index, true);
        });

        path.addEventListener("mouseout", () => {
            path.setAttribute("stroke-width", "22");
            centerTextValue.textContent = total;
            centerTextLabel.textContent = "TOTAL CASES";
            highlightLegendItem(index, false);
        });

        svg.appendChild(path);

        // Add legend list item
        const legendItem = document.createElement("div");
        legendItem.className = "legend-item";
        legendItem.id = `legend-item-${index}`;
        legendItem.innerHTML = `
            <div class="legend-label-group">
                <span class="legend-color-dot" style="background-color: ${color}"></span>
                <span>${escapeHtml(name)}</span>
            </div>
            <span class="legend-val">${count} (${Math.round(percentage * 100)}%)</span>
        `;
        
        legendItem.addEventListener("mouseover", () => {
            path.setAttribute("stroke-width", "28");
            centerTextValue.textContent = count;
            centerTextLabel.textContent = name.substring(0, 16).toUpperCase();
        });
        legendItem.addEventListener("mouseout", () => {
            path.setAttribute("stroke-width", "22");
            centerTextValue.textContent = total;
            centerTextLabel.textContent = "TOTAL CASES";
        });

        legend.appendChild(legendItem);
    });

    container.appendChild(svg);
}

function highlightLegendItem(index, active) {
    const item = document.getElementById(`legend-item-${index}`);
    if (item) {
        if (active) {
            item.style.backgroundColor = "rgba(255, 255, 255, 0.08)";
            item.style.borderColor = "rgba(255, 255, 255, 0.15)";
        } else {
            item.style.backgroundColor = "rgba(255, 255, 255, 0.02)";
            item.style.borderColor = "transparent";
        }
    }
}

// --- DISEASE ONTOLOGY (DOID) autocomplete typeahead ---

function setupAutocomplete() {
    const searchInput = document.getElementById("case-indication-search");
    const resultsContainer = document.getElementById("doid-autocomplete-results");
    const doidHidden = document.getElementById("case-indication-doid");
    const nameHidden = document.getElementById("case-indication-name");
    const badge = document.getElementById("doid-selected-badge");
    const badgeText = document.getElementById("badge-text");
    const badgeRemove = document.getElementById("badge-remove");

    let timeout = null;

    searchInput.addEventListener("input", () => {
        clearTimeout(timeout);
        const query = searchInput.value.trim();

        if (query.length < 2) {
            resultsContainer.innerHTML = "";
            resultsContainer.classList.add("hidden");
            return;
        }

        // Debounce search requests to 200ms
        timeout = setTimeout(async () => {
            try {
                const response = await fetch(`/api/doid/search?q=${encodeURIComponent(query)}`);
                if (response.ok) {
                    const matches = await response.json();
                    renderAutocompleteMatches(matches);
                }
            } catch (err) {
                console.error("DOID search request failed:", err);
            }
        }, 200);
    });

    function renderAutocompleteMatches(matches) {
        resultsContainer.innerHTML = "";
        
        if (matches.length === 0) {
            resultsContainer.innerHTML = `<div class="autocomplete-item" style="cursor: default; color: #64748b;">No matching indications found</div>`;
            resultsContainer.classList.remove("hidden");
            return;
        }

        matches.forEach(item => {
            const div = document.createElement("div");
            div.className = "autocomplete-item";
            div.innerHTML = `<strong>${escapeHtml(item.name)}</strong> (${escapeHtml(item.doid)})`;
            div.addEventListener("click", () => {
                selectDoid(item.doid, item.name);
            });
            resultsContainer.appendChild(div);
        });

        resultsContainer.classList.remove("hidden");
    }

    function selectDoid(doid, name) {
        doidHidden.value = doid;
        nameHidden.value = name;
        
        badgeText.textContent = `${name} (${doid})`;
        badge.classList.remove("hidden");
        
        searchInput.value = "";
        searchInput.classList.add("hidden");
        resultsContainer.classList.add("hidden");
    }

    badgeRemove.addEventListener("click", clearDoidSelection);

    // Hide dropdown if clicked outside
    document.addEventListener("click", (e) => {
        if (!e.target.closest(".relative")) {
            resultsContainer.classList.add("hidden");
        }
    });
}

function clearDoidSelection() {
    document.getElementById("case-indication-doid").value = "";
    document.getElementById("case-indication-name").value = "";
    document.getElementById("doid-selected-badge").classList.add("hidden");
    
    const searchInput = document.getElementById("case-indication-search");
    searchInput.classList.remove("hidden");
    searchInput.value = "";
}

// --- FILE DRAG & DROP ZONE HANDLING ---

function setupDragAndDrop() {
    const dropzone = document.getElementById("vcf-dropzone");
    const fileInput = document.getElementById("case-vcf-file");
    const fileInfo = document.getElementById("dropzone-file-info");
    const fileNameLabel = fileInfo.querySelector(".file-name-label");
    const removeButton = document.getElementById("remove-file-button");
    const prompt = dropzone.querySelector(".dropzone-prompt");

    // Click zone to trigger native browser selector
    dropzone.addEventListener("click", (e) => {
        if (e.target !== removeButton && !removeButton.contains(e.target)) {
            fileInput.click();
        }
    });

    fileInput.addEventListener("change", (e) => {
        if (fileInput.files.length > 0) {
            handleFileSelection(fileInput.files[0]);
        }
    });

    // Drag-over styling shifts
    ["dragenter", "dragover"].forEach(eventName => {
        dropzone.addEventListener(eventName, (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropzone.classList.add("dragover");
        }, false);
    });

    ["dragleave", "drop"].forEach(eventName => {
        dropzone.addEventListener(eventName, (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropzone.classList.remove("dragover");
        }, false);
    });

    dropzone.addEventListener("drop", (e) => {
        const dt = e.dataTransfer;
        const files = dt.files;
        if (files.length > 0) {
            handleFileSelection(files[0]);
        }
    });

    removeButton.addEventListener("click", (e) => {
        e.stopPropagation();
        clearSelectedFile();
    });
}

function handleFileSelection(file) {
    const name = file.name;
    
    // Preliminary client extension validate
    if (!name.endsWith(".vcf") && !name.endsWith(".vcf.gz")) {
        alert("Client check: Invalid file format. Please upload a .vcf or .vcf.gz genomic file.");
        clearSelectedFile();
        return;
    }

    selectedVcfFile = file;
    
    const fileInfo = document.getElementById("dropzone-file-info");
    const fileNameLabel = fileInfo.querySelector(".file-name-label");
    const prompt = document.querySelector(".dropzone-prompt");
    
    // Show selected details
    const fileSizeMB = (file.size / (1024 * 1024)).toFixed(2);
    fileNameLabel.textContent = `${name} (${fileSizeMB} MB)`;
    fileInfo.classList.remove("hidden");
    prompt.classList.add("hidden");
}

function clearSelectedFile() {
    selectedVcfFile = null;
    document.getElementById("case-vcf-file").value = "";
    document.getElementById("dropzone-file-info").classList.add("hidden");
    document.querySelector(".dropzone-prompt").classList.remove("hidden");
}

// --- NEW CASE SUBMISSION ---

function setupCaseSubmission() {
    const form = document.getElementById("create-case-form");
    const caseError = document.getElementById("case-error");
    const caseSuccess = document.getElementById("case-success");
    const submitBtn = document.getElementById("submit-case-button");

    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        caseError.classList.add("hidden");
        caseSuccess.classList.add("hidden");

        const patientName = document.getElementById("case-patient-name").value.trim();
        const patientAge = document.getElementById("case-patient-age").value;
        const patientSex = document.getElementById("case-patient-sex").value;
        const transcriptDb = document.getElementById("case-transcript-db").value;
        const referenceGenome = document.getElementById("case-reference-genome").value;
        
        const indicationDoid = document.getElementById("case-indication-doid").value;
        const indicationName = document.getElementById("case-indication-name").value;

        // Validate DOID has been selected
        if (!indicationDoid || !indicationName) {
            caseError.textContent = "Please select a valid disease indication from the autocomplete dropdown list.";
            caseError.classList.remove("hidden");
            return;
        }

        // Validate File Selected
        if (!selectedVcfFile) {
            caseError.textContent = "Please drag & drop or select a valid genomic VCF file to submit.";
            caseError.classList.remove("hidden");
            return;
        }

        // Disable button & change label to show progress
        submitBtn.disabled = true;
        submitBtn.textContent = "Uploading & Ingesting...";

        // Construct multi-part payload
        const formData = new FormData();
        formData.append("patient_name", patientName);
        formData.append("patient_age", patientAge);
        formData.append("patient_sex", patientSex);
        formData.append("transcript_db", transcriptDb);
        formData.append("reference_genome", referenceGenome);
        formData.append("indication_doid", indicationDoid);
        formData.append("indication_name", indicationName);
        formData.append("vcf_file", selectedVcfFile);

        try {
            const response = await fetch("/api/cases", {
                method: "POST",
                body: formData
            });

            const data = await response.json();
            if (response.ok) {
                caseSuccess.classList.remove("hidden");
                form.reset();
                clearDoidSelection();
                clearSelectedFile();
                
                // Refresh dashboard case list & metrics
                await initDashboard();

                setTimeout(() => {
                    closeModal("case-modal");
                    caseSuccess.classList.add("hidden");
                }, 1000);
            } else {
                caseError.textContent = data.detail || "Ingestion rejected by pipeline server.";
                caseError.classList.remove("hidden");
            }
        } catch (err) {
            caseError.textContent = "Network error occurred during VCF ingestion upload.";
            caseError.classList.remove("hidden");
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = "Securely Ingest Case";
        }
    });
}

// --- POLLING CONTROLS ---

function startPolling() {
    if (pollingInterval) return;
    console.log("Starting dashboard status polling...");
    pollingInterval = setInterval(async () => {
        await fetchDashboardStats();
        // Fetch cases only, database status changes will trigger stop if none are processing
        const tableBody = document.getElementById("cases-table-body");
        try {
            const response = await fetch("/api/cases");
            if (response.ok) {
                const cases = await response.json();
                let hasActiveProcessing = false;
                
                cases.sort((a, b) => new Date(b.upload_timestamp) - new Date(a.upload_timestamp));
                
                // Re-render table dynamically during polling to update badges without complete redraw flicker
                tableBody.innerHTML = "";
                cases.forEach(c => {
                    if (c.status === "Processing") {
                        hasActiveProcessing = true;
                    }
                    
                    const tr = document.createElement("tr");
                    if (c.is_archived) tr.classList.add("row-archived");

                    const formattedDate = new Date(c.upload_timestamp).toLocaleString();
                    const parts = c.vcf_path.split("/");
                    const filename = parts[parts.length - 1];

                    let actionButton = "";
                    if (c.status === "Pending" || c.status === "Failed") {
                        actionButton = `<button onclick="processCase(${c.id})" class="btn btn-primary btn-sm">Process Case</button>`;
                    } else if (c.status === "Processing") {
                        actionButton = `<button disabled class="btn btn-secondary btn-sm" style="cursor: not-allowed; opacity: 0.7;">Processing...</button>`;
                    } else if (c.status === "Completed") {
                        actionButton = `<button onclick="viewVariants(${c.id})" class="btn btn-secondary btn-sm" style="border-color: var(--color-primary); color: var(--color-primary);">Review Variants</button>`;
                    }

                    const statusTitle = c.status_message ? escapeHtml(c.status_message) : c.status;

                    tr.innerHTML = `
                        <td><span class="case-id">CASE-${c.id.toString().padStart(4, '0')}</span></td>
                        <td>
                            <div class="case-demog">
                                <strong>${escapeHtml(c.patient_name)}</strong>
                                <span class="case-demog-sub">Age ${c.patient_age} • ${c.patient_sex}</span>
                            </div>
                        </td>
                        <td>
                            <div class="case-demog">
                                <strong>${escapeHtml(c.indication_name)}</strong>
                                <span class="case-demog-sub">${escapeHtml(c.indication_doid)}</span>
                            </div>
                        </td>
                        <td><span class="transcript-ref">${c.transcript_db}</span></td>
                        <td><span class="genome-ref-badge">${escapeHtml(c.reference_genome)}</span></td>
                        <td>
                            <div class="case-path" title="${escapeHtml(c.vcf_path)}">
                                ${escapeHtml(filename)}
                            </div>
                        </td>
                        <td>${formattedDate}</td>
                        <td>
                            <span class="status-badge status-${c.status.toLowerCase()}" title="${statusTitle}">
                                ${c.status}
                            </span>
                        </td>
                        <td>${actionButton}</td>
                    `;
                    tableBody.appendChild(tr);
                });
                
                if (!hasActiveProcessing) {
                    stopPolling();
                }
            }
        } catch (e) {
            console.error("Polling fetch failed:", e);
        }
    }, 3000);
}

function stopPolling() {
    if (pollingInterval) {
        console.log("Stopping dashboard status polling.");
        clearInterval(pollingInterval);
        pollingInterval = null;
    }
}

// --- PIPELINE ACTIONS ---

async function processCase(caseId) {
    try {
        const response = await fetch(`/api/cases/${caseId}/process`, {
            method: "POST"
        });
        if (response.ok) {
            await fetchCases(); // Initiates list and starts polling
        } else {
            const data = await response.json();
            alert(`Failed to start processing: ${data.detail || "Server error"}`);
        }
    } catch (err) {
        alert(`Network error starting case pipeline: ${err}`);
    }
}

// --- VARIANT REVIEW CONTROLLERS ---

function viewVariants(caseId) {
    window.open(`/cases/${caseId}/review`, '_blank');
}

function renderVariantReviewTable(variants) {
    const tableBody = document.getElementById("variants-table-body");
    tableBody.innerHTML = "";

    if (variants.length === 0) {
        tableBody.innerHTML = `
            <tr>
                <td colspan="9" class="table-placeholder">Zero variants survived the hard-filtering criteria (HIGH/MODERATE impact and gnomAD AF &le; 0.01).</td>
            </tr>`;
        return;
    }

    variants.forEach((v, index) => {
        const tr = document.createElement("tr");
        
        const afStr = v.gnomad_af === 0 ? "Novel (0.0000)" : v.gnomad_af.toFixed(5);
        const impactClass = v.impact.toLowerCase() === "high" ? "impact-high" : "impact-moderate";

        tr.innerHTML = `
            <td style="text-align: center;">
                <input type="checkbox" class="variant-select" data-hgvsg="${v.hgvsg}">
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
                selectedVariantsSet.add(hgvsg);
            } else {
                selectedVariantsSet.delete(hgvsg);
            }
            
            // Keep "Select All" checkbox state in sync
            const allChecked = Array.from(checkBoxes).every(box => box.checked);
            document.getElementById("select-all-variants").checked = allChecked;
            
            updateProceedButton();
        });
    });
}

function updateProceedButton() {
    const proceedBtn = document.getElementById("proceed-variants-button");
    const count = selectedVariantsSet.size;
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

function setupVariantReviewListeners() {
    const selectAll = document.getElementById("select-all-variants");
    
    // Select All Handler
    selectAll.addEventListener("change", (e) => {
        const checkBoxes = document.querySelectorAll("#variants-table-body .variant-select");
        checkBoxes.forEach(cb => {
            cb.checked = e.target.checked;
            const hgvsg = cb.getAttribute("data-hgvsg");
            if (e.target.checked) {
                selectedVariantsSet.add(hgvsg);
            } else {
                selectedVariantsSet.delete(hgvsg);
            }
        });
        updateProceedButton();
    });

    // Close button click
    document.getElementById("review-close-button").addEventListener("click", () => {
        document.getElementById("variant-review-container").classList.add("hidden");
    });
    document.getElementById("review-cancel-button").addEventListener("click", () => {
        document.getElementById("variant-review-container").classList.add("hidden");
    });

    // Proceed variants button submit
    const proceedBtn = document.getElementById("proceed-variants-button");
    const errorDiv = document.getElementById("review-error");
    const successDiv = document.getElementById("review-success");

    proceedBtn.addEventListener("click", async () => {
        errorDiv.classList.add("hidden");
        successDiv.classList.add("hidden");

        const selectedList = Array.from(selectedVariantsSet);
        if (selectedList.length === 0) return;

        proceedBtn.disabled = true;
        proceedBtn.textContent = "Submitting Review...";

        try {
            const response = await fetch(`/api/cases/${activeReviewCaseId}/variants/confirm`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ selected_hgvsg: selectedList })
            });

            const data = await response.json();
            if (response.ok) {
                successDiv.textContent = data.message || "Variants successfully confirmed.";
                successDiv.classList.remove("hidden");
                
                // Console trace log as required
                console.log(`[CLINICAL VARIANTS CONFIRMED] Case ID: CASE-${activeReviewCaseId} | Selected:`, selectedList);
                
                setTimeout(() => {
                    document.getElementById("variant-review-container").classList.add("hidden");
                    successDiv.classList.add("hidden");
                }, 1500);
            } else {
                errorDiv.textContent = data.detail || "Validation check rejected selections.";
                errorDiv.classList.remove("hidden");
            }
        } catch (err) {
            errorDiv.textContent = "Network error occurred sending selected variants.";
            errorDiv.classList.remove("hidden");
        } finally {
            proceedBtn.disabled = false;
            updateProceedButton();
        }
    });
}

// Helper to escape HTML tags to prevent XSS in table injections
function escapeHtml(str) {
    if (!str) return '';
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}
