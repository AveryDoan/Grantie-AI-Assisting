// Visual regression: in every combination of state, the four big step pills (full bar and slim strip) share the same top and
// height within 1px, and so do the sub-step cards. Renders visual/index.html with Vite and measures it in headless Chrome.
// Skipped (not failed) when Chrome is not installed on this machine.
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const CHROME = [process.env.CHROME_PATH, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome", "/usr/bin/chromium"]
  .find((p) => p && existsSync(p));

test("every pill and sub-step card shares one top and one height", { skip: !CHROME && "Chrome not found", timeout: 120000 }, async () => {
  const { createServer } = await import("vite");
  const server = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), server: { port: 0, strictPort: false }, logLevel: "silent" });
  await server.listen();
  const port = (server.httpServer!.address() as { port: number }).port;
  const debugPort = 9400 + Math.floor(Math.random() * 400);
  const chrome = spawn(CHROME!, ["--headless=new", "--disable-gpu", `--remote-debugging-port=${debugPort}`, "--window-size=1280,900",
    `--user-data-dir=${mkdtempSync(join(tmpdir(), "visual-"))}`, "about:blank"], { stdio: "ignore" });
  try {
    let tabs: { type: string; webSocketDebuggerUrl: string }[] = [];
    for (let i = 0; i < 50 && !tabs.length; i++) {
      await new Promise((r) => setTimeout(r, 200));
      tabs = await fetch(`http://127.0.0.1:${debugPort}/json`).then((r) => r.json()).catch(() => []);
    }
    const ws = new WebSocket(tabs.find((t) => t.type === "page")!.webSocketDebuggerUrl);
    await new Promise((r) => (ws.onopen = r));
    let id = 0;
    const pending = new Map<number, (v: unknown) => void>();
    ws.onmessage = (m) => { const d = JSON.parse(String(m.data)); if (d.id && pending.has(d.id)) { pending.get(d.id)!(d.result); pending.delete(d.id); } };
    const send = (method: string, params: object = {}) => new Promise<any>((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
    await send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
    await send("Page.navigate", { url: `http://localhost:${port}/visual/` });
    let groups: { name: string; tops: number[]; heights: number[] }[] = [];
    for (let i = 0; i < 60 && !groups.length; i++) {
      await new Promise((r) => setTimeout(r, 250));
      const out = await send("Runtime.evaluate", { returnByValue: true, expression: `[...document.querySelectorAll('[data-group]')].map(g => {
        const r = [...g.querySelectorAll(g.dataset.item)].map(b => b.getBoundingClientRect());
        return { name: g.dataset.group, tops: r.map(x => x.top), heights: r.map(x => x.height) }; })` });
      groups = out?.result?.value ?? [];
    }
    ws.close();
    assert.ok(groups.length >= 40, `rendered ${groups.length} groups`);
    const bad = groups.filter((g) => g.tops.length !== 4 || Math.max(...g.tops) - Math.min(...g.tops) > 1 || Math.max(...g.heights) - Math.min(...g.heights) > 1);
    assert.deepEqual(bad.map((g) => `${g.name}: tops ${g.tops.map(Math.round)} heights ${g.heights.map(Math.round)}`), []);
  } finally {
    chrome.kill();
    await server.close();
  }
});
