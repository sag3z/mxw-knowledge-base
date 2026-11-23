/* conv.js — панель разговора (drawer) и ридер статей */
import { icon } from "./icons.js";
import * as api from "./api.js";
import {
  $, esc, escAttr, escRe, CAT, SCHEME_RU, MONS, parseHash, nav,
  fmtDate, fmtDateShort, fmtTime, humanSize, convTitle, clip, graph,
  drawerIsOpen, drawerCur, drawerSetContent, drawerOpenUI, drawerSetTitle,
  drawerPushStack, navParam, applyAnon, linkify, urlLink, openViewer, act,
  tipShow, tipHide, avHtml, isAnon, anonName,
} from "./ui.js";

let curConv = null;                       // {d, byId, groups, photos}
let convMode = localStorage.getItem("mxw_convMode") || "main";   // main | noflood | all
let convExpanded = new Set();
let qWords = [];
const marked = window.marked;

const plural = (n, one, few, many) => {   // «1 источник / 2 источника / 5 источников»
  const a = Math.abs(n) % 100, b = a % 10;
  return a > 10 && a < 20 ? many : b > 1 && b < 5 ? few : b === 1 ? one : many;
};

/* URL вслед за содержимым панели — без записи в историю */
function syncUrl(arg) {
  const { route } = parseHash();
  if (arg.kind === "conv") nav(route, { conv: arg.arg, q: arg.opts && arg.opts.q, focus: arg.opts && arg.opts.focus }, false);
  else nav(route, {}, false);
}

const markHits = t => qWords.length && t
  ? t.replace(new RegExp("(?<![\\p{L}\\p{N}_])(" + qWords.slice().sort((a, b) => b.length - a.length).map(escRe).join("|") + ")", "giu"), "<mark>$1</mark>")
  : t;

/* ---------- открытие: из основного поля — push ( назад браузера закроет панель) ---------- */
export function openConv(id, opts = {}) {
  if (opts.push) { navParam("conv", id, { q: opts.q, focus: opts.focus }, true); return; }
  return renderConv(id, opts);
}
/* статья — отдельной страницей #/articles?article=… (articles.js); назад браузера вернёт обратно */
export function openArticle(path) {
  nav("articles", { article: path }, true);
}
export async function openArticleByName(name, opts = {}) {
  const a = await api.findArticle(name);
  if (a) openArticle(a.path, opts);
}

/* текущее содержимое — для кнопки «←» (восстановление без записи в историю) */
function pushCurToStack() {
  const cur = drawerCur();
  if (cur) drawerPushStack(() => cur.restore());
}

async function setDrawerContent(arg, title, more, { skipStack } = {}) {
  if (!skipStack && drawerIsOpen()) pushCurToStack();   // панель уже открыта — «←» вернёт прежнее
  drawerSetContent(arg);
  drawerOpenUI(title, more);
}

