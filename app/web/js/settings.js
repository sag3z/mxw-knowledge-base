/* settings.js — «Настройки» в боковой панели: какой ИИ — «мозг» чата.
   Любой OpenAI-совместимый провайдер (OpenRouter, OpenAI, DeepSeek, Gemini, Groq, Ollama…):
   адрес API, ключ, модель ответа и (по желанию) быстрая модель для переформулировки запроса.
   Всё хранится в localStorage этого браузера. В архиве запросы идут из браузера прямо
   в указанный API; на локальном сайте — через свой сервер (127.0.0.1), который пересылает
   их провайдеру. Пункт «Сервер» (только на локальном сайте) — ключи сервера из .llm_env.
   Смысловой поиск по базе работает без ключа — локальная модель прямо в браузере/на сервере. */
import { icon } from "./icons.js";
import { isStandalone, llmModels, llmTest } from "./api.js";
import { $, esc } from "./ui.js";

const KEYS = { provider: "mxw_provider", key: "mxw_key", base: "mxw_base",
               chat: "mxw_chat_model", fast: "mxw_fast_model" };

/* пресеты: адрес API, где взять ключ, модель по умолчанию (пусто — выбрать из списка) */
const PROVIDERS = [
  { id: "server", name: "Сервер (ключи из .llm_env)", serverOnly: true },
  { id: "openrouter", name: "OpenRouter — сотни моделей по одному ключу", base: "https://openrouter.ai/api/v1",
    keys: "https://openrouter.ai/keys", model: "deepseek/deepseek-chat" },
  { id: "openai", name: "OpenAI", base: "https://api.openai.com/v1", keys: "https://platform.openai.com/api-keys" },
  { id: "deepseek", name: "DeepSeek", base: "https://api.deepseek.com/v1", keys: "https://platform.deepseek.com/",
    model: "deepseek-flash" },
  { id: "gemini", name: "Google Gemini", base: "https://generativelanguage.googleapis.com/v1beta/openai",
    keys: "https://aistudio.google.com/apikey" },
  { id: "groq", name: "Groq", base: "https://api.groq.com/openai/v1", keys: "https://console.groq.com/keys" },
  { id: "mistral", name: "Mistral", base: "https://api.mistral.ai/v1", keys: "https://console.mistral.ai/" },
  { id: "xai", name: "xAI (Grok)", base: "https://api.x.ai/v1", keys: "https://console.x.ai/" },
  { id: "together", name: "Together AI", base: "https://api.together.xyz/v1", keys: "https://api.together.ai/" },
  { id: "ollama", name: "Ollama (на этом компьютере, без ключа)", base: "http://localhost:11434/v1", local: true },
  { id: "lmstudio", name: "LM Studio (на этом компьютере, без ключа)", base: "http://localhost:1234/v1", local: true },
  { id: "custom", name: "Свой адрес (любой OpenAI-совместимый)", base: "" },
];
const byId = id => PROVIDERS.find(p => p.id === id);

/* имена, которые API принимает, но в /models не отдаёт (проверено запросами 26.11.2026):
   у DeepSeek две модели — deepseek-flash и deepseek-v4-pro, остальное — старые имена */
const ALIASES = {
  "https://api.deepseek.com/v1": { "deepseek-chat": "deepseek-flash", "deepseek-reasoner": "deepseek-flash",
                                   "deepseek-v4-flash": "deepseek-flash", "deepseek-coder": "deepseek-flash" },
};
/* что на самом деле стоит за общим именем (по документации провайдера, api-docs.deepseek.com, 17.10.2026);
   поиск в списке ищет и по этим подписям — «4.1» или «1017» находят deepseek-flash */
const NOTES = {
  "https://api.deepseek.com/v1": { "deepseek-flash": "DeepSeek-V4.1-Flash-1017 — самая новая",
                                   "deepseek-v4-pro": "DeepSeek-V4-Pro" },
};
const curBase = () => ($("#cfgBase").value || "").trim().replace(/\/+$/, "");
const aliasesNow = () => ALIASES[curBase()] || {};
const notesNow = () => NOTES[curBase()] || {};
const withAliases = ids => {
  if (!ids.length) return ids;
  const extra = Object.keys(aliasesNow()).filter(a => !ids.includes(a));
  return extra.length ? [...ids, ...extra] : ids;
};

const get = k => { try { return localStorage.getItem(k) || ""; } catch { return ""; } };
const put = (k, v) => {
  v = (v || "").trim();
  try { v ? localStorage.setItem(k, v) : localStorage.removeItem(k); } catch { /* приватный режим */ }
};

