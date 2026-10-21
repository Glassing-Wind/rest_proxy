#!/usr/bin/env node
"use strict";

// Run: node scripts/check_task_cockpit_ui.cjs (Node 18+, no dependencies).
// Synthetic, offline DOM/transport checks. These do not replace a real browser,
// accessibility review, HTTP acceptance, or proof of model/provider operation.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const staticRoot = path.join(__dirname, "../graphrag_core/task_cockpit/static");
const clientSource = fs.readFileSync(path.join(staticRoot, "app.js"), "utf8");
const htmlSource = fs.readFileSync(path.join(staticRoot, "index.html"), "utf8");
const TOKEN = "synthetic-cockpit-capability-0123456789abcdef";
const SECOND_TOKEN = "synthetic-other-capability-0123456789abcdef";

class Element {
    constructor(tag = "div", attributes = "") {
        this.tagName = tag.toUpperCase();
        this.children = [];
        this.dataset = {};
        this.value = "";
        this.hidden = /\bhidden\b/.test(attributes);
        this.disabled = /\bdisabled\b/.test(attributes);
        this.textContent = "";
        this.listeners = new Map();
        const classes = new Set();
        this.classList = {
            toggle(name, force) {
                const add = force === undefined ? !classes.has(name) : force;
                if (add) classes.add(name); else classes.delete(name);
            },
            contains(name) { return classes.has(name); },
        };
    }

    set innerHTML(value) { throw new Error("Unsafe HTML sink used by client"); }
    set outerHTML(value) { throw new Error("Unsafe HTML sink used by client"); }
    insertAdjacentHTML() { throw new Error("Unsafe HTML sink used by client"); }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    addEventListener(name, listener) {
        if (!this.listeners.has(name)) this.listeners.set(name, []);
        this.listeners.get(name).push(listener);
    }
    emit(name, event = {}) {
        for (const listener of this.listeners.get(name) || []) listener(event);
    }
}

function deferred() {
    let resolve, reject;
    const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
    return { promise, resolve, reject };
}

function status(overrides = {}) {
    return {
        schema_version: 1, read_only: true, project: "synthetic-project", task_id: "synthetic-task",
        revision: 3, status: "claimed", attempts: 1, claim_present: true, claim_expired: false,
        checkpoint_present: true, recovery_count: 0, submission_count: 0, review_count: 0,
        assessment_count: 0, human_review_required: null, latest_review_actor_type: "unspecified",
        failures: [], failure_history_complete: false,
        note: "Only instrumented generation failures retained; absence is not success",
        ...overrides,
    };
}

function client(hash = "") {
    const elements = new Map();
    for (const match of htmlSource.matchAll(/<([a-z][\w-]*)\b([^>]*\bid="([^"]+)"[^>]*)>/gi)) {
        assert.ok(!elements.has(match[3]), "Static element IDs must be unique");
        elements.set(match[3], new Element(match[1], match[2]));
    }
    const element = (id) => {
        assert.ok(elements.has(id), `Client references absent static element ${id}`);
        return elements.get(id);
    };
    const events = [];
    const requests = [];
    const windowEvents = new Map();
    const noStorage = new Proxy({}, {
        get() { throw new Error("Browser storage must not retain local capabilities"); },
        set() { throw new Error("Browser storage must not retain local capabilities"); },
    });
    const window = {
        location: { hash, pathname: "/", search: "" },
        localStorage: noStorage, sessionStorage: noStorage,
        addEventListener(name, listener) { windowEvents.set(name, listener); },
    };
    const document = {
        getElementById: element,
        createElement: (tag) => new Element(tag),
        write() { throw new Error("Unsafe document.write sink used by client"); },
    };
    Object.defineProperty(document, "cookie", {
        get() { throw new Error("Cookie access must not retain local capabilities"); },
        set() { throw new Error("Cookie access must not retain local capabilities"); },
    });
    const context = vm.createContext({
        document, window, URLSearchParams, localStorage: noStorage, sessionStorage: noStorage,
        history: {
            replaceState(_state, _title, destination) {
                events.push({ operation: "replaceState", destination });
                window.location.hash = "";
            },
        },
        fetch(url, options) {
            const pending = deferred();
            events.push({ operation: "fetch", url });
            requests.push({ url, options, ...pending });
            return pending.promise;
        },
    });
    vm.runInContext(clientSource, context, { filename: "task-cockpit-app.js" });
    return {
        element, elements, events, requests, window,
        connect(token = TOKEN) {
            element("token").value = token;
            let prevented = false;
            element("connect-form").emit("submit", { preventDefault() { prevented = true; } });
            assert.equal(prevented, true);
            assert.equal(element("token").value, "", "Input must be cleared after submission");
        },
        refresh() { element("refresh").emit("click"); },
        disconnect() { element("disconnect").emit("click"); },
        pagehide() { windowEvents.get("pagehide")(); },
        respond(index, value = status(), code = 200) {
            requests[index].resolve({ ok: code >= 200 && code < 300, status: code, json: async () => value });
        },
    };
}

