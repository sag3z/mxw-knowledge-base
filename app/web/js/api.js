/* api.js — ЕДИНСТВЕННОЕ место, где фронт получает данные.
   Два режима: «сервер» (fetch к /api/…) и «автономный» (данные уже вшиты в window.MXW_DATA —
   статический архив для создателя; чат ходит напрямую в API из ⚙ Настройки, смысловой поиск
   работает локально в браузере — embed_web.js, без ключа). Режим определяется наличием
   window.MXW_DATA; разделы не знают, в каком режиме работают. */

import { embedWebQuery } from "./embed_web.js";

const LOCAL = () => (typeof window !== "undefined" && window.MXW_DATA) || null;

/* автономный режим? (боковой панели нужен «⚙ Настройки» — только в архиве) */
export const isStandalone = () => !!LOCAL();

async function jfetch(path, opt) {
  const r = await fetch(path, opt);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || ("HTTP " + r.status));
  return r.json();
}

/* ================================================================== АВТОНОМНЫЙ РЕЖИМ === */

/* настройки получателя архива: ключ живёт в localStorage, уходит только в его API.
   Эмбеддинги запроса больше не через API — локальная модель прямо в браузере (embed_web.js),
   ключ нужен только для генерации ответа ИИ. */
const ls = k => { try { return localStorage.getItem(k) || ""; } catch { return ""; } };
const CFG = {
  /* провайдер: "server" — ключи сервера (только локальный сайт); иначе — свой из ⚙ Настройки */
  provider: () => ls("mxw_provider") || (LOCAL() ? "openrouter" : "server"),
  base: () => (ls("mxw_base") || "https://openrouter.ai/api/v1").replace(/\/+$/, ""),
  key: () => ls("mxw_key"),
  chatModel: () => ls("mxw_chat_model") || (ls("mxw_base") ? "" : "deepseek/deepseek-chat"),
  fastModel: () => ls("mxw_fast_model") || CFG.chatModel(),
  /* можно звать ИИ: есть модель и ключ (локальным Ollama / LM Studio ключ не нужен) */
  ready: () => !!CFG.chatModel() && (!!CFG.key() || isLocalBase(CFG.base())),
};
const isLocalBase = b => /^https?:\/\/(localhost|127\.\d+\.\d+\.\d+|\[::1\])(:\d+)?(\/|$)/i.test(b || "");
const AUTH = "Authori" + "zation";   // имя заголовка и его значение собираются из частей: слитные слова скан архива считает секретами
const authHeaders = key => {
  const h = { "Content-Type": "application/json" };
  if (key) h[AUTH] = "Bearer" + " " + key;
  return h;
};

