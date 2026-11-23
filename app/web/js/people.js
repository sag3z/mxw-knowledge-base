/* people.js — раздел «Люди» (#/people): кто в чём разбирается.
   Данные — /api/experts (experts.py): люди (n_key = полезные сообщения, профиль, навыки)
   и их темы — только категории и типы схем (кластеры не используются). Сетка карточек
   с аватарами, поиск по навыкам/профилям/темам с подсветкой, чипы тем (фильтр + сортировка
   по силе в теме), карточка человека в drawer (темы полосками, «где он писал по делу»,
   «спросить ИИ»). Стример-мод: псевдонимы и инициалы от псевдонима. */
import { icon } from "./icons.js";
import {
  $, esc, escAttr, escRe, CAT, fmtDate, fmtDateShort, convTitle, parseHash, nav, navParam,
  act, isAnon, anonName, anonText, authorFromAnon, ensureGraph, drawerIsOpen, drawerCur, drawerSetContent,
  drawerOpenUI, drawerSetTitle, drawerPushStack, onAnon,
} from "./ui.js";
import * as api from "./api.js";

/* ---------- состояние ---------- */
let mounted = false;          // DOM раздела построен
let rendered = false;         // первый рендер сетки сделан
let ex = null;                // {people, topics} из /api/experts
let exP = null;               // промис загрузки (один на сессию)
let topicMax = new Map();     // key темы → score лидера (ширина полосок карточек)
let state = { q: "", topic: null, showAll: false };

const SCHEME_COLOR = "#f5a524";   // полоски/точки типов схем (как блок «Схема» в разговоре)
const MONS_G = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
  "августа", "сентября", "октября", "ноября", "декабря"];   // родительный: «с июня»
const MONS_N = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль",
  "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];    // именительный: «по ноябрь»
const MONS_P = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле",
  "августе", "сентябре", "октябре", "ноябре", "декабре"];   // предложный: «в июне»

function ensureExperts() {
  if (!exP) exP = api.getExperts().then(d => { ex = d; return d; });
  return exP;
}

/* ---------- мелочи: имена, аватары, числа ---------- */
const dispName = a => isAnon() ? anonName(a) : a;
const fmtN = n => (n || 0).toLocaleString("ru-RU");
const plural = (n, one, few, many) => {
  const a = Math.abs(n) % 100, b = a % 10;
  return a > 10 && a < 20 ? many : b > 1 && b < 5 ? few : b === 1 ? one : many;
};
const topicsOf = p => p.topics || p.top || [];

const fnvH = s => { let h = 0x811c9dc5; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; } return h >>> 0; };
/* инициалы по код-поинтам: эмодзи/иероглиф в нике не рвётся на пол-символа
   (половинка суррогатной пары — невалидный текст, ломает DOM) */
function initials(name) {
  const w = (name || "").trim().split(/\s+/).filter(Boolean);
  const cps = s => [...(s || "?")];
  const s = w.length >= 2 ? cps(w[0])[0] + cps(w[1])[0] : cps(w[0])[0] + (cps(w[0])[1] || "");
  return s.toUpperCase();
}
/* круглый аватар с инициалами: цвет фона — из хеша имени (в стример-моде — псевдонима), приглушённый */
function avatarHtml(author, cls = "") {
  const n = dispName(author);
  const h = fnvH(n);
  return `<span class="avatar ${cls}" style="background:hsl(${h % 360} 26% 31%);color:hsl(${h % 360} 52% 80%)">${esc(initials(n))}</span>`;
}

/* «с июня по ноябрь» (если годы разные — с годами) */
function activeRange(p) {
  const f = (p.first_ts || "").slice(0, 10), l = (p.last_ts || "").slice(0, 10);
  if (!f || !l) return "";
  const fd = new Date(f + "T12:00:00"), ld = new Date(l + "T12:00:00");
  if (f === l) return `в ${MONS_P[fd.getMonth()]}`;
  const y = fd.getFullYear() !== ld.getFullYear();
  return `с ${MONS_G[fd.getMonth()]}${y ? " " + fd.getFullYear() : ""} по ${MONS_N[ld.getMonth()]}${y ? " " + ld.getFullYear() : ""}`;
}

