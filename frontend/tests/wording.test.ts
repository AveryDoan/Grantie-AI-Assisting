// "Use not eligible, not selected, needs evidence. Never failed or rejected on any button or label." Scans the text an officer or
// applicant can see: JSX text, and string literals that are shown (sentences, labels, the branches of a ternary). Class names,
// comparisons and type names are code, not wording, and are skipped.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../src/", import.meta.url));
const files = (dir: string): string[] => readdirSync(dir).flatMap((f) => {
  const p = join(dir, f);
  return statSync(p).isDirectory() ? files(p) : /\.tsx$/.test(f) || /^(api|auth|dashboard)\.ts$/.test(f) ? [p] : [];
});
const BANNED = /\b(failed|fail|rejected|rejects?|reject(?:ion)?|fraud\w*|cheat\w*|suspicious|fake)\b/i;

export function shownStrings(src: string): string[] {
  const code = src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n").map((l) => l.replace(/^\s*\/\/.*$/, "").replace(/\s\/\/\s.*$/, "")).join("\n")
    .replace(/className=\{?`[^`]*`\}?|className="[^"]*"/g, "")
    .replace(/(===|!==|case)\s*"[^"]*"/g, "")
    .replace(/\b(kind|key|status|type|stage|variant|name)\s*:\s*"[^"]*"/g, "");   // code values, not wording
  const out: string[] = [];
  for (const m of code.matchAll(/(?<![=-])>([^<>{}=]*[A-Za-z][^<>{}=]*)</g)) out.push(m[1]);                       // JSX text
  for (const m of code.matchAll(/(\?|:|\|\||\?\?|\(|,|=)\s*"([^"\n]+)"/g)) if (/\s|^[A-Z]/.test(m[2]) || m[1] === "?" || m[1] === ":") out.push(m[2]);
  for (const m of code.matchAll(/`([^`]*\s[^`]*)`/g)) out.push(m[1]);
  return out;
}

test("no shown text says failed, rejected, fraud or similar", () => {
  const hits: string[] = [];
  for (const f of files(root)) {
    for (const s of shownStrings(readFileSync(f, "utf8"))) if (BANNED.test(s) && !/^[\w.-]+$/.test(s.trim()) || /^(failed|rejected)$/i.test(s.trim())) {
      if (BANNED.test(s)) hits.push(`${f.replace(root, "")}: ${s.trim().slice(0, 80)}`);
    }
  }
  assert.deepEqual(hits, []);
});

test("the checker finds banned words in shown text", () => {
  assert.ok(shownStrings(`<Button>Mark as failed</Button>`).some((s) => BANNED.test(s)));
  assert.ok(shownStrings(`const t = ok ? "Passed" : "Rejected";`).some((s) => BANNED.test(s)));
  assert.ok(!shownStrings(`<div className="rv-failed">Could not verify</div>`).some((s) => BANNED.test(s)));
});
