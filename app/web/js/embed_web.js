/* embed_web.js — эмбеддинг поискового запроса прямо в браузере: без сети, без ключа
   (только автономный архив; серверный режим ищет на сервере через /api/search).

   Та же модель, что у локального сервера (embed_local.py): multilingual-e5-small, 384-d,
   ONNX int8. Инференс — onnxruntime-web 1.14 (app/lib/ort.min.js, UMD): wasm, один поток,
   без SharedArrayBuffer (file:// не даёт его), proxy выключен. Токенайзер XLM-R
   (SentencePiece/Unigram + precompiled charsmap) — из tokenizer.json через вендорную
   сборку transformers.js v2 (app/lib/transformers.min.js, только класс токенайзера).

   Ограничение file://: fetch локальных файлов запрещён → модель (model_quantized.onnx,
   ~118 МБ), tokenizer.json и ort-wasm-simd.wasm упакованы в base64-шарды app/model/*.js
   (по ≤ 20 МБ, грузятся динамическими <script> при первом поиске и собираются в байты).
   Сессия создаётся из буфера (InferenceSession.create(Uint8Array)), wasm-файл подсовывается
   рантайму через blob:-URL (ort 1.14: env.wasm.wasmPaths объектом) — никаких fetch с диска.

   Совместимость с сервером: префикс «query: », обрезка 512 токенов, mean pooling по
   attention_mask + L2; косинус с embed_local.embed_query ≥ 0.997 (проверено в Node). */
"use strict";

const MODEL_DIR = "app/model";
const RETRY_MS = 60000;   // после неудачи не пробуем загрузить модель заново минуту

let _scriptP = {};
function loadScript(src) {
  return _scriptP[src] ||= new Promise((res, rej) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = () => res();
    s.onerror = () => { delete _scriptP[src]; rej(new Error("не загрузился " + src)); };
    document.head.appendChild(s);
  });
}

/* base64-шарды (границы кратны 3 → каждый кусок декодируется независимо) → один Uint8Array */
function shardsToBytes(parts) {
  const lens = parts.map(p => atob(p).length);
  const out = new Uint8Array(lens.reduce((a, b) => a + b, 0));
  let off = 0;
  for (const p of parts) {
    const bin = atob(p);
    for (let i = 0; i < bin.length; i++) out[off + i] = bin.charCodeAt(i);
    off += bin.length;
  }
  return out;
}

async function loadModelFile(name, onStatus, label) {
  const list = (window.MXW_MODEL.files || {})[name] || [];
  if (!list.length) throw new Error("в архиве нет файла модели: " + name);
  const parts = [];
  for (let i = 0; i < list.length; i++) {   // шард добавляет одну base64-строку в window.MXW_MODEL[name]
    await loadScript(`${MODEL_DIR}/${list[i]}`);
    parts.push(...window.MXW_MODEL[name]);
    window.MXW_MODEL[name] = [];   // экономим память: расшифрованное больше не нужно
    if (onStatus && list.length > 1)
      onStatus(`Загружаю модель поиска… ${i + 1}/${list.length} (один раз, ~10 с)`);
  }
  if (parts.length !== list.length) throw new Error("модель поиска загрузилась не полностью");
  return shardsToBytes(parts);
}

let _ready = null, _failedAt = 0;

async function initModel(onStatus) {
  if (_ready) return _ready;
  if (Date.now() - _failedAt < RETRY_MS)
    throw new Error("модель поиска не загрузилась (повторю через минуту)");
  if (onStatus) onStatus("Загружаю модель поиска… (один раз, ~10 с)");
  _ready = (async () => {
    await loadScript(`${MODEL_DIR}/manifest.js`);
    if (onStatus) onStatus("Загружаю модель поиска… (один раз, ~10 с)");

    // 1) токенайзер: transformers.js + tokenizer.json из шардов
    const [tokBytes] = await Promise.all([
      loadModelFile("tok", onStatus),
      loadScript("app/lib/transformers.min.js"),
    ]);
    const tokJSON = JSON.parse(new TextDecoder().decode(tokBytes));
    const tokenizer = new window.transformers.PreTrainedTokenizer(
      tokJSON, { model_type: "xlm-roberta" });

    // 2) ort: wasm через blob:-URL (fetch с file:// запрещён), один поток, без proxy
    await loadScript("app/lib/ort.min.js");
    const ort = window.ort;
    const [modelBytes, wasmBytes] = await Promise.all([
      loadModelFile("model", onStatus),
      loadModelFile("wasm", onStatus),
    ]);
    ort.env.wasm.numThreads = 1;    // без SharedArrayBuffer (file://)
    ort.env.wasm.proxy = false;
    ort.env.wasm.wasmPaths = { "ort-wasm-simd.wasm": URL.createObjectURL(
      new Blob([wasmBytes], { type: "application/wasm" })) };

    // 3) сессия из буфера
    if (onStatus) onStatus("Готовлю модель поиска…");
    const session = await ort.InferenceSession.create(modelBytes);
    return { ort, tokenizer, session };
  })().catch(err => {
    _ready = null;
    _failedAt = Date.now();
    throw err;
  });
  return _ready;
}

/* Ленивая инициализация модели: первый вызов грузит шарды и создаёт сессию (~10 с),
   остальные — мгновенно. onStatus(text) — индикатор в чате/поиске. */
export function embedWebReady(onStatus) { return initModel(onStatus); }

/* Запрос → вектор 384-d, L2-нормирован (аналог embed_local.embed_query). */
export async function embedWebQuery(text, onStatus) {
  const { ort, tokenizer, session } = await initModel(onStatus);
  const enc = tokenizer("query: " + text, {
    add_special_tokens: true, return_tensor: false,
    truncation: true, max_length: 512,
  });
  const seq = enc.input_ids.length, dim = 384;
  const ids = new ort.Tensor("int64", BigInt64Array.from(enc.input_ids.map(BigInt)), [1, seq]);
  const mask = new ort.Tensor("int64", BigInt64Array.from(enc.attention_mask.map(BigInt)), [1, seq]);
  const ttype = new ort.Tensor("int64", BigInt64Array.from(new Array(seq).fill(0).map(BigInt)), [1, seq]);
  const res = await session.run({ input_ids: ids, attention_mask: mask, token_type_ids: ttype });
  const hidden = res.last_hidden_state.data;   // [1, seq, 384]
  const v = new Float32Array(dim);
  let cnt = 0;
  for (let i = 0; i < seq; i++) {
    if (!enc.attention_mask[i]) continue;
    cnt++;
    const off = i * dim;
    for (let j = 0; j < dim; j++) v[j] += hidden[off + j];
  }
  let nn = 0;
  for (let j = 0; j < dim; j++) { v[j] /= cnt || 1; nn += v[j] * v[j]; }
  const inv = 1 / Math.sqrt(nn || 1);
  for (let j = 0; j < dim; j++) v[j] *= inv;
  return v;
}
