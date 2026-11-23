/* chat.js — единый чат с базой (#/chat): поиск + ответ ИИ в одном окне, вид как у claude.ai.
   Лента (вопрос справа пузырём, ответ слева на всю ширину с аватаром «MXW»), потоковый ответ
   со сносками [n] на карточки источников, «Показать все» — бывший поиск с фильтрами,
   история чатов в localStorage, уточнения (период/ветка/схемы) и pin-контекст. */
import { icon, emptyHtml } from "./icons.js";
import * as api from "./api.js";
import {
  $, esc, escAttr, CAT, parseHash, nav, act, isAnon, graph, ensureGraph,
  fmtDate, fmtDateShort, nodeTitle, tipShow, tipHide, clip, anonText, onAnon,
} from "./ui.js";
import { openConv, openArticle } from "./conv.js";

const marked = window.marked;

/* ---------- примеры для пустого экрана ---------- */
const EXAMPLES = [
  "Что сейчас можно заминтить на Robinhood Chain?",
  "Как не словить бан Claude из РФ?",
  "Какие антидетекты советовали для мультиаккаунтов?",
  "Что было с клеймом Ethereal?",
  "Кто разбирается в минт-ботах?",
  "Какие проекты оказались скамом?",
];
const PERIODS = [["", "всё время"], ["7", "7 дней"], ["30", "30 дней"], ["90", "90 дней"]];
const CHATS_KEY = "mxw_chats";
const CHATS_MAX = 30;
const PIN_CONV_RE = /^(nft|ai|life|crypto|perps|predict|soft|shitki|important)-c[\d-]+$/;

/* ---------- состояние ---------- */
let mounted = false;
let chats = [];                 // localStorage: [{id, title, ts, msgs}]
let cur = null;                 // текущий чат (элемент chats) или null
let busy = false;               // идёт ответ
let abortCtl = null;            // AbortController текущего стрима
let pinState = null;            // {kind:"conv"|"article", arg, title}
let refine = { period: "", branch: "", schemes: false };
let seq = 0;                    // id сообщений
let stick = true;               // автопрокрутка, пока пользователь не уехал вверх
const srcOpen = new Set();      // раскрытые «Нашлось в базе»
let menuEl = null;              // всплывающее меню «⋯» у недавнего чата

const nextId = () => ++seq;
const plural = (n, one, few, many) => {
  const a = Math.abs(n) % 100, b = a % 10;
  return a > 10 && a < 20 ? many : b > 1 && b < 5 ? few : b === 1 ? one : many;
};
const daysAgoISO = n => new Date(Date.now() - n * 864e5).toISOString().slice(0, 10);
const cap1 = s => { const cps = [...s]; return (cps[0] || "").toUpperCase() + cps.slice(1).join(""); };

/* ---------- инициализация: недавние чаты в сайдбаре ---------- */
export function chatInit() {
  try { chats = JSON.parse(localStorage.getItem(CHATS_KEY) || "[]"); } catch { chats = []; }
  if (!Array.isArray(chats)) chats = [];   // старый фронт (/old) хранил под этим ключом {list: [...]} другого формата
  for (const c of chats) for (const m of c.msgs || []) {   // прерванные на середине стримы — «замораживаем»
    m.streaming = false;
    m.status = "";
  }
  renderRecent();
  $("#recent").addEventListener("click", e => {
    const btn = e.target.closest(".ri-menu");
    if (btn) { e.stopPropagation(); recentMenu(btn); return; }
    const li = e.target.closest("li[data-cid]");
    if (li) openChat(li.dataset.cid);
  });
  document.addEventListener("click", e => {
    if (menuEl && !e.target.closest(".rmenu")) closeMenu();
  });
  onAnon(() => {   // стример-мод: мгновенно перерисовать всё на экране (без перезагрузки и запросов)
    renderRecent();
    if (!mounted) return;
    const sc = $("#chatScroll");
    const top = sc ? sc.scrollTop : 0;
    renderFeed();   // текст ответов, карточки источников, «Показать все» — от данных
    if (sc) sc.scrollTop = top;   // без прыжка прокрутки
  });
}