/* ---------- разговор ---------- */
async function renderConv(id, { q, focus, fromStack } = {}) {
  const cur = !fromStack && drawerCur();
  if (cur && cur.kind === "conv" && cur.arg === id && (cur.opts || {}).q === (q || null)) return;   // уже открыт
  const arg = {
    kind: "conv", arg: id, opts: { q, focus },
    restore: () => renderConv(id, { q, focus, fromStack: true }),
  };
  await setDrawerContent(arg, "Разговор", id, { skipStack: !!fromStack });
  syncUrl(arg);
  let d;
  try { d = await api.getConv(id, q, focus); }
  catch (err) { $("#dbody").innerHTML = `<div class="empty"><div class="e-t">Не удалось открыть разговор</div><div class="e-h">${esc(err.message)}</div></div>`; return; }
  if (drawerCur() !== arg) return;        // пока грузилось, открыли другое

  qWords = q ? (q.toLowerCase().match(/[\p{L}\p{N}_]+/gu) || []).filter(w => w.length >= 2) : [];
  d.byId = new Map(d.messages.map(m => [m.id, m]));
  curConv = { d, byId: d.byId, groups: [], photos: d.messages.flatMap(m => (m.files || []).filter(f => f.kind === "photos").map(f => f.i)) };
  convExpanded = new Set();

  const cat = CAT[d.category] || CAT.other;
  const sameDay = (d.end || "").slice(0, 10) === (d.start || "").slice(0, 10);
  const when = d.start
    ? `${fmtDate(d.start)}, ${fmtTime(d.start)} — ${sameDay ? fmtTime(d.end) : fmtDate(d.end) + ", " + fmtTime(d.end)}`
    : "";
  drawerSetTitle("Разговор");

  mrefN = 0;
  const chips = [
    when ? `<span class="cm-i">${icon("calendar", 14)}${esc(when)}</span>` : "",
    d.topic ? `<span class="cm-i">${icon("chat", 14)}${esc(d.topic)}</span>` : "",
    `<span class="chip"><span class="dot" style="background:${cat[1]}"></span>${esc(cat[0])}</span>`,
    `<span class="cm-i">${d.messages.length} ${plural(d.messages.length, "сообщение", "сообщения", "сообщений")}</span>`,
    d.verdict === "keep" ? `<span class="chip star">⭐ ценное</span>` : "",
  ].filter(Boolean).join("");
  let h = `<h2 class="cv-title">${esc(convTitle(d.summary))}</h2>
    <div class="cv-meta">${chips}</div>
    <div class="cv-card">
      <div class="cv-h">Кратко</div>
      <div class="cv-t">${d.summary ? mrefs(esc(d.summary)) : "<i>без выжимки</i>"}</div>
    </div>`;
  if (d.has_scheme && d.scheme_summary) {
    const types = (d.scheme_types || []).map(t => SCHEME_RU[t] || t);
    h += `<div class="cv-card scheme">
      <div class="cv-h">Схема${types.length ? types.map(t => `<span class="chip">${esc(t)}</span>`).join("") : ""}</div>
      <div class="cv-t">${mrefs(esc(d.scheme_summary))}</div>
    </div>`;
  }
  if ((d.tags || []).length) {
    h += `<div class="cv-tags">${d.tags.slice(0, 3).map(t => `<span class="chip">${esc(t.replace(/^#/, ""))}</span>`).join("")}</div>`;
  }
  h += `<div class="mrow">
      <div class="seg">
        <button class="${convMode === "main" ? "on" : ""} mbtn" data-act="conv-mode" data-arg="main"
          title="Главное — ключевые сообщения разметки, найденные по запросу и их ближайшие соседи">Главное</button>
        <button class="${convMode === "noflood" ? "on" : ""} mbtn" data-act="conv-mode" data-arg="noflood"
          title="Без флуда — все содержательные сообщения, без коротких реакций">Без флуда</button>
        <button class="${convMode === "all" ? "on" : ""} mbtn" data-act="conv-mode" data-arg="all"
          title="Всё — все сообщения, включая реакции и смайлики">Всё</button>
      </div>
      <span class="cnt" id="pcnt"></span>
    </div>
    <div id="msgs"></div>
    <div class="askrow"><button class="btn" data-act="ask-conv" data-arg="${escAttr(id)}">${icon("chat", 16)}Спросить ИИ про этот разговор</button></div>`;
  $("#dbody").innerHTML = h;
  renderMsgs();
  applyAnon($("#dbody"));
  if (focus != null) jumpToMsg(+focus);
}

/* ---------- ссылки на сообщения в выжимке: (#10635), (#10643–#10652), #10714 → номер-сноска ---------- */
let mrefN = 0;
const MREF_GROUP_RE = /\s*\((#\d+(?:\s*[–—-]\s*#?\d+)?(?:\s*[,;]\s*#\d+(?:\s*[–—-]\s*#?\d+)?)*)\)/g;
function mrefs(escaped) {
  const one = ref => {
    const id = +ref.match(/\d+/)[0];
    return `<a class="mref" data-act="jump-msg" data-arg="${id}">${++mrefN}</a>`;
  };
  return escaped
    .replace(MREF_GROUP_RE, (m, inner) => `<sup class="mrefs">${inner.split(/\s*[,;]\s*/).map(one).join('<span class="sc">,</span>')}</sup>`)
    .replace(/(^|[\s(])#(\d{3,})(?:\s*[–—-]\s*#?\d+)?/g, (m, pre, n) => `${pre}<sup class="mrefs">${one("#" + n)}</sup>`);
}
function mrefTip(id) {
  const m = curConv && curConv.byId.get(id);
  if (!m) return `<b>Сообщение</b><div class="m">не найдено в этом разговоре</div>`;
  const who = m.author ? (isAnon() ? anonName(m.author) : m.author) : "сообщение";
  const t = clip(((m.text || "") || (m.media_text || []).map(x => x.text).join(" ") || (m.media || []).join(" ") || "…").replace(/\s+/g, " ").trim(), 160);
  return `<b>${esc(who)} <span class="m">${esc(fmtTime(m.ts))}</span></b><div class="m">${esc(t)}${t.length >= 160 ? "…" : ""}</div>`;
}

/* ---------- сообщения ---------- */
function quoteHtml(m) {   // «↩ <начало исходного>» — клик прыгает к исходному
  const src = curConv.byId.get(m.reply_to);
  if (!src) return "";
  const t = clip(((src.text || "") || (src.media_text || []).map(x => x.text).join(" ")
    || (src.media || []).join(" ") || "…").replace(/\s+/g, " ").trim(), 90);
  const who = src.author ? `<span class="rq-a" data-author="${escAttr(src.author)}">${esc(src.author)}</span>` : "";
  return `<div class="rq" data-act="jump-msg" data-arg="${src.id}" title="Перейти к сообщению, на которое это ответ">${icon("reply", 13)}${who}<span class="rq-t">${esc(t)}</span></div>`;
}

const DANGER_RE = /\.(zip|exe|bat|js|apk|msi)$/i;
const mediaHit = t => qWords.length && qWords.some(w => (t || "").toLowerCase().includes(w));
const mUrl = (f, thumb) => api.mediaUrl(f.i, thumb && f.th);
const mbSize = n => n >= 104857600 ? Math.round(n / 1048576) + " МБ" : (n / 1048576).toFixed(1) + " МБ";

function mtFor(m, f, used) {   // расшифровка/описание, привязанное к файлу по kind+имени
  const k = m.media_text.findIndex((x, i) => !used.has(i) && x.kind === f.kind && x.file === f.name);
  if (k < 0) return null;
  used.add(k);
  return m.media_text[k].text || "";
}

function mediaHtml(m) {   // файлы сообщения по kind + привязанные расшифровки
  const files = m.files || [];
  const used = new Set();
  const fmt = t => m.hit ? markHits(esc(t)) : esc(t);
  const dtHtml = t => `<details class="media"${mediaHit(t) ? " open" : ""}><summary>показать расшифровку</summary><div>${fmt(t)}</div></details>`;
  const photoDesc = t => `<div class="media mdesc${mediaHit(t) ? " full" : ""}" title="клик — развернуть целиком" data-act="toggle-full">${fmt(t)}</div>`;
  let h = "";
  const photos = files.filter(f => f.kind === "photos");
  if (photos.length) {
    h += `<div class="pgrid">` + photos.map(f =>
      `<img loading="lazy" alt="Фото из чата" src="${mUrl(f, 1)}" data-orig="${mUrl(f)}" data-act="photo" data-arg="${f.i}" title="Открыть фото на весь экран">`).join("") + `</div>`;
    for (const f of photos) { const t = mtFor(m, f, used); if (t) h += photoDesc(t); }
  }
  for (const f of files.filter(f => f.kind === "voice_messages")) {
    h += `<div class="voice"><audio controls preload="none" src="${mUrl(f)}"></audio></div>`;
    const t = mtFor(m, f, used); if (t) h += dtHtml(t);
  }
  for (const f of files.filter(f => f.kind === "round_video_messages")) {
    h += `<video class="round" controls preload="none" src="${mUrl(f)}"></video>`;
    const t = mtFor(m, f, used); if (t) h += dtHtml(t);
  }
  const anim = (m.media || []).some(l => /Animation/i.test(l));   // GIF-анимация из экспорта
  for (const f of files.filter(f => f.kind === "video_files")) {
    if (f.ex === 1) {   // > 25 МБ или лёгкий архив — видео в архив не попало
      h += `<div class="media">видео не включено в архив (${mbSize(f.size)})</div>`;
      const t = mtFor(m, f, used); if (t) h += dtHtml(t);
      continue;
    }
    h += `<video class="vfile" ${anim ? "autoplay muted loop playsinline" : 'controls preload="none"'} src="${mUrl(f)}"></video>`;
    const t = mtFor(m, f, used); if (t) h += dtHtml(t);
  }
  for (const f of files.filter(f => f.kind === "files")) {
    if (f.ex === 2) {   // > 10 МБ или потерялось — файла в архиве нет
      h += `<div class="frow">${icon("clip", 14)}${esc(f.name)} <span class="fs">(${humanSize(f.size)})</span> — не включено в архив</div>`;
      continue;
    }
    h += `<div class="frow">${icon("clip", 14)}<a href="${mUrl(f)}">${esc(f.name)}</a> <span class="fs">(${humanSize(f.size)})</span> — <a href="${mUrl(f)}">скачать</a></div>`;
    if (DANGER_RE.test(f.name)) h += `<div class="warn">${icon("alert", 15)}архив/программа из чата — не запускай без проверки</div>`;
  }
  return h;
}

function msgHtml(m, d, cont = false) {
  const txt = m.hit ? markHits(esc(m.text)) : esc(m.text);
  const hasFiles = (m.files || []).length > 0;
  const label = m.media.length && !hasFiles && !m.media_text.length ? ` <span class="d">${esc(m.media.join(" "))}</span>` : "";
  const media = hasFiles ? mediaHtml(m) +   // расшифровки без своего файла в индексе — прежним блоком
      m.media_text.filter(x => !m.files.some(f => f.kind === x.kind && f.name === x.file))
        .map(x => `<div class="media"><b>${esc(x.kind)}</b> ${m.hit ? markHits(esc(x.text)) : esc(x.text)}</div>`).join("")
    : m.media_text.map(x => `<div class="media"><b>${esc(x.kind)}</b> ${m.hit ? markHits(esc(x.text)) : esc(x.text)}</div>`).join("");
  const sameDay = (m.ts || "").slice(0, 10) === (d.start || "").slice(0, 10);
  const tm = sameDay ? fmtTime(m.ts) : `${fmtDateShort(m.ts)}, ${fmtTime(m.ts)}`;
  const who = m.author
    ? `<span class="a" data-act="person" data-arg="${escAttr(m.author)}" data-author="${escAttr(m.author)}" title="Карточка участника: чем он помогал в чате">${esc(m.author)}</span>`
    : `<span class="a anon-a">без имени</span>`;
  const cls = ["msg", m.key && "key", m.hit && "hit", m.noise && "noise", cont && "cont"].filter(Boolean).join(" ");
  const gut = cont
    ? `<span class="m-tm">${esc(fmtTime(m.ts))}</span>`
    : (m.author ? avHtml(m.author) : `<span class="mav">?</span>`);
  return `<div class="${cls}" id="m${m.id}">
      <div class="m-gut">${gut}</div>
      <div class="m-body">${quoteHtml(m)}
        ${cont ? "" : `<div class="m-head">${who}<span class="d">${esc(tm)}${m.fwd ? ` · переслано от <span class="fa" data-fwd="${escAttr(m.fwd)}">${esc(m.fwd)}</span>` : ""}</span></div>`}
        ${m.text || label ? `<div class="txt">${m.text ? urlLink(txt) : ""}${label}</div>` : ""}
        ${media}
      </div></div>`;
}

function renderMsgs() {
  const d = curConv.d, msgs = d.messages;
  const vis = msgs.map(m => convMode === "all" ? true : convMode === "noflood" ? !m.noise : !!m.show);
  const groups = [];
  for (let s = null, k = 0; k <= msgs.length; k++) {   // группа = подряд идущие скрытые
    const v = k < msgs.length ? vis[k] : true;
    if (!v && s == null) s = k;
    if (v && s != null) { groups.push({ key: msgs[s].id, s, e: k }); s = null; }
  }
  curConv.groups = groups;
  const gByStart = {};
  for (const g of groups) { gByStart[g.s] = g; if (convExpanded.has(g.key)) for (let i = g.s; i < g.e; i++) vis[i] = true; }
  let h = "", k = 0, prev = null;
  while (k < msgs.length) {
    if (vis[k]) {
      const m = msgs[k];
      const cont = prev && m.author && prev.author === m.author && !m.reply_to && !m.fwd && !prev.fwd
        && Math.abs(Date.parse(m.ts) - Date.parse(prev.ts)) < 5 * 60e3;
      h += msgHtml(m, d, cont); prev = m; k++;
    } else {
      const g = gByStart[k], n = g.e - g.s;
      h += `<button class="hid" data-act="expand-group" data-arg="${g.key}"><span>${icon("chev-down", 14)}Показать ещё ${n} ${plural(n, "сообщение", "сообщения", "сообщений")}</span></button>`;
      k = g.e; prev = null;
    }
  }
  $("#msgs").innerHTML = h;
  $("#pcnt").textContent = `показано ${$("#msgs").querySelectorAll(".msg").length} из ${msgs.length}`;
  document.querySelectorAll("#dbody .mbtn").forEach(b => b.classList.toggle("on", b.dataset.arg === convMode));
  $("#msgs").querySelectorAll("img").forEach(img => {   // миниатюра не загрузилась → оригинал (data-orig); не вышло — убрать ячейку
    img.addEventListener("error", () => {
      if (img.dataset.r || !img.dataset.orig) { img.remove(); return; }
      img.dataset.r = "1";
      img.src = img.dataset.orig;
    });
  });
  applyAnon($("#msgs"));
}

function jumpToMsg(id) {   // прокрутка к сообщению; если оно в свёрнутой группе — раскрыть
  let el = document.getElementById("m" + id);
  if (!el) {
    const k = curConv.d.messages.findIndex(m => m.id === id);
    const g = curConv.groups.find(g => k >= g.s && k < g.e);
    if (!g) return;
    convExpanded.add(g.key); renderMsgs();
    el = document.getElementById("m" + id);
  }
  if (el) { el.scrollIntoView({ block: "center", behavior: "smooth" }); el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 2600); }
}

/* ---------- статья ---------- */
export function parseFM(md) {
  /* vault в Windows-переводах строк (CRLF) — нормализуем до \n, дальше регулярки
     ищут только \n. Без /s точка не пересекает строки — frontmatter не вырезался. */
  const text = String(md || "").replace(/\r\n?/g, "\n");
  const m = text.match(/^---\s*\n(.*?)\n---\s*\n/s);
  const fm = {};
  if (m) for (const line of m[1].split("\n")) {
    const i = line.indexOf(":");
    if (i > 0) fm[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return { fm, body: text.replace(/^---\s*\n.*?\n---\s*\n/s, "") };
}
const prettyPeriod = s => (s || "").replace(/(\d{4})-(\d{2})-(\d{2})/g, (_, y, mo, da) => `${+da} ${MONS[+mo - 1]} ${y}`);

/* ---------- проверка фактов: пометки в тексте + плашка в шапке ---------- */

/* inline-сноски Obsidian ⚠️^[текст] (и просто ^[текст]) после неподтверждённых утверждений:
   marked их не знает — на сайте был виден сырой текст ^[…]. До передачи в marked меняем их
   на значок ⚠ (жёлтый #f5a524, cursor:help, класс fc-mark) с текстом сноски в подсказке.
   Текст сноски — не HTML: экранируем. */
const FC_MARK_RE = /(?:⚠️?\s*)?\^\[([^\]]*)\]/g;
export function fcMarks(md) {
  return md.replace(FC_MARK_RE, (m, note) =>
    `<span class="fc-mark" data-fc="${escAttr(note)}" title="${escAttr(note)}">⚠</span>`);
}

/* плашка в мета-строке шапки: «✓ проверено 20 ноя 2026 · подтверждено 90% · исправлено 3»
   (вторичный цвет). Нет поля «проверено» (даты) — плашки нет; подтверждено < 80% — жёлтая точка. */
export function fcPlateHtml(fm) {
  const dt = (fm["проверено"] || "").match(/\d{4}-\d{2}-\d{2}/);
  if (!dt) return "";
  const parts = [`проверено ${fmtDate(dt[0])}`];
  const pc = parseInt((fm["подтверждено"] || "").replace(/\D+/g, ""), 10);
  if (Number.isFinite(pc)) parts.push(`подтверждено ${pc}%`);
  const nFix = parseInt(fm["исправлено"], 10);
  if (Number.isFinite(nFix)) parts.push(`исправлено ${nFix}`);
  const dot = Number.isFinite(pc) && pc < 80
    ? `<span class="dot" style="background:#f5a524" title="подтверждено меньше 80%"></span>` : "";
  return `<span class="fc-plate">${dot}<span>✓ ${esc(parts.join(" · "))}</span></span>`;
}

/* ---------- список разговоров кластера (клик по названию кластера на карте) ---------- */
export function showClusterList(k) {
  const arg = { kind: "cluster", arg: k, restore: () => render(true) };
  const render = async fromStack => {
    const gr = graph();
    const cl = gr && gr.clusters.find(c => c.k === +k);
    const nodes = gr ? gr.nodes.filter(n => n.k === +k && n.v !== "drop")
      .sort((a, b) => (b.t || "").localeCompare(a.t || "")) : [];
    const name = cl ? cl.name : "Тема";
    await setDrawerContent(arg, name, null, { skipStack: !!fromStack });
    syncUrl(arg);
    if (!cl) { $("#dbody").innerHTML = `<div class="empty"><div class="e-t">Тема не найдена</div></div>`; return; }
    $("#dbody").innerHTML = `
      ${cl.descr ? `<div class="dsub" style="margin-bottom:12px">${esc(cl.descr)}</div>` : ""}
      <div class="dsub" style="margin-bottom:12px">${nodes.length} разговоров</div>
      <div class="cl-list">${nodes.map(convCardHtml).join("")}</div>`;
    $("#dbody").scrollTop = 0;
  };
  return render(false);
}
function convCardHtml(n) {
  const cat = CAT[n.c] || CAT.other;
  return `<div class="card" data-act="conv" data-arg="${escAttr(n.id)}" style="margin-bottom:12px">
    <div class="ccard-t">${esc(convTitle(n.s))}</div>
    <div class="ccard-m">
      <span>${esc(fmtDateShort(n.t))}</span>
      <span class="dot" style="background:${cat[1]}" title="${esc(cat[0])}"></span><span>${esc(cat[0])}</span>
      <span>${n.n} сообщ.</span>
      ${n.v === "keep" ? `<span class="star">⭐ ценное</span>` : ""}
    </div></div>`;
}

/* ---------- регистрация действий ---------- */
export function convInit() {
  /* пометки проверки фактов (⚠ в тексте статьи): подсказка — общий tooltip, как у сносок чата */
  $("#dbody").addEventListener("mouseover", e => {
    const fc = e.target.closest(".fc-mark");
    if (fc) tipShow(`<b>⚠ проверка фактов</b><div class="m">${esc(fc.dataset.fc || "")}</div>`, e.clientX, e.clientY);
  });
  $("#dbody").addEventListener("mouseover", e => {
    const r = e.target.closest(".mref");
    if (r) tipShow(mrefTip(+r.dataset.arg), e.clientX, e.clientY);
  });
  $("#dbody").addEventListener("mouseout", e => { if (e.target.closest(".fc-mark, .mref")) tipHide(); });

  act("conv", (id, el, e, inDrawer) => {
    openConv(id, { q: el.dataset.q || null, focus: el.dataset.focus || null, push: !inDrawer });
  });
  act("article", path => openArticle(path));
  act("article-name", name => openArticleByName(name));
  act("conv-mode", m => {
    convMode = m; localStorage.setItem("mxw_convMode", m);
    if (curConv) renderMsgs();
  });
  act("expand-group", key => {
    convExpanded.add(+key); renderMsgs();
    const el = document.getElementById("m" + key);
    if (el) el.scrollIntoView({ block: "nearest" });
  });
  act("jump-msg", id => jumpToMsg(+id));
  act("ask-conv", id => nav("chat", { pin: id }, true));   // чат с контекстом разговора
  act("ask-article", path => nav("chat", { pin: path }, true));   // чат с контекстом статьи
  act("toggle-full", (a, el) => el.classList.toggle("full"));
  act("photo", i => { if (curConv) openViewer(curConv.photos, +i); });
  act("dmore", (a, el) => {
    const cur = drawerCur();
    const text = cur ? (cur.kind === "conv" ? cur.arg : cur.kind === "article" ? cur.arg : "") : "";
    if (text) navigator.clipboard?.writeText(text).then(() => {
      el.innerHTML = icon("check");
      setTimeout(() => { el.innerHTML = icon("more"); }, 1200);
    });
  });
}
