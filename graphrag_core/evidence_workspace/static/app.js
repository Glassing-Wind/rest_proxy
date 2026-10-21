"use strict";

// The token and preview bytes remain in this page's memory. Nothing is stored in
// browser storage, and source content is never interpreted as HTML or a URL.
const state = {
    token: "", cases: [], currentCase: null, relationshipId: null, sourceId: null,
    citation: null, query: "", reviewFilter: "all", busy: false,
    caseRequest: 0, sourceRequest: 0, sourceAbort: null, previewUrl: null,
    objectUrls: new Set(), session: 0, listRequest: 0, requests: new Set(),
};
const $ = (id) => document.getElementById(id);
const evidenceLabels = {
    observation: "Observation", source_claim: "Source claim", inference: "Inference",
};
const reviewLabels = {
    unreviewed: "Unreviewed", accepted: "Accepted", rejected: "Rejected", amended: "Amended",
};

function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
}

function valueText(value) {
    if (value === undefined || value === null || value === "") return "Not supplied";
    return typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
}

function labelText(value) {
    return String(value || "Unknown").replace(/_/g, " ");
}

function badge(value, type) {
    const labels = type === "evidence" ? evidenceLabels : reviewLabels;
    const known = Object.prototype.hasOwnProperty.call(labels, value);
    return node("span", "badge" + (known ? " " + value : ""), labels[value] || labelText(value));
}

function showNotice(message, error = false) {
    $("notice").textContent = message;
    $("notice").className = "notice" + (error ? " error" : "");
    $("notice").hidden = !message;
    document.querySelectorAll(".dialog-notice").forEach((notice) => notice.remove());
    const openDialog = document.querySelector("dialog[open]");
    if (openDialog && error && message) {
        const notice = node("p", "notice error dialog-notice", message);
        notice.setAttribute("role", "alert");
        openDialog.querySelector(".dialog-actions").before(notice);
    }
}

function setBusy(busy) {
    state.busy = busy;
    document.querySelectorAll("[data-mutation]").forEach((control) => { control.disabled = busy; });
    $("case-select").disabled = busy;
    $("refresh-cases").disabled = busy;
    $("report-button").disabled = busy || !state.currentCase;
    $("backup-button").disabled = busy || !state.currentCase;
    $("workspace").setAttribute("aria-busy", String(busy));
}

class ApiError extends Error {
    constructor(message, status) { super(message); this.status = status; }
}

function assertSession(session) {
    if (session !== state.session || !state.token) throw new DOMException("Workspace session ended.", "AbortError");
}

function abortRequests() {
    state.requests.forEach((controller) => controller.abort());
    state.requests.clear();
}

async function api(path, options = {}) {
    const session = state.session;
    assertSession(session);
    const controller = new AbortController();
    const previousSignal = options.signal;
    const abort = () => controller.abort();
    if (previousSignal?.aborted) controller.abort();
    else previousSignal?.addEventListener("abort", abort, { once: true });
    state.requests.add(controller);
    const release = () => {
        state.requests.delete(controller);
        previousSignal?.removeEventListener("abort", abort);
    };
    const headers = new Headers(options.headers || {});
    headers.set("Authorization", "Bearer " + state.token);
    try {
        const response = await fetch(path, {
            ...options, signal: controller.signal, headers, credentials: "omit", cache: "no-store", referrerPolicy: "no-referrer",
        });
        assertSession(session);
        if (!response.ok) {
            let message = "Local server returned HTTP " + response.status + ".";
            try {
                const detail = await response.json();
                message = valueText(detail.detail || detail.error || message);
            } catch (_) { /* Do not display an untrusted HTML error response. */ }
            assertSession(session);
            throw new ApiError(message, response.status);
        }
        const read = async (method) => {
            try {
                const value = await response[method]();
                assertSession(session);
                return value;
            } finally { release(); }
        };
        return { json: () => read("json"), blob: () => read("blob") };
    } catch (error) {
        release();
        throw error;
    }
}

async function jsonApi(path, body, options = {}) {
    const request = { ...options };
    if (body !== undefined) {
        request.method = "POST";
        request.body = JSON.stringify(body);
        request.headers = { ...request.headers, "Content-Type": "application/json" };
    }
    return (await api(path, request)).json();
}

function casePath(caseId = state.currentCase?.case_id) {
    return "/api/cases/" + encodeURIComponent(caseId);
}