function closeMenu() { if (menuEl) { menuEl.remove(); menuEl = null; } }

function recentMenu(btn) {
  closeMenu();
  const cid = btn.closest("li").dataset.cid;
  const r = btn.getBoundingClientRect();
  menuEl = document.createElement("div");
  menuEl.className = "rmenu";
  menuEl.style.top = r.bottom + 4 + "px";
  menuEl.style.left = Math.min(r.left, innerWidth - 160) + "px";
  const del = document.createElement("button");
  del.textContent = "Удалить";
  del.addEventListener("click", () => {
    closeMenu();
    chats = chats.filter(c => c.id != cid);
    if (cur && cur.id == cid) chatNew();
    persistChats();
  });
  menuEl.appendChild(del);
  document.body.appendChild(menuEl);
}

function renderRecent() {
  const ul = $("#recent");
  if (!ul) return;
  ul.innerHTML = chats.map(c => {
    const t = anonText(c.title || "Без названия");   // заголовок = первый вопрос: мог содержать ник
    return `<li data-cid="${c.id}" class="${cur && cur.id === c.id ? "on" : ""}" title="${escAttr(t)}">
      <span class="t">${esc(t)}</span><button class="ri-menu" title="Действия: удалить чат из истории">${icon("more", 16)}</button>
    </li>`;
  }).join("");
}

function persistChats() {
  if (cur) cur.ts = Date.now();
  chats.sort((a, b) => (b.ts || 0) - (a.ts || 0));
  try { localStorage.setItem(CHATS_KEY, JSON.stringify(chats.slice(0, CHATS_MAX))); }
  catch { localStorage.removeItem(CHATS_KEY); }   // переполнение — история не критична
  renderRecent();
}

function openChat(cid) {
  if (busy && abortCtl) abortCtl.abort();   // текущий ответ останавливаем
  const c = chats.find(x => String(x.id) === String(cid));
  if (!c) return;
  cur = c;
  renderFeed();
  persistChats();
}

export function chatNew() {
  if (busy && abortCtl) abortCtl.abort();
  cur = null;
  pinState = null;
  renderFeed();
  const inp = $("#cInput");
  if (inp) { inp.focus(); }
}

/* ---------- монтирование экрана ---------- */
export function chatRoute(params) {
  if (!mounted) mountChat();
  const q = params.get("q");
  const pin = params.get("pin");
  if (pin && !pinState) setPinFromArg(pin);
  if (q) {
    cleanHashParams("q", "pin");
    if (!busy) send(q);
  } else if (pin) {
    cleanHashParams("pin");
  }
}

function cleanHashParams(...keys) {
  const { route, params } = parseHash();
  keys.forEach(k => params.delete(k));
  const qs = params.toString();
  history.replaceState(null, "", "#/" + route + (qs ? "?" + qs : ""));
}

