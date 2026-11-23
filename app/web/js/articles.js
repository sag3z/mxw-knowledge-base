/* articles.js — раздел «Статьи» (#/articles): библиотека и страница чтения.
   Без параметра — библиотека: слева разделы (папки vault), справа статьи раздела; поиск по
   названию идёт по всем разделам. «Схемы» — серии (хаб + подстатьи с линией слева).
   #/articles?article=<путь> — статья отдельной страницей (колонка 720px, оглавление справа),
   ссылки на разговоры [[life-c0417]] → надстрочные номера-сноски с подсказкой и списком
   «Источники» внизу; клик по сноске открывает разговор в правой панели поверх статьи. */
import { icon } from "./icons.js";
import * as api from "./api.js";
import { $, esc, escAttr, MONS, CAT, SCHEME_RU, convTitle, fmtDate, fmtDateShort, ensureGraph, graph,
  tipShow, tipHide, nav, anonText } from "./ui.js";
import { parseFM, fcMarks, fcPlateHtml } from "./conv.js";

const marked = window.marked;
let mounted = false;
let list = null;              // /api/articles (кэш на сессию)
let aP = null;
let state = { q: "", sec: localStorage.getItem("mxw_artSec") || "Схемы" };
let curPath = null;           // открытая статья

const plural = (n, one, few, many) => {
  const a = Math.abs(n) % 100, b = a % 10;
  return a > 10 && a < 20 ? many : b > 1 && b < 5 ? few : b === 1 ? one : many;
};

/* период frontmatter («2026-06-16 — 2026-11-12») → «июн — ноя 2026» */
function prettyPeriod(s) {
  const m = (s || "").match(/(\d{4})-(\d{2})-(\d{2})(?:\D+(\d{4})-(\d{2})-(\d{2}))?/);
  if (!m) return "";
  const [, y1, mo1, , y2, mo2] = m;
  if (!y2) return `${MONS[+mo1 - 1]} ${y1}`;
  return +mo1 === +mo2 && y1 === y2
    ? `${MONS[+mo1 - 1]} ${y1}`
    : `${MONS[+mo1 - 1]} — ${MONS[+mo2 - 1]} ${y2}`;
}

/* разделы: порядок и подписи */
const SEC_ORDER = ["Схемы", "NFT", "AI, вайбкодинг и разработка", "Инструменты, софты и сервисы",
  "Безопасность и скамы", "Трейдинг, перпы, арбитраж, мемкоины", "Дропы, тестнеты и фарм активностей",
  "Предикшены и ставки", "Новости рынка", "Работа и заработок", "Комьюнити MXW", "Разное полезное",
  "Медиа-конспекты", "Проекты и инструменты", ""];
const secTitle = s => s === "Схемы" ? "Схемы абуза" : s === "Проекты и инструменты" ? "Проекты (справочник)" : s || "Обзор";
const secColor = s => ({
  "Схемы": "#f5a524", "NFT": "var(--c-nft)", "AI, вайбкодинг и разработка": "var(--c-ai)",
  "Инструменты, софты и сервисы": "var(--c-tools)", "Безопасность и скамы": "var(--c-security)",
  "Трейдинг, перпы, арбитраж, мемкоины": "var(--c-trading)", "Дропы, тестнеты и фарм активностей": "var(--c-drops)",
  "Предикшены и ставки": "var(--c-predict)", "Новости рынка": "var(--c-news)", "Работа и заработок": "var(--c-jobs)",
  "Комьюнити MXW": "var(--c-community)",
}[s] || "var(--c-other)");

function ensureArticles() {
  if (!aP) aP = api.getArticles().then(d => { list = d; return d; });
  return aP;
}

/* вход из роутера: params.article — страница статьи, иначе библиотека */
export function mountArticles(params) {
  if (!mounted) {
    mounted = true;
    buildDOM();
    wire();
  }
  const path = params && params.get("article");
  const root = document.querySelector('.view[data-view="articles"]');
  if (path) {
    root.classList.add("reading");
    if (path !== curPath) renderPage(path);
    return Promise.resolve();
  }
  root.classList.remove("reading");
  curPath = null;
  if (list) { renderLib(); return Promise.resolve(); }
  $("#artList").innerHTML = '<div class="art-sk skel"></div>'.repeat(6);
  return ensureArticles().then(renderLib, err => {
    $("#artList").innerHTML = `<div class="empty"><div class="e-ic">${icon("book", 26)}</div>
      <div class="e-t">Не удалось загрузить статьи</div><div class="e-h">${esc(err.message)}</div></div>`;
  });
}