let wrap = null;

export function settingsInit() {
  const btn = document.createElement("button");
  btn.id = "setBtn";
  btn.className = "btn ghost anon";
  btn.title = "Какой ИИ отвечает в чате: провайдер, ключ, модели";
  btn.innerHTML = `<span class="ic">${icon("settings")}</span><span class="lbl">Настройки ИИ</span>`;
  btn.addEventListener("click", openSettings);
  $("#anonBtn").before(btn);
  /* Esc закрывает только окно настроек (перехват до общих обработчиков ui.js) */
  document.addEventListener("keydown", e => {
    if (e.key !== "Escape" || !wrap || wrap.hidden) return;
    if (popFor) closePop(); else closeSettings();   // Esc сначала закрывает список моделей
    e.stopPropagation();
  }, true);
}

function providerOptions() {
  return PROVIDERS.filter(p => !p.serverOnly || !isStandalone())
    .map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
}

function buildModal() {
  wrap = document.createElement("div");
  wrap.id = "setWrap";
  wrap.hidden = true;
  wrap.innerHTML = `
    <div id="setVeil"></div>
    <div id="setPanel" role="dialog" aria-modal="true" aria-label="Настройки ИИ">
      <div class="set-h">
        <span>Настройки ИИ</span>
        <button id="setX" class="btn ghost icon" title="Закрыть (Esc)" aria-label="Закрыть">${icon("x")}</button>
      </div>
      <p class="set-sub">Какая модель отвечает в чате. Подходит любой OpenAI-совместимый API.
        Поиск по базе работает и без ключа. Ключ хранится только в этом браузере и уходит только
        в выбранный API.</p>
      <label class="set-row"><span>Провайдер</span>
        <select id="cfgProv" class="set-in">${providerOptions()}</select></label>
      <div id="cfgOwn">
        <label class="set-row"><span>Адрес API <em class="set-hint">до /chat/completions</em></span>
          <input type="text" id="cfgBase" class="set-in" placeholder="https://…/v1" spellcheck="false" autocomplete="off"></label>
        <label class="set-row" id="cfgKeyRow"><span>API-ключ <a id="cfgKeyLink" target="_blank" rel="noopener">получить ключ ${icon("external", 12)}</a></span>
          <span class="set-pair">
            <input type="password" id="cfgKey" class="set-in" placeholder="вставь ключ" autocomplete="off" spellcheck="false">
            <button type="button" id="cfgEye" class="btn icon" title="Показать / скрыть ключ" aria-label="Показать ключ">${icon("eye")}</button>
          </span></label>
        <div class="set-row"><span>Модель ответа — «мозг» чата</span>
          <span class="set-pair">
            <span class="set-combo">
              <input type="text" id="cfgChat" class="set-in" placeholder="выбери из списка" spellcheck="false" autocomplete="off"
                     role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="mdlPop">
              <button type="button" class="combo-btn" data-for="cfgChat" tabindex="-1" aria-label="Открыть список моделей">${icon("chev-down", 16)}</button>
            </span>
            <button type="button" id="cfgLoad" class="btn" title="Обновить список моделей провайдера">${icon("retry", 16)}<span>Обновить</span></button>
          </span></div>
        <div class="set-row"><span>Модель для поиска <em class="set-hint">быстрая и дешёвая; пусто — та же</em></span>
          <span class="set-combo">
            <input type="text" id="cfgFast" class="set-in" placeholder="та же, что для ответа" spellcheck="false" autocomplete="off"
                   role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="mdlPop">
            <button type="button" class="combo-btn" data-for="cfgFast" tabindex="-1" aria-label="Открыть список моделей">${icon("chev-down", 16)}</button>
          </span></div>
      </div>
      <div id="mdlPop" class="mdl-pop" role="listbox" hidden></div>
      <div id="cfgStatus" class="set-status" role="status" aria-live="polite"></div>
      <div class="set-btns">
        <button id="cfgSave" class="btn primary">Сохранить</button>
        <button id="cfgTest" class="btn">${icon("check", 16)}<span>Проверить</span></button>
        <button id="cfgClear" class="btn ghost">Сбросить</button>
      </div>
    </div>`;
  document.body.appendChild(wrap);
  $("#setVeil").addEventListener("click", closeSettings);
  $("#setX").addEventListener("click", closeSettings);
  $("#cfgSave").addEventListener("click", saveSettings);
  $("#cfgClear").addEventListener("click", clearSettings);
  $("#cfgTest").addEventListener("click", testSettings);
  $("#cfgLoad").addEventListener("click", () => { loadModels(true); const i = $("#cfgChat"); i.focus(); openPop(i); });
  $("#cfgProv").addEventListener("change", () => { applyPreset(true); modelsMaybeStale(); });
  wireCombo();
  $("#cfgEye").addEventListener("click", () => {
    const k = $("#cfgKey"), show = k.type === "password";
    k.type = show ? "text" : "password";
    $("#cfgEye").innerHTML = icon(show ? "eye-off" : "eye");
  });
  wrap.addEventListener("keydown", e => {   // Enter в любом поле — сохранить
    if (e.key === "Enter" && e.target.tagName === "INPUT") { closePop(); saveSettings(); }
  });
}

