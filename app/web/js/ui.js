/* ui.js — общие компоненты: константы, форматирование, псевдонимы, drawer, viewer, tooltip, роутинг по hash */
import { icon } from "./icons.js";
import * as api from "./api.js";

/* ---------- константы ---------- */
export const CAT = {
  nft: ["NFT", "#7c6cff"], ai_dev: ["AI и разработка", "#2ec5ff"], tools: ["Инструменты", "#3ddc97"],
  security: ["Безопасность", "#ff5d5d"], trading: ["Трейдинг", "#f5a524"], drops_testnets: ["Дропы и тестнеты", "#c56cff"],
  market_news: ["Новости рынка", "#9aa4b5"], predictions: ["Предикшены", "#ff8fd8"], jobs_income: ["Работа", "#8be04e"],
  community: ["Комьюнити", "#ffd23f"], other: ["Разное", "#6e7891"],
};
export const SCHEME_RU = {
  multiaccount: "Мультиаккаунты", antisybil: "Антисибил", farms: "Фермы", accounts: "Аккаунты",
  bots_soft: "Боты и софт", kyc_verification: "KYC", proxy_antidetect: "Прокси и антидетект", bans_risks: "Баны и риски",
};
export const EVTYPE = {
  mint: ["минт", "#7c6cff"], claim: ["клейм", "#2ec5ff"], whitelist: ["вайтлист", "#3ddc97"],
  testnet: ["тестнет", "#c56cff"], snapshot: ["снапшот", "#9aa4b5"], tge: ["TGE", "#ff5d5d"],
  airdrop: ["аирдроп", "#ff8fd8"], deadline: ["дедлайн", "#f5a524"], launch: ["запуск", "#8be04e"],
  other: ["прочее", "#6e7891"],
};
export const MONS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

/* ---------- мелочи ---------- */
export const $ = s => document.querySelector(s);
export const esc = s => (s || "").replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
export const escAttr = s => esc(s).replace(/"/g, "&quot;");
export const escRe = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export const fmtDate = iso => {          // «14 авг 2026»
  const d = new Date(iso.slice(0, 10) + "T12:00:00");
  return `${d.getDate()} ${MONS[d.getMonth()]} ${d.getFullYear()}`;
};
export const fmtDateShort = iso => {     // «14 авг»
  const d = new Date(iso.slice(0, 10) + "T12:00:00");
  return `${d.getDate()} ${MONS[d.getMonth()]}`;
};
export const fmtTime = ts => (ts || "").slice(11, 16);   // «22:53»
export const humanSize = n => n == null ? "" : n < 1024 ? n + " Б" : n < 1048576 ? (n / 1024).toFixed(1) + " KB" : (n / 1048576).toFixed(1) + " MB";

/* обрезка строки по код-поинтам: не рвёт суррогатные пары (эмодзи, иероглифы),
   иначе в DOM попадает непарный суррогат — невалидный текст */
export const clip = (s, n) => [...String(s)].slice(0, n).join("");

/* заголовок разговора: первое предложение выжимки, ~90 символов, обрез по слову */
export function convTitle(s) {
  s = (s || "").replace(/\s+/g, " ").trim();
  if (!s) return "Без выжимки";
  let t = s;
  // конец предложения: [.!?] + пробел/конец, но не точка в дате/числе («26.06») и не инициал («С. Rollins»)
  for (const m of s.matchAll(/[.!?](?=\s|$)/g)) {
    const w = (s.slice(0, m.index).match(/(\S+)$/) || [])[1] || "";
    if (w.length <= 1 || /\d$/.test(w)) continue;
    t = s.slice(0, m.index).trim() || s;
    break;
  }
  if (t.length > 90) {
    t = clip(t, 90);
    const sp = t.lastIndexOf(" ");
    if (sp > 50) t = t.slice(0, sp);
    t = t.trim() + "…";
  }
  return t;
}

/* ---------- данные карты (один раз на сессию, общий кэш) ---------- */
let GRAPH = null, graphP = null;
export function setGraph(g) { GRAPH = g; }
export function graph() { return GRAPH; }
export function ensureGraph() {
  if (!graphP) graphP = api.getGraph().then(g => { GRAPH = g; return g; });
  return graphP;
}
export function nodeTitle(id) {
  const n = GRAPH && GRAPH.nodes.find(x => x.id === id);
  return n ? convTitle(n.s) : id;
}

/* ---------- стример-мод: ники → «Участник XXX» ---------- */
const fnv = s => { let h = 0x811c9dc5; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; } return h >>> 0; };
export const anonName = s => "Участник " + (fnv(s) & 0xfff).toString(16).padStart(3, "0");
export const authorColor = s => `hsl(${fnv(s) % 360} 70% 76%)`;
let anon = localStorage.getItem("mxw_anon") === "1";
let authorLUT = new Map();
let anonRules = null;   // { re, map } — подстановка ников в отображаемых текстах
const anonCbs = new Set();   // подписки на смену режима: разделы с авторами перерисовываются
export const isAnon = () => anon;
export function onAnon(cb) { anonCbs.add(cb); }