/* ---------- темы ---------- */
const topicKey = t => t.kind + ":" + t.topic;
function topicName(t) { return t.kind === "category" ? (CAT[t.topic] || [t.topic])[0] : t.topic; }
function topicColor(t) { return t.kind === "category" ? (CAT[t.topic] || CAT.other)[1] : SCHEME_COLOR; }
function parseTopicKey(k) {
  const i = k.indexOf(":");
  if (i < 1) return null;
  const kind = k.slice(0, i), topic = k.slice(i + 1);
  if (kind !== "category" && kind !== "scheme") return null;
  if (ex && ex.topics && !ex.topics.some(t => t.kind === kind && t.topic === topic)) return null;
  return { kind, topic };
}
function rebuildMax() {
  topicMax = new Map();
  for (const p of ex.people) for (const t of topicsOf(p)) {
    const k = topicKey(t);
    if (!topicMax.has(k) || topicMax.get(k) < t.score) topicMax.set(k, t.score);
  }
}

/* ---------- поиск: по навыкам, профилю и названиям тем (по частям слов) ---------- */
const qTokens = q => ((q || "").toLowerCase().match(/[\p{L}\p{N}_]+/gu) || []).filter(w => w.length >= 2);
function personHay(p) {
  return ((p.skills || []).join(" ") + " " + (p.profile || "") + " "
    + topicsOf(p).map(topicName).join(" ")).toLowerCase();
}
function hl(text, toks) {
  if (!text) return "";
  if (!toks || !toks.length) return esc(text);
  const re = new RegExp("(" + toks.slice().sort((a, b) => b.length - a.length).map(escRe).join("|") + ")", "gi");
  return esc(text).replace(re, "<mark>$1</mark>");
}

/* ---------- карточка в сетке ---------- */
function barHtml(t, max, toks) {
  const w = Math.max(4, Math.round(100 * (t.score || 0) / (max || 1)));
  return `<div class="pp-bar">
    <span class="pp-bar-t">${hl(topicName(t), toks)}</span>
    <span class="pp-bar-l"><i style="width:${w}%;background:${topicColor(t)}"></i></span>
    <span class="pp-bar-n" title="полезных сообщений по теме">${fmtN(t.n_key)}</span>
  </div>`;
}

function personCard(p, toks, key) {
  const t = key ? topicsOf(p).find(x => topicKey(x) === key) : null;
  const n = t ? t.n_key : p.n_key;
  const range = activeRange(p);
  const stat = `⭐ ${fmtN(n)} ${plural(n, "полезное сообщение", "полезных сообщения", "полезных сообщений")}${t ? " по теме" : ""}`
    + (range ? ` · ${range}` : "");
  const bars = topicsOf(p).slice(0, 3).map(tp => barHtml(tp, topicMax.get(topicKey(tp)) || 1, toks)).join("");
  return `<div class="card pcard" data-act="person" data-arg="${escAttr(p.author)}" title="Карточка участника">
    <div class="pcard-h">
      ${avatarHtml(p.author)}
      <div class="pcard-main">
        <div class="pcard-n">${esc(dispName(p.author))}</div>
        <div class="pcard-stat">${esc(stat)}</div>
      </div>
    </div>
    ${p.profile ? `<div class="pcard-prof">${hl(p.profile, toks)}</div>` : ""}
    ${(p.skills || []).length ? `<div class="pcard-skills">${p.skills.slice(0, 3).map(s => `<span class="chip">${hl(s, toks)}</span>`).join("")}</div>` : ""}
    ${bars ? `<div class="pcard-bars"><div class="pp-bars-h">сильнее всего в</div>${bars}</div>` : ""}
  </div>`;
}

function restRow(p, toks, key) {
  const t = key ? topicsOf(p).find(x => topicKey(x) === key) : null;
  const n = t ? t.n_key : p.n_key;
  const tops = topicsOf(p).slice(0, 3).map(topicName).join(", ");
  return `<div class="pp-row" data-act="person" data-arg="${escAttr(p.author)}" title="Карточка участника: чем он помогал в чате">
    ${avatarHtml(p.author, "sm")}
    <span class="pp-row-n">${esc(dispName(p.author))}</span>
    <span class="pp-row-m">⭐ ${fmtN(n)} ${plural(n, "полезное сообщение", "полезных сообщения", "полезных сообщений")}${t ? " по теме" : ""}</span>
    <span class="pp-row-t">${esc(tops)}</span>
  </div>`;
}

/* ---------- рендер раздела ---------- */
function renderChips() {
  const sel = state.topic ? topicKey(state.topic) : "";
  $("#ppChips").innerHTML = (ex.topics || []).map(tp => {
    const k = topicKey(tp);
    return `<button class="chip pp-chip${k === sel ? " on" : ""}" data-topic="${escAttr(k)}"
      title="${escAttr(topicName(tp))} · ${tp.n_people} ${plural(tp.n_people, "человек", "человека", "человек")} — нажми, чтобы показать, кто силён в теме">
      <span class="dot" style="background:${topicColor(tp)}"></span>${esc(topicName(tp))}</button>`;
  }).join("");
}

