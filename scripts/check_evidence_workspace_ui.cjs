#!/usr/bin/env node
"use strict";

// Run: node scripts/check_evidence_workspace_ui.cjs (Node 18+, no dependencies).
// Offline client-boundary regressions with synthetic data and delayed responses.
// This small DOM stub checks async state isolation and raw import transport; it
// does not replace browser integration, accessibility, or backend security tests.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const clientSource = fs.readFileSync(path.join(
    __dirname, "../graphrag_core/evidence_workspace/static/app.js"), "utf8");

class Element {
    constructor() {
        this.children = [];
        this.dataset = {};
        this.value = "";
        this.files = [];
        this.hidden = false;
        this.style = {};
        this.listeners = new Map();
        this.attributes = new Map();
        this.textContent = "";
    }

    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    get firstElementChild() { return this.children[0]; }
    addEventListener(name, listener) {
        if (!this.listeners.has(name)) this.listeners.set(name, []);
        this.listeners.get(name).push(listener);
    }
    emit(name, event) {
        for (const listener of this.listeners.get(name) || []) listener(event);
    }
    setAttribute(name, value) { this.attributes.set(name, value); }
    removeAttribute(name) { this.attributes.delete(name); }
    focus() {}
    close() {}
    reset() {}
    before() {}
    remove() {}
    click() {}
    querySelector() { return null; }
    reportValidity() { return true; }
}

function deferred() {
    let resolve;
    const promise = new Promise((done) => { resolve = done; });
    return { promise, resolve };
}

function client() {
    const elements = new Map();
    let createdUrls = 0;
    const element = (id) => {
        if (!elements.has(id)) elements.set(id, new Element());
        return elements.get(id);
    };
    const context = vm.createContext({
        document: {
            getElementById: element,
            querySelectorAll() { return []; },
            querySelector() { return null; },
            createElement() { return new Element(); },
            body: new Element(),
        },
        window: { location: { hash: "", pathname: "/", search: "" } },
        history: { replaceState() {} },
        URLSearchParams, Headers, AbortController, DOMException, Blob, Uint8Array, btoa,
        URL: {
            createObjectURL() { createdUrls += 1; return "blob:synthetic-preview"; },
            revokeObjectURL() {},
        },
        setTimeout,
    });
    const run = (code) => vm.runInContext(code, context);
    vm.runInContext(clientSource, context, { filename: "evidence-workspace-app.js" });
    run("state.token = 'synthetic-token'; state.session = 1;");
    return { context, element, run, createdUrls: () => createdUrls };
}

async function flush() {
    // Advance only microtasks: the mocked transport has no timers or sockets.
    for (let index = 0; index < 40; index += 1) await Promise.resolve();
}

async function caseListAfterDisconnect() {
    const c = client();
    const body = deferred();
    c.context.fetch = async () => ({ ok: true, json: () => body.promise });
    const pending = c.run("refreshCases()");
    await flush();
    c.run("disconnect()");
    body.resolve({ cases: [{ case_id: "synthetic-case", title: "Synthetic private title" }] });
    await assert.rejects(pending, (error) => error.name === "AbortError");
    assert.equal(c.run("state.cases.length"), 0);
    assert.equal(c.element("case-select").children.length, 0);
}

async function olderListCannotReplaceNewerList() {
    const c = client();
    const older = deferred();
    const newer = deferred();
    let calls = 0;
    c.context.fetch = async () => ({ ok: true, json: () => (++calls === 1 ? older.promise : newer.promise) });
    const first = c.run("refreshCases()");
    await flush();
    const second = c.run("refreshCases()");
    await flush();
    newer.resolve({ cases: [{ case_id: "newer-case", title: "Newer synthetic snapshot" }] });
    await second;
    older.resolve({ cases: [{ case_id: "older-case", title: "Older synthetic snapshot" }] });
    await assert.rejects(first, (error) => error.name === "AbortError");
    assert.equal(c.run("state.cases[0].case_id"), "newer-case");
}

async function initialConnectAfterDisconnect() {
    const c = client();
    const body = deferred();
    c.context.fetch = async () => ({ ok: true, json: () => body.promise });
    const pending = c.run("connect('synthetic-token')");
    await flush();
    c.run("disconnect()");
    body.resolve({ cases: [{ case_id: "synthetic-case", title: "Synthetic private title" }] });
    await pending;
    assert.equal(c.element("app-shell").hidden, true);
    assert.equal(c.run("state.cases.length"), 0);
    assert.equal(c.run("state.token"), "");
}

async function caseBodyAfterDisconnect() {
    const c = client();
    const body = deferred();
    c.context.fetch = async () => ({ ok: true, json: () => body.promise });
    const pending = c.run("loadCase('synthetic-case')");
    await flush();
    c.run("disconnect()");
    body.resolve({ case_id: "synthetic-case", title: "Synthetic private title" });
    await assert.rejects(pending, (error) => error.name === "AbortError");
    assert.equal(c.run("state.currentCase"), null);
    assert.equal(c.element("case-title").textContent, "Your evidence workspace");
}

async function artifactBodyAfterDisconnect() {
    const c = client();
    const body = deferred();
    c.context.fetch = async () => ({ ok: true, blob: () => body.promise });
    c.run("state.currentCase = {case_id: 'synthetic-case'};");
    const pending = c.run("downloadArtifact('backup')");
    await flush();
    c.run("disconnect()");
    body.resolve(new Blob(["synthetic archive bytes"]));
    await assert.rejects(pending, (error) => error.name === "AbortError");
    assert.equal(c.createdUrls(), 0);
    assert.equal(c.run("state.objectUrls.size"), 0);
}

async function originalJsonReachesStrictDecoder() {
    const c = client();
    const original = '{\n "case_id": "synthetic", "title": "First", "title": "Second"\n}\n';
    let transportBody;
    c.context.fetch = async (url, options) => {
        assert.equal(url, "/api/import");
        assert.equal(options.headers.get("Content-Type"), "application/json");
        transportBody = options.body;
        return { ok: false, status: 400, json: async () => ({ detail: "Duplicate JSON object key" }) };
    };
    c.element("import-file").files = [{ size: Buffer.byteLength(original), text: async () => original }];
    c.element("import-form").emit("submit", { preventDefault() {} });
    await flush();
    assert.equal(transportBody, original, "Duplicate keys and formatting must reach the strict server decoder");
    assert.equal(c.element("notice").textContent, "Duplicate JSON object key");
    assert.equal(c.run("state.currentCase"), null);
    assert.equal(c.run("state.busy"), false);
}

async function main() {
    const checks = [
        caseListAfterDisconnect, olderListCannotReplaceNewerList,
        initialConnectAfterDisconnect, caseBodyAfterDisconnect,
        artifactBodyAfterDisconnect, originalJsonReachesStrictDecoder,
    ];
    for (const check of checks) await check();
    console.log(`Evidence workspace UI: ${checks.length} offline async/import checks passed.`);
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