function mountChat() {
  mounted = true;
  const root = document.querySelector('.view[data-view="chat"]');
  root.innerHTML = `
    <div class="chat-scroll" id="chatScroll">
      <div class="chat-col">
        <div class="chat-hero" id="chatHero">
          <div class="hero-t">Что найти в чате MXW?</div>
          <div class="hero-slot" id="heroSlot"></div>
          <div class="hero-chips">${EXAMPLES.map(x =>
            `<button class="hchip" data-q="${escAttr(x)}">${esc(x)}</button>`).join("")}</div>
          <div class="hero-links">
            <a class="hlink" href="#/events"><span class="ic">${icon("calendar")}</span><span><b>Что активно сейчас</b><small>минты, клеймы, дедлайны</small></span></a>
            <a class="hlink" href="#/people"><span class="ic">${icon("users")}</span><span><b>Кто в чём разбирается</b><small>эксперты по темам</small></span></a>
            <a class="hlink" href="#/articles"><span class="ic">${icon("book")}</span><span><b>Статьи базы</b><small>конспекты и схемы</small></span></a>
          </div>
        </div>
        <div id="chatFeed"></div>
      </div>
    </div>
    <div class="chat-bottom" id="chatBottom" hidden>
      <div class="chat-col">
        <div id="bottomSlot"></div>
      </div>
    </div>`;

  /* композер — один узел, живёт то в hero, то внизу экрана */
  const wrap = document.createElement("div");
  wrap.className = "composer-wrap";
  wrap.innerHTML = `
    <div class="pre-field" id="preField"></div>
    <div class="composer">
      <div class="c-row">
        <textarea id="cInput" rows="1" placeholder="Спроси что угодно о чате MXW…" title="Enter — отправить, Shift+Enter — перенос"></textarea>
        <button class="csend" id="cSend" disabled title="Отправить">${icon("arrow-up", 18)}</button>
      </div>
      <div class="c-tools">
        <button class="btn ghost" id="refineBtn" title="Сузить поиск: период, ветка, только схемы">${icon("sliders", 16)}Уточнить</button>
      </div>
      <div class="refine-pop" id="refinePop" hidden>
        <div class="r-row"><span class="r-l">Период</span>
          <div class="seg" id="rPeriod">${PERIODS.map(([v, l]) => `<button data-v="${v}">${l}</button>`).join("")}</div>
        </div>
        <div class="r-row"><span class="r-l">Ветка</span><select id="rBranch"><option value="">все ветки</option></select></div>
        <div class="r-row"><span class="r-l">Только</span>
          <label class="r-chk"><input type="checkbox" id="rSchemes"> разговоры со схемами</label>
        </div>
      </div>
    </div>
    <div class="c-hint">ИИ отвечает по материалам чата MXW и может ошибаться — проверяй источники</div>`;
  document.getElementById("heroSlot").appendChild(wrap);

  const inp = $("#cInput");
  inp.addEventListener("input", () => { autosize(inp); setSendState(); });
  inp.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(inp.value); }
  });
  $("#cSend").addEventListener("click", () => { busy ? abortCtl && abortCtl.abort() : send(inp.value); });
  $("#refineBtn").addEventListener("click", e => { e.stopPropagation(); toggleRefine(); });
  document.addEventListener("click", e => {
    const pop = $("#refinePop");
    if (pop && !pop.hidden && !e.target.closest(".composer-wrap")) pop.hidden = true;
  });
  $("#rPeriod").addEventListener("click", e => {
    const b = e.target.closest("button");
    if (!b) return;
    refine.period = b.dataset.v;
    renderRefine();
  });
  $("#rBranch").addEventListener("change", e => { refine.branch = e.target.value; renderRefine(); });
  $("#rSchemes").addEventListener("change", e => { refine.schemes = e.target.checked; renderRefine(); });

  ensureGraph().then(g => {   // ветки для уточнения
    $("#rBranch").innerHTML = `<option value="">все ветки</option>` + (g.topics || [])
      .map(t => `<option value="${escAttr(t.short)}">${esc(t.title)}</option>`).join("");
    $("#rBranch").value = refine.branch || "";
  }).catch(() => { });

  /* делегирование: примеры-чипы, действия над сообщениями, источники, сноски */
  $("#chatScroll").addEventListener("click", e => {
    const chip = e.target.closest(".hchip");
    if (chip) { send(chip.dataset.q); return; }
    const line = e.target.closest(".src-line");
    if (line) { toggleSources(+line.dataset.sid); return; }
    const ma = e.target.closest("[data-mact]");
    if (ma) msgAction(ma.dataset.mact, +ma.dataset.mid, ma);
  });
  $("#chatScroll").addEventListener("change", e => {   // фильтры «Показать все»
    const el = e.target.closest("[data-f]");
    if (el) fullFilterChanged(el);
  });
  $("#chatScroll").addEventListener("mouseover", e => {   // карточка источника на сноске [n]
    const fn = e.target.closest(".fn");
    if (!fn) return;
    const m = msgByDom(fn);
    const c = m && m.sources && m.sources[+fn.dataset.fn - 1];
    if (c) tipShow(srcTipHtml(c), e.clientX, e.clientY);
  });
  $("#chatScroll").addEventListener("mouseout", e => { if (e.target.closest(".fn")) tipHide(); });

  const sc = $("#chatScroll");
  sc.addEventListener("scroll", () => { stick = sc.scrollHeight - sc.scrollTop - sc.clientHeight < 90; });

  renderFeed();
  inp.focus();
}

