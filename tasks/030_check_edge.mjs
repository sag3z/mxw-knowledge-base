/* tasks/030_check_edge.mjs — автономный архив под headless Edge через DevTools Protocol.
   Открывает file://…/Открыть карту.html БЕЗ --allow-file-access-from-files, гоняет
   смысловой поиск и чат без ключа, меряет время первой загрузки модели и повторного
   запроса, собирает ошибки консоли, снимает скриншот в data/ui_chat_archive.png.

   Запуск:  node tasks/030_check_edge.mjs "<путь к папке архива>" [out.png]
   Печатает JSON-отчёт в stdout (карточки источников, тайминги, ошибки консоли). */
import { spawn } from "node:child_process";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { setTimeout as sleep } from "node:timers/promises";

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ARCHIVE = process.argv[2];
const OUT_PNG = process.argv[3] || "data/ui_chat_archive.png";
const Q = "Какие антидетекты советовали для мультиаккаунтов?";
if (!ARCHIVE) { console.error("usage: node 030_check_edge.mjs <archive folder> [out.png]"); process.exit(2); }

const entry = pathToFileURL(join(ARCHIVE, "Открыть карту.html")).href;
const port = 9300 + Math.floor(Math.random() * 200);
const profile = await mkdtemp(join(tmpdir(), "edge-mxw-"));
const proc = spawn(EDGE, [
  "--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  "--no-first-run", "--no-default-browser-check", "--disable-gpu",
  "--window-size=1440,900", "about:blank",
], { stdio: ["ignore", "ignore", "ignore"] });
proc.on("exit", () => {});