function sourcePath(caseId, sourceId) {
    return casePath(caseId) + "/sources/" + encodeURIComponent(sourceId);
}

function dropPreview() {
    if (state.previewUrl) {
        URL.revokeObjectURL(state.previewUrl);
        state.objectUrls.delete(state.previewUrl);
        state.previewUrl = null;
    }
}

function disconnect(message = "") {
    state.session += 1;
    state.token = "";
    abortRequests();
    state.listRequest += 1;
    state.caseRequest += 1;
    state.sourceRequest += 1;
    state.sourceAbort?.abort();
    state.objectUrls.forEach((url) => URL.revokeObjectURL(url));
    state.objectUrls.clear();
    state.previewUrl = null;
    state.currentCase = null;
    state.cases = [];
    state.relationshipId = null;
    state.sourceId = null;
    state.citation = null;
    state.query = "";
    state.reviewFilter = "all";
    $("import-form").reset();
    $("restore-form").reset();
    $("relationship-detail").replaceChildren();
    $("source-inspector").replaceChildren();
    $("relationship-list").replaceChildren();
    $("case-select").replaceChildren();
    $("source-select").replaceChildren();
    $("coverage-panel").replaceChildren();
    $("relationship-search").value = "";
    $("review-filter").value = "all";
    $("relationship-count").textContent = "0";
    $("notice").textContent = "";
    $("notice").hidden = true;
    setBusy(false);
    $("case-title").textContent = "Your evidence workspace";
    $("case-subtitle").textContent = "Start with the synthetic demo or import an evidence package.";
    $("case-workspace").hidden = true;
    $("coverage-panel").hidden = true;
    $("empty-workspace").hidden = false;
    document.querySelectorAll("dialog[open]").forEach((dialog) => dialog.close());
    $("app-shell").hidden = true;
    $("auth-screen").hidden = false;
    $("access-token").value = "";
    $("auth-message").textContent = message;
    $("access-token").focus();
}

async function handleError(error, mutation = false) {
    const session = state.session;
    if (error.name === "AbortError") return;
    if (error.status === 401 || error.status === 403) {
        disconnect("The local server did not accept this token. Enter its current access token.");
        return;
    }
    if (error.status === 409 && mutation && state.currentCase) {
        const caseId = state.currentCase.case_id;
        try { await loadCase(caseId); } catch (_) { /* Retain the initial conflict explanation. */ }
        if (session !== state.session || !state.token) return;
        showNotice("The case changed before this action was saved. The latest case has been reloaded. Inspect the current evidence and review history before submitting again; your action was not replayed.", true);
        return;
    }
    showNotice(error.message || "Unable to reach the local evidence server.", true);
}

async function mutate(action) {
    if (state.busy) return;
    const session = state.session;
    setBusy(true);
    try { assertSession(session); await action(session); }
    catch (error) { if (session === state.session) await handleError(error, true); }
    finally { if (session === state.session) setBusy(false); }
}

async function connect(token) {
    state.session += 1;
    abortRequests();
    const session = state.session;
    state.token = token.trim();
    if (!state.token) return;
    $("access-token").value = "";
    $("auth-message").textContent = "Connecting to your local workspace…";
    try {
        await refreshCases();
        assertSession(session);
        $("auth-screen").hidden = true;
        $("app-shell").hidden = false;
        $("auth-message").textContent = "";
        showNotice("");
        $("workspace").focus();
    } catch (error) {
        if (error.name === "AbortError" || session !== state.session) return;
        disconnect(error.status === 401 || error.status === 403
            ? "The local server did not accept this token."
            : "Could not open the local workspace. Check that its server is running.");
    }
}

async function refreshCases(selectId) {
    const requestId = ++state.listRequest;
    const payload = await jsonApi("/api/cases");
    if (requestId !== state.listRequest) throw new DOMException("Case list request superseded.", "AbortError");
    state.cases = payload.cases || [];
    const select = $("case-select");
    select.replaceChildren(node("option", "", "Select a case"));
    select.firstElementChild.value = "";
    state.cases.forEach((item) => {
        const option = node("option", "", (item.title || "Untitled case") + " · " + item.case_id);
        option.value = item.case_id;
        select.append(option);
    });
    const desiredId = selectId || state.currentCase?.case_id;
    if (desiredId && state.cases.some((item) => item.case_id === desiredId)) {
        select.value = desiredId;
        if (selectId) await loadCase(desiredId);
    }
}