function autosize(inp) {
  inp.style.height = "auto";
  inp.style.height = Math.min(inp.scrollHeight, 200) + "px";
}

function setSendState() {
  const b = $("#cSend");
  if (!b) return;
  b.innerHTML = busy ? icon("stop", 16) : icon("arrow-up", 18);
  b.title = busy ? "Остановить" : "Отправить";
  b.classList.toggle("stop", busy);
  b.disabled = !busy && !($("#cInput").value || "").trim();
}

/* ---------- pin-контекст («Вопрос про: … ✕») ---------- */
function setPinFromArg(arg) {
  const isConv = PIN_CONV_RE.test(arg);
  pinState = { kind: isConv ? "conv" : "article", arg, title: arg };
  if (isConv) {
    ensureGraph().then(() => { if (pinState && pinState.arg === arg) { pinState.title = nodeTitle(arg); renderPreField(); } })
      .catch(() => { });
  } else {
    pinState.title = decodeURIComponent(arg).split("/").pop().replace(/\.md$/, "");
  }
  renderPreField();
}

function renderPreField() {
  const pf = $("#preField");
  if (!pf) return;
  let h = "";
  if (pinState) {
    h += `<span class="pin-pl">Вопрос про: <b>${esc(pinState.title)}</b><button id="pinX" title="Убрать контекст">${icon("x", 14)}</button></span>`;
  }
  if (refine.period) h += `<span class="fchip">${esc((PERIODS.find(p => p[0] === refine.period) || ["", refine.period])[1])}<button data-rx="period">${icon("x", 14)}</button></span>`;
  if (refine.branch) {
    const g = graph();
    const t = g && g.topics.find(x => x.short === refine.branch);
    h += `<span class="fchip">ветка: ${esc(t ? t.title : refine.branch)}<button data-rx="branch">${icon("x", 14)}</button></span>`;
  }
  if (refine.schemes) h += `<span class="fchip">только схемы<button data-rx="schemes">${icon("x", 14)}</button></span>`;
  pf.innerHTML = h;
  const x = $("#pinX");
  if (x) x.addEventListener("click", () => { pinState = null; renderPreField(); });
  pf.querySelectorAll("[data-rx]").forEach(b => b.addEventListener("click", () => {
    const k = b.dataset.rx;
    if (k === "schemes") refine.schemes = false; else refine[k] = "";
    renderRefine();
  }));
}

function toggleRefine() {
  const pop = $("#refinePop");
  pop.hidden = !pop.hidden;
  renderRefine();
}

function renderRefine() {
  $("#rPeriod").querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === refine.period));
  const br = $("#rBranch");
  if (br && refine.branch) br.value = refine.branch;
  $("#rSchemes").checked = !!refine.schemes;
  renderPreField();
}

/* ---------- отправка ---------- */
function send(text) {
  text = (text || "").trim();
  if (!text || busy) return;
  if (!cur) {
    cur = { id: Date.now(), title: clip(text, 60), ts: Date.now(), msgs: [] };
    chats.unshift(cur);
  }
  cur.title = cur.title || clip(text, 60);
  cur.msgs.push({ id: nextId(), role: "user", content: text });
  const am = { id: nextId(), role: "assistant", content: "", status: "Ищу в базе…", sources: null, query: "", streaming: true };
  cur.msgs.push(am);
  persistChats();
  const inp = $("#cInput");
  if (inp) { inp.value = ""; autosize(inp); }
  renderFeed();
  stream(am);
}