function infoText(gridN, restN) {
  const parts = [];
  if (state.q.trim()) parts.push(`запрос «${state.q.trim()}»`);
  if (state.topic) parts.push(`тема «${topicName(state.topic)}»`);
  const total = gridN + restN;
  if (parts.length) return `Нашлось: ${fmtN(total)} ${plural(total, "участник", "участника", "участников")} (${parts.join(", ")})`;
  return `С карточкой: ${fmtN(gridN)} · без карточки: ${fmtN(restN)}`;
}

function applyFilters() {
  if (!ex) return;
  renderChips();
  const toks = qTokens(state.q);
  const key = state.topic ? topicKey(state.topic) : null;
  let list = ex.people.slice();   // с сервера — по числу полезных сообщений (n_key)
  if (key) list = list.filter(p => topicsOf(p).some(t => topicKey(t) === key));
  if (toks.length) list = list.filter(p => { const hay = personHay(p); return toks.every(t => hay.includes(t)); });
  if (key) list.sort((a, b) => {   // выбранный чип — по силе в теме (score)
    const ta = topicsOf(a).find(t => topicKey(t) === key), tb = topicsOf(b).find(t => topicKey(t) === key);
    return (tb ? tb.score : 0) - (ta ? ta.score : 0);
  });
  const grid = list.filter(p => p.profile);
  const rest = list.filter(p => !p.profile);
  $("#ppInfo").textContent = infoText(grid.length, rest.length);
  $("#ppGrid").innerHTML = grid.length ? grid.map(p => personCard(p, toks, key)).join("")
    : rest.length ? ""   // с карточкой никого, но есть люди без карточки — ниже
      : `<div class="empty"><div class="e-ic">${icon("users", 26)}</div><div class="e-t">Никого не нашлось</div><div class="e-h">Попробуй другое слово или сними фильтр темы</div></div>`;
  renderRest(rest, toks, key);
}

function renderRest(rest, toks, key) {
  const box = $("#ppRest");
  if (state.showAll) {
    box.innerHTML = rest.length ? `
      <div class="pp-rest-h">
        <span>Без карточки · ${fmtN(rest.length)}</span>
        <button class="btn ghost sm" id="ppCollapse" title="Свернуть список обратно">Свернуть ▴</button>
      </div>
      <div class="pp-rest">${rest.map(p => restRow(p, toks, key)).join("")}</div>` : "";
  } else {
    box.innerHTML = rest.length
      ? `<button class="btn pp-all" id="ppAll" title="Показать участников без карточки-профиля">Показать всех (${fmtN(rest.length)})</button>` : "";
  }
}

/* ---------- монтирование экрана ---------- */
function cleanHashParams(...keys) {
  const { route, params } = parseHash();
  keys.forEach(k => params.delete(k));
  const qs = params.toString();
  history.replaceState(null, "", "#/" + route + (qs ? "?" + qs : ""));
}

function buildDOM() {
  const root = document.querySelector('.view[data-view="people"]');
  root.innerHTML = `
    <div class="view-head">
      <div class="view-title">
        <h1>Кто в чём разбирается</h1>
        <p class="sub">По содержательным сообщениям в чате: чем больше полезных сообщений человек написал по теме, тем выше он в списке</p>
      </div>
    </div>
    <div class="pp-top">
      <div class="pp-search field">
        <span class="f-ic">${icon("search", 17)}</span>
        <input type="text" id="ppq" placeholder="Кто разбирается в… (например: минт-боты, антидетект, перпы)"
          title="Поиск по навыкам, профилю и темам — совпадения подсвечиваются">
        <button id="ppqX" class="btn ghost icon f-x" title="Очистить поиск" hidden>${icon("x", 16)}</button>
      </div>
      <div class="pp-chips" id="ppChips"><span class="loading">Загружаю</span></div>
      <div class="dsub" id="ppInfo"></div>
    </div>
    <div class="view-scroll">
      <div class="pp-grid" id="ppGrid">${'<div class="card pcard skel"></div>'.repeat(8)}</div>
      <div id="ppRest"></div>
    </div>`;
}