/* правила подстановки: только ЦЕЛЬНЫЕ ники, по целым словам, регистронезависимо,
    длинные раньше («Max Wayld 福» целиком, а не «Max»). Разбивать многословные ники
    на отдельные слова нельзя: в данных это обычные слова («Крипта для народа» → «крипта»,
    «дети хоронят коня» → «дети», «KISA | arc локальных моделей прайм» → «моделей») —
    их замена исказила бы тексты ответов. Однословные ники ≥ 4 символов («Kapitoshich»)
    покрываются цельным совпадением. Семантика — как у _anon_sub на сервере (server.py):
    одинаковая подстановка для ответов, полученных с anon и без */
function buildAnonRules(authors) {
  const list = [...new Set((authors || []))]
    .map(a => String(a || "").trim())
    .filter(a => a.length >= 3 && /\p{L}/u.test(a))
    .sort((a, b) => [...b].length - [...a].length);
  if (!list.length) return null;
  const map = new Map();   // совпадение (в нижнем регистре) → канонический ник → его псевдоним
  const alts = [];
  const add = (s, canon) => {
    const k = s.toLowerCase();
    if (map.has(k)) return;
    map.set(k, canon);
    alts.push(escRe(s));
  };
  for (const a of list) add(a, a);
  if (!alts.length) return null;
  try {
    return { re: new RegExp("(?<![\\p{L}\\p{N}_])(" + alts.join("|") + ")(?![\\p{L}\\p{N}_])", "giu"), map };
  } catch { return null; }
}

export function setAuthorLUT(authors) {
  authorLUT = new Map((authors || []).map(a => [a.toLowerCase(), a]));
  anonRules = buildAnonRules(authors);
}

/* ники в отображаемом тексте (ответы ИИ, сниппеты источников, заголовки чатов…) → псевдонимы.
   Выключен стример-мод — текст без изменений; исходные данные не мутируются */
export function anonText(s) {
  if (!anon || !anonRules || s == null) return s;
  return String(s).replace(anonRules.re, m => {
    const canon = anonRules.map.get(m.toLowerCase());
    return canon != null ? anonName(canon) : m;
  });
}

/* «Участник xxx» (deep-link ?person= в стример-моде) → настоящий ник; не псевдоним — как есть */
export function authorFromAnon(s) {
  const str = String(s || "");
  const canon = authorLUT.get(str.toLowerCase());
  if (canon) return canon;
  if (!/^Участник [0-9a-f]{3}$/i.test(str)) return str;
  const want = str.toLowerCase();
  for (const a of authorLUT.values()) if (anonName(a).toLowerCase() === want) return a;
  return str;
}

/* кружок-аватар: инициалы и приглушённый цвет из хеша показываемого имени (в стример-моде — псевдонима) */
export function initialsOf(name) {
  const w = (name || "").replace(/[^\p{L}\p{N}\s]/gu, " ").trim().split(/\s+/).filter(Boolean);
  const cps = s => [...(s || "?")];
  const s = w.length >= 2 ? cps(w[0])[0] + cps(w[1])[0] : cps(w[0] || "?")[0] + (cps(w[0] || "")[1] || "");
  return s.toUpperCase();
}
export function avatarStyle(shown) {
  const h = fnv(shown) % 360;
  return `background:hsl(${h} 26% 27%);color:hsl(${h} 55% 82%)`;
}
export function avHtml(author, cls = "mav") {
  const shown = anon ? anonName(author) : author;
  return `<span class="${cls}" data-av="${escAttr(author)}" style="${avatarStyle(shown)}">${esc(initialsOf(shown))}</span>`;
}
export function applyAnon(root) {   // data-author/data-fwd → псевдоним (или настоящие имена)
  (root || document).querySelectorAll("[data-av]").forEach(el => {
    const shown = anon ? anonName(el.dataset.av) : el.dataset.av;
    el.textContent = initialsOf(shown);
    el.setAttribute("style", avatarStyle(shown));
  });
  (root || document).querySelectorAll("[data-author]").forEach(el => {
    const name = el.dataset.author || "";
    if (anon && name) el.textContent = anonName(name);
    else el.textContent = name;
    if (el.classList.contains("a")) el.style.color = authorColor(anon ? anonName(name) : name);
    else el.style.color = anon && name ? authorColor(name) : "";
  });
  (root || document).querySelectorAll("[data-fwd]").forEach(el => {
    const name = el.dataset.fwd || "";
    const canon = anon && name ? authorLUT.get(name.toLowerCase()) : undefined;
    el.textContent = canon != null ? anonName(canon) : name;   // совпал с автором → псевдоним; канал — как есть
  });
}