function apiMessages() {
  return cur.msgs
    .filter(m => !m.streaming && (m.role === "user" || (m.content || "").trim()))
    .slice(-16)
    .map(m => ({ role: m.role, content: m.content }));
}

function stream(am) {
  busy = true;
  setSendState();
  stick = true;
  abortCtl = new AbortController();
  const body = { messages: apiMessages(), anon: isAnon() };
  if (pinState) body.pin = pinState.arg;
  if (refine.period) body.from = daysAgoISO(+refine.period);
  if (refine.branch) body.branch = refine.branch;
  if (refine.schemes) body.schemes = true;

  let raf = 0;
  const paint = () => renderMsgMain(am);
  api.chatStream(body, {
    meta: o => {
      am.sources = o.sources || [];
      am.query = o.query || "";
      am.status = "Пишу ответ…";
      renderMsgSrc(am);
      renderMsgMain(am);
    },
    status: o => { am.status = cap1(o.text || ""); renderMsgMain(am); },
    delta: o => {
      if (am.status) { am.status = ""; renderMsgMain(am); }
      am.content += o.text || "";
      if (!raf) raf = requestAnimationFrame(() => { raf = 0; paint(); });
    },
    done: () => finish(am, null),
  }, abortCtl.signal).catch(err => {
    if (err && err.name === "AbortError") finish(am, "stop");
    else finish(am, (err && err.message) || String(err));
  });
}

function finish(am, err) {
  busy = false;
  abortCtl = null;
  am.streaming = false;
  am.status = "";
  if (err === "stop") am.stopped = true;
  else if (err) am.error = err;
  persistChats();
  renderMsgMain(am);
  renderMsgSrc(am);
  setSendState();
}

/* ---------- рендер ленты ---------- */
function setMode(empty) {
  const hero = $("#chatHero"), bottom = $("#chatBottom");
  if (!hero) return;
  hero.style.display = empty ? "" : "none";
  bottom.hidden = empty;
  const slot = empty ? $("#heroSlot") : $("#bottomSlot");
  const wrap = document.querySelector(".composer-wrap");
  if (wrap && slot && wrap.parentElement !== slot) slot.appendChild(wrap);
}

function renderFeed() {
  const feed = $("#chatFeed");
  if (!feed) return;
  const msgs = cur ? cur.msgs : [];
  setMode(!msgs.length);
  feed.innerHTML = msgs.map(m => m.role === "user" ? userHtml(m) : assistantHtml(m)).join("");
  renderRecent();
  scrollBottom(true);
}

const userHtml = m =>
  `<div class="chat-msg u"><div class="ubub">${esc(m.content)}</div></div>`;

const assistantHtml = m =>
  `<div class="chat-msg a" id="cm${m.id}" data-mid="${m.id}">
    <div class="am-av" title="ИИ по базе MXW">${icon("spark", 16)}</div>
    <div class="am-body">
      <div class="am-src" id="csrc${m.id}">${m.sources ? sourcesHtml(m) : ""}</div>
      <div class="am-main" id="cmain${m.id}">${mainHtml(m)}</div>
    </div>
  </div>`;

function renderMsgSrc(am) {
  const el = document.getElementById("csrc" + am.id);
  if (el) el.innerHTML = am.sources ? sourcesHtml(am) : "";
}

function renderMsgMain(am) {
  const el = document.getElementById("cmain" + am.id);
  if (el) { el.innerHTML = mainHtml(am); scrollBottom(); }
}