async function loadCase(caseId) {
    const session = state.session;
    const requestId = ++state.caseRequest;
    const payload = await jsonApi(casePath(caseId));
    if (requestId !== state.caseRequest || !state.token) throw new DOMException("Case request superseded.", "AbortError");
    const changedCase = state.currentCase?.case_id !== caseId;
    state.currentCase = payload;
    if (changedCase) {
        state.query = "";
        state.reviewFilter = "all";
        $("relationship-search").value = "";
        $("review-filter").value = "all";
        state.relationshipId = null;
        state.sourceId = null;
        state.citation = null;
    }
    if (!payload.relationships.some((item) => item.id === state.relationshipId)) {
        state.relationshipId = payload.relationships[0]?.id || null;
    }
    $("case-select").value = caseId;
    $("case-title").textContent = payload.title || payload.case_id;
    $("case-subtitle").textContent = "Case " + payload.case_id + " · Revision " + payload.revision + " · Review decisions preserve the original evidence.";
    $("empty-workspace").hidden = true;
    $("case-workspace").hidden = false;
    const relation = currentRelationship();
    if (state.citation) {
        const previous = state.citation;
        state.citation = relation?.citations?.find((citation) => citation.source_id === previous.source_id
            && citation.start_line === previous.start_line && citation.end_line === previous.end_line
            && JSON.stringify(citation.bbox) === JSON.stringify(previous.bbox)) || null;
    }
    if (state.sourceId && !payload.sources.some((source) => source.id === state.sourceId)) state.sourceId = null;
    if (!state.sourceId) {
        state.citation = relation?.citations?.[0] || null;
        state.sourceId = state.citation?.source_id || payload.sources[0]?.id || null;
    }
    renderCoverage();
    renderRelationships();
    renderDetail();
    renderSourceSelect();
    await inspectSource(state.sourceId, state.citation);
    assertSession(session);
    setBusy(state.busy);
}

function currentRelationship() {
    return state.currentCase?.relationships.find((item) => item.id === state.relationshipId);
}

function entity(id) {
    return state.currentCase.entities.find((item) => item.id === id) || { id, label: id, kind: "Unknown entity" };
}

function renderCoverage() {
    const data = state.currentCase;
    const coverage = data.coverage || {};
    const panel = $("coverage-panel");
    panel.replaceChildren();
    const unreviewed = data.relationships.filter((item) => !item.review || item.review.decision === "unreviewed").length;
    [[data.sources.length, "source records"], [data.relationships.length, "relationships"], [unreviewed, "awaiting review"]].forEach(([value, label]) => {
        const metric = node("div", "coverage-metric");
        metric.append(node("strong", "", value), node("span", "", label));
        panel.append(metric);
    });
    const description = node("div", "coverage-description");
    description.append(node("strong", "", "Importer-declared coverage: " + labelText(coverage.status || "unknown")));
    const notes = Array.isArray(coverage.notes) ? coverage.notes.join(" ") : valueText(coverage.notes);
    description.append(node("p", "", notes));
    description.append(node("p", "", "Known coverage gaps: " + (coverage.unavailable_source_count || 0)
        + " unavailable source records; " + (coverage.unlisted_source_count || 0) + " expected sources not listed."));
    if (coverage.expected_source_count !== undefined && coverage.expected_source_count !== null) {
        description.append(node("p", "", "Expected sources declared by importer: " + coverage.expected_source_count + "."));
    }
    if (coverage.declared_complete !== undefined) {
        description.append(node("p", "", "Importer declared acquisition complete: " + (coverage.declared_complete ? "yes" : "no") + ". This is a caller declaration, not independently verified completeness."));
    }
    panel.append(description);
    panel.hidden = false;
}

