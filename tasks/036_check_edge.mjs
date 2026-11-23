/* tasks/036_check_edge.mjs — отображение пометок проверки фактов (задача 036) под
   headless Edge через DevTools Protocol. Открывает локальный сайт
   http://127.0.0.1:8765/#/articles?article=Схемы/Прокси и антидетект.md и проверяет:
     - в тексте статьи нет сырой подстроки «^[» (inline-сноски Obsidian);
     - элементов .fc-mark в ридере ≥ 10, у них есть title и data-fc;
     - в шапке есть плашка .fc-plate («✓ проверено … · подтверждено … · исправлено …»);
     - общий tooltip #tip показывается при наведении на .fc-mark (как у сносок чата);
     - в списке статей (#/articles) у проверенных есть отметки .art-fc («✓ 93%»).
   Скриншот — data/ui_article_marks.png (открывать не нужно).

   Запуск:  node tasks/036_check_edge.mjs [out.png]
   Сервер (python app/server.py) должен уже слушать 127.0.0.1:8765.
   Печатает JSON-отчёт в stdout; exit 1, если какая-то проверка не прошла. */
import { spawn } from "node:child_process";
import { writeFile } from "node:fs/promises";
import { setTimeout as sleep } from "node:timers/promises";

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const OUT_PNG = process.argv[2] || "data/ui_article_marks.png";
const BASE = "http://127.0.0.1:8765/";
const ARTICLE = "Схемы/Прокси и антидетект.md";
const URL = BASE + "#/articles?article=" + encodeURIComponent(ARTICLE);

const port = 9500 + Math.floor(Math.random() * 200);
const profile = (await mkdtemp()).path;
const proc = spawn(EDGE, [
  "--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  "--no-first-run", "--no-default-browser-check", "--disable-gpu",
  "--window-size=1440,900", "about:blank",
], { stdio: ["ignore", "ignore", "ignore"] });
proc.on("exit", () => {});

async function mkdtemp() {
  const { mkdtemp: mk } = await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const { join } = await import("node:path");
  return { path: await mk(join(tmpdir(), "edge-mxw-")) };
}