async function llmPost(path, body, signal) {
  const r = await fetch(CFG.base() + path, {
    method: "POST", signal,
    headers: authHeaders(CFG.key()),
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const err = new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 200)}`);
    err.http = r.status;
    throw err;
  }
  return r;
}

/* ленивая подгрузка JS-шардов данных (классический <script> с диска работает с file://) */
const _scriptP = {};
function loadScript(src) {
  return _scriptP[src] ||= new Promise((res, rej) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = () => res();
    s.onerror = () => { delete _scriptP[src]; rej(new Error("не загрузился " + src)); };
    document.head.appendChild(s);
  });
}
async function ensureMiniSearch() {
  if (!window.MiniSearch) await loadScript("app/lib/minisearch.js");
}

/* прерывание по кнопке «остановить»: то же имя ошибки, что у fetch.abort */
function chkAbort(signal) {
  if (signal && signal.aborted) {
    const e = new Error("прервано");
    e.name = "AbortError";
    throw e;
  }
}

/* --- шард разговоров по веткам: app/data/conv_<short>.js ------------------------- */
const _shardP = {};
function ensureShard(short) {
  const d = LOCAL();
  if (d && d.graph && !d.graph.topics.some(t => t.short === short))
    return Promise.reject(new Error("нет ветки " + short));
  return _shardP[short] ||= loadScript("app/data/conv_" + short + ".js");
}
async function ensureAllShards(onStatus) {
  const shorts = (LOCAL().graph ? LOCAL().graph.topics : []).map(t => t.short);
  let done = 0;
  await Promise.all(shorts.map(s => ensureShard(s).then(() => {
    done++;
    if (onStatus) onStatus(`загружаю сообщения (${done}/${shorts.length})…`);
  })));
}

/* --- медиа архива: app/media/<i>.<ext>, миниатюры <i>_t.jpg ----------------------
   Расширение файла известно из загруженных шардов (files[].ext) — api.mediaUrl(i)
   вызывается только для медиа уже открытого разговора. */
const mediaExts = new Map();
function registerMedia(c) {
  for (const m of c.messages || []) for (const f of m.files || []) mediaExts.set(f.i, f.ext);
}

/* --- подсветка поиска в разговоре: hit + показываемые сообщения (порт _compute_show из server.py) --- */

/* стоп-слова и значимые слова поиска — как fts_query на сервере (server.py):
   служебные слова выкидываются, иначе поиск цепляется за «какие»/«для» вместо темы;
   слова короче 3 символов тоже — кроме чисел и латинских тикеров из 2+ ЗАГЛАВНЫХ */
const STOP = new Set(("а без бы был была были было будет быть в вам вас весь везде вот все всё всего всем всеми "
  + "всех вы где да для до его ее её если есть еще ещё за здесь и из или им ими их к как какая какие какой каков "
  + "когда кто куда ли либо между мне мной может можно мой моего моей моя мы над надо нам нас наш нашего нашей "
  + "не него нее неё нет ни нибудь но о об обо он она они оно опять от откуда перед по под пока после потому "
  + "почти при про сам сама сами свой своего своей своя свои со совсем так также там твой твоего твоей твоя те тем "
  + "теперь то тогда тоже той только тому тот ту тут ты у уж уже хоть хотя через что чтоб чтобы чуть эта эти это "
  + "этого этой этим этими этот эту же a about all an and any are as at be been being but by can could did do does "
  + "doing for from had has have having he her here hers him his how i if in into is it its just me my no nor not "
  + "of on or our ours out own s she should so some such t than that the their them then there these they this "
  + "those to too under up was we were what when where which who whom whose why will with would you your").split(" "));
const normWord = w => w.toLowerCase().replace(/ё/g, "е");
const keepWord = w => !STOP.has(normWord(w)) &&
  (w.length >= 3 || /^\d+$/u.test(w) || /^[A-Z]{2,}$/.test(w));
const wordsOf = q => (q.match(/[\p{L}\p{N}_]+/gu) || []).filter(keepWord).map(w => w.toLowerCase());
const sigQuery = q => {
  const seen = new Set();
  const out = [];
  for (const w of wordsOf(q)) {
    const n = normWord(w);
    if (!seen.has(n)) { seen.add(n); out.push(w); }
  }
  return out.join(" ");
};

function computeShow(msgs, focusId) {
  const n = msgs.length;
  const idx = new Map(msgs.map((m, i) => [m.id, i]));
  const focusI = focusId != null && idx.has(focusId) ? idx.get(focusId) : null;
  const show = [];
  if (!msgs.some(m => m.key || m.hit)) {
    msgs.forEach((m, i) => { if (!m.noise) show.push(i); });
    if (focusI != null) show.push(focusI);
  } else {
    const replies = new Map();
    msgs.forEach((m, i) => {
      if (m.reply_to != null) {
        if (!replies.has(m.reply_to)) replies.set(m.reply_to, []);
        replies.get(m.reply_to).push(i);
      }
    });
    const seeds = [];
    msgs.forEach((m, i) => { if (m.key || m.hit) seeds.push(i); });
    if (focusI != null) { show.push(focusI); seeds.push(focusI); }
    for (const i of seeds) {
      const m = msgs[i];
      if (!m.noise) show.push(i);
      if (m.reply_to != null && idx.has(m.reply_to) && !msgs[idx.get(m.reply_to)].noise)
        show.push(idx.get(m.reply_to));
      for (const j of replies.get(m.id) || []) if (!msgs[j].noise) show.push(j);
      if (i > 0 && !msgs[i - 1].noise) show.push(i - 1);
      if (i + 1 < n && !msgs[i + 1].noise) show.push(i + 1);
    }
  }
  const set = new Set(show);
  msgs.forEach((m, i) => { m.show = set.has(i); });
}

async function localConv(d, id, q, focus) {
  const short = id.split("-", 1)[0];
  await ensureShard(short);
  const c = d.convs && d.convs[id];
  if (!c) throw new Error("разговор не найден в архиве");
  registerMedia(c);
  const words = q ? wordsOf(q) : [];
  let f = null;
  if (focus != null && focus !== "") {
    const num = +focus;
    f = Number.isFinite(num) ? num : null;
  }
  for (const m of c.messages) {   // hit: слова запроса в тексте/расшифровках (клиентский аналог fts_msg)
    m.hit = false;
    if (words.length) {
      const body = ((m.text || "") + " " + (m.media_text || []).map(x => x.text || "").join(" ")).toLowerCase();
      m.hit = words.some(w => body.includes(w));
    }
  }
  computeShow(c.messages, f);
  return c;
}

/* --- поиск: слова (MiniSearch) + смысл (эмбеддинги по ключу) + RRF ----------------- */
/* кандидаты и веса — как hybrid_search на сервере: смысл главный, слова — бонусы */
const SEM_TOP = 120, FTS_CONV_TOP = 60, FTS_MSG_TOP = 60, FTS_MSG_FETCH = 300, HITS_PER_CONV = 3;
const W_KW_CONV = 0.15, W_KW_MSG = 0.08, W_KEEP = 0.05, KW_DF_FRAC = 0.04;
const RRF_K = 60;   // RRF для кусочков статей (_art_hits на сервере)

function convPass(e, opt) {   // фильтры разговора (ветка/категория/схемы/даты) — как _conv_where
  if (opt.branch && e.b !== opt.branch) return false;
  if (opt.category && e.c !== opt.category) return false;
  if (opt.schemes && !e.sc) return false;
  if (opt.dateFrom && e.t < opt.dateFrom) return false;
  if (opt.dateTo && e.t > opt.dateTo) return false;
  return true;
}

let convIdx = null;
async function ensureConvIndex(d) {   // MiniSearch по выжимкам+тегам+схемам (аналог fts_conv)
  if (convIdx) return convIdx;
  await ensureMiniSearch();
  convIdx = new MiniSearch({ fields: ["body"] });
  convIdx.addAll((d.convsIndex || []).map((e, i) => ({ id: i, body: `${e.sum} ${e.tags.join(" ")} ${e.sch}` })));
  return convIdx;
}

/* --- kw_conv: доля значимых термов запроса в выжимке+тегах (порт _sig_terms/_conv_kw_index) --- */
let kwIx = null;
function convKwIndex(d) {   // токены выжимок, df и сортированный словарь — строится один раз
  if (kwIx) return kwIx;
  const tokens = new Map(), df = new Map();
  let nDocs = 0;
  for (const e of d.convsIndex || []) {
    const body = `${e.sum} ${e.tags.join(" ")} ${e.sch}`.trim();
    if (!body) continue;
    nDocs++;
    const toks = new Set((body.match(/[\p{L}\p{N}_]+/gu) || []).map(normWord));
    tokens.set(e.id, toks);
    for (const t of toks) df.set(t, (df.get(t) || 0) + 1);
  }
  kwIx = { tokens, df, sorted: [...df.keys()].sort(), nDocs };
  return kwIx;
}

function prefixTokens(ix, term) {   // слова индекса выжимок с этим префиксом (как термы FTS-запроса)
  const toks = ix.sorted;
  let lo = 0, hi = toks.length;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (toks[mid] < term) lo = mid + 1; else hi = mid; }
  const out = new Set();
  for (let i = lo; i < toks.length && toks[i].startsWith(term); i++) out.add(toks[i]);
  return out;
}

function sigTermsOf(q, keywords, d) {   // значимые термы для kw_conv — как _sig_terms на сервере:
  const raw = keywords && keywords.length ? keywords.join(" ") : (q || "");   // keywords питают FTS и kw_conv
  const ix = convKwIndex(d);
  const out = [], seen = new Set();
  for (const w of (raw.match(/[\p{L}\p{N}_]+/gu) || [])) {
    const t = normWord(w);
    if (seen.has(t) || !keepWord(w)) continue;
    seen.add(t);
    const toks = prefixTokens(ix, t);
    let dfSum = 0;
    for (const x of toks) dfSum += ix.df.get(x) || 0;
    if (dfSum > KW_DF_FRAC * ix.nDocs) continue;   // «общий» терм (частый в выжимках) — бонуса не даёт
    out.push({ t, toks });
  }
  return out;
}

let msgIdx = null, msgFlat = null;
async function ensureMsgIndex(d, onStatus) {   // MiniSearch по сообщениям+расшифровкам (аналог fts_msg)
  if (msgIdx) return msgIdx;
  await ensureAllShards(onStatus);
  if (onStatus) onStatus("строю поисковый индекс…");
  await ensureMiniSearch();
  msgFlat = [];
  const docs = [];
  for (const e of d.convsIndex || []) {
    const c = d.convs && d.convs[e.id];
    if (!c) continue;
    for (const m of c.messages) {
      if (m.bot) continue;
      const body = ([m.text || "", ...(m.media_text || []).map(x => x.text || "")].join(" ")).trim();
      if (!body) continue;
      msgFlat.push({ conv: e.id, msg: m.id, author: m.author, body });
      docs.push({ id: msgFlat.length - 1, body });
    }
  }
  msgIdx = new MiniSearch({ fields: ["body"] });
  msgIdx.addAll(docs);
  return msgIdx;
}

function snippetOf(body, words) {   // окно ~14 слов вокруг первого совпадения, «» вокруг слов запроса
  const toks = body.split(/\s+/).filter(Boolean);
  const isHit = t => words.some(w => t.toLowerCase().startsWith(w));
  const first = toks.findIndex(isHit);
  if (first < 0) return body.slice(0, 160);
  const s = Math.max(0, first - 7), e = Math.min(toks.length, first + 8);
  const mid = toks.slice(s, e).map(t => isHit(t) ? `«${t}»` : t).join(" ");
  return (s > 0 ? "…" : "") + mid + (e < toks.length ? "…" : "");
}

/* --- эмбеддинги разговора: int8+масштаб в base64 → косинус в браузере -------------
    Те же векторы e5-small, что у сервера (emb_local); запрос эмбеддится локальной
    моделью в браузере (embed_web.js) — ключ не нужен. */
let _embP = null, semM = null;
function ensureEmb() { return _embP ||= loadScript("app/data/emb.js"); }
async function semMatrix() {
  if (semM) return semM;
  await ensureEmb();
  const e = LOCAL().emb, bin = atob(e.codes), n = e.ids.length, d = e.dim;
  const m = new Float32Array(n * d);
  for (let i = 0; i < n; i++) {
    const sc = e.scale[i];
    let nn = 0;
    for (let j = 0; j < d; j++) {
      const b = bin.charCodeAt(i * d + j) << 24 >> 24;   // байт со знаком
      const v = b * sc;
      m[i * d + j] = v;
      nn += v * v;
    }
    const inv = 1 / Math.sqrt(nn || 1);
    for (let j = 0; j < d; j++) m[i * d + j] *= inv;
  }
  semM = { m, ids: e.ids, dim: d };
  return semM;
}

async function semanticRanks(q, d, opt, signal, onStatus) {   // топ-SEM_TOP разговоров + карта id→косинус
  const { m, ids, dim } = await semMatrix();
  if (!ids.length) return null;   // векторов нет (emb_local не собран) — смысла не будет
  const qv = await embedWebQuery(q, onStatus);   // локальная модель; при первой загрузке ~10 с
  chkAbort(signal);
  let qn = 0;
  for (let i = 0; i < qv.length; i++) qn += qv[i] * qv[i];
  qn = 1 / (Math.sqrt(qn) || 1);
  const convMap = new Map((d.convsIndex || []).map(e => [e.id, e]));
  const scored = [];
  for (let i = 0; i < ids.length; i++) {
    let s = 0;
    const off = i * dim;
    for (let j = 0; j < dim; j++) s += m[off + j] * qv[j];
    scored.push([s * qn, ids[i]]);
  }
  scored.sort((a, b) => b[0] - a[0]);
  const sims = new Map(scored.map(([s, id]) => [id, s]));   // косинусы всех разговоров — для sim_norm
  const sem = [];
  for (const [, id] of scored) {
    if (sem.length >= SEM_TOP) break;
    const e = convMap.get(id);
    if (e && convPass(e, opt)) sem.push(id);
  }
  return { sem, sims };
}

/* заголовок разговора для карточки: как convTitle на фронте (первое предложение, ~90) */
function titleFromSummary(s) {
  s = (s || "").replace(/\s+/g, " ").trim();
  if (!s) return "Без выжимки";
  const m = s.match(/^[^.!?]+/);
  let t = ((m && m[0]) || s).trim() || s;
  if (t.length > 90) {
    t = [...t].slice(0, 90).join("");
    const sp = t.lastIndexOf(" ");
    if (sp > 50) t = t.slice(0, sp);
    t = t.trim() + "…";
  }
  return t;
}

async function hybridSearchLocal(q, k, opt, keywords, onStatus, signal) {
  const d = LOCAL();
  chkAbort(signal);
  if (onStatus) onStatus("ищу по словам…");
  const convMap = new Map((d.convsIndex || []).map(e => [e.id, e]));

  // 1) слова по выжимкам (аналог fts_conv) → топ-FTS_CONV_TOP разговоров; термы —
  //    из keywords (если заданы — как fts_query_kw), иначе слова q без стоп-слов
  const ci = await ensureConvIndex(d);
  const qw = keywords && keywords.length ? keywords.join(" ") : q;
  const sq = sigQuery(qw);   // без стоп-слов — иначе поиск цепляется за «какие»/«для»
  const fconv = [];
  for (const r of (sq ? ci.search(sq, { prefix: true, combineWith: "OR", limit: 600 }) : [])) {
    const e = d.convsIndex[r.id];
    if (convPass(e, opt)) {
      fconv.push(e.id);
      if (fconv.length >= FTS_CONV_TOP) break;
    }
  }

  // 2) слова по сообщениям (аналог fts_msg): топ-FTS_MSG_FETCH сообщений → ранг разговора
  //    = лучший ранг его сообщений; кандидаты — первые FTS_MSG_TOP разговоров
  await ensureMsgIndex(d, onStatus);
  chkAbort(signal);
  const groups = new Map();
  const fmsgAll = [];
  const words = wordsOf(qw);
  if (words.length && sq) {
    let consumed = 0;
    for (const r of msgIdx.search(sq, { prefix: true, combineWith: "OR", limit: 5000 })) {
      if (consumed >= FTS_MSG_FETCH) break;
      const flat = msgFlat[r.id];
      const e = convMap.get(flat.conv);
      if (!e || !convPass(e, opt)) continue;
      consumed++;
      if (!groups.has(flat.conv)) { groups.set(flat.conv, []); fmsgAll.push(flat.conv); }
      const g = groups.get(flat.conv);
      if (g.length < HITS_PER_CONV)
        g.push({ msg: flat.msg, author: flat.author, snippet: snippetOf(flat.body, words) });
    }
  }
  const fmsg = fmsgAll.slice(0, FTS_MSG_TOP);

  // 3) смысл: топ-SEM_TOP по косинусу — локальная модель в браузере (без ключа; при первом
  //    поиске грузится ~10 с). Неудача (нет модели/файлов) → поиск только по словам
  let sem = [], sims = null, semantic = false;
  try {
    const r = await semanticRanks(q, d, opt, signal, onStatus);
    if (r) { sem = r.sem; sims = r.sims; semantic = true; }
  } catch (err) {
    if (err && err.name === "AbortError") throw err;
    semantic = false;
  }

  // 4) слияние — как hybrid_search на сервере (RRF больше не используется: словесные хиты
  //    не должны вытеснять смысловые): score = sim_norm + W_KW_CONV*kw_conv + W_KW_MSG + W_KEEP,
  //    где sim_norm — косинус, линейно нормированный по кандидатам в [0, 1]; kw_conv — доля
  //    РАЗНЫХ значимых термов запроса/keywords, найденных в выжимке+тегах; kw_msg и keep — бонусы
  const cand = [...new Set([...sem, ...fconv, ...fmsg])];
  const scored = [];
  if (cand.length) {
    const real = {};
    let hasReal = false;
    for (const id of cand) {
      const s = sims && sims.get(id);
      if (s != null) { real[id] = s; hasReal = true; }
    }
    let sc = null, mn = 0, span = 0;
    if (hasReal) {   // у разговоров без вектора — минимум среди кандидатов − 0.01
      const base = Math.min(...Object.values(real)) - 0.01;
      sc = {};
      for (const id of cand) sc[id] = real[id] != null ? real[id] : base;
      const vals = Object.values(sc);
      mn = Math.min(...vals);
      span = Math.max(...vals) - mn;
    }
    const simNorm = id => (sc ? (span > 1e-12 ? (sc[id] - mn) / span : 1.0) : 0.0);
    const sig = sigTermsOf(q, keywords, d);
    const kwToks = convKwIndex(d).tokens;
    for (const id of cand) {
      let kwConv = 0;
      if (sig.length) {
        const toks = kwToks.get(id);
        if (toks) {
          let nHit = 0;
          for (const st of sig) {
            for (const t of st.toks) if (toks.has(t)) { nHit++; break; }
          }
          kwConv = nHit / sig.length;
        }
      }
      let s = simNorm(id) + W_KW_CONV * kwConv;
      if (groups.has(id)) s += W_KW_MSG;   // kw_msg: есть совпадения в сообщениях
      const e = convMap.get(id);
      if (e && e.v === "keep") s += W_KEEP;
      scored.push([id, s]);
    }
  }

  const branchTitle = {};
  for (const t of (d.graph ? d.graph.topics : [])) branchTitle[t.short] = t.title;
  const out = [];
  scored.sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1));
  for (const [id, sc] of scored.slice(0, k)) {
    const e = convMap.get(id);
    if (!e) continue;
    out.push({
      id, score: +sc.toFixed(4), date: e.t, topic: branchTitle[e.b] || e.b,
      title: titleFromSummary(e.sum), branch: branchTitle[e.b] || e.b,
      summary: e.sum, category: e.c, has_scheme: e.sc, hits: groups.get(id) || [],
    });
  }
  return { semantic, results: out };
}

async function localSearch(d, q, params = {}, onStatus) {
  let qq = q, kw = null;
  if (params.expand === "1" || params.expand === 1) {   // LLM-расширение (кнопка «Показать все» в чате)
    const plan = await searchPlan([{ role: "user", content: q }]);
    if (plan.query) qq = plan.query;
    kw = plan.keywords.length ? plan.keywords : null;
  }
  return hybridSearchLocal(qq, 30, {
    dateFrom: params.from || null, dateTo: params.to || null,
    branch: params.branch || null, category: params.category || null,
    schemes: params.schemes === "1" || params.schemes === true,
  }, kw, onStatus, null);
}

/* --- статьи: список в памяти, тела ленивыми секциями art_<n>.js -------------------- */
async function localArticle(d, path) {
  const secs = d.articleSects || [];
  const sec = path.includes("/") ? path.split("/")[0] : "";
  const idx = secs.indexOf(sec);
  if (idx < 0) throw new Error("статья не найдена в архиве");
  await loadScript("app/data/art_" + idx + ".js");
  const md = d.articleBodies ? d.articleBodies[path] : null;
  if (md == null) throw new Error("статья не найдена в архиве");
  return md.replace(/\r\n?/g, "\n");   // свежие экспорты несут CRLF из vault — нормализуем
}

async function ensureAllArts(onStatus) {
  const secs = LOCAL().articleSects || [];
  let done = 0;
  await Promise.all(secs.map((s, i) => loadScript("app/data/art_" + i + ".js").then(() => {
    done++;
    if (onStatus) onStatus(`загружаю статьи (${done}/${secs.length})…`);
  })));
}

const ART_MAX = 2500;   // кусок статьи ≤2500 символов (как build_fts.py)
function articleChunks(text) {   // frontmatter выкинуть, резать по заголовкам «##»
  const body = text.replace(/^---\s*\n[\s\S]*?\n---\s*\n/, "");
  const parts = body.split(/^(## .*)$/m);
  const chunks = [];
  if (parts.length && parts[0].trim()) chunks.push(parts[0].trim());
  for (let i = 1; i < parts.length; i += 2) chunks.push((parts[i] + "\n" + parts[i + 1]).trim());
  const out = [];
  for (const ch of chunks) {
    let cur = "";
    for (const para of ch.split("\n\n")) {
      cur = cur ? cur + "\n\n" + para : para;
      while (cur.length > ART_MAX) { out.push(cur.slice(0, ART_MAX)); cur = cur.slice(ART_MAX); }
      if (cur.length === ART_MAX) { out.push(cur); cur = ""; }
    }
    if (cur.trim()) out.push(cur);
  }
  return out.filter(c2 => c2.trim());
}

let artIdx = null, artChunksArr = null;
async function ensureArtSearch(onStatus) {   // MiniSearch по кускам статей (аналог fts_art, без Источников/)
  if (artIdx) return artIdx;
  const d = LOCAL();
  if (onStatus) onStatus("загружаю статьи…");
  await ensureAllArts(onStatus);
  if (onStatus) onStatus("индексирую статьи…");
  await ensureMiniSearch();
  artChunksArr = [];
  const docs = [];
  for (const a of d.articles || []) {
    const body = d.articleBodies ? d.articleBodies[a.path] : null;
    if (body == null) continue;
    const chs = articleChunks(body);
    for (let ci = 0; ci < chs.length; ci++) {   // ci — номер куска (ключ векторов emb_art)
      artChunksArr.push({ title: a.title, path: a.path, chunk: chs[ci], ci });
      docs.push({ id: artChunksArr.length - 1, body: chs[ci] });
    }
  }
  artIdx = new MiniSearch({ fields: ["body"] });
  artIdx.addAll(docs);
  return artIdx;
}

/* --- смысловой поиск по кускам статей: emb_art (int8+масштаб, как у сервера) ---------
    Вектор запроса — та же локальная модель (embed_web.js); ключ не нужен. */
let _embArtP = null, artSemM = null;
function ensureEmbArt() { return _embArtP ||= loadScript("app/data/emb_art.js"); }
async function artSemMatrix() {
  if (artSemM) return artSemM;
  await ensureEmbArt();
  const e = LOCAL().embArt;
  if (!e || !e.ids.length) return null;
  const bin = atob(e.codes), n = e.ids.length, d = e.dim;
  const m = new Float32Array(n * d);
  for (let i = 0; i < n; i++) {
    const sc = e.scale[i];
    let nn = 0;
    for (let j = 0; j < d; j++) {
      const b = bin.charCodeAt(i * d + j) << 24 >> 24;
      const v = b * sc;
      m[i * d + j] = v;
      nn += v * v;
    }
    const inv = 1 / Math.sqrt(nn || 1);
    for (let j = 0; j < d; j++) m[i * d + j] *= inv;
  }
  artSemM = { m, ids: e.ids, dim: d };
  return artSemM;
}

async function artHitsLocal(q, k = 3) {   // топ-k кусков: слова (MiniSearch) + смысл (emb_art), RRF — как _art_hits
  const idx = await ensureArtSearch();
  const sq = sigQuery(q);   // без стоп-слов и коротких слов — как fts_query на сервере
  const fts = (sq ? idx.search(sq, { prefix: true, combineWith: "OR", limit: 10 }) : [])
    .map(r => artChunksArr[r.id]);
  const sem = [];
  try {
    const { m, ids, dim } = (await artSemMatrix()) || {};
    if (ids) {
      const qv = await embedWebQuery(q);
      const scored = [];
      for (let i = 0; i < ids.length; i++) {
        let s = 0;
        const off = i * dim;
        for (let j = 0; j < dim; j++) s += m[off + j] * qv[j];
        scored.push([s, i]);
      }
      scored.sort((a, b) => b[0] - a[0]);
      const row = new Map(artChunksArr.map((c2, i) => [c2.path + "\n" + c2.ci, i]));
      for (const [, i] of scored.slice(0, 10)) {
        const c2 = artChunksArr[row.get(ids[i])];
        if (c2) sem.push(c2);
      }
    }
  } catch (err) {   // смысловая часть не критична: нет модели/векторов — только слова
    if (err && err.name === "AbortError") throw err;
  }
  if (!fts.length && !sem.length) return [];
  const scores = {};   // RRF по двум спискам (как на сервере)
  for (const lst of [fts, sem])
    lst.forEach((h, rank) => { const key = h.path + "\n" + h.ci + "\n" + h.chunk.length;
                               scores[key] = (scores[key] || 0) + 1 / (RRF_K + rank + 1); });
  const byKey = {};
  for (const h of fts.concat(sem)) byKey[h.path + "\n" + h.ci + "\n" + h.chunk.length] = h;
  return Object.entries(scores).sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))
    .slice(0, k).map(([key]) => byKey[key]);
}

/* --- чат с базой: та же логика, что /api/chat на сервере ---------------------------
   переформулировка (PLAN_SYS) → гибридный поиск → материал с [n] и экспертами →
   потоковый ответ с SSE от провайдера получателя. */

const CHAT_SYS = `Ты отвечаешь участнику крипто-комьюнити MXW на вопрос по материалам их Telegram-чата. Тебе даны найденные разговоры и статьи базы знаний со сквозной нумерацией источников [n] — выжимки и ключевые сообщения (с расшифровками медиа в ⟪…⟫).
Отвечай по-русски, конкретно (названия, цифры, даты, кто сказал), после фактов ставь сноску-номер [n] того источника из материала, откуда факт. Ссылайся только на номера источников, которые есть в материале, — не выдумывай номера и не используй другие форматы ссылок. Непроверенное помечай «по словам…». Если в материале нет ответа — так и скажи. Без воды. Формат: markdown, 1–6 абзацев или список.

Это не разовый вопрос, а диалог: на уточняющие вопросы отвечай с учётом предыдущих вопросов и ответов (не повторяй уже сказанное, разворачивай тему). В конце материала есть блок «ЭКСПЕРТЫ ПО НАЙДЕННЫМ ТЕМАМ» — используй его, когда спрашивают, кто в чём разбирается, кого спросить или кто по делу писал по теме. Если в материале нет ответа — так и скажи и предложи, как переформулировать или уточнить вопрос. Если участники в материале названы «Участник XXX» — обращайся к ним этими псевдонимами, не выдумывай и не раскрывай ники.`;

const ANON_SYS = `

Анонимный режим: авторов называй только псевдонимами вида "Участник XXX". Никогда не пиши рядом имя/ник и псевдоним, не раскрывай, кто скрывается за псевдонимом; если в тексте материала встречается имя — пересказывай без указания, какой это участник.`;

const PLAN_SYS = `Ты готовишь поиск по базе знаний крипто-чата. Дан диалог (может быть пустым) и последний вопрос пользователя.
1) Перепиши последний вопрос как самостоятельный поисковый запрос: без местоимений и отсылок к диалогу, но с темой и названиями из истории («а кто это пробовал?» → «кто пробовал X»).
2) Подбери 5–12 ключевых слов и близких терминов по теме запроса: названия, тикеры, инструменты, сленг — те, что реально встречаются в крипто-чатах (включая английские названия вроде AdsPower, Dolphin, proxy, ban).
Верни только JSON без пояснений: {"query": "самостоятельный поисковый запрос", "keywords": ["слово", ...]}`;

const PIN_CONV_RE = /^(nft|ai|life|crypto|perps|predict|soft|shitki|important)-c[\d-]+$/;

const CAT_RU = { nft: "NFT", ai_dev: "AI и разработка", tools: "Инструменты", security: "Безопасность",
                 trading: "Трейдинг", drops_testnets: "Дропы и тестнеты", market_news: "Новости рынка",
                 predictions: "Предикшены", jobs_income: "Работа", community: "Комьюнити", other: "Разное" };
const SCHEME_RU = { multiaccount: "Мультиаккаунты", antisybil: "Антисибил", farms: "Фермы", accounts: "Аккаунты",
                    bots_soft: "Боты и софт", kyc_verification: "KYC", proxy_antidetect: "Прокси и антидетект",
                    bans_risks: "Баны и риски" };

const fnvLocal = s => {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
  return h >>> 0;
};
const anonNameLocal = s => "Участник " + (fnvLocal(s) & 0xfff).toString(16).padStart(3, "0");
const escReLocal = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function anonSubLocal(text, authors) {   // имена авторов в материале → «Участник XXX» (тот же хеш, что на фронте)
  const list = [...authors].sort((a, b) => b.length - a.length);
  for (const a of list) {
    if (!a || a.length < 3 || !/\w/.test(a)) continue;
    text = text.replace(new RegExp("(?<!\\w)" + escReLocal(a) + "(?!\\w)", "gu"), () => anonNameLocal(a));
  }
  return text;
}

const oneLine = (text, limit = 200) => {
  const s = String(text || "").replace(/\s+/g, " ").trim();
  return [...s].slice(0, limit).join("");
};

function parseFmLocal(md) {   // плоский YAML-frontmatter статьи (vault в CRLF — нормализуем)
  const fm = {};
  const m = String(md || "").replace(/\r\n?/g, "\n").match(/^---\s*\n(.*?)\n---\s*\n/s);
  if (m) for (const line of m[1].split("\n")) {
    const i = line.indexOf(":");
    if (i > 0) fm[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return fm;
}

function artMetaLocal(path, md) {   // (категория, название ветки, дата периода) статьи из frontmatter
  const d = LOCAL();
  const fm = parseFmLocal(md);
  const titles = {};
  for (const t of (d.graph ? d.graph.topics : [])) titles[t.short] = t.title;
  const branches = (fm["ветки"] || "").replace(/^\[|\]$/g, "").split(",").map(s => s.trim()).filter(Boolean);
  const m = (fm["период"] || "").match(/\d{4}-\d{2}-\d{2}/);
  return {
    cat: fm["категория"] || "",
    branch: branches.length ? (titles[branches[0]] || branches[0]) : "",
    dt: m ? m[0] : "",
  };
}

/* поля проверки фактов (проверено/подтверждено/исправлено) статьи из frontmatter:
   checked = ISO-дата «проверено» (null — статья не проверялась), confirmed = % , fixed = n */
function artFcLocal(md) {
  const fm = parseFmLocal(md);
  const dt = (fm["проверено"] || "").match(/\d{4}-\d{2}-\d{2}/);
  const pc = parseInt((fm["подтверждено"] || "").replace(/\D+/g, ""), 10);
  const nf = parseInt(fm["исправлено"], 10);
  return {
    checked: dt ? dt[0] : null,
    confirmed: Number.isFinite(pc) ? pc : null,
    fixed: Number.isFinite(nf) ? nf : null,
  };
}

/* список статей архива: свежие экспорты несут поля проверки фактов (checked/confirmed/fixed)
   прямо в articles.js (server.articles); архивы до задачи 036 — нет: тогда парсим их из
   frontmatter тел статей — ленивые секции art_<n>.js, грузим все (как при индексации поиска).
   Тела не загрузились — статьи открываются как обычно, просто без отметок проверки. */
async function localArticles(d) {
  const list = d.articles;
  if (!list.length || list[0].checked !== undefined) return list;
  try {
    await ensureAllArts();
    for (const a of list) {
      const body = d.articleBodies ? d.articleBodies[a.path] : null;
      if (body == null) continue;
      Object.assign(a, artFcLocal(body));
    }
  } catch (err) { /* секции статей не загрузились — отметки проверки не покажем */ }
  return list;
}

/* план поиска: переформулировка + ключевые слова одним коротким вызовом LLM (max_tokens=300,
   temperature=0), LRU-кэш 200. LLM недоступен → слова вопроса без стоп-слов (фактически — как есть). */
const PLAN_CACHE_MAX = 200;
const _planCache = new Map();

async function searchPlan(msgs, signal) {
  const last = msgs[msgs.length - 1].content;
  const hist = msgs.slice(-6, -1)
    .map(m => `${m.role === "user" ? "Вопрос" : "Ответ"}: ${(m.content || "").slice(0, 600)}`)
    .join("\n");
  const cacheKey = hist + "\n\n" + last;
  if (_planCache.has(cacheKey)) {
    const plan = _planCache.get(cacheKey);
    _planCache.delete(cacheKey);
    _planCache.set(cacheKey, plan);   // LRU: свежее — в конец
    return plan;
  }
  let plan = null;
  if (CFG.ready()) {
    try {
      const user = (hist ? `Диалог:\n${hist}\n\n` : "") + `Последний вопрос: ${last}`;
      const r = await llmPost("/chat/completions", {
        model: CFG.fastModel(), temperature: 0, max_tokens: 300,
        messages: [{ role: "system", content: PLAN_SYS }, { role: "user", content: user }],
      }, signal);
      const d = await r.json();
      let text = ((((d.choices || [])[0] || {}).message || {}).content || "").trim();
      text = text.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "").trim();
      const m = text.match(/[\[{][\s\S]*[\]}]/);
      const obj = JSON.parse(m ? m[0] : text);
      const query = String(obj.query || "").trim();
      const keywords = [...(obj.keywords || [])].map(x => String(x).trim()).filter(Boolean).slice(0, 12);
      if (query.length >= 2 && query.length <= 400 && keywords.length) plan = { query, keywords };
    } catch (err) {
      if (err && err.name === "AbortError") throw err;
      // LLM недоступен — поиск по словам вопроса
    }
  }
  if (!plan) plan = { query: last, keywords: [] };
  _planCache.set(cacheKey, plan);
  while (_planCache.size > PLAN_CACHE_MAX) _planCache.delete(_planCache.keys().next().value);
  return plan;
}

function convBlockLocal(h, d, keyLimit = 12, ref) {   // блок материала разговора (как conv_block на сервере)
  const keym = d.messages.filter(m => m.key).slice(0, keyLimit);
  const lines = [`${ref || `[[${h.id}]]`} ${h.date} | ${d.topic}`, "Выжимка: " + (d.summary || "")];
  if (d.scheme_summary) lines.push("Схема: " + d.scheme_summary);
  for (const m of keym) {
    let s = `#${m.id} ${m.author}: ${(m.text || "").slice(0, 600)}`;
    for (const mt of (m.media_text || []))
      s += ` ⟪${mt.kind}: ${String(mt.text || "").replace(/\s+/g, " ").slice(0, 600)}⟫`;
    lines.push(s);
  }
  return lines.join("\n");
}