function renderRelationships() {
    if (!state.currentCase) return;
    const relationships = state.currentCase.relationships.filter((item) => {
        const review = item.review?.decision || "unreviewed";
        const searchText = [item.id, item.statement, item.effective_statement, item.predicate,
            entity(item.source).label, entity(item.target).label, item.explanation].join(" ").toLowerCase();
        return (state.reviewFilter === "all" || state.reviewFilter === review) && searchText.includes(state.query.toLowerCase());
    });
    $("relationship-count").textContent = relationships.length + " / " + state.currentCase.relationships.length;
    const list = $("relationship-list");
    list.replaceChildren();
    relationships.forEach((item) => {
        const li = node("li");
        const button = node("button", "relationship-card");
        button.type = "button";
        if (item.id === state.relationshipId) button.setAttribute("aria-current", "true");
        button.append(node("div", "entity-pair", entity(item.source).label + " → " + entity(item.target).label));
        button.append(node("span", "relationship-summary", item.effective_statement || item.statement));
        const badges = node("div", "badges");
        badges.append(badge(item.evidence_status, "evidence"), badge(item.review?.decision || "unreviewed", "review"));
        button.append(badges);
        button.addEventListener("click", async () => {
            state.relationshipId = item.id;
            state.citation = item.citations?.[0] || null;
            renderRelationships();
            renderDetail();
            const selected = $("relationship-list").querySelector("[aria-current='true']");
            selected?.focus({ preventScroll: true });
            if (state.citation) await inspectSource(state.citation.source_id, state.citation);
            else await inspectSource(null, null);
        });
        li.append(button);
        list.append(li);
    });
    $("no-relationships").hidden = relationships.length !== 0;
}

function renderDetail() {
    const container = $("relationship-detail");
    container.replaceChildren();
    const item = currentRelationship();
    if (!item) {
        container.append(node("p", "small-empty", "This case has no relationships. Sources remain available in the inspector."));
        return;
    }
    container.append(node("p", "finding-id", "RELATIONSHIP " + item.id));
    const flow = node("div", "endpoint-flow");
    [item.source, item.target].forEach((id, index) => {
        if (index) flow.append(node("span", "flow-arrow", "→"));
        const endpoint = node("div", "endpoint");
        const record = entity(id);
        endpoint.append(node("strong", "", record.label), node("small", "", labelText(record.kind) + " · " + record.id));
        flow.append(endpoint);
    });
    container.append(flow, node("p", "predicate", item.predicate));
    container.append(node("p", "statement", item.effective_statement || item.statement));
    container.append(node("p", "explanation", item.explanation || "No explanation was supplied."));
    if (item.review?.decision === "amended") {
        const original = node("div", "original-statement");
        original.append(node("strong", "", "Original extracted statement"), node("span", "", item.statement));
        container.append(original);
    }
    const states = node("div", "state-key");
    [["EVIDENCE STATUS", badge(item.evidence_status, "evidence")], ["HUMAN REVIEW", badge(item.review?.decision || "unreviewed", "review")]].forEach(([label, status]) => {
        const group = node("div");
        group.append(node("label", "", label), status);
        states.append(group);
    });
    container.append(states, node("h3", "", "Supporting sources"));
    const citations = node("div", "citation-list");
    (item.citations || []).forEach((citation, index) => {
        const source = state.currentCase.sources.find((candidate) => candidate.id === citation.source_id);
        const button = node("button", "citation-button");
        button.type = "button";
        button.dataset.citationIndex = String(index);
        if (state.citation === citation) button.setAttribute("aria-current", "true");
        const content = node("div");
        content.append(node("strong", "", source?.label || citation.source_id));
        const location = citation.bbox ? "Image region" : citation.start_line
            ? "Lines " + citation.start_line + "–" + (citation.end_line || citation.start_line) : "Source reference";
        content.append(node("small", "", location + " · " + valueText(citation.validation?.status || citation.validation || "Validation not supplied")));
        if (citation.quote) content.append(node("span", "citation-quote", "“" + citation.quote + "”"));
        button.append(node("span", "citation-number", index + 1), content);
        button.addEventListener("click", async () => {
            state.citation = citation;
            highlightCitationButtons();
            await inspectSource(citation.source_id, citation);
        });
        citations.append(button);
    });
    if (!(item.citations || []).length) citations.append(node("p", "help", "No citations are attached. This relationship is unsupported by a source reference."));
    container.append(citations, reviewForm(item), renderHistory(item));
}

function highlightCitationButtons() {
    const citations = currentRelationship()?.citations || [];
    document.querySelectorAll(".citation-button").forEach((button) => {
        if (citations[Number(button.dataset.citationIndex)] === state.citation) button.setAttribute("aria-current", "true");
        else button.removeAttribute("aria-current");
    });
}