try {
  /* --- ждём DevTools-эндпоинт ------------------------------------------------------- */
  let target = null;
  for (let i = 0; i < 100 && !target; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/json/list`);
      const list = await r.json();
      target = list.find(t => t.type === "page");
    } catch { await sleep(200); }
  }
  if (!target) throw new Error("DevTools не поднялся");
  const ws = new WebSocket(target.webSocketDebuggerUrl);
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
      consoleEvents.push({ kind: "exception", text: d.exception?.description || d.text || "" });
    }
  };
  const send = (method, params = {}) => new Promise((res, rej) => {
    const i = ++seq;
    pending.set(i, { res, rej });
    ws.send(JSON.stringify({ id: i, method, params }));
  });
  const evaluate = async (expression, timeoutMs = 60000) => {
    const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true, timeout: timeoutMs });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  };

  await send("Runtime.enable");
  await send("Page.enable");

  /* --- открываем deep-link статьи --------------------------------------------------- */
  const loadP = new Promise(res => {
    ws.addEventListener("message", ev => {
      const m = JSON.parse(ev.data);
      if (m.method === "Page.loadEventFired") res();
    });
  });
  await send("Page.navigate", { url: URL });
  await Promise.race([loadP, sleep(15000)]);

  /* ждём: список статей загрузился и статья в drawer отрендерилась (≥10 .fc-mark) */
  const waitDom = async (expr, ms) => {
    const deadline = Date.now() + ms;
    for (;;) {
      const n = await evaluate(expr).catch(() => 0);
      if (n) return n;
      if (Date.now() > deadline) return 0;
      await sleep(250);
    }
  };
  const nRows = await waitDom(`document.querySelectorAll("#artGroups .art-row").length`, 30000);
  const nMarks = await waitDom(`document.querySelectorAll("#dbody .fc-mark").length >= 10 ? document.querySelectorAll("#dbody .fc-mark").length : 0`, 30000);
  await sleep(400);

  /* --- проверки ---------------------------------------------------------------------- */
  const reader = await evaluate(`(() => {
    const body = document.querySelector("#dbody .md");
    const marks = [...document.querySelectorAll("#dbody .fc-mark")];
    const plate = document.querySelector("#dbody .fc-plate");
    return {
      title: document.querySelector("#dtitle")?.textContent || "",
      rawFootnoteLeft: body ? body.textContent.includes("^[") : null,
      nMarks: marks.length,
      marksHaveAttrs: marks.every(m => m.dataset.fc && m.title && m.title === m.dataset.fc),
      firstNote: marks[0]?.dataset.fc || "",
      plateText: plate ? plate.textContent.replace(/\\s+/g, " ").trim() : "",
      plateYellowDot: plate ? !!plate.querySelector(".dot") : false,
      plateInMeta: !!document.querySelector(".art-meta .fc-plate"),
      markCursor: marks[0] ? getComputedStyle(marks[0]).cursor : "",
      markColor: marks[0] ? getComputedStyle(marks[0]).color : "",
    };
  })()`);

  /* общий tooltip из ui.js: mouseover на .fc-mark → #tip виден с текстом сноски; mouseout → скрыт */
  const tip = await evaluate(`(() => {
    const m = document.querySelector("#dbody .fc-mark");
    m.dispatchEvent(new MouseEvent("mouseover", { bubbles: true, clientX: 200, clientY: 200 }));
    const shown = getComputedStyle(document.querySelector("#tip")).display;
    const text = document.querySelector("#tip").textContent.replace(/\\s+/g, " ").trim();
    m.dispatchEvent(new MouseEvent("mouseout", { bubbles: true }));
    const hidden = getComputedStyle(document.querySelector("#tip")).display;
    return { shown, text, hiddenAfterOut: hidden };
  })()`);

  /* список статей: у проверенных отметка «✓ N%» справа от названия */
  const list = await evaluate(`(() => {
    const rows = [...document.querySelectorAll("#artGroups .art-row")];
    const fcs = [...document.querySelectorAll("#artGroups .art-fc")];
    const sample = rows.find(r => r.querySelector(".art-fc"));
    return {
      nRows: rows.length,
      nFc: fcs.length,
      fcTexts: fcs.slice(0, 5).map(f => f.textContent.trim()),
      sampleTitle: sample ? sample.querySelector(".art-t").textContent : "",
      sampleFc: sample ? sample.querySelector(".art-fc").textContent.trim() : "",
      fcAfterTitle: sample ? !!(sample.querySelector(".art-t").nextElementSibling?.classList.contains("art-fc")) : false,
    };
  })()`);

  /* --- скриншот: ридер статьи с плашкой и значками (не открывать) -------------------- */
  await evaluate(`(() => { const b = document.querySelector("#dbody"); if (b) b.scrollTop = 0; return true; })()`);
  await sleep(300);
  const shot = await send("Page.captureScreenshot", { format: "png" });
  await writeFile(OUT_PNG, Buffer.from(shot.data, "base64"));

  const report = {
    url: URL, png: OUT_PNG,
    checks: {
      noRawFootnote: reader.rawFootnoteLeft === false,
      nMarks: reader.nMarks,
      marksOk: reader.nMarks >= 10,
      marksHaveAttrs: reader.marksHaveAttrs,
      plate: reader.plateText,
      plateOk: /^✓ проверено \d{2} \D+ \d{4} · подтверждено \d+%/.test(reader.plateText),
      plateYellowDot: reader.plateYellowDot,
      markCursor: reader.markCursor,
      markColor: reader.markColor,
      tipShown: tip.shown === "block",
      tipText: tip.text,
      tipHiddenAfterOut: tip.hiddenAfterOut === "none",
      listFcCount: list.nFc,
      listOk: list.nFc > 0,
      listSample: { title: list.sampleTitle, fc: list.sampleFc, fcAfterTitle: list.fcAfterTitle },
    },
    nListRows: nRows,
    reader,
    list,
    consoleErrors: consoleEvents.filter(e => ["error", "exception"].includes(e.kind)),
  };
  process.stdout.write(JSON.stringify(report, null, 1));

  const c = report.checks;
  const failed = Object.entries({
    noRawFootnote: c.noRawFootnote, marksOk: c.marksOk, marksHaveAttrs: c.marksHaveAttrs,
    plateOk: c.plateOk, tipShown: c.tipShown, tipHiddenAfterOut: c.tipHiddenAfterOut, listOk: c.listOk,
  }).filter(([, v]) => !v);
  if (failed.length) { console.error("\nFAILED:", failed.map(([k]) => k).join(", ")); process.exit(1); }
} finally {
  proc.kill();
}