function mainHtml(am) {
  let h = "";
  if (am.status) h += `<div class="am-status">${esc(am.status)}</div>`;
  if (am.content) {
    h += `<div class="md chat-md${am.streaming ? " cur" : ""}">${renderMd(am)}</div>`;
  }
  if (am.error) h += `<div class="am-err">${icon("alert", 16)}<span>${esc(am.error)}</span></div>`;
  else if (am.stopped) h += `<div class="am-note">остановлено</div>`;
  if (!am.streaming && am.content) {
    h += `<div class="am-actions">
      <button class="btn ghost sm" data-mact="copy" data-mid="${am.id}" title="Скопировать ответ">${icon("copy", 15)}<span>Скопировать</span></button>
      <button class="btn ghost sm" data-mact="retry" data-mid="${am.id}" title="Спросить заново">${icon("retry", 15)}<span>Повторить</span></button>
    </div>`;
  }
  return h;
}

/* markdown + сноски [n] → кликабельные чипы источников; [[…]] из модели убираем.
   Стример-мод: ответ, полученный с выключенным режимом, показываем с псевдонимами
   (am.content хранит исходный текст — при выключении режима возвращается настоящий) */
function renderMd(am) {
  let html = marked.parse((anonText(am.content) || "").replace(/\[\[[^\]]*\]\]/g, ""));
  const maxN = am.sources ? am.sources.length : 0;
  if (maxN) {
    html = html.replace(/\[(\d{1,2})\]/g, (m0, d) => {
      const n = +d;
      if (n < 1 || n > maxN) return m0;
      const c = am.sources[n - 1];
      const isConv = c.kind === "conv";
      return `<sup class="fn" data-fn="${n}" data-act="${isConv ? "conv" : "article"}"
        data-arg="${escAttr(isConv ? c.id : c.path)}">${n}</sup>`;
    });
  }
  return html;
}

/* ---------- источники: свёрнутая строка + список карточек + «Показать все» ---------- */
function sourcesHtml(am) {
  const src = am.sources || [];
  if (!src.length) return "";
  const open = srcOpen.has(am.id);
  const shown = src.slice(0, 6);
  return `<div class="src-line" data-sid="${am.id}" title="Развернуть или свернуть список найденных источников">
      ${icon("layers", 15)} Нашлось в базе: ${src.length} ${plural(src.length, "источник", "источника", "источников")}
      <span class="sl-arrow${open ? " open" : ""}">${icon("chev-down", 15)}</span>
    </div>
    <div class="src-list" ${open ? "" : "hidden"}>
      ${shown.map(c => srcCardHtml(c)).join("")}
      ${src.length > 6 ? `<button class="btn ghost sm src-all" data-mact="all" data-mid="${am.id}">Показать все (${src.length})</button>` : ""}
      <div class="src-full" id="csf${am.id}" data-mid="${am.id}">${fullBoxHtml(am)}</div>
    </div>`;
}

function markSnip(s) {   /* «совпавшие слова» из FTS-сниппета — в <mark> */
  return s.replace(/«([^»]{1,60})»/g, "<mark>$1</mark>");
}

function srcCardHtml(c) {
  const isConv = c.kind === "conv";
  const cat = CAT[c.category];
  const title = anonText(c.title);        // ответы, полученные без стример-мода, могут содержать ники
  const snippet = anonText(c.snippet);
  return `<div class="src-card" data-act="${isConv ? "conv" : "article"}" data-arg="${escAttr(isConv ? c.id : c.path)}">
    <span class="src-n">${c.n}</span>
    <div class="src-b">
      <div class="src-t">${esc(title)}</div>
      <div class="src-m">${[
        c.date ? esc(fmtDateShort(c.date)) : "",
        c.branch ? esc(c.branch) : "",
        cat ? `<span class="dot" style="background:${cat[1]}" title="${escAttr(cat[0])}"></span>${esc(cat[0])}` : "",
      ].filter(Boolean).join('<span class="dm">·</span>')}</div>
      ${snippet ? `<div class="src-s">${markSnip(esc(snippet))}</div>` : ""}
    </div>
  </div>`;
}