function field(label, input) {
    const wrapper = node("div");
    const labelNode = node("label", "", label);
    labelNode.htmlFor = input.id;
    wrapper.append(labelNode, input);
    return wrapper;
}

function reviewForm(item) {
    const section = node("section", "review-section");
    section.append(node("h3", "", "Review this relationship"));
    section.append(node("p", "", "Record your judgment with a reason. Reviewer labels are self-reported; accepting a finding does not verify identity, ownership, or payment."));
    const form = node("form", "review-form");
    const decision = node("select");
    decision.id = "review-decision";
    [["accepted", "Accept relationship"], ["rejected", "Reject relationship"], ["amended", "Amend statement"]].forEach(([value, text]) => {
        const option = node("option", "", text);
        option.value = value;
        decision.append(option);
    });
    const amendment = node("textarea");
    amendment.id = "review-amendment";
    amendment.value = item.effective_statement || item.statement || "";
    amendment.maxLength = 10000;
    const amendmentField = field("Reviewed statement", amendment);
    amendmentField.hidden = true;
    decision.addEventListener("change", () => {
        amendmentField.hidden = decision.value !== "amended";
        amendment.required = decision.value === "amended";
    });
    const reviewer = node("input");
    reviewer.id = "review-reviewer";
    reviewer.type = "text";
    reviewer.required = true;
    reviewer.maxLength = 120;
    reviewer.autocomplete = "off";
    reviewer.placeholder = "Your name or reviewer label";
    const reason = node("textarea");
    reason.id = "review-reason";
    reason.required = true;
    reason.maxLength = 10000;
    reason.placeholder = "What did you check, and why is this decision supported?";
    const submit = node("button", "button primary", "Save review decision");
    submit.type = "submit";
    submit.dataset.mutation = "true";
    submit.disabled = state.busy;
    form.append(field("Decision", decision), amendmentField, field("Reviewer label", reviewer), field("Required reason", reason), submit);
    const caseId = state.currentCase.case_id;
    form.addEventListener("submit", (event) => {
        event.preventDefault();
        if (!form.reportValidity()) return;
        if (!reviewer.value.trim() || !reason.value.trim() || (decision.value === "amended" && !amendment.value.trim())) {
            showNotice("Enter a reviewer label, a reason, and a nonempty statement when amending.", true);
            return;
        }
        const payload = {
            relationship_id: item.id, decision: decision.value, reviewer: reviewer.value.trim(),
            reason: reason.value.trim(), expected_revision: state.currentCase.revision,
        };
        if (decision.value === "amended") payload.statement = amendment.value.trim();
        mutate(async () => {
            await jsonApi(casePath(caseId) + "/reviews", payload);
            await loadCase(caseId);
            await refreshCases();
            showNotice("Review saved. The original extraction and previous review decisions remain in the case history.");
        });
    });
    section.append(form);
    return section;
}

function renderHistory(item) {
    const section = node("section");
    section.append(node("h3", "", "Review history"));
    const history = item.history || [];
    if (!history.length) {
        section.append(node("p", "help", "No human review decisions have been recorded."));
        return section;
    }
    const list = node("ol", "history");
    [...history].reverse().forEach((event) => {
        const li = node("li");
        const header = node("div", "history-header");
        header.append(badge(event.decision, "review"), node("strong", "", event.reviewer || "Reviewer label not supplied"));
        li.append(header, node("div", "history-date", valueText(event.recorded_at || event.created_at || event.timestamp || event.at)));
        li.append(node("p", "", event.reason || "No reason recorded."));
        if (event.statement) li.append(node("p", "", "Statement: " + event.statement));
        list.append(li);
    });
    section.append(list);
    return section;
}

function renderSourceSelect() {
    const select = $("source-select");
    select.replaceChildren(node("option", "", "Select a source"));
    select.firstElementChild.value = "";
    state.currentCase.sources.forEach((source) => {
        const option = node("option", "", source.label || source.id);
        option.value = source.id;
        select.append(option);
    });
}

function metadataRow(label, value, className = "") {
    const row = node("div", "meta-row");
    row.append(node("dt", "", label), node("dd", className, valueText(value)));
    return row;
}

