import { test } from "node:test";
import assert from "node:assert/strict";

// a minimal sessionStorage so the helper can be tested without a browser
const store = new Map<string, string>();
(globalThis as any).sessionStorage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) };
const { readSelection } = await import("../src/persisted.ts");

test("a saved selection is read back, and a missing or broken one falls back", () => {
  assert.equal(readSelection("queue.stage", "all"), "all");
  store.set("grantie.sel.queue.stage", JSON.stringify("3"));
  assert.equal(readSelection("queue.stage", "all"), "3");
  store.set("grantie.sel.rounds.picked", "{broken");
  assert.deepEqual(readSelection("rounds.picked", []), []);
});

test("storage that throws never breaks the screen", () => {
  (globalThis as any).sessionStorage = { getItem: () => { throw new Error("blocked"); } };
  assert.equal(readSelection("x", 5), 5);
});