function srcTipHtml(c) {
  const isConv = c.kind === "conv";
  const cat = CAT[c.category];
  return `<b>${esc(anonText(c.title))}</b>
    <span class="m">${[c.date ? fmtDate(c.date) : "", c.branch || "", cat ? cat[0] : ""].filter(Boolean).join(" · ")}</span>
    ${c.snippet ? `<div class="m">${esc(anonText(c.snippet))}</div>` : ""}`;
}

function toggleSources(sid) {
  if (srcOpen.has(sid)) srcOpen.delete(sid);
  else srcOpen.add(sid);
  const m = cur && cur.msgs.find(x => x.id === sid);
  if (m) renderMsgSrc(m);
}

/* «Показать все» — бывший поиск: полный список с фильтрами (период, ветка, категория, схемы).
   Результаты храним данными (m._fullData) и перерисовываем из них — смена стример-мода
   обновляет сниппеты без нового запроса; _fullHtml — закэшированный html (ошибка/загрузка) */
function fullFiltersHtml(m) {
  const state = m._full || { period: "", branch: "", category: "", schemes: false };
  const cats = Object.entries(CAT);
  const topics = (graph() || { topics: [] }).topics || [];
  const q = m.query || (cur && cur.msgs.filter(x => x.role === "user").pop() || {}).content || "";
  return `<div class="sf-filters">
      <select data-f="period">${PERIODS.map(([v, l]) => `<option value="${v}"${state.period === v ? " selected" : ""}>${l}</option>`).join("")}</select>
      <select data-f="branch"><option value="">все ветки</option>${topics.map(t =>
        `<option value="${escAttr(t.short)}"${state.branch === t.short ? " selected" : ""}>${esc(t.title)}</option>`).join("")}</select>
      <select data-f="category"><option value="">все категории</option>${cats.map(([k, v]) =>
        `<option value="${k}"${state.category === k ? " selected" : ""}>${esc(v[0])}</option>`).join("")}</select>
      <label class="r-chk"><input type="checkbox" data-f="schemes"${state.schemes ? " checked" : ""}> только схемы</label>
      <span class="sf-q" title="поисковый запрос этого ответа">запрос: ${esc(anonText(q))}</span>
    </div>`;
}

function fullResultsHtml(m) {
  const r = m._fullData || {};
  const rows = (r.results || []).map((h, i) => searchRowHtml(h, i + 1)).join("");
  const noSem = r.semantic === false   // по смыслу не искали — объясняем, почему только слова
    ? `<div class="sf-note">🔎 по смыслу не искал${api.isStandalone()
        ? " — модель поиска не загрузилась, поэтому только по словам" : " — смысловой поиск недоступен"}</div>`
    : "";
  return (rows || `<div class="am-note">Ничего не нашлось — попробуй ослабить фильтры</div>`) + noSem;
}

function fullBoxHtml(m) {
  if (!m._fullData) return m._fullHtml || "";
  return fullFiltersHtml(m) + `<div class="sf-results">${fullResultsHtml(m)}</div>`;
}

async function showAll(mid) {
  const m = cur && cur.msgs.find(x => x.id === mid);
  const box = document.getElementById("csf" + mid);
  if (!m || !box) return;
  srcOpen.add(mid);
  if (m._fullData) { box.innerHTML = fullBoxHtml(m); m._fullHtml = box.innerHTML; return; }   // уже искали — из данных
  if (m._fullHtml) return;   // уже разворачивали (ошибка поиска) — восстановлен из _fullHtml
  m._full = { period: "", branch: "", category: "", schemes: false };
  box.innerHTML = fullFiltersHtml(m) + `<div class="sf-results"><div class="loading">Ищу</div></div>`;
  await runFullSearch(m, box);
}

