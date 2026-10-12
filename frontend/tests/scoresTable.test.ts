import { test } from "node:test";
import assert from "node:assert/strict";
import { filterRows, nextSort, placesLine, sortRows } from "../src/scoresTable.ts";

const row = (reference: string, total: string | null, over: object = {}) => ({ application_id: reference, reference, name: null, marks: { M1: total ? Number(total) : null }, total, total_exact: total,
  status: total ? "complete" : "waiting", reason: null, marked: 1, of: 1, decision: null, ready: !!total, why_not_ready: null, deep_link: "", ...over }) as never;
const rows = [row("APP-C", "70"), row("APP-A", "80"), row("APP-B", null), row("APP-D", "80")];

test("the default order is by reference, and sorting is only by an explicit click", () => {
  assert.deepEqual(sortRows(rows, null).map((r) => r.reference), ["APP-A", "APP-B", "APP-C", "APP-D"]);
  assert.deepEqual(sortRows(rows, nextSort(null, "total")).map((r) => r.reference), ["APP-C", "APP-A", "APP-D", "APP-B"]);          // ascending, Not yet last
  assert.deepEqual(sortRows(rows, { key: "total", dir: "desc" }).map((r) => r.reference), ["APP-A", "APP-D", "APP-C", "APP-B"]);  // ties stay in reference order
  assert.equal(nextSort({ key: "total", dir: "desc" }, "total"), null);                                                               // a third click goes back
});

test("a missing total is Not yet (last), never zero, and tied totals are equal", () => {
  const s = sortRows(rows, { key: "total", dir: "asc" });
  assert.equal(s[s.length - 1].total, null);
  assert.equal(sortRows(rows, { key: "total", dir: "desc" })[0].total, sortRows(rows, { key: "total", dir: "desc" })[1].total);
});

test("filters and the places line", () => {
  assert.equal(filterRows(rows, { search: "app-b", decision: "all", marks: "all" }).length, 1);
  assert.equal(filterRows(rows, { search: "", decision: "all", marks: "waiting" }).length, 1);
  assert.equal(placesLine({ key: "total", dir: "desc" }, 2, 4), 2);
  assert.equal(placesLine({ key: "total", dir: "asc" }, 2, 4), 2);
  assert.equal(placesLine(null, 2, 4), null);                       // only in a total-sorted view
  assert.equal(placesLine({ key: "total", dir: "desc" }, null, 4), null);
});

test("no rank, top or best anywhere in the table helpers", async () => {
  const { readFileSync } = await import("node:fs");
  const src = readFileSync(new URL("../src/scoresTable.ts", import.meta.url), "utf8").replace(/\/\/.*$/gm, "");
  assert.ok(!/\b(rank|top|best|medal|percentile)\b/i.test(src));
});