function buildDOM() {
  const root = document.querySelector('.view[data-view="articles"]');
  root.innerHTML = `
    <div class="art-lib">
      <div class="view-head">
        <div class="view-title">
          <h1>Статьи</h1>
          <p class="sub">Конспекты чата по проектам, схемам абуза, трейдингу и новостям рынка — с номерами-ссылками на исходные разговоры</p>
        </div>
      </div>
      <div class="art-top">
        <div class="field art-search">
          <span class="f-ic">${icon("search", 17)}</span>
          <input type="text" id="artq" placeholder="Найти статью по названию…" title="Поиск по названию статьи во всех разделах">
          <button id="artqX" class="btn ghost icon f-x" title="Очистить поиск" hidden>${icon("x", 16)}</button>
        </div>
        <div class="dsub" id="artInfo"></div>
      </div>
      <div class="art-body">
        <nav class="art-secs" id="artSecs"></nav>
        <div class="art-list" id="artList"></div>
      </div>
    </div>
    <div class="art-page" id="artPage"></div>`;
}

function wire() {
  const inp = $("#artq");
  let t = 0;
  inp.addEventListener("input", () => {
    state.q = inp.value;
    $("#artqX").hidden = !inp.value;
    clearTimeout(t);
    t = setTimeout(renderLib, 100);
  });
  inp.addEventListener("keydown", e => { if (e.key === "Escape") { e.stopPropagation(); inp.value = ""; state.q = ""; $("#artqX").hidden = true; renderLib(); } });
  $("#artqX").addEventListener("click", () => {
    inp.value = ""; state.q = ""; $("#artqX").hidden = true;
    renderLib(); inp.focus();
  });
  $("#artSecs").addEventListener("click", e => {
    const b = e.target.closest("[data-sec]");
    if (!b) return;
    state.sec = b.dataset.sec;
    localStorage.setItem("mxw_artSec", state.sec);
    if (state.q) { inp.value = ""; state.q = ""; $("#artqX").hidden = true; }
    renderLib();
    $("#artList").scrollTop = 0;
  });

  /* подсказки страницы статьи: сноска-источник и пометка проверки фактов */
  const page = $("#artPage");
  page.addEventListener("mouseover", e => {
    const s = e.target.closest(".src");
    if (s) { tipShow(srcTipHtml(s.dataset.arg), e.clientX, e.clientY); return; }
    const fc = e.target.closest(".fc-mark");
    if (fc) tipShow(`<b>Не подтверждено источником</b><div class="m">${esc(fc.dataset.fc || "")}</div>`, e.clientX, e.clientY);
  });
  page.addEventListener("mouseout", e => { if (e.target.closest(".src, .fc-mark")) tipHide(); });
  page.addEventListener("click", e => {
    const t = e.target.closest("[data-toc]");
    if (t) {
      e.preventDefault();
      const h = document.getElementById(t.dataset.toc);
      if (h) $("#apScroll").scrollTo({ top: h.offsetTop - 24, behavior: "smooth" });
      page.classList.remove("toc-open");
      return;
    }
    if (e.target.closest("#apTocBtn")) { page.classList.toggle("toc-open"); return; }
    if (e.target.closest("#apSrcMore")) { page.querySelector(".ap-srcs").classList.add("all"); e.target.closest("#apSrcMore").remove(); }
  });
}

/* ================= библиотека ================= */
function sections() {
  const by = new Map();
  for (const a of list) {
    if (!by.has(a.section)) by.set(a.section, []);
    by.get(a.section).push(a);
  }
  const order = s => { const i = SEC_ORDER.indexOf(s); return i < 0 ? 50 : i; };
  return [...by.entries()].sort((a, b) => order(a[0]) - order(b[0]));
}

