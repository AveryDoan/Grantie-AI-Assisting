// usage: node shoot.mjs <url-hash-path> <out.png> [width] [height]
import { spawn } from "node:child_process";
const [,, path, out, w = "1280", h = "900"] = process.argv;
const KEY = process.env.KEY;
const base = "http://localhost:5173/";
const tok = (await (await fetch("http://localhost:8000/demo/login", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ role: "officer" }) })).json()).access_token;
const chrome = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", ["--headless=new", "--disable-gpu", "--remote-debugging-port=9333", `--window-size=${w},${h}`, "--user-data-dir=/tmp/shots/profile", "about:blank"], { stdio: "ignore" });
await new Promise((r) => setTimeout(r, 2500));
const tabs = await (await fetch("http://localhost:9333/json")).json();
const ws = new WebSocket(tabs.find((t) => t.type === "page").webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let id = 0; const pending = new Map();
ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d.result); pending.delete(d.id); } };
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
await send("Emulation.setDeviceMetricsOverride", { width: +w, height: +h, deviceScaleFactor: 1, mobile: false });
await send("Page.navigate", { url: base });
await new Promise((r) => setTimeout(r, 1500));
await send("Runtime.evaluate", { expression: `sessionStorage.setItem(${JSON.stringify(KEY)}, ${JSON.stringify(tok)})` });
await send("Page.navigate", { url: base + "?r=" + Date.now() + path });
await new Promise((r) => setTimeout(r, 3500));
if (process.env.CLICK) { await send("Runtime.evaluate", { expression: process.env.CLICK }); await new Promise((r) => setTimeout(r, 1500)); }
const shot = await send("Page.captureScreenshot", { format: "png" });
(await import("node:fs")).writeFileSync(out, Buffer.from(shot.data, "base64"));
chrome.kill(); process.exit(0);