/* поля под выбранный провайдер; reset — пользователь сменил провайдера: адрес и модели из пресета */
function applyPreset(reset) {
  const p = byId($("#cfgProv").value) || byId("custom");
  const own = p.id !== "server";
  $("#cfgOwn").hidden = !own;
  if (reset && own) {
    $("#cfgBase").value = p.base || "";
    $("#cfgChat").value = p.model || "";
    $("#cfgFast").value = "";
    status("");
  }
  $("#cfgBase").readOnly = !!p.base && p.id !== "custom" && !p.local;
  $("#cfgKeyRow").hidden = !!p.local;
  const link = $("#cfgKeyLink");
  link.hidden = !p.keys;
  if (p.keys) link.href = p.keys;
}

function status(text, kind = "") {
  const el = $("#cfgStatus");
  el.textContent = text;
  el.className = "set-status" + (kind ? " " + kind : "");
}

function form() {
  return { provider: $("#cfgProv").value, base: $("#cfgBase").value.trim().replace(/\/+$/, ""),
           key: $("#cfgKey").value.trim(), model: $("#cfgChat").value.trim(), fast: $("#cfgFast").value.trim() };
}

function guessProvider(base) {
  if (!base) return isStandalone() ? "openrouter" : "server";
  const p = PROVIDERS.find(x => x.base && x.base === base.replace(/\/+$/, ""));
  return p ? p.id : "custom";
}

function openSettings() {
  if (!wrap) buildModal();
  let prov = get(KEYS.provider) || guessProvider(get(KEYS.base));
  if (!byId(prov) || (prov === "server" && isStandalone())) prov = "openrouter";
  $("#cfgProv").value = prov;
  const p = byId(prov);
  $("#cfgBase").value = get(KEYS.base) || p.base || "";
  $("#cfgKey").value = get(KEYS.key);
  $("#cfgKey").type = "password";
  $("#cfgEye").innerHTML = icon("eye");
  $("#cfgChat").value = get(KEYS.chat) || p.model || "";
  $("#cfgFast").value = get(KEYS.fast);
  applyPreset(false);
  status(prov === "server" ? "Чат отвечает через провайдеров, настроенных на сервере." : "");
  wrap.hidden = false;
  $("#cfgProv").focus();
  modelsMaybeStale();                  // есть ключ — список моделей грузится сразу, в фоне
}

function closeSettings() { closePop(); if (wrap) wrap.hidden = true; }

function validate(f, needModel = true) {
  if (f.provider === "server") return "";
  if (!/^https?:\/\/\S+/.test(f.base)) return "Адрес API должен начинаться с http:// или https://";
  if (needModel && !f.model) return "Выбери модель ответа в списке";
  if (!f.key && !(byId(f.provider) || {}).local && !/localhost|127\.0\.0\.1/.test(f.base))
    return "Нужен API-ключ";
  return "";
}

function saveSettings() {
  const f = form();
  const err = validate(f);
  if (err) { status(err, "bad"); return; }
  put(KEYS.provider, f.provider);
  if (f.provider === "server") {
    for (const k of ["key", "base", "chat", "fast"]) put(KEYS[k], "");
  } else {
    put(KEYS.base, f.base); put(KEYS.key, f.key); put(KEYS.chat, f.model); put(KEYS.fast, f.fast);
  }
  status("Сохранено", "ok");
  setTimeout(closeSettings, 450);
}

function clearSettings() {
  for (const k of Object.values(KEYS)) put(k, "");
  openSettings();
  status("Настройки сброшены", "ok");
}