function renderLib() {
  if (!list) return;
  const secs = sections();
  if (!secs.some(([s]) => s === state.sec)) state.sec = secs[0][0];
  const q = state.q.trim().toLowerCase();
  const match = a => (a.title || "").toLowerCase().includes(q);
  const hits = q ? new Map(secs.map(([s, arr]) => [s, arr.filter(match)])) : null;

  $("#artSecs").innerHTML = secs.map(([s, arr]) => {
    const n = hits ? hits.get(s).length : arr.length;
    const on = !q && s === state.sec;
    return `<button class="as-item${on ? " on" : ""}${hits && !n ? " dim" : ""}" data-sec="${escAttr(s)}">
      <i style="background:${secColor(s)}"></i><span>${esc(secTitle(s))}</span><b>${n}</b></button>`;
  }).join("");

  if (q) {
    const total = [...hits.values()].reduce((a, x) => a + x.length, 0);
    $("#artInfo").textContent = total ? `Нашлось: ${total} ${plural(total, "статья", "статьи", "статей")}` : "";
    $("#artList").innerHTML = total
      ? secs.filter(([s]) => hits.get(s).length).map(([s]) =>
          `<div class="al-sec"><div class="al-h"><i style="background:${secColor(s)}"></i>${esc(secTitle(s))}<span>${hits.get(s).length}</span></div>
           ${hits.get(s).sort(byTitle).map(a => artRow(a, "", q)).join("")}</div>`).join("")
      : `<div class="empty"><div class="e-ic">${icon("search", 26)}</div><div class="e-t">Ничего не нашлось</div>
          <div class="e-h">Поиск идёт по названию статьи. Чтобы найти по содержанию — спроси в чате</div></div>`;
    return;
  }
  $("#artInfo").textContent = `${list.length} ${plural(list.length, "статья", "статьи", "статей")} в ${secs.length} разделах`;
  const arr = secs.find(([s]) => s === state.sec)[1];
  $("#artList").innerHTML = `<div class="al-sec">
      <div class="al-title"><i style="background:${secColor(state.sec)}"></i>${esc(secTitle(state.sec))}<span>${arr.length} ${plural(arr.length, "статья", "статьи", "статей")}</span></div>
      ${state.sec === "Схемы" ? schemesHtml(arr) : arr.slice().sort(byTitle).map(a => artRow(a)).join("")}
    </div>`;
}

const byTitle = (a, b) => a.title.localeCompare(b.title, "ru");

/* «Схемы абуза»: одиночные обзорные статьи, затем серии (хаб + подстатьи) */
function schemesHtml(arr) {
  const series = new Map();
  for (const a of arr) if (a.series) {
    if (!series.has(a.series)) series.set(a.series, []);
    series.get(a.series).push(a);
  }
  const tops = arr.filter(a => !a.series).sort(byTitle);
  let h = `<div class="al-group">${tops.filter(a => !series.has(a.title)).map(a => artRow(a)).join("")}</div>`;
  for (const [name, subs] of [...series.entries()].sort((a, b) => b[1].length - a[1].length)) {
    const hub = tops.find(a => a.title === name);
    h += `<div class="al-series">
      <div class="al-sh">${hub ? artRow(hub, "hub") : `<div class="art-row hub"><span class="art-t">${esc(name)}</span></div>`}
        <span class="al-sn">серия · ${subs.length} ${plural(subs.length, "статья", "статьи", "статей")}</span></div>
      <div class="al-subs">${subs.sort(byTitle).map(a => artRow(a, "sub")).join("")}</div>
    </div>`;
  }
  return h;
}

function artRow(a, cls = "", q = "") {
  const meta = [
    a.period ? esc(prettyPeriod(a.period)) : "",
    a.sources ? `${a.sources} ${plural(a.sources, "источник", "источника", "источников")}` : "",
  ].filter(Boolean).join(" · ");
  const fc = a.checked
    ? `<span class="art-fc${a.confirmed != null && a.confirmed < 80 ? " low" : ""}" title="Статья прошла проверку фактов${a.confirmed != null ? `: подтверждено ${a.confirmed}% утверждений` : ""}">${icon("check", 13)}${a.confirmed != null ? a.confirmed + "%" : ""}</span>`
    : "";
  let t = esc(a.title);
  if (q) t = t.replace(new RegExp("(" + q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "ig"), "<mark>$1</mark>");
  return `<a class="art-row${cls ? " " + cls : ""}" href="#/articles?article=${encodeURIComponent(a.path)}" title="${escAttr(a.title)}">
      <span class="art-t">${t}</span>${fc}
      ${meta ? `<span class="art-m">${meta}</span>` : ""}
    </a>`;
}

/* ================= страница статьи ================= */
const CONV_ID = "(?:nft|ai|life|crypto|perps|predict|soft|shitki|important)-c[\\d-]+";
const SRC_RUN_RE = new RegExp(`(?:[ \\t]*\\[\\[${CONV_ID}(?:\\|[^\\]]*)?\\]\\])+`, "g");
const ONE_SRC_RE = new RegExp(`\\[\\[(${CONV_ID})(?:\\|[^\\]]*)?\\]\\]`, "g");

