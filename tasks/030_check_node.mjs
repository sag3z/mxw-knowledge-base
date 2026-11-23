/* tasks/030_check_node.mjs — проверка совпадения векторов браузерной реализации
   (embed_web.js из СОБРАННОГО архива) с серверной (embed_local.embed_query, Python)
   на 5 строках: косинус ≥ 0.99. Считает задачи 030 п.4.

   Запуск (cwd = корень собранного архива, где лежат app/bundle.js, app/model/*.js, app/lib):
       node <repo>/tasks/030_check_node.mjs '["строка1", ...]'
   Если app/bundle.js нет — читает js/embed_web.js напрямую (мини-архив для отладки).
   Шимит window/document (шарды модели грузятся «<script>»-ами, как в браузере);
   onnxruntime-web — тот же ort.min.js, wasm через blob:-URL (Node fetch умеет blob:).
   Векторы и тайминги пишет JSON'ом в stdout. */
import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { join } from "node:path";

/* --- шимы браузерного окружения ------------------------------------------------------- */
globalThis.self = globalThis;
globalThis.window = globalThis;
globalThis.document = {
  createElement() { return { src: "", onload: null, onerror: null }; },
  head: {
    async appendChild(s) {   // «браузер» грузит script: читаем файл с диска и исполняем в global
      try {
        (0, eval)(await readFile(join(process.cwd(), s.src.replaceAll("\\", "/")), "utf8"));
        s.onload && s.onload();
      } catch (e) { s.onerror && s.onerror(e); }
    },
  },
};

const STRINGS = JSON.parse(process.argv[2] || "[]") || [];

/* модуль embed_web: из bundle.js архива (штатный путь) либо отдельным файлом (мини-архив) */
let embedWebQuery;
try {
  const bundle = await readFile("app/bundle.js", "utf8");
  const m = bundle.match(/^__m\.embed_web = \(function\(\)\{[\s\S]*?\n\}\)\(\);$/m);
  if (!m) throw new Error("в app/bundle.js нет модуля embed_web");
  (0, eval)("var __m = {};\n" + m[0]);     // IIFE сам положит __m.embed_web
  embedWebQuery = globalThis.__m.embed_web.embedWebQuery;
} catch {
  const mod = await import(pathToFileURL(join(process.cwd(), "js/embed_web.js")).href);
  embedWebQuery = mod.embedWebQuery;
}

const out = [];
for (const q of STRINGS) {
  const t0 = performance.now();
  const v = await embedWebQuery(q);
  out.push({ q, ms: Math.round(performance.now() - t0), vec: Array.from(v) });
  console.error(`embed_web: «${q}» — ${out[out.length - 1].ms} мс`);
}
process.stdout.write(JSON.stringify(out));