const errText = e => {
  const m = String((e && e.message) || e || "ошибка");
  if (/Failed to fetch|NetworkError|Load failed/i.test(m))
    return isStandalone()
      ? "Нет связи с API. Возможно, провайдер не пускает запросы прямо из браузера (CORS) — попробуй OpenRouter, а для Ollama запусти её с OLLAMA_ORIGINS=*"
      : "Сервер сайта не запущен — запусти ярлык «MXW карта» и не закрывай окно «MXW server», потом нажми ещё раз";
  return m.slice(0, 260);
};

/* ---- список моделей: грузится сам (есть адрес и ключ), кэш по адресу+ключу ---- */
const modelCache = new Map();          // base\nkey → [id]
let models = [];                       // список для текущих адреса и ключа
let loading = null;                    // Promise загрузки в полёте
let loadErr = "";

const cacheKey = f => f.base + "\n" + f.key;
const canLoad = f => f.provider !== "server" && !validate(f, false);

/* force — кнопка «Обновить»: мимо кэша и с сообщением в статусе */
async function loadModels(force = false) {
  const f = form();
  if (f.provider === "server") return [];
  const err = validate(f, false);
  if (err) { models = []; loadErr = err; if (force) status(err, "bad"); renderPop(); return []; }
  const ck = cacheKey(f);
  if (!force && modelCache.has(ck)) { models = modelCache.get(ck); loadErr = ""; renderPop(); return models; }
  if (loading) return loading;
  loadErr = "";
  $("#cfgLoad").disabled = true;
  if (force) status("Загружаю список моделей…");
  renderPop();
  loading = (async () => {
    try {
      const ids = await llmModels({ base: f.base, key: f.key });
      modelCache.set(ck, ids);
      if (cacheKey(form()) === ck) models = ids;
      if (force || !ids.length)
        status(ids.length ? `Моделей: ${ids.length} — выбери в списке` : "Провайдер не отдал список моделей — впиши название вручную",
               ids.length ? "ok" : "bad");
      return ids;
    } catch (e) {
      models = [];
      loadErr = "Список не загрузился: " + errText(e);
      status(loadErr, "bad");
      return [];
    } finally {
      loading = null;
      $("#cfgLoad").disabled = false;
      renderPop();
    }
  })();
  return loading;
}

/* адрес или ключ поменялись — подгрузить список заново (тихо) */
function modelsMaybeStale() {
  const f = form();
  models = modelCache.get(cacheKey(f)) || [];
  if (canLoad(f) && !modelCache.has(cacheKey(f))) loadModels();
}

/* ---- выпадающий список (один на оба поля, position: fixed — не обрезается окном) ---- */
let popFor = null;                     // input, к которому открыт список
let popAct = -1;                       // подсвеченный пункт
let popItems = [];

function openPop(input) {
  popFor = input;
  input.setAttribute("aria-expanded", "true");
  popAct = -1;
  renderPop(true);
  if (!models.length && !loading && canLoad(form())) loadModels();
}

function closePop() {
  if (!popFor) return;
  popFor.setAttribute("aria-expanded", "false");
  popFor = null;
  $("#mdlPop").hidden = true;
}

function placePop() {
  const pop = $("#mdlPop"), r = popFor.getBoundingClientRect();
  const below = innerHeight - r.bottom - 12, above = r.top - 12;
  const up = below < 220 && above > below;
  const h = Math.min(300, Math.max(120, up ? above : below));
  Object.assign(pop.style, { left: r.left + "px", width: r.width + "px", maxHeight: h + "px",
                             top: up ? "" : (r.bottom + 4) + "px", bottom: up ? (innerHeight - r.top + 4) + "px" : "" });
}