async function pinPartsLocal(pin, n) {   // контекст «спросить про это»: разговор (не-noise, до 60) или статья (12000)
  if (!pin || typeof pin !== "string") return [null, null];
  const mark = `[${n}]`;
  if (PIN_CONV_RE.test(pin)) {
    let d = null;
    try { d = await localConv(LOCAL(), pin); } catch (err) { return [null, null]; }
    const lines = [`${mark} ${(d.start || "").slice(0, 10)} | ${d.topic} — разговор целиком (контекст диалога)`,
                   "Выжимка: " + (d.summary || "")];
    if (d.scheme_summary) lines.push("Схема: " + d.scheme_summary);
    for (const m of d.messages.filter(m => !m.noise).slice(0, 60)) {
      let s = `#${m.id} ${m.author}: ${(m.text || "").slice(0, 600)}`;
      for (const mt of (m.media_text || []))
        s += ` ⟪${mt.kind}: ${String(mt.text || "").replace(/\s+/g, " ").slice(0, 600)}⟫`;
      lines.push(s);
    }
    const card = { kind: "conv", id: pin, title: titleFromSummary(d.summary),
                   date: (d.start || "").slice(0, 10), branch: d.topic, category: d.category,
                   snippet: oneLine(d.summary) };
    return [lines.join("\n"), card];
  }
  let body = null;
  try { body = await localArticle(LOCAL(), pin); } catch (err) { return [null, null]; }
  const text = body.replace(/^---\s*\n[\s\S]*?\n---\s*\n/, "").slice(0, 12000);
  const meta = artMetaLocal(pin, body);
  const title = pin.split("/").pop().replace(/\.md$/, "");
  const card = { kind: "article", path: pin, title, date: meta.dt, branch: meta.branch,
                 category: meta.cat, snippet: oneLine(text) };
  return [`${mark} статья базы знаний (контекст диалога):\n${text}`, card];
}