async function inspectSource(sourceId, citation, startLine) {
    const requestId = ++state.sourceRequest;
    state.sourceAbort?.abort();
    state.sourceAbort = new AbortController();
    dropPreview();
    state.sourceId = sourceId;
    state.citation = citation;
    $("source-select").value = sourceId || "";
    const inspector = $("source-inspector");
    inspector.replaceChildren();
    if (!sourceId || !state.currentCase) {
        inspector.append(node("p", "small-empty", "Choose a supporting citation or select a case source above."));
        return;
    }
    const source = state.currentCase.sources.find((item) => item.id === sourceId);
    if (!source) {
        inspector.append(node("p", "small-empty", "This citation references a source that is not available in this case."));
        return;
    }
    inspector.append(node("h2", "source-title", source.label || source.id));
    const badges = node("div", "badges");
    badges.append(node("span", "badge", labelText(source.kind)), node("span", "badge", source.media_type || "Unknown media"));
    inspector.append(badges);
    const metadata = node("dl", "source-meta");
    metadata.append(metadataRow("SHA-256 · preserved bytes", source.sha256, "hash-text"));
    metadata.append(metadataRow("Observed / acquired", source.observed_at));
    metadata.append(metadataRow("Original source reference", source.original_reference));
    metadata.append(metadataRow("Source revision", source.source_revision));
    metadata.append(metadataRow("Size / availability", valueText(source.bytes) + " bytes · " + valueText(source.availability)));
    metadata.append(metadataRow("Integrity", source.integrity));
    metadata.append(metadataRow("Current source freshness", source.freshness));
    if (source.check) {
        metadata.append(metadataRow("Last operator check", valueText(source.check.created_at || source.check.timestamp)
            + " · Revision " + valueText(source.check.revision)));
        metadata.append(metadataRow("Compared current-file SHA-256", source.check.current_sha256 || "Operator recorded original missing", "hash-text"));
    } else metadata.append(metadataRow("Last operator check", "Not checked"));
    inspector.append(metadata);
    if (citation?.validation) inspector.append(node("div", "source-state", "Citation validation: " + valueText(citation.validation)));
    if (citation?.quote) inspector.append(node("blockquote", "source-quote", citation.quote));
    const content = node("div");
    inspector.append(content);
    inspector.append(comparisonForm(source));
    const availability = String(source.availability || "").toLowerCase();
    if (["missing", "unavailable", "reference_only"].includes(availability) || !source.sha256) {
        content.append(node("p", "source-state warning", "Preserved source bytes are unavailable. The reference and citation are retained; content cannot be inspected."));
        return;
    }
    content.append(node("p", "help", "Loading preserved source…"));
    const caseId = state.currentCase.case_id;
    try {
        const rasterTypes = new Set(["image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp", "image/avif"]);
        if (rasterTypes.has(source.media_type)) {
            const response = await api(sourcePath(caseId, sourceId) + "/content", { signal: state.sourceAbort.signal });
            const blob = await response.blob();
            if (requestId !== state.sourceRequest) return;
            content.replaceChildren();
            const url = URL.createObjectURL(new Blob([blob], { type: source.media_type }));
            state.objectUrls.add(url);
            state.previewUrl = url;
            const figure = node("div", "source-image");
            const image = node("img");
            image.src = url;
            image.alt = "Preserved source: " + (source.label || source.id);
            image.addEventListener("error", () => {
                content.replaceChildren(node("p", "source-state warning", "This browser could not decode the preserved image. Its source reference, hash, and bytes remain available in the recovery archive."));
            });
            figure.append(image);
            if (citation?.bbox) {
                const bounds = citation.bbox;
                const overlay = node("div", "region-overlay");
                const clamp = (value) => Math.max(0, Math.min(1, Number(value) || 0));
                overlay.style.left = clamp(bounds.x) * 100 + "%";
                overlay.style.top = clamp(bounds.y) * 100 + "%";
                overlay.style.width = clamp(bounds.width) * 100 + "%";
                overlay.style.height = clamp(bounds.height) * 100 + "%";
                overlay.setAttribute("aria-hidden", "true");
                figure.append(overlay);
            }
            content.append(figure, node("p", "image-caption", citation?.bbox
                ? "Outlined region uses normalized coordinates from the top-left of the preserved image. Region selection does not verify a transcription or the image's authenticity."
                : "Preserved image bytes. Image content and transcriptions require review; appearance alone does not establish authenticity."));
            return;
        }
        const firstLine = startLine || Math.max(1, Number(citation?.start_line || 1) - 2);
        const details = await jsonApi(sourcePath(caseId, sourceId) + "?start_line=" + firstLine + "&max_lines=200", undefined, { signal: state.sourceAbort.signal });
        if (requestId !== state.sourceRequest) return;
        content.replaceChildren();
        const lines = details.numbered_lines || [];
        if (lines.length) {
            const excerpt = node("div", "excerpt");
            excerpt.setAttribute("aria-label", "Preserved source excerpt with line numbers");
            lines.forEach((line) => {
                const highlighted = citation?.start_line && line.number >= citation.start_line && line.number <= (citation.end_line || citation.start_line);
                const row = node("div", "excerpt-line" + (highlighted ? " highlight" : ""));
                row.append(node("span", "line-number", line.number), node("span", "line-text", line.text));
                excerpt.append(row);
            });
            content.append(excerpt, node("p", "image-caption", "Showing lines " + lines[0].number + "–" + lines[lines.length - 1].number + " of " + valueText(details.line_count) + "."));
            if (details.next_line) {
                const more = node("button", "button secondary more-lines", "Next source excerpt →");
                more.type = "button";
                more.addEventListener("click", () => inspectSource(sourceId, citation, details.next_line));
                content.append(more);
            }
        } else content.append(node("p", "source-state warning", "No text excerpt is available for this source media type. Metadata and source bytes remain preserved."));
    } catch (error) {
        if (error.name === "AbortError" || requestId !== state.sourceRequest) return;
        content.replaceChildren(node("p", "source-state warning", "Source content could not be inspected: " + error.message));
        if (error.status === 401 || error.status === 403) await handleError(error);
    }
}