async function flush() {
    // Advance microtasks only: no sockets, listeners, timers or background work.
    for (let index = 0; index < 40; index += 1) await Promise.resolve();
}

function assertDisconnected(c) {
    assert.equal(c.element("task-view").hidden, true);
    assert.equal(c.element("access").hidden, false);
    assert.equal(c.element("disconnect").hidden, true);
    assert.equal(c.element("refresh").disabled, true);
    assert.equal(c.element("token").value, "");
    assert.equal(c.element("task-id").textContent, "");
    assert.equal(c.element("project-side").textContent, "Connect to view");
    assert.equal(c.element("failures").children.length, 0);
}

async function fragmentRemovedBeforeTransport() {
    const c = client("#token=" + encodeURIComponent(TOKEN));
    assert.equal(c.window.location.hash, "");
    assert.equal(c.events[0].operation, "replaceState");
    assert.equal(c.events[0].destination, "/");
    assert.equal(c.events[1].operation, "fetch");
    assert.equal(c.requests.length, 1);
    const request = c.requests[0];
    assert.equal(request.url, "/api/v1/task-status");
    assert.equal(request.options.headers.Authorization, "Bearer " + TOKEN);
    assert.equal(request.options.credentials, "omit");
    assert.equal(request.options.cache, "no-store");
    assert.equal(request.options.redirect, "error");
    c.respond(0);
    await flush();
    assert.equal(c.element("task-view").hidden, false);
    assert.equal(c.element("token").value, "");
    for (const element of c.elements.values()) assert.ok(!element.textContent.includes(TOKEN));
}

async function invalidFragmentAndManualAccess() {
    for (const hash of ["#token=short", "#unrelated=private", "#token=" + "x".repeat(257)]) {
        const c = client(hash);
        assert.equal(c.window.location.hash, "");
        assert.equal(c.requests.length, 0);
        c.connect("invalid");
        assert.equal(c.requests.length, 0);
        assert.equal(c.element("notice").classList.contains("error"), true);
        c.connect();
        assert.equal(c.requests.length, 1);
        assert.equal(c.element("refresh").disabled, true);
        c.respond(0);
        await flush();
        assert.equal(c.element("task-view").hidden, false);
        assert.equal(c.element("access").hidden, true);
        assert.equal(c.element("refresh").disabled, false);
    }
}

async function delayedFetchCannotReconnectAfterDisconnect() {
    const c = client();
    c.connect();
    c.disconnect();
    c.respond(0, status({ project: "private-delayed-project" }));
    await flush();
    assertDisconnected(c);
    c.refresh();
    assert.equal(c.requests.length, 1, "Disconnect must clear the capability");
}

async function delayedBodyCannotReconnectAfterDisconnect() {
    const c = client();
    const body = deferred();
    let bodyRequested = false;
    c.connect();
    c.requests[0].resolve({ ok: true, status: 200, json() { bodyRequested = true; return body.promise; } });
    await flush();
    assert.equal(bodyRequested, true);
    c.disconnect();
    body.resolve(status({ project: "private-delayed-body" }));
    await flush();
    assertDisconnected(c);
}

async function olderResponseCannotReplaceNewConnection() {
    const c = client();
    c.connect();
    c.connect(SECOND_TOKEN);
    c.respond(1, status({ revision: 8, project: "newer-project" }));
    await flush();
    assert.equal(c.element("revision").textContent, "8");
    c.respond(0, {}, 401);
    await flush();
    assert.equal(c.element("task-view").hidden, false);
    assert.equal(c.element("project-side").textContent, "newer-project");
    assert.equal(c.element("refresh").disabled, false);
    c.refresh();
    assert.equal(c.requests[2].options.headers.Authorization, "Bearer " + SECOND_TOKEN);
    c.respond(2, status({ revision: 9 }));
    await flush();
    assert.equal(c.element("revision").textContent, "9");
}