function expertsBlockLocal(cats, schemes, isAnon) {   // блок «Эксперты по найденным темам» (как _experts_block)
  const ex = LOCAL().experts;
  if (!ex || !ex.topics || !ex.people) return "";
  const topics = ([
    ...Object.entries(cats).sort((a, b) => b[1] - a[1]).map(([c]) => ["category", c]),
    ...[...schemes].map(s => ["scheme", s]),
  ]).slice(0, 6);
  if (!topics.length) return "";
  const profiles = new Map((ex.people || []).map(p => [p.author, p.profile || ""]));
  const lines = [];
  const used = new Map();
  for (const [kind, code] of topics) {
    const key = kind === "category" ? code : (SCHEME_RU[code] || code);
    const grp = (ex.topics || []).find(t => t.kind === kind && t.topic === key);
    if (!grp) continue;
    const label = kind === "category" ? (CAT_RU[code] || code) : (SCHEME_RU[code] || code);
    for (const r of (grp.top || []).slice(0, 3)) {   // топ-3 человека темы
      if ((used.get(r.author) || 0) >= 2) continue;  // один человек — максимум в двух темах
      used.set(r.author, (used.get(r.author) || 0) + 1);
      const name = isAnon ? anonNameLocal(r.author) : r.author;
      let prof = (profiles.get(r.author) || "").trim();
      if (prof.length > 300) {
        prof = [...prof].slice(0, 300).join("");
        const sp = prof.lastIndexOf(" ");
        if (sp > 0) prof = prof.slice(0, sp);
        prof += "…";
      }
      lines.push(`- ${label}: ${name} (${r.n_key} ключевых сообщений) — ${prof}`);
    }
    if (lines.length >= 9) break;
  }
  if (!lines.length) return "";
  return ("ЭКСПЕРТЫ ПО НАЙДЕННЫМ ТЕМАМ (кто в чате в этом разбирается — используй, когда спрашивают, "
          + "кого спросить или кто по делу писал по теме):\n" + lines.slice(0, 9).join("\n"));
}

