import { test } from "node:test";
import assert from "node:assert/strict";
import { stageCounts, stageOf } from "../src/stages.ts";

const items = [
  { phase: "mine", current_step: 1 }, { phase: "mine", current_step: 1 }, { phase: "mine", current_step: 2 }, { phase: "mine", current_step: 3 },
  { phase: "outcome", current_step: 4 }, { phase: "waiting", current_step: 1 }, { phase: "done", current_step: 4 },
] as never[];

test("every application is in exactly one stage and the stages add up to the total", () => {
  const c = stageCounts(items);
  assert.deepEqual(c, { "1": 2, "2": 1, "3": 1, "4": 2, waiting: 1 });
  assert.equal(Object.values(c).reduce((a, b) => a + b, 0), items.length);
});

test("waiting is its own stage; signed-off applications sit with Outcome", () => {
  assert.equal(stageOf({ phase: "waiting", current_step: 1 }), "waiting");
  assert.equal(stageOf({ phase: "done", current_step: 4 }), "4");
  assert.equal(stageOf({ phase: "outcome", current_step: 4 }), "4");
});