let srcOrder = [];            // id разговоров в порядке первого появления
let srcMeta = new Map();      // id → {title, date, branch, n} (из графа или /api/conv)

function srcLinks(md) {
  const num = new Map();
  srcOrder = [];
  const out = md.replace(SRC_RUN_RE, run => {
    const ids = [...run.matchAll(ONE_SRC_RE)].map(m => m[1]);
    const parts = [...new Set(ids)].map(id => {
      if (!num.has(id)) { num.set(id, srcOrder.length + 1); srcOrder.push(id); }
      return `<a class="src" data-act="conv" data-arg="${escAttr(id)}">${num.get(id)}</a>`;
    });
    return `<sup class="srcs">${parts.join('<span class="sc">,</span>')}</sup>`;
  });
  /* ссылки на другие статьи: [[Название]] / [[Название|подпись]] → обычная ссылка */
  return out.replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (m, name, label) =>
    `<a class="art-link" data-act="article-name" data-arg="${escAttr(name)}">${esc(label || name.split("/").pop())}</a>`);
}

function nodeMeta(id) {
  if (srcMeta.has(id)) return srcMeta.get(id);
  const g = graph();
  const n = g && g.nodes.find(x => x.id === id);
  if (!n) return null;
  const t = g.topics.find(x => x.short === n.b);
  const m = { title: anonText(convTitle(n.s)), date: n.t, branch: t ? t.title : n.b, n: n.n, cat: CAT[n.c] };
  srcMeta.set(id, m);
  return m;
}

function srcTipHtml(id) {
  const m = nodeMeta(id);
  if (!m) return `<b>Разговор</b><div class="m">Загружаю…</div>`;
  return `<b>${esc(m.title)}</b><div class="m">${[m.date ? fmtDate(m.date.slice(0, 10)) : "", m.branch, m.n ? `${m.n} сообщ.` : ""].filter(Boolean).map(esc).join(" · ")}</div>
    <div class="m tip-hint">Нажми, чтобы открыть разговор</div>`;
}