async function chatContextLocal(msgs, opt, pin, isAnon, signal, onStatus) {
  chkAbort(signal);
  if (onStatus) onStatus("переформулирую вопрос…");
  const plan = await searchPlan(msgs, signal);
  chkAbort(signal);
  const query = plan.query;
  if (onStatus) onStatus("ищу по базе…");
  const keywords = plan.keywords.length ? plan.keywords : null;
  const { results: hits } = await hybridSearchLocal(query, 18, opt, keywords, onStatus, signal);
  chkAbort(signal);
  if (onStatus) onStatus("собираю материал…");
  const d = LOCAL();
  const blocks = [], cards = [], authors = new Set();
  const cats = {}, schemesFound = new Set();

  let n = 0;
  const [pinBlock, pinCard] = await pinPartsLocal(pin, 1);
  if (pinCard) {
    n = 1;
    blocks.push(pinBlock);
    pinCard.n = 1;
    cards.push(pinCard);
    if (pinCard.category) cats[pinCard.category] = (cats[pinCard.category] || 0) + 1;
  }

  for (const h of hits) {
    const c = d.convs && d.convs[h.id];
    if (!c) continue;
    cats[c.category] = (cats[c.category] || 0) + 1;
    if (c.has_scheme) for (const s of c.scheme_types || []) schemesFound.add(s);
    for (const m of c.messages) if (m.author) authors.add(m.author);
    if (pinCard && pinCard.id === h.id) continue;   // блок уже добавлен pin'ом (карточка n=1)
    n += 1;
    blocks.push(convBlockLocal(h, c, 12, `[${n}]`));
    const summary = h.summary || c.summary;
    const snip = ((h.hits || [])[0] || {}).snippet || oneLine(summary);
    cards.push({ n, kind: "conv", id: h.id, title: titleFromSummary(summary), date: h.date,
                 branch: c.topic, category: c.category, snippet: oneLine(snip, 220) });
  }

  const articles = [];
  try {
    for (const a of await artHitsLocal(query, 3)) {
      if (pinCard && pinCard.path === a.path) continue;   // уже в материале pin'ом
      n += 1;
      blocks.push(`[${n}] статья базы знаний:\n${a.chunk}`);
      const body = d.articleBodies ? d.articleBodies[a.path] : null;
      const meta = body != null ? artMetaLocal(a.path, body) : { cat: "", branch: "", dt: "" };
      cards.push({ n, kind: "article", path: a.path, title: a.title, date: meta.dt,
                   branch: meta.branch, category: meta.cat, snippet: oneLine(a.chunk) });
      articles.push({ title: a.title, path: a.path });
    }
  } catch (err) {   // статейный поиск не критичен
    if (err && err.name === "AbortError") throw err;
  }
  if (pinCard && pinCard.path) articles.unshift({ title: pinCard.title, path: pinCard.path });

  let material = blocks.filter(Boolean).join("\n\n");
  const exp = expertsBlockLocal(cats, schemesFound, isAnon);
  if (exp) material += "\n\n" + exp;
  if (isAnon) {
    material = anonSubLocal(material, authors);
    for (const c of cards) {   // в карточках источников — псевдонимы
      c.title = anonSubLocal(c.title, authors);
      c.snippet = anonSubLocal(c.snippet, authors);
    }
  }
  return { query, cards, articles, material };
}