function wire() {
  const inp = $("#ppq");
  let t = 0;
  inp.addEventListener("input", () => {
    state.q = inp.value;
    $("#ppqX").hidden = !inp.value;
    clearTimeout(t);
    t = setTimeout(applyFilters, 120);   // лёгкий дебаунс
  });
  inp.addEventListener("keydown", e => { if (e.key === "Escape") { e.stopPropagation(); inp.blur(); } });
  $("#ppqX").addEventListener("click", () => {
    inp.value = ""; state.q = ""; $("#ppqX").hidden = true;
    applyFilters(); inp.focus();
  });
  $("#ppChips").addEventListener("click", e => {
    const b = e.target.closest(".pp-chip");
    if (!b) return;
    const k = b.dataset.topic;
    state.topic = state.topic && topicKey(state.topic) === k ? null : parseTopicKey(k);
    applyFilters();
  });
  document.querySelector('.view[data-view="people"]').addEventListener("click", e => {
    if (e.target.closest("#ppAll")) { state.showAll = true; applyFilters(); }
    else if (e.target.closest("#ppCollapse")) { state.showAll = false; applyFilters(); }
  });
}

export function mountPeople(params) {
  if (!mounted) {
    mounted = true;
    buildDOM();
    wire();
    onAnon(() => {   // стример-мод: псевдонимы, инициалы и цвета от псевдонимов
      if (!mounted) return;
      if (ex) applyFilters();   // сетка карточек
      const cur = drawerCur();
      if (cur && cur.kind === "person" && drawerIsOpen())
        renderPerson(cur.arg, { fromStack: true });   // открытая карточка — перерисовка целиком, как при открытии (и заголовки разговоров с никами из выжимок)
    });
  }
  const q = params.get("q");
  const tp = params.get("topic");
  return ensureExperts().then(() => {
    rebuildMax();
    const deep = q != null || tp != null;   // пришли по глубокой ссылке
    if (q != null) {
      state.q = q;
      $("#ppq").value = q;
      $("#ppqX").hidden = !q;
    }
    if (tp) {
      const t = parseTopicKey(tp);
      if (t) state.topic = t;
    }
    if (deep) cleanHashParams("q", "topic");   // person остаётся (открывает карточку)
    if (deep || !rendered) { rendered = true; applyFilters(); }
  }, err => {
    $("#ppChips").innerHTML = "";
    $("#ppInfo").textContent = "";
    $("#ppGrid").innerHTML = `<div class="empty"><div class="e-ic">${icon("users", 26)}</div><div class="e-t">Не удалось загрузить людей</div><div class="e-h">${esc(err.message)}</div></div>`;
  });
}

/* ---------- карточка человека (drawer) ---------- */
/* разговоры «где он писал по делу»: примеры его тем → узлы карты (заголовок, дата, ветка) */
async function personConvs(p) {
  let g;
  try { g = await ensureGraph(); } catch { return []; }
  const byId = new Map((g.nodes || []).map(n => [n.id, n]));
  const titles = new Map((g.topics || []).map(t => [t.short, t.title]));
  const agg = new Map();
  for (const t of topicsOf(p)) for (const e of t.examples || []) {
    if (byId.has(e.id)) agg.set(e.id, (agg.get(e.id) || 0) + (e.n || 0));
  }
  return [...agg.entries()]
    .map(([id, n]) => {
      const node = byId.get(id);
      return { id, n, node, branch: titles.get(node.b) || node.b };
    })
    .sort((a, b) => b.n - a.n || (b.node.t || "").localeCompare(a.node.t || ""))
    .slice(0, 8);
}

/* строка разговора в «Где он писал по делу»: заголовок — первое предложение выжимки узла,
   выжимки упоминают ники → anonText (как заголовки источников в чате); без стример-мода — как есть */
function personConvRow(c) {
  const cat = CAT[c.node.c] || CAT.other;
  return `<div class="card pp-dconv" data-act="conv" data-arg="${escAttr(c.id)}"
    title="${fmtN(c.n)} ${plural(c.n, "полезное сообщение", "полезных сообщения", "полезных сообщений")} автора в этом разговоре">
    <div class="ccard-t">${esc(anonText(convTitle(c.node.s)))}</div>
    <div class="ccard-m">
      <span>${esc(fmtDateShort(c.node.t))}</span>
      <span>${esc(c.branch)}</span>
      <span class="dot" style="background:${cat[1]}" title="${esc(cat[0])}"></span><span>${esc(cat[0])}</span>
      <span>⭐ ${fmtN(c.n)}</span>
    </div></div>`;
}