export function setAnon(v) {
  anon = v;
  localStorage.setItem("mxw_anon", v ? "1" : "0");
  document.body.classList.toggle("anon-on", v);
  const b = $("#anonBtn");
  if (b) {
    b.querySelector(".ic").innerHTML = icon(v ? "eye" : "eye-off");
    b.querySelector(".lbl").textContent = v ? "Показать ники" : "Скрыть ники";
  }
  const body = $("#dbody");
  if (document.body.classList.contains("drawer-open") && body) applyAnon(body);
  anonCbs.forEach(cb => cb(v));
}

/* ---------- tooltip ---------- */
export function tipShow(html, x, y) {
  const t = $("#tip");
  t.innerHTML = html;
  t.style.display = "block";
  tipMove(x, y);
}
export function tipMove(x, y) {
  const t = $("#tip");
  if (t.style.display !== "block") return;
  const w = t.offsetWidth, h = t.offsetHeight;
  t.style.left = Math.min(x + 14, innerWidth - w - 8) + "px";
  t.style.top = Math.min(y + 16, innerHeight - h - 8) + "px";
}
export function tipHide() { $("#tip").style.display = "none"; }

/* ---------- полноэкранный просмотр фото ---------- */
let curPhotos = [], vPos = 0;
export function openViewer(photos, i) {
  const p = photos.indexOf(i);
  if (p < 0) return;
  curPhotos = photos; vPos = p;
  $("#vimg").src = api.mediaUrl(i);
  $("#vcnt").textContent = curPhotos.length > 1 ? `${p + 1} / ${curPhotos.length}` : "";
  $("#vprev").style.display = $("#vnext").style.display = curPhotos.length > 1 ? "" : "none";
  $("#viewer").classList.add("open");
}
function stepPhoto(d) {
  vPos = (vPos + d + curPhotos.length) % curPhotos.length;
  $("#vimg").src = api.mediaUrl(curPhotos[vPos]);
  $("#vcnt").textContent = `${vPos + 1} / ${curPhotos.length}`;
}
export function viewerOpen() { return $("#viewer").classList.contains("open"); }
export function closeViewer() { $("#viewer").classList.remove("open"); $("#vimg").src = ""; }