/* showAll — только что открыли: весь список, даже если в поле уже выбранная модель */
function renderPop(showAll = false) {
  if (!popFor) return;
  const pop = $("#mdlPop");
  const q = popFor.value.trim().toLowerCase();
  const all = withAliases(models);
  const alias = aliasesNow(), notes = notesNow();
  const hay = m => (m + " " + (notes[m] || "")).toLowerCase();
  const exact = all.some(m => m.toLowerCase() === q);
  const list = (showAll || !q || exact) ? all : all.filter(m => hay(m).includes(q));
  popItems = list;                     // все модели, без ограничения (у OpenRouter их 470+)
  let html = "";
  if (models.length && !loading)
    html += `<div class="mdl-note mdl-count">${list.length === all.length ? `Все модели провайдера: ${all.length}` : `Найдено ${list.length} из ${all.length}`}</div>`;
  if (popFor.id === "cfgFast")
    html += `<div class="mdl-opt mdl-same" data-v="" role="option">та же, что для ответа</div>`;
  if (loading) html += `<div class="mdl-note">Загружаю список моделей…</div>`;
  else if (loadErr) html += `<div class="mdl-note bad">${esc(loadErr)}</div>`;
  else if (!models.length) html += `<div class="mdl-note">${canLoad(form()) ? "Список пуст — впиши название вручную" : "Укажи ключ — список моделей загрузится сам"}</div>`;
  else if (!popItems.length) html += `<div class="mdl-note">Нет моделей с «${esc(q)}»</div>`;
  html += popItems.map((m, i) => {
    const cur = m.toLowerCase() === q;
    const al = alias[m] ? `<span class="mdl-alias">старое имя, отвечает ${esc(alias[m])}</span>`
             : notes[m] ? `<span class="mdl-alias mdl-real">${esc(notes[m])}</span>` : "";
    return `<div class="mdl-opt${cur ? " cur" : ""}${i === popAct ? " act" : ""}" data-i="${i}" role="option" aria-selected="${cur}">${esc(m)}${al}</div>`;
  }).join("");
  pop.innerHTML = html;
  pop.hidden = false;
  placePop();
  if (showAll) {                       // прокрутить к выбранной модели
    const c = pop.querySelector(".mdl-opt.cur");
    if (c) c.scrollIntoView({ block: "center" });
  }
}

function pickPop(v) {
  if (!popFor) return;
  popFor.value = v;
  const inp = popFor;
  closePop();
  inp.focus();
  if (inp.id === "cfgChat") status(v ? `Модель ответа: ${v}. Нажми «Проверить» или «Сохранить».` : "", v ? "ok" : "");
}

function movePop(d) {
  if (!popItems.length) return;
  popAct = (popAct + d + popItems.length) % popItems.length;
  renderPop();
  const a = $("#mdlPop").querySelector(".mdl-opt.act");
  if (a) a.scrollIntoView({ block: "nearest" });
}

function wireCombo() {
  for (const id of ["cfgChat", "cfgFast"]) {
    const inp = $("#" + id);
    inp.addEventListener("focus", () => openPop(inp));
    inp.addEventListener("click", () => { if (popFor !== inp) openPop(inp); });
    inp.addEventListener("input", () => { if (popFor !== inp) openPop(inp); popAct = -1; renderPop(); });
    inp.addEventListener("keydown", e => {
      if (e.key === "ArrowDown") { e.preventDefault(); if (popFor !== inp) openPop(inp); else movePop(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); movePop(-1); }
      else if (e.key === "Enter" && popFor === inp && popAct >= 0) { e.preventDefault(); e.stopPropagation(); pickPop(popItems[popAct]); }
      else if (e.key === "Tab") closePop();
    });
  }
  wrap.querySelectorAll(".combo-btn").forEach(b => b.addEventListener("mousedown", e => {
    e.preventDefault();                // не уводим фокус из поля
    const inp = $("#" + b.dataset.for);
    if (popFor === inp) closePop(); else { inp.focus(); openPop(inp); }
  }));
  const pop = $("#mdlPop");
  pop.addEventListener("mousedown", e => e.preventDefault());   // клик по списку не снимает фокус
  pop.addEventListener("click", e => {
    const o = e.target.closest(".mdl-opt");
    if (!o) return;
    pickPop(o.dataset.v !== undefined ? o.dataset.v : popItems[+o.dataset.i]);
  });
  document.addEventListener("mousedown", e => {
    if (popFor && !e.target.closest(".set-combo") && !e.target.closest("#mdlPop")) closePop();
  });
  $("#setPanel").addEventListener("scroll", () => { if (popFor) placePop(); });
  addEventListener("resize", () => { if (popFor) placePop(); });
  $("#cfgKey").addEventListener("change", modelsMaybeStale);
  $("#cfgKey").addEventListener("paste", () => setTimeout(modelsMaybeStale, 0));
  $("#cfgBase").addEventListener("change", modelsMaybeStale);
}

async function testSettings() {
  const f = form();
  if (f.provider === "server") { status("Пункт «Сервер» проверяется вопросом в чате", ""); return; }
  const err = validate(f);
  if (err) { status(err, "bad"); return; }
  status("Спрашиваю модель…");
  $("#cfgTest").disabled = true;
  try {
    const r = await llmTest({ base: f.base, key: f.key, model: f.model });
    status(`Работает: «${(r.reply || "—").slice(0, 80)}» за ${(r.ms / 1000).toFixed(1)} с. Не забудь сохранить.`, "ok");
  } catch (e) {
    status("Не отвечает: " + errText(e), "bad");
  } finally {
    $("#cfgTest").disabled = false;
  }
}