export function openPerson(name, opts = {}) {
  if (!name) return;
  if (opts.push) {   // hash → onHash → openPerson; в стример-моде в URL только псевдоним, не ник
    navParam("person", isAnon() ? anonName(name) : name, {}, true); return;
  }
  return renderPerson(name, opts);
}

async function renderPerson(name0, { fromStack } = {}) {
  const name = authorFromAnon(name0);   // «Участник xxx» из deep-link → настоящий ник (для данных)
  const cur = !fromStack && drawerCur();
  if (cur && cur.kind === "person" && cur.arg === name) return;   // уже открыт
  const arg = { kind: "person", arg: name, restore: () => renderPerson(name, { fromStack: true }) };
  try { await ensureExperts(); }
  catch (err) {
    drawerSetContent(arg);
    drawerOpenUI(dispName(name), null);
    $("#dbody").innerHTML = `<div class="empty"><div class="e-ic">${icon("users", 26)}</div><div class="e-t">Не удалось загрузить</div><div class="e-h">${esc(err.message)}</div></div>`;
    return;
  }
  const p = ex ? ex.people.find(x => x.author === name) : null;
  const disp = dispName(name);
  if (!fromStack && drawerIsOpen()) {   // панель уже открыта — «←» вернёт прежнее
    const prev = drawerCur();
    if (prev) drawerPushStack(() => prev.restore());
  }
  drawerSetContent(arg);
  drawerOpenUI(disp, null);
  nav(parseHash().route, { person: isAnon() ? anonName(name) : name }, false);   // deep-link: в стример-моде — псевдоним

  if (!p) {   // бот или автор без полезных сообщений
    drawerSetTitle(disp);
    $("#dbody").innerHTML = `<div class="empty"><div class="e-ic">${icon("user", 26)}</div><div class="e-t">Нет карточки</div>
      <div class="e-h">${esc(disp)} — бот или участник без полезных сообщений</div></div>`;
    return;
  }

  drawerSetTitle(disp);
  const tops = topicsOf(p);
  const tMax = Math.max(1, ...tops.map(t => t.score || 0));
  let h = `
    <div class="pp-dhead">
      ${avatarHtml(name, "big")}
      <div class="pp-dmain">
        <div class="pp-dname">${esc(disp)}</div>
        <div class="dsub">активен: ${esc(fmtDate(p.first_ts))} — ${esc(fmtDate(p.last_ts))}</div>
      </div>
    </div>
    <div class="dsub pp-dstats">⭐ ${fmtN(p.n_key)} ${plural(p.n_key, "полезное сообщение", "полезных сообщения", "полезных сообщений")}
      · ${fmtN(p.n_convs)} ${plural(p.n_convs, "разговор", "разговора", "разговоров")}
      · ${fmtN(p.n_msgs)} ${plural(p.n_msgs, "сообщение", "сообщения", "сообщений")} всего</div>
    ${p.profile ? `<div class="sum">${esc(p.profile)}</div>` : ""}
    ${(p.skills || []).length ? `<div class="pp-dskills">${p.skills.map(s => `<span class="chip">${esc(s)}</span>`).join("")}</div>` : ""}
    ${tops.length ? `<div class="pp-dsec">Темы</div>
      <div>${tops.map(t => barHtml(t, tMax, [])).join("")}</div>` : ""}
    <div class="pp-dsec">Где он писал по делу</div>
    <div id="ppDConvs"><div class="loading">Ищу разговоры</div></div>
    <div class="askrow"><button class="btn" data-act="person-ask" data-arg="${escAttr(name)}">${icon("chat", 16)}Спросить ИИ про его опыт</button></div>`;
  $("#dbody").innerHTML = h;
  $("#dbody").scrollTop = 0;

  personConvs(p).then(list => {
    if (drawerCur() !== arg) return;   // пока грузилось, открыли другое
    const box = $("#ppDConvs");
    if (!box) return;
    box.innerHTML = list.length ? list.map(personConvRow).join("")
      : `<div class="dsub">Не нашлось разговоров — редко писал по делу</div>`;
  });
}

/* ---------- действия (делегирование через ui.js) ---------- */
act("person", (name, el, e, inDrawer) => openPerson(name, { push: !inDrawer }));

act("person-ask", name => {   // чат с готовым вопросом про опыт (псевдоним — в стример-моде)
  const p = ex && ex.people.find(x => x.author === name);
  const top = p && topicsOf(p)[0];
  const who = dispName(name);
  const q = top ? `Что ${who} советовал и рассказывал про ${topicName(top)}?`
    : `Что ${who} советовал и рассказывал по делу в этом чате?`;
  nav("chat", { q }, true);
});