function comparisonForm(source) {
    const section = node("section", "comparison");
    section.append(node("h3", "", "Compare current original"), node("p", "", "Select the current file to compare its hash with the preserved copy. The original and preserved bytes are not overwritten."));
    const form = node("form");
    const input = node("input");
    input.type = "file";
    input.id = "compare-source-file";
    input.required = true;
    const label = node("label", "sr-only", "Select current source file for hash comparison");
    label.htmlFor = input.id;
    const submit = node("button", "button secondary", "Compare selected file");
    submit.type = "submit";
    submit.dataset.mutation = "true";
    submit.disabled = state.busy;
    form.append(label, input, submit);
    const caseId = state.currentCase.case_id;
    form.addEventListener("submit", (event) => {
        event.preventDefault();
        const file = input.files[0];
        if (!file) return;
        mutate(async (session) => {
            if (file.size > 8 * 1024 * 1024) throw new Error("This comparison accepts files up to 8 MiB. Select a smaller source file.");
            const content = new Uint8Array(await file.arrayBuffer());
            assertSession(session);
            let encoded = "";
            for (let offset = 0; offset < content.length; offset += 8192) {
                encoded += String.fromCharCode(...content.subarray(offset, offset + 8192));
            }
            await jsonApi(casePath(caseId) + "/checks", { source_id: source.id, content_base64: btoa(encoded), expected_revision: state.currentCase.revision });
            await loadCase(caseId);
            showNotice("Current-file comparison recorded. Inspect the source freshness result; the preserved evidence was not changed.");
        });
    });
    const missing = node("button", "text-button", "Record that the original is missing");
    missing.type = "button";
    missing.dataset.mutation = "true";
    missing.disabled = state.busy;
    missing.addEventListener("click", () => mutate(async () => {
        await jsonApi(casePath(caseId) + "/checks", { source_id: source.id, missing: true, expected_revision: state.currentCase.revision });
        await loadCase(caseId);
        showNotice("The current original was recorded as missing. Any preserved copy remains available in this case.");
    }));
    section.append(form, missing);
    return section;
}

async function downloadArtifact(kind) {
    if (!state.currentCase) return;
    const caseId = state.currentCase.case_id;
    const response = await api(casePath(caseId) + "/" + kind);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    state.objectUrls.add(url);
    const link = node("a");
    link.href = url;
    const safeId = String(caseId).replace(/[^A-Za-z0-9_.-]/g, "_");
    link.download = safeId + (kind === "backup" ? "-private-recovery.zip" : "-reviewed-report.md");
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => { URL.revokeObjectURL(url); state.objectUrls.delete(url); }, 30000);
    showNotice(kind === "backup"
        ? "Recovery ZIP downloaded. It includes original source bytes and all history; keep it in private storage. Restore it as a new case to check recovery."
        : "Reviewed report downloaded. It contains reviewed conclusions and review counts, not a full source archive or complete history. Inspect it before sharing.");
}