/* ---------- роутинг по hash ---------- */
export function parseHash() {
  const h = (location.hash || "").replace(/^#\/?/, "");
  const [r, qs] = h.split("?");
  const route = ["chat", "map", "events", "people", "topics", "articles"].includes(r) ? r : "map";
  return { route, params: new URLSearchParams(qs || "") };
}
export function nav(route, params = {}, push = true) {
  /* разговор поверх страницы статьи: ?article= остаётся в адресе */
  if (route === "articles" && !("article" in params)) {
    const cur = parseHash();
    if (cur.route === "articles" && cur.params.get("article")) params = { article: cur.params.get("article"), ...params };
  }
  const qs = Object.entries(params).filter(([, v]) => v != null && v !== "").map(([k, v]) => [k, v]);
  const h = "#/" + route + (qs.length ? "?" + new URLSearchParams(qs).toString() : "");
  if (h === location.hash) return;
  if (push) location.hash = h;
  else history.replaceState(null, "", h);
}
export const curRoute = () => parseHash().route;

/* навигация к разговору/статье: из основного поля — push (назад браузера закроет панель),
   изнутри drawer — replace + стек для кнопки «←» */
export function navParam(name, value, extra = {}, push = true) {
  const params = { [name]: value, ...extra };
  nav(curRoute(), params, push);
}

/* ---------- drawer ---------- */
const drawerStack = [];   // стек «←» внутри панели: {restore}
let drawerArg = null;     // {kind, arg} текущего содержимого

export const drawerIsOpen = () => document.body.classList.contains("drawer-open");
export function drawerCur() { return drawerArg; }
export function drawerSetContent(arg) { drawerArg = arg; }

export function drawerOpenUI(title, more) {
  $("#dtitle").textContent = title || "";
  $("#dmore").style.display = more ? "" : "none";
  $("#dbody").innerHTML = `<div class="loading">Открываю</div>`;
  $("#dbody").scrollTop = 0;
  document.body.classList.add("drawer-open");
}
export function drawerSetTitle(t) { $("#dtitle").textContent = t || ""; }
export function drawerClearStack() { drawerStack.length = 0; $("#dback").hidden = true; }
export function drawerCloseUI() {
  document.body.classList.remove("drawer-open");
  drawerStack.length = 0;
  drawerArg = null;
  $("#dback").hidden = true;
}
/* закрытие панели: убрать ?conv/?article/?person/?q/?focus из hash без записи в историю */
export function drawerClose() {
  if (!drawerIsOpen()) return;
  drawerCloseUI();
  const { route, params } = parseHash();
  ["conv", "person", "q", "focus"].forEach(k => params.delete(k));
  if (route !== "articles") params.delete("article");   // на странице статьи она остаётся
  const qs = params.toString();
  history.replaceState(null, "", "#/" + route + (qs ? "?" + qs : ""));
}
export function drawerPushStack(restore) {
  drawerStack.push(restore);
  $("#dback").hidden = false;
}
export async function drawerBack() {
  const r = drawerStack.pop();
  $("#dback").hidden = drawerStack.length === 0;
  if (r) await r();
}

/* ---------- делегирование кликов: data-act ---------- */
const ACTS = new Map();
export function act(name, fn) { ACTS.set(name, fn); }
function wireActs() {
  document.addEventListener("click", e => {
    const el = e.target.closest("[data-act]");
    if (!el || el.disabled) return;
    const fn = ACTS.get(el.dataset.act);
    if (!fn) return;
    if (el.tagName === "A" || el.tagName === "BUTTON") e.preventDefault();
    fn(el.dataset.arg, el, e, !!el.closest("#drawer"));
  });
}

/* ---------- ссылки [[...]] в markdown ---------- */
const CONV_RE = /^(nft|ai|life|crypto|perps|predict|soft|shitki|important)-c[\d-]+$/;
export function linkify(html, valid) {
  return html.replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (m, id, label) => {
    if (valid && !valid.has(id)) return m;
    return CONV_RE.test(id)
      ? `<a data-act="conv" data-arg="${escAttr(id)}">[${esc(label || id)}]</a>`
      : `<a data-act="article-name" data-arg="${escAttr(id)}">[${esc(label || id)}]</a>`;
  });
}
/* http-ссылки в тексте сообщений → кликабельные, в новой вкладке */
export function urlLink(escapedText) {
  return escapedText.replace(/(https?:\/\/[^\s<]+)/g, m => {
    const t = m.replace(/[.,;:!?)]+$/, "");
    const rest = m.slice(t.length);
    return `<a href="${escAttr(t)}" target="_blank" rel="noopener noreferrer">${t}</a>${rest}`;
  });
}

/* ---------- карточка разговора (для списков) ---------- */
export function convCard(n, extraAttr = "") {
  const cat = CAT[n.c] || CAT.other;
  return `<div class="card" data-act="conv" data-arg="${escAttr(n.id)}" ${extraAttr}>
    <div class="ccard-t">${esc(convTitle(n.s))}</div>
    <div class="ccard-m">
      <span>${esc(fmtDateShort(n.t))}</span>
      <span class="dot" style="background:${cat[1]}" title="${esc(cat[0])}"></span><span>${esc(cat[0])}</span>
      <span>${n.n} сообщ.</span>
      ${n.sc ? `<span>схема</span>` : ""}
      ${n.v === "keep" ? `<span class="star">⭐ ценное</span>` : ""}
    </div></div>`;
}

/* ---------- init: статические обработчики ---------- */
export function initUI() {
  wireActs();
  document.addEventListener("mousemove", e => tipMove(e.clientX, e.clientY));

  $("#viewer").addEventListener("click", e => { if (e.target.id === "vimg" || e.target.id === "viewer") closeViewer(); });
  $("#vprev").addEventListener("click", e => { e.stopPropagation(); stepPhoto(-1); });
  $("#vnext").addEventListener("click", e => { e.stopPropagation(); stepPhoto(1); });
  $("#vclose").addEventListener("click", closeViewer);

  $("#dclose").addEventListener("click", drawerClose);
  $("#dback").addEventListener("click", () => drawerBack());
  $("#drawerVeil").addEventListener("click", drawerClose);

  document.addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    const pop = document.getElementById("refinePop");   // попап «⚙ Уточнить» в чате
    if (pop && !pop.hidden) { pop.hidden = true; return; }
    if (viewerOpen()) { closeViewer(); return; }
    if (drawerIsOpen()) drawerClose();
  });
}
