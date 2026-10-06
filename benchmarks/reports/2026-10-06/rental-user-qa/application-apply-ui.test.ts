import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { describe, expect, it } from "vitest";

// DOM state regression tests; native constraint validation is checked in Chromium.
interface Field {
  value: string;
  checked: boolean;
  disabled: boolean;
  textContent: string;
  classList: { toggle(name: string, add: boolean): unknown; contains(name: string): boolean };
  change?: () => void;
  submit?: () => void;
  addEventListener(name: "change" | "submit", callback: () => void): void;
  querySelector(): Field;
  querySelectorAll(): Field[];
}

function createPage(application: object | null = null) {
  const elements = new Map<string, Field>();
  const element = (id: string): Field => {
    if (!elements.has(id)) {
      const classes = new Set<string>();
      elements.set(id, {
        value: "", checked: false, disabled: false, textContent: "",
        classList: {
          toggle: (name: string, add: boolean) => add ? classes.add(name) : classes.delete(name),
          contains: (name: string) => classes.has(name),
        },
        addEventListener(name: "change" | "submit", callback: () => void) { this[name] = callback; },
        querySelector: () => element("submit"),
        querySelectorAll: () => Array.from(elements.entries())
          .filter(([key]) => key.startsWith("co-") && key !== "co-applicant-section")
          .map(([, value]) => value),
      });
    }
    return elements.get(id)!;
  };
  ["first-name", "last-name", "email", "phone", "employer-name", "job-title", "monthly-income"]
    .forEach((suffix) => element(`co-${suffix}`));
  vm.runInNewContext(fs.readFileSync(path.join(process.cwd(), "src/public/assets/application-apply.js"), "utf8"), {
    document: { getElementById: element },
    window: { location: { search: "?token=fixture" } },
    URLSearchParams,
    fetch: async () => ({ ok: true, json: async () => ({ invite: { email: "qa@example.test" }, application }) }),
  });
  return { element, settled: () => new Promise<void>((resolve) => setImmediate(resolve)) };
}

describe("application co-applicant field participation", () => {
  it("disables inactive fields and preserves values across toggles", async () => {
    const page = createPage();
    await page.settled();
    const toggle = page.element("has-co-applicant");
    const email = page.element("co-email");
    expect(email.disabled).toBe(true);
    toggle.checked = true;
    toggle.change!();
    expect(email.disabled).toBe(false);
    email.value = "invalid-email";
    toggle.checked = false;
    toggle.change!();
    expect(email.disabled).toBe(true);
    expect(email.value).toBe("invalid-email");
    toggle.checked = true;
    toggle.change!();
    expect(email.disabled).toBe(false);
  });

  it.each(["DRAFT", "SUBMITTED"])("hydrates %s with appropriate editability", async (status) => {
    const page = createPage({ status, applicants: [{ role: "CO_APPLICANT", email: "co@example.test" }] });
    await page.settled();
    expect(page.element("has-co-applicant").checked).toBe(true);
    expect(page.element("co-applicant-section").classList.contains("hidden")).toBe(false);
    expect(page.element("co-email").value).toBe("co@example.test");
    expect(page.element("co-email").disabled).toBe(status === "SUBMITTED");
  });
});