async function olderBodyCannotReplaceNewerSnapshot() {
    const c = client();
    const older = deferred();
    c.connect();
    c.requests[0].resolve({ ok: true, status: 200, json: () => older.promise });
    await flush();
    c.connect(SECOND_TOKEN);
    c.respond(1, status({ revision: 7 }));
    await flush();
    older.resolve(status({ revision: 1 }));
    await flush();
    assert.equal(c.element("revision").textContent, "7");
    assert.equal(c.element("refresh").disabled, false);
}

async function unsafeStringsRemainText() {
    const c = client();
    const markup = '<img src=x onerror="private()"><script>private()</script>';
    c.connect();
    c.respond(0, status({ project: markup, task_id: markup, failures: [
        { attempt: 1, category: "timeout", stage: "generation" },
    ] }));
    await flush();
    assert.equal(c.element("task-view").hidden, false);
    assert.equal(c.element("project-side").textContent, markup);
    assert.equal(c.element("task-id").textContent, markup);
    assert.equal(c.element("failures").children[0].tagName, "TR");
    assert.equal(c.element("failures").children[0].children[1].textContent, "Timeout");
    assert.equal(c.element("failure-table").hidden, false);
    assert.equal(c.element("failure-empty").hidden, true);
    assert.equal(c.element("review-policy").textContent, "Review policy unavailable");
}

async function refreshFailureClearsOldSnapshotAndCanRecover() {
    const c = client();
    c.connect();
    c.respond(0);
    await flush();
    c.refresh();
    assert.equal(c.element("refresh").disabled, true);
    c.respond(1, { private_error: "private-provider-body" }, 503);
    await flush();
    assert.equal(c.element("task-view").hidden, true);
    assert.equal(c.element("task-id").textContent, "");
    assert.equal(c.element("notice").classList.contains("error"), true);
    assert.ok(!c.element("notice").textContent.includes("private-provider-body"));
    assert.equal(c.element("refresh").disabled, false);
    c.refresh();
    c.respond(2, status({ revision: 4, status: "review_pending", human_review_required: true }));
    await flush();
    assert.equal(c.element("status").textContent, "Awaiting review");
    assert.equal(c.element("review-policy").textContent, "Human review required");
    assert.equal(c.element("revision").textContent, "4");
}

async function invalidStatusAndNetworkFailureDoNotClaimSuccess() {
    const c = client();
    c.connect();
    c.respond(0, status({ status: "__proto__" }));
    await flush();
    assert.equal(c.element("task-view").hidden, true);
    assert.equal(c.element("notice").classList.contains("error"), true);
    c.refresh();
    c.requests[1].reject(new Error("private-network-message"));
    await flush();
    assert.equal(c.element("task-view").hidden, true);
    assert.ok(!c.element("notice").textContent.includes("private-network-message"));
    assert.equal(c.element("refresh").disabled, false);
}

async function rejectedCapabilityAndPagehideClearSession() {
    const c = client();
    c.connect();
    c.respond(0, status());
    await flush();
    c.refresh();
    c.respond(1, {}, 401);
    await flush();
    assertDisconnected(c);
    c.refresh();
    assert.equal(c.requests.length, 2);
    c.connect(SECOND_TOKEN);
    c.respond(2, status({ project: "new-session" }));
    await flush();
    c.refresh();
    c.pagehide();
    c.respond(3, status({ project: "late-pagehide-response" }));
    await flush();
    assertDisconnected(c);
}

async function main() {
    const checks = [
        fragmentRemovedBeforeTransport, invalidFragmentAndManualAccess,
        delayedFetchCannotReconnectAfterDisconnect, delayedBodyCannotReconnectAfterDisconnect,
        olderResponseCannotReplaceNewConnection, olderBodyCannotReplaceNewerSnapshot,
        unsafeStringsRemainText, refreshFailureClearsOldSnapshotAndCanRecover,
        invalidStatusAndNetworkFailureDoNotClaimSuccess, rejectedCapabilityAndPagehideClearSession,
    ];
    for (const check of checks) await check();
    console.log(`Task Cockpit UI: ${checks.length} offline session/rendering checks passed.`);
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