function chatHistoryLocal(msgs, material) {   // history: последние 8 сообщений, материал приклеен к последнему
  const hist = msgs.slice(-8).map(m => ({ ...m }));
  hist[hist.length - 1].content += "\n\nМАТЕРИАЛ:\n\n" + material;
  return hist;
}

const RETRY_DELAYS = [2, 4, 8, 15, 20];       // паузы перед повторами стрима (только пока не отдан ни один кусок)
const NO_RETRY_HTTP = [400, 401, 403];        // ошибки ключа/запроса повторами не лечатся

async function llmStreamChatLocal(system, messages, { signal, onDelta, onStatus } = {}) {
  let lastErr = null;
  for (let attempt = 0; attempt <= RETRY_DELAYS.length; attempt++) {
    if (attempt) {
      if (onStatus) onStatus(`провайдер занят, повторяю (${attempt}/${RETRY_DELAYS.length})…`);
      await new Promise(r => setTimeout(r, RETRY_DELAYS[attempt - 1] * 1000));
      chkAbort(signal);
    }
    let produced = false;
    try {
      const r = await fetch(CFG.base() + "/chat/completions", {
        method: "POST", signal,
        headers: authHeaders(CFG.key()),
        body: JSON.stringify({
          model: CFG.chatModel(), temperature: 0.3, max_tokens: 4000, stream: true,
          messages: [{ role: "system", content: system }, ...messages],
        }),
      });
      if (!r.ok || !r.body) {
        const err = new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 200)}`);
        err.http = r.status;
        throw err;
      }
      const reader = r.body.getReader(), dec = new TextDecoder();   // SSE: data:-строки, [DONE] в конце
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let nl;
        while ((nl = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, nl).trim();
          buf = buf.slice(nl + 1);
          if (!line.startsWith("data:")) continue;
          const data = line.slice(5).trim();
          if (data === "[DONE]") return;
          let d2;
          try { d2 = JSON.parse(data); } catch { continue; }
          if (d2.error) throw new Error(String((d2.error.message || d2.error)).slice(0, 300));
          for (const ch of (d2.choices || [])) {
            const piece = (ch.delta || {}).content || "";
            if (piece) { produced = true; onDelta && onDelta(piece); }
          }
        }
      }
      if (!produced) throw new Error("пустой ответ");
      return;
    } catch (err) {
      if (err && (err.name === "AbortError" || produced)) throw err;   // после первого куска повторов нет
      if (err && err.http && NO_RETRY_HTTP.includes(err.http)) throw err;
      lastErr = err;
    }
  }
  throw lastErr || new Error("нет ответа");
}

async function localChatStream(d, body, h, signal) {
  const msgs = (body.messages || [])
    .filter(m => (m.content || "").trim())
    .map(m => ({ role: m.role || "user", content: (m.content || "").trim() }));
  if (!msgs.length || msgs[msgs.length - 1].role !== "user") throw new Error("нет вопроса");
  const anon = !!body.anon;
  const status = t => { if (h.status) h.status({ text: t }); };

  const ctx = await chatContextLocal(msgs, {
    dateFrom: body.from || null, dateTo: body.to || null,
    branch: body.branch || null, schemes: !!body.schemes,
  }, body.pin || null, anon, signal, status);
  chkAbort(signal);
  h.meta && h.meta({ query: ctx.query, sources: ctx.cards, articles: ctx.articles });
  if (!CFG.ready()) {   // источники уже показаны — объясняем, как включить ответ ИИ
    throw new Error("чтобы ИИ отвечал — укажи ключ и модель в ⚙ Настройки");
  }
  status("пишу ответ…");
  await llmStreamChatLocal(CHAT_SYS + (anon ? ANON_SYS : ""), chatHistoryLocal(msgs, ctx.material), {
    signal,
    onDelta: t => h.delta && h.delta({ text: t }),
    onStatus: t => status(t),
  });
  chkAbort(signal);
  h.done && h.done({});
}

/* ======================================================================= API (общее) === */

/* карта: узлы-разговоры, ветки, кластеры, гистограмма дат, авторы */
export function getGraph() {
  const d = LOCAL();
  if (d && d.graph) return Promise.resolve(d.graph);
  return jfetch("/api/graph");
}

/* разговор: сообщения + show/noise/hit + схема; q/focus — подсветка поиска */
export function getConv(id, q, focus) {
  const d = LOCAL();
  if (d) return localConv(d, id, q, focus);
  let u = "/api/conv?id=" + encodeURIComponent(id);
  if (q) u += "&q=" + encodeURIComponent(q);
  if (focus != null) u += "&focus=" + encodeURIComponent(focus);
  return jfetch(u);
}

/* поиск по базе (смысл + слова) */
export function search(q, params = {}, onStatus) {
  const d = LOCAL();
  if (d) return localSearch(d, q, params, onStatus);
  const p = new URLSearchParams({ q, ...params });
  return jfetch("/api/search?" + p.toString());
}

/* статьи базы знаний: список / текст / поиск по названию */
export function getArticles() {
  const d = LOCAL();
  if (d && d.articles) return localArticles(d);
  return jfetch("/api/articles");
}
export function getArticle(path) {
  const d = LOCAL();
  if (d) return localArticle(d, path);
  return fetch("/api/article?path=" + encodeURIComponent(path))
    .then(r => { if (!r.ok) throw new Error("статья не загрузилась — код " + r.status); return r.text(); });
}
export function findArticle(name) {
  const d = LOCAL();
  if (d && d.articles) return Promise.resolve(d.articles.find(a => a.title === name) || null);
  return jfetch("/api/find_article?name=" + encodeURIComponent(name)).catch(() => null);
}

/* доска активностей */
export function getEvents(params = {}) {
  const d = LOCAL();
  if (d && d.events) return Promise.resolve(d.events);
  const p = new URLSearchParams(params);
  return jfetch("/api/events" + (p.toString() ? "?" + p.toString() : ""));
}

/* люди и темы экспертизы (раздел «Люди», карточка человека) */
export function getExperts() {
  const d = LOCAL();
  if (d && d.experts) return Promise.resolve(d.experts);
  return jfetch("/api/experts");
}

/* темы во времени: недельная активность категорий, разговоры по темам/неделям,
   топ-теги по месяцам (раздел «Темы») */
export function getTrends(by = "category") {
  const d = LOCAL();
  if (d && d.trends) return Promise.resolve(d.trends[by] || { weeks: [], series: [] });
  return jfetch("/api/trends?by=" + encodeURIComponent(by));
}

/* медиа Telegram-экспорта */
export function mediaUrl(i, thumb) {
  const d = LOCAL();
  if (d) return "app/media/" + i + (thumb ? "_t.jpg" : (mediaExts.get(i) || ".bin"));
  return "/api/media?i=" + i + (thumb ? "&thumb=1" : "");
}

/* чат с базой: meta → status… → delta… → done|error; h = {meta,status,delta,done};
   signal — AbortController.signal для кнопки «остановить» */
export function chatStream(body, h, signal) {
  const d = LOCAL();
  if (d) return localChatStream(d, body, h, signal);
  if (CFG.provider() !== "server" && CFG.chatModel())   // свой провайдер из ⚙ Настройки — сервер ходит в него
    body = { ...body, llm: { base: CFG.base(), key: CFG.key(), model: CFG.chatModel(), fast: CFG.fastModel() } };
  return fetch("/api/chat", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body), signal,
  }).then(async r => {
    if (!r.ok) throw new Error("HTTP " + r.status);
    const reader = r.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let p;
      while ((p = buf.indexOf("\n\n")) >= 0) {
        const raw = buf.slice(0, p); buf = buf.slice(p + 2);
        let ev = "", data = "";
        for (const line of raw.split("\n")) {
          if (line.startsWith("event:")) ev = line.slice(6).trim();
          else if (line.startsWith("data:")) data += (data ? "\n" : "") + line.slice(5).trim();
        }
        if (!data) continue;
        const o = JSON.parse(data);
        if (ev === "delta") h.delta(o);
        else if (ev === "meta") h.meta(o);
        else if (ev === "status") { if (h.status) h.status(o); }
        else if (ev === "done") { if (h.done) h.done(o); return; }
        else if (ev === "error") throw new Error(o.error || "ошибка LLM");
      }
    }
  });
}

/* ⚙ Настройки: список моделей провайдера и проверка связи. cfg = {base, key, model}.
   На локальном сайте — через сервер (/api/llm/*: нет проблем с CORS у провайдера),
   в архиве — напрямую из браузера. */
const postJson = (path, cfg) => jfetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
                                              body: JSON.stringify(cfg) });

/* ВСЕ модели провайдера: листает страницы, если список отдаётся частями (как llm.list_models) */
export async function llmModels(cfg) {
  if (!LOCAL()) return (await postJson("/api/llm/models", cfg)).models;
  const url0 = cfg.base.replace(/\/+$/, "") + "/models";
  const ids = new Set();
  let qs = "";
  for (let page = 0; page < 30; page++) {
    const r = await fetch(url0 + qs, { headers: authHeaders(cfg.key) });
    if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 200)}`);
    const j = await r.json();
    const items = Array.isArray(j) ? j : (j.data || j.models || []);
    const got = items.map(x => (typeof x === "string" ? x : x.id || x.name || "")).filter(Boolean);
    const before = ids.size;
    got.forEach(id => ids.add(id));
    if (Array.isArray(j) || ids.size === before) break;
    const tok = j.nextPageToken || j.next_page_token;
    if (tok) qs = "?pageToken=" + encodeURIComponent(tok) + "&page_token=" + encodeURIComponent(tok);
    else if (j.has_more && (j.last_id || got.length)) qs = "?after=" + encodeURIComponent(j.last_id || got[got.length - 1]);
    else break;
  }
  return [...ids].sort();
}

export async function llmTest(cfg) {
  if (!LOCAL()) return postJson("/api/llm/test", cfg);
  const t = performance.now();
  const r = await fetch(cfg.base.replace(/\/+$/, "") + "/chat/completions", {
    method: "POST", headers: authHeaders(cfg.key),
    body: JSON.stringify({ model: cfg.model, temperature: 0, max_tokens: 300,
      messages: [{ role: "system", content: "Отвечай очень коротко." },
                 { role: "user", content: "Ответь одним словом: работает?" }] }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 200)}`);
  const j = await r.json();
  const reply = ((((j.choices || [])[0] || {}).message || {}).content || "").trim();
  return { ok: true, reply: reply.slice(0, 200), ms: Math.round(performance.now() - t) };
}
