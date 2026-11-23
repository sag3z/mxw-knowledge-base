/* settings.js — «Настройки» в боковой панели: какой ИИ — «мозг» чата.
   Любой OpenAI-совместимый провайдер (OpenRouter, OpenAI, DeepSeek, Gemini, Groq, Ollama…):
   адрес API, ключ, модель ответа и (по желанию) быстрая модель для переформулировки запроса.
   Всё хранится в localStorage этого браузера. В архиве запросы идут из браузера прямо
   в указанный API; на локальном сайте — через свой сервер (127.0.0.1), который пересылает
   их провайдеру. Пункт «Сервер» (только на локальном сайте) — ключи сервера из .llm_env.
   Смысловой поиск по базе работает без ключа — локальная модель прямо в браузере/на сервере. */
import { icon } from "./icons.js";
import { isStandalone, llmModels, llmTest } from "./api.js";
import { $, esc, escAttr } from "./ui.js";

const KEYS = { provider: "mxw_provider", key: "mxw_key", base: "mxw_base",
               chat: "mxw_chat_model", fast: "mxw_fast_model" };

/* пресеты: адрес API, где взять ключ, модель по умолчанию (пусто — выбрать из списка) */
const PROVIDERS = [
  { id: "server", name: "Сервер (ключи из .llm_env)", serverOnly: true },
  { id: "openrouter", name: "OpenRouter — сотни моделей по одному ключу", base: "https://openrouter.ai/api/v1",
    keys: "https://openrouter.ai/keys", model: "deepseek/deepseek-chat" },
  { id: "openai", name: "OpenAI", base: "https://api.openai.com/v1", keys: "https://platform.openai.com/api-keys" },
  { id: "deepseek", name: "DeepSeek", base: "https://api.deepseek.com/v1", keys: "https://platform.deepseek.com/",
    model: "deepseek-chat" },
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
    closeSettings();
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
        <label class="set-row"><span>Модель ответа — «мозг» чата</span>
          <span class="set-pair">
            <input type="text" id="cfgChat" class="set-in" list="cfgModels" placeholder="начни вводить или загрузи список" spellcheck="false" autocomplete="off">
            <button type="button" id="cfgLoad" class="btn" title="Загрузить список моделей провайдера">${icon("list", 16)}<span>Модели</span></button>
          </span></label>
        <label class="set-row"><span>Модель для поиска <em class="set-hint">быстрая и дешёвая; пусто — та же</em></span>
          <input type="text" id="cfgFast" class="set-in" list="cfgModels" placeholder="та же, что для ответа" spellcheck="false" autocomplete="off"></label>
        <datalist id="cfgModels"></datalist>
      </div>
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
  $("#cfgLoad").addEventListener("click", loadModels);
  $("#cfgProv").addEventListener("change", () => applyPreset(true));
  $("#cfgEye").addEventListener("click", () => {
    const k = $("#cfgKey"), show = k.type === "password";
    k.type = show ? "text" : "password";
    $("#cfgEye").innerHTML = icon(show ? "eye-off" : "eye");
  });
  wrap.addEventListener("keydown", e => {   // Enter в любом поле — сохранить
    if (e.key === "Enter" && e.target.tagName === "INPUT") saveSettings();
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
    $("#cfgModels").innerHTML = "";
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
}

function closeSettings() { if (wrap) wrap.hidden = true; }

function validate(f, needModel = true) {
  if (f.provider === "server") return "";
  if (!/^https?:\/\/\S+/.test(f.base)) return "Адрес API должен начинаться с http:// или https://";
  if (needModel && !f.model) return "Укажи модель ответа (кнопка «Модели» загрузит список)";
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
      : "Нет связи с сервером сайта";
  return m.slice(0, 260);
};

async function loadModels() {
  const f = form();
  const err = validate(f, false);
  if (err) { status(err, "bad"); return; }
  if (f.provider === "server") return;
  status("Загружаю список моделей…");
  $("#cfgLoad").disabled = true;
  try {
    const ids = await llmModels({ base: f.base, key: f.key });
    $("#cfgModels").innerHTML = ids.slice(0, 3000).map(id => `<option value="${escAttr(id)}"></option>`).join("");
    status(ids.length ? `Моделей: ${ids.length}. Начни вводить название в поле — появятся подсказки.`
                      : "Провайдер вернул пустой список — впиши модель вручную", ids.length ? "ok" : "bad");
    if (ids.length) $("#cfgChat").focus();
  } catch (e) {
    status("Список не загрузился: " + errText(e), "bad");
  } finally {
    $("#cfgLoad").disabled = false;
  }
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