try {
  /* --- ждём DevTools-эндпоинт ------------------------------------------------------- */
  let targets = null;
  for (let i = 0; i < 100 && !targets; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/json/list`);
      const list = await r.json();
      targets = list.filter(t => t.type === "page");
    } catch { await sleep(200); }
  }
  if (!targets?.length) throw new Error("DevTools не поднялся");
  const ws = new WebSocket(targets[0].webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error("ws")); });

  let seq = 0;
  const pending = new Map();
  const consoleEvents = [];
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const { res, rej } = pending.get(m.id);
      pending.delete(m.id);
      m.error ? rej(new Error(m.error.message)) : res(m.result);
    } else if (m.method === "Runtime.consoleAPICalled") {
      consoleEvents.push({ kind: m.params.type, text: (m.params.args || []).map(a => a.value ?? a.description ?? "").join(" ") });
    } else if (m.method === "Runtime.exceptionThrown") {
      const d = m.params.exceptionDetails;
      consoleEvents.push({ kind: "exception", text: (d.exception?.description || d.text || "") });
    }
  };
  const send = (method, params = {}) => new Promise((res, rej) => {
    const i = ++seq;
    pending.set(i, { res, rej });
    ws.send(JSON.stringify({ id: i, method, params }));
  });
  const evaluate = async (expression, timeoutMs = 240000) => {
    const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true, timeout: timeoutMs });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  };

  await send("Runtime.enable");
  await send("Page.enable");
  await send("Log.enable").catch(() => {});

  /* --- открываем архив с диска (file://, без флагов) --------------------------------- */
  const loadP = new Promise(res => {
    const h = m => { if (m.method === "Page.loadEventFired") { ws.removeEventListener?.("message", h); res(); } };
    ws.addEventListener("message", ev => {
      const m = JSON.parse(ev.data);
      if (m.method === "Page.loadEventFired") res();
    });
  });
  await send("Page.navigate", { url: entry });
  await Promise.race([loadP, sleep(15000)]);
  await sleep(1500);   // boot: график, сайдбар

  /* --- 1) модель: первая загрузка и повторный запрос -------------------------------- */
  const model = await evaluate(`(async () => {
    const t0 = performance.now();
    await __m.embed_web.embedWebReady(t => {});
    const loadMs = performance.now() - t0;
    const t1 = performance.now();
    await __m.embed_web.embedWebQuery("прогрев");
    const warmMs = performance.now() - t1;
    const t2 = performance.now();
    await __m.embed_web.embedWebQuery("проверка скорости поиска");
    const queryMs = performance.now() - t2;
    return { loadMs: Math.round(loadMs), warmMs: Math.round(warmMs), queryMs: Math.round(queryMs) };
  })()`);

  /* --- 2) поиск: api.search (смысл + слова) ------------------------------------------ */
  const search = await evaluate(`(async () => {
    const t0 = performance.now();
    const r = await __m.api.search(${JSON.stringify(Q)});
    const ms = Math.round(performance.now() - t0);
    return { ms, semantic: r.semantic, n: r.results.length,
             top: r.results.slice(0, 10).map(x => ({ id: x.id, title: x.title })) };
  })()`);

  /* --- 3) чат без ключа: вопрос → источники + подсказка про ⚙ Настройки -------------- */
  const runChat = (q) => evaluate(`(async () => {
    location.hash = "#/chat";
    await new Promise(r => setTimeout(r, 400));
    const inp = document.querySelector("#cInput");
    inp.value = ${JSON.stringify(q)};
    inp.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    const t0 = performance.now();
    const deadline = Date.now() + 240000;
    while (Date.now() < deadline) {
      await new Promise(r => setTimeout(r, 500));
      const err = document.querySelector(".am-err");
      const cards = [...document.querySelectorAll("#chatFeed .src-card")].map(c => ({
        n: c.querySelector(".src-n")?.textContent || "",
        kind: c.dataset.act || "",
        title: c.querySelector(".src-t")?.textContent || "",
        meta: c.querySelector(".src-m")?.textContent || "",
        snippet: c.querySelector(".src-s")?.textContent || "",
      }));
      if (err && cards.length)
        return { ms: Math.round(performance.now() - t0), err: err.textContent, n: cards.length,
                 total: (document.querySelector("#chatFeed .src-line")?.textContent || "").replace(/\\s+/g, " ").trim(),
                 cards: cards.slice(0, 8) };
      if (err && !cards.length && Date.now() - t0 > 60000)
        return { ms: Math.round(performance.now() - t0), err: err.textContent, n: 0, cards: [] };
    }
    return { timeout: true, n: 0, cards: [] };
  })()`);
  const chat = await runChat(Q);

  /* --- скриншот: раскрываем источники ------------------------------------------------ */
  await evaluate(`document.querySelector("#chatFeed .src-line")?.click(); true`).catch(() => {});
  await sleep(400);
  await evaluate(`(() => { const sc = document.querySelector("#chatScroll"); if (sc) sc.scrollTop = sc.scrollHeight; return true; })()`);
  await sleep(400);
  const shot = await send("Page.captureScreenshot", { format: "png" });
  await writeFile(OUT_PNG, Buffer.from(shot.data, "base64"));

  /* --- 4) статейный поиск: вопрос по теме статьи → карточки 📄 (artHitsLocal) --------
     В ленте видны только топ-6 карточек (slice(0,6) в chat.js), статьи идут после 18
     разговоров (n=19+), поэтому видна по счётчику «Нашлось в базе: N»: 18 — только
     разговоры, 21 — добавились 3 статьи. */
  const artChat = await runChat("Как детектируют ИИ-контент и что с водяными знаками?");
  const articles = {
    totalLine: artChat.total || "",
    n: artChat.cards.filter(c => c.kind === "article").length,
    top: artChat.cards.filter(c => c.kind === "article").slice(0, 3).map(c => ({ title: c.title, snippet: c.snippet.slice(0, 140) })),
  };

  const report = { entry, model, search, chat, articles, png: OUT_PNG,
                   consoleErrors: consoleEvents.filter(e => ["error", "exception"].includes(e.kind)) };
  process.stdout.write(JSON.stringify(report, null, 1));
} finally {
  proc.kill();
}