function setupEvents() {
    ["demo-button", "empty-demo-button", "import-button", "restore-button"].forEach((id) => { $(id).dataset.mutation = "true"; });
    document.querySelectorAll(".file-dialog button[type='submit']").forEach((button) => { button.dataset.mutation = "true"; });
    $("token-form").addEventListener("submit", (event) => { event.preventDefault(); connect($("access-token").value); });
    $("disconnect-button").addEventListener("click", () => disconnect("Disconnected. The access token and source previews have been cleared from this page."));
    $("refresh-cases").addEventListener("click", async () => {
        try {
            await refreshCases();
            if (state.currentCase) await loadCase(state.currentCase.case_id);
            showNotice("Case list and current case refreshed.");
        } catch (error) { await handleError(error); }
    });
    $("case-select").addEventListener("change", async (event) => {
        if (!event.target.value) return;
        try { await loadCase(event.target.value); showNotice(""); }
        catch (error) { await handleError(error); }
    });
    $("relationship-search").addEventListener("input", (event) => { state.query = event.target.value; renderRelationships(); });
    $("review-filter").addEventListener("change", (event) => { state.reviewFilter = event.target.value; renderRelationships(); });
    $("source-select").addEventListener("change", (event) => { state.citation = null; highlightCitationButtons(); inspectSource(event.target.value || null, null); });
    const demo = () => mutate(async () => {
        const result = await jsonApi("/api/demo", {});
        await refreshCases(result.case_id);
        showNotice("Synthetic demo created. These sources and relationships are fictional and contain no personal case evidence.");
    });
    $("demo-button").addEventListener("click", demo);
    $("empty-demo-button").addEventListener("click", demo);
    $("import-button").addEventListener("click", () => $("import-dialog").showModal());
    $("restore-button").addEventListener("click", () => $("restore-dialog").showModal());
    document.querySelectorAll("[data-close]").forEach((button) => {
        button.addEventListener("click", () => $(button.dataset.close).close());
    });
    $("import-form").addEventListener("submit", (event) => {
        event.preventDefault();
        const file = $("import-file").files[0];
        if (!file || !$("import-form").reportValidity()) return;
        mutate(async (session) => {
            if (file.size > 48 * 1024 * 1024) throw new Error("JSON imports must be 48 MiB or smaller.");
            const raw = await file.text();
            assertSession(session);
            const response = await api("/api/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: raw });
            const result = await response.json();
            $("import-dialog").close();
            $("import-form").reset();
            await refreshCases(result.case_id);
            showNotice("Evidence package imported as a separate local case. Inspect declared coverage and supporting sources before reviewing findings.");
        });
    });
    $("restore-form").addEventListener("submit", (event) => {
        event.preventDefault();
        const file = $("restore-file").files[0];
        if (!file || !$("restore-form").reportValidity()) return;
        mutate(async (session) => {
            if (file.size > 56 * 1024 * 1024) throw new Error("Recovery archives must be 56 MiB or smaller.");
            const headers = { "Content-Type": "application/zip" };
            const requestedId = $("restore-case-id").value.trim();
            if (requestedId) headers["X-Restore-Case-Id"] = requestedId;
            const body = await file.arrayBuffer();
            assertSession(session);
            const response = await api("/api/restore", { method: "POST", headers, body });
            const result = await response.json();
            $("restore-dialog").close();
            $("restore-form").reset();
            await refreshCases(result.case_id);
            showNotice("Recovery archive restored into a new case. Inspect the preserved sources and review history; existing cases were not replaced.");
        });
    });
    $("report-button").addEventListener("click", () => mutate(() => downloadArtifact("report")));
    $("backup-button").addEventListener("click", () => mutate(() => downloadArtifact("backup")));
}

setupEvents();
// Keep the original token-bearing fragment out of persistent top-level bindings.
(() => {
    const initialFragment = new URLSearchParams(window.location.hash.slice(1));
    const initialToken = initialFragment.get("token");
    if (window.location.hash) history.replaceState(null, "", window.location.pathname + window.location.search);
    if (initialToken) connect(initialToken);
})();
