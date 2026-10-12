// "No side stripes or left-border accents anywhere in the product." Fails if a stripe pattern comes back in any stylesheet or
// component: border-left / border-inline-start (any form), Tailwind border-l-*, inline borderLeft, inset box-shadow stripes,
// or a narrow absolutely placed ::before / ::after bar. A plain 1px full border is fine.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../src/", import.meta.url));
function files(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? files(p) : /\.(css|tsx|ts)$/.test(f) ? [p] : [];
  });
}

const PATTERNS: [RegExp, string][] = [
  [/border-left(-width|-color|-style)?\s*:/i, "border-left"],
  [/border-inline-start/i, "border-inline-start"],
  [/\bborder-l(-\d+|-\[)/, "Tailwind border-l-*"],
  [/borderLeft\w*\s*:/, "inline borderLeft style"],
  [/inset\s+[1-9]\d*px\s+0(px)?\s+0/i, "inset box-shadow stripe"],
];

test("no component uses a side stripe or left-border accent", () => {
  const hits: string[] = [];
  for (const f of files(root)) {
    readFileSync(f, "utf8").split("\n").forEach((line, i) => {
      for (const [re, what] of PATTERNS) if (re.test(line)) hits.push(`${f.replace(root, "")}:${i + 1} ${what}`);
    });
  }
  assert.deepEqual(hits, []);
});

test("no narrow pseudo-element bar on the left edge", () => {
  const hits: string[] = [];
  for (const f of files(root).filter((x) => x.endsWith(".css"))) {
    for (const m of readFileSync(f, "utf8").matchAll(/([^{}]*::(?:before|after)[^{]*)\{([^}]*)\}/g)) {
      const body = m[2];
      if (/position:\s*absolute/.test(body) && /\bleft:/.test(body) && /\bwidth:\s*[2-6]px/.test(body) && /\bheight:\s*(\d{2,}px|100%)/.test(body)) {
        hits.push(`${f.replace(root, "")}: ${m[1].trim()}`);
      }
    }
  }
  assert.deepEqual(hits, []);
});

test("the design notes say so", () => {
  assert.match(readFileSync(new URL("../src/tokens.css", import.meta.url), "utf8"), /No side stripes or left-border accents anywhere in the product/);
});
