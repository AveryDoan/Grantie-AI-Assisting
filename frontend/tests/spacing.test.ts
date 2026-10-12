// Layout contract, measured in headless Chrome at 1440, 1024, 768 and 390 wide (and the drawer at 1280x720, 1440x900, 390x844):
//  1. nothing inside a card sits within 12px of the card's border (unless it is marked data-bleed);
//  2. the drawer starts at or below the header, its close button is visible and clickable, and its action bar is visible without scrolling.
// Uses the real stylesheets on a fixed harness page (visual/layout.html). Skipped, not failed, when Chrome is not installed.
// (Playwright is not a dependency of this project, so this drives Chrome over its debugging protocol like tests/visual.test.ts.)
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const CHROME = [process.env.CHROME_PATH, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome", "/usr/bin/chromium"].find((p) => p && existsSync(p));

const MEASURE = `(() => {
  const out = { tight: [], drawer: null };
  for (const card of document.querySelectorAll('[data-card]')) {
    const c = card.getBoundingClientRect();
    const fixed = getComputedStyle(card).position === 'fixed';
    for (const el of card.querySelectorAll('*')) {
      if (el.closest('[data-bleed]') || el.children.length && !/^(INPUT|TEXTAREA|SELECT|BUTTON|TD|TH)$/.test(el.tagName)) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2 || r.top < c.top || r.bottom > c.bottom + 0.5) continue;      // empty, or scrolled out of the card
      if (el.closest('.rv-table-wrap') && el.tagName !== 'TABLE' && !/^(TD|TH)$/.test(el.tagName)) continue;
      const gaps = { left: r.left - c.left, right: c.right - r.right, top: r.top - c.top, bottom: c.bottom - r.bottom };
      // content that scrolls inside the card (the drawer body) can legitimately reach its top and bottom edge: only the sides count
      const scrolls = (() => { for (let p = el.parentElement; p && p !== card; p = p.parentElement) { const o = getComputedStyle(p).overflowY; if (o === 'auto' || o === 'scroll') return true; } return false; })();
      const bad = Object.entries(gaps).filter(([k, g]) => g < 12 && !(fixed && g < 0) && !(scrolls && (k === 'top' || k === 'bottom')));
      if (bad.length && !/^(TD|TH)$/.test(el.tagName)) out.tight.push(card.getAttribute('aria-label') || card.className.split(' ')[0] || 'card', el.tagName + '.' + String(el.className).split(' ')[0], bad.map(([k, g]) => k + ':' + Math.round(g)).join(','));
    }
  }
  const h = document.querySelector('[data-header]').getBoundingClientRect();
  const d = document.querySelector('[data-drawer]').getBoundingClientRect();
  const x = document.querySelector('[data-close]').getBoundingClientRect();
  const a = document.querySelector('[data-actions]').getBoundingClientRect();
  const hit = document.elementFromPoint(x.left + x.width / 2, x.top + x.height / 2);
  out.drawer = { headerBottom: h.bottom, drawerTop: d.top, closeVisible: x.top >= h.bottom - 0.5 && x.bottom <= innerHeight, closeClickable: !!hit && (hit === document.querySelector('[data-close]') || document.querySelector('[data-close]').contains(hit)),
    actionsVisible: a.bottom <= innerHeight + 0.5 && a.top >= h.bottom, actionsBottom: a.bottom, vh: innerHeight };
  return out;
})()`;

test("spacing and drawer layout hold at every width", { skip: !CHROME && "Chrome not found", timeout: 180000 }, async () => {
  const { createServer } = await import("vite");
  const server = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), server: { port: 0, strictPort: false }, logLevel: "silent" });
  await server.listen();
  const port = (server.httpServer!.address() as { port: number }).port;
  const debugPort = 9800 + Math.floor(Math.random() * 300);
  const chrome = spawn(CHROME!, ["--headless=new", "--disable-gpu", `--remote-debugging-port=${debugPort}`, "--window-size=1440,900", `--user-data-dir=${mkdtempSync(join(tmpdir(), "spacing-"))}`, "about:blank"], { stdio: "ignore" });
  try {
    let tabs: { type: string; webSocketDebuggerUrl: string }[] = [];
    for (let i = 0; i < 50 && !tabs.length; i++) { await new Promise((r) => setTimeout(r, 200)); tabs = await fetch(`http://127.0.0.1:${debugPort}/json`).then((r) => r.json()).catch(() => []); }
    const ws = new WebSocket(tabs.find((t) => t.type === "page")!.webSocketDebuggerUrl);
    await new Promise((r) => (ws.onopen = r));
    let id = 0; const pending = new Map<number, (v: unknown) => void>();
    ws.onmessage = (m) => { const d = JSON.parse(String(m.data)); if (d.id && pending.has(d.id)) { pending.get(d.id)!(d.result); pending.delete(d.id); } };
    const send = (method: string, params: object = {}) => new Promise<any>((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
    const run = async (w: number, h: number) => {
      await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: w < 500 });
      await send("Page.navigate", { url: `http://localhost:${port}/visual/layout.html?w=${w}` });
      for (let i = 0; i < 60; i++) {
        await new Promise((r) => setTimeout(r, 250));
        const ok = await send("Runtime.evaluate", { returnByValue: true, expression: "!!document.querySelector('[data-actions]')" });
        if (ok?.result?.value) break;
      }
      await new Promise((r) => setTimeout(r, 400));
      return (await send("Runtime.evaluate", { returnByValue: true, expression: MEASURE })).result.value as { tight: string[]; drawer: Record<string, number | boolean> };
    };
    const problems: string[] = [];
    for (const [w, h] of [[1440, 900], [1024, 768], [768, 1024], [390, 844]]) {
      const r = await run(w, h);
      for (let i = 0; i < r.tight.length; i += 3) problems.push(`${w}px: ${r.tight[i]} > ${r.tight[i + 1]} (${r.tight[i + 2]})`);
    }
    assert.deepEqual([...new Set(problems)], []);
    for (const [w, h] of [[1280, 720], [1440, 900], [390, 844]]) {
      const { drawer: d } = await run(w, h);
      assert.ok((d.drawerTop as number) >= (d.headerBottom as number) - 0.5, `${w}x${h}: drawer top ${d.drawerTop} is under the header (${d.headerBottom})`);
      assert.ok(d.closeVisible && d.closeClickable, `${w}x${h}: the close button is not visible and clickable`);
      assert.ok(d.actionsVisible, `${w}x${h}: the action bar is not visible without scrolling (bottom ${d.actionsBottom} of ${d.vh})`);
    }
    ws.close();
  } finally { chrome.kill(); await server.close(); }
});