async function runFullSearch(m, box) {
  const state = m._full || {};
  const res = box.querySelector(".sf-results");
  if (!res) return;
  res.innerHTML = `<div class="loading">Ищу</div>`;
  const q = m.query || (cur && cur.msgs.filter(x => x.role === "user").pop() || {}).content || "";
  const params = {};
  if (state.period) params.from = daysAgoISO(+state.period);
  if (state.branch) params.branch = state.branch;
  if (state.category) params.category = state.category;
  if (state.schemes) params.schemes = "1";
  params.expand = "1";   // поиск из чата: LLM-ключевые слова к этому запросу
  const onStatus = t => { if (res.querySelector(".loading")) res.querySelector(".loading").textContent = t; };
  let r;
  try { r = await api.search(q, params, onStatus); }
  catch (err) { res.innerHTML = `<div class="am-err">${icon("alert", 16)}<span>${esc(err.message)}</span></div>`; m._fullHtml = box.innerHTML; return; }
  m._fullData = r;   // для перерисовки при смене стример-мода — без нового запроса
  res.innerHTML = fullResultsHtml(m);
  m._fullHtml = box.innerHTML;
}

/* фильтры «Показать все» — через делегирование change (переживает пере-рендер) */
function fullFilterChanged(el) {
  const box = el.closest(".src-full");
  if (!box) return;
  const m = cur && cur.msgs.find(x => x.id === +box.dataset.mid);
  if (!m || !m._full) return;
  m._full[el.dataset.f] = el.type === "checkbox" ? el.checked : el.value;
  runFullSearch(m, box);
}

function searchRowHtml(h, n) {
  const cat = CAT[h.category] || CAT.other;
  const snip = anonText(((h.hits || [])[0] || {}).snippet || clip(h.summary || "", 180));
  return `<div class="src-card" data-act="conv" data-arg="${escAttr(h.id)}">
    <span class="src-n">${n}</span>
    <div class="src-b">
      <div class="src-t">${esc(anonText(h.title || h.id))}</div>
      <div class="src-m">${[
        h.date ? esc(fmtDateShort(h.date)) : "",
        h.branch ? esc(h.branch) : "",
        `<span class="dot" style="background:${cat[1]}" title="${escAttr(cat[0])}"></span>${esc(cat[0])}`,
        h.has_scheme ? "схема" : "",
      ].filter(Boolean).join('<span class="dm">·</span>')}</div>
      <div class="src-s">${markSnip(esc(snip))}</div>
    </div>
  </div>`;
}

/* ---------- действия над сообщениями ---------- */
function msgByDom(el) {
  const node = el.closest(".chat-msg[data-mid]");
  return node && cur ? cur.msgs.find(m => m.id === +node.dataset.mid) : null;
}

function msgAction(name, mid, btn) {
  const m = cur && cur.msgs.find(x => x.id === mid);
  if (!m) return;
  if (name === "copy") {
    navigator.clipboard?.writeText(anonText(m.content)).then(() => {   // как на экране (стример-мод — псевдонимы)
      const t = btn.innerHTML;
      btn.innerHTML = icon("check", 15) + "<span>Скопировано</span>";
      setTimeout(() => { btn.innerHTML = t; }, 1200);
    });
  } else if (name === "retry") {
    retryLast();
  } else if (name === "all") {
    showAll(mid);
  }
}

function retryLast() {
  if (busy || !cur) return;
  while (cur.msgs.length && cur.msgs[cur.msgs.length - 1].role !== "user") cur.msgs.pop();
  if (!cur.msgs.length) return;
  const am = { id: nextId(), role: "assistant", content: "", status: "Ищу в базе…", sources: null, query: "", streaming: true };
  cur.msgs.push(am);
  persistChats();
  renderFeed();
  stream(am);
}

function scrollBottom(force) {
  const sc = $("#chatScroll");
  if (sc && (force || stick)) sc.scrollTop = sc.scrollHeight;
}