async function renderPage(path) {
  curPath = path;
  const page = $("#artPage");
  page.classList.remove("toc-open");
  page.innerHTML = `<div class="ap-scroll" id="apScroll"><div class="ap-wrap"><article class="ap-main">
      <div class="ap-crumbs"><a href="#/articles" class="ap-back">${icon("arrow-left", 16)}Все статьи</a></div>
      <div class="skel" style="height:38px;width:70%;margin:18px 0 14px"></div>
      <div class="skel" style="height:16px;width:40%;margin-bottom:32px"></div>
      ${'<div class="skel" style="height:15px;margin:10px 0"></div>'.repeat(8)}
    </article></div></div>`;
  let md;
  try { [md] = await Promise.all([api.getArticle(path), ensureGraph().catch(() => null), ensureArticles().catch(() => null)]); }
  catch (err) {
    page.querySelector(".ap-main").innerHTML = `<div class="ap-crumbs"><a href="#/articles" class="ap-back">${icon("arrow-left", 16)}Все статьи</a></div>
      <div class="empty"><div class="e-ic">${icon("file", 26)}</div><div class="e-t">Не удалось открыть статью</div><div class="e-h">${esc(err.message)}</div></div>`;
    return;
  }
  if (curPath !== path) return;

  const { fm, body } = parseFM(md);
  const parts = path.split("/");
  const sec = parts.length > 1 ? parts[0] : "";
  let text = body.replace(/^\s+/, "");
  let title = parts[parts.length - 1].replace(/\.md$/, "");
  const h1 = text.match(/^# (.+)\n/);
  if (h1) { title = h1[1].trim(); text = text.slice(h1[0].length); }

  const html = marked.parse(srcLinks(fcMarks(text)));
  const kat = SCHEME_RU[fm["категория"]] || (CAT[fm["категория"]] || [])[0] || "";
  const katColor = SCHEME_RU[fm["категория"]] ? "#f5a524" : (CAT[fm["категория"]] || [])[1];
  const nSrc = srcOrder.length || parseInt(fm["источников"], 10) || 0;
  const meta = [
    kat ? `<span class="chip"><span class="dot" style="background:${katColor || "var(--c-other)"}"></span>${esc(kat)}</span>` : "",
    fm["период"] ? `<span class="ap-mi">${icon("calendar", 14)}${esc(prettyPeriod(fm["период"]))}</span>` : "",
    nSrc ? `<span class="ap-mi">${icon("chat", 14)}${nSrc} ${plural(nSrc, "источник", "источника", "источников")}</span>` : "",
    fcPlateHtml(fm),
  ].filter(Boolean).join("");

  const srcItems = srcOrder.map((id, i) => {
    const m = nodeMeta(id);
    return `<li data-act="conv" data-arg="${escAttr(id)}"><span class="sn">${i + 1}</span><span class="st">${esc(m ? m.title : id)}</span>
      <span class="sm">${m ? [m.date ? fmtDateShort(m.date.slice(0, 10)) : "", m.branch].filter(Boolean).map(esc).join(" · ") : ""}</span></li>`;
  }).join("");

  page.innerHTML = `<div class="ap-scroll" id="apScroll"><div class="ap-wrap">
    <article class="ap-main">
      <div class="ap-crumbs">
        <a href="#/articles" class="ap-back">${icon("arrow-left", 16)}Все статьи</a>
        <span class="ap-sep">/</span><span>${esc(secTitle(sec))}</span>
        <button class="btn ghost icon" id="apTocBtn" title="Оглавление">${icon("list")}</button>
      </div>
      <h1 class="ap-h1">${esc(title)}</h1>
      <div class="ap-meta">${meta}</div>
      <div class="ap-md">${html}</div>
      ${srcOrder.length ? `<section class="ap-srcsec">
        <h2 id="ap-sources">Источники</h2>
        <p class="ap-srcsub">Разговоры чата, на которых основана статья. Номер в тексте — ссылка на разговор из этого списка</p>
        <ol class="ap-srcs${srcOrder.length <= 12 ? " all" : ""}">${srcItems}</ol>
        ${srcOrder.length > 12 ? `<button class="btn sm" id="apSrcMore">Показать все ${srcOrder.length}</button>` : ""}
      </section>` : ""}
      <div class="ap-ask"><button class="btn" data-act="ask-article" data-arg="${escAttr(path)}">${icon("chat", 16)}Спросить ИИ про эту статью</button></div>
    </article>
    <aside class="ap-toc" id="apToc"></aside>
  </div></div>`;
  $("#apScroll").scrollTop = 0;
  buildToc();
  /* разговоров нет в графе (например, отброшенные при сортировке) — заголовки догружаем */
  const missing = srcOrder.filter(id => !nodeMeta(id));
  for (const id of missing.slice(0, 40)) {
    api.getConv(id).then(d => {
      const t = (graph() && graph().topics.find(x => x.short === d.branch)) || null;
      srcMeta.set(id, { title: anonText(convTitle(d.summary)), date: d.start || d.date || "", branch: t ? t.title : (d.branch_title || d.branch || ""), n: (d.messages || []).length });
      const li = page.querySelector(`.ap-srcs li[data-arg="${CSS.escape(id)}"] .st`);
      if (li && curPath === path) li.textContent = srcMeta.get(id).title;
    }).catch(() => {});
  }
}

function buildToc() {
  const scroller = $("#apScroll");
  const hs = [...scroller.querySelectorAll(".ap-md h2, .ap-md h3, #ap-sources")];
  hs.forEach((h, i) => { if (!h.id) h.id = "ap-h" + i; });
  const toc = $("#apToc");
  if (hs.length < 2) { toc.innerHTML = ""; $("#apTocBtn").hidden = true; return; }
  toc.innerHTML = `<div class="toc-h">На этой странице</div>` + hs.map(h =>
    `<a href="#" data-toc="${h.id}" class="${h.tagName === "H3" ? "l3" : ""}">${esc(h.textContent)}</a>`).join("");
  const links = new Map([...toc.querySelectorAll("a")].map(a => [a.dataset.toc, a]));
  /* активный пункт — последний заголовок выше трети экрана */
  const spy = () => {
    let cur = hs[0].id;
    const top = scroller.getBoundingClientRect().top;
    for (const h of hs) if (h.getBoundingClientRect().top - top < scroller.clientHeight * 0.33) cur = h.id;
    links.forEach((a, id) => a.classList.toggle("on", id === cur));
  };
  scroller.addEventListener("scroll", spy, { passive: true });
  links.get(hs[0].id).classList.add("on");
}

/* открытие статьи откуда угодно — отдельной страницей (назад браузера вернёт обратно) */
export function goArticle(path) { nav("articles", { article: path }, true); }
