---
категория: ai_dev
ветки: [ai, life]
период: 2026-06-17 — 2026-09-26
источников: 11
модель: deepseek-v4.1-flash
---
# Генерация изображений и дизайн: Nano Banana, Images 2.5, референс-листы и промпты

## Кратко

Главный тезис ветки: модели не надо просить «нарисовать дизайн с нуля» — ей надо давать готовые варианты и референсы, тогда она делает копию нужного [[ai-c0278]]. Под это выстроены три практики: библиотеки готовых промптов с адаптацией под задачу через ИИ [[ai-c0023]] [[ai-c0024]], референс-листы персонажей через Nano Banana для консистентности в кадрах [[ai-c0025]] и image-first воркфлоу в Codex, где сначала генерится визуал в Images 2.5, а потом Sol/Astra пишут код [[ai-c0295]]. Официально ChatGPT Images 2.5 вышел 8 сентября 2026: на 50% быстрее, точечное редактирование, режим Sketch, шаблоны, раскатка на всех [[ai-c0260]]. Там, где модели пытаются «придумать» с нуля, результат плохой: 40 логотипов для автосервиса от Astra за час вышли детскими и кривыми [[ai-c0309]].

## Библиотеки промптов и адаптация

ProstoXleb в ответ на вопрос про фотореалистичную генерацию посоветовал библиотеку промптов https://youmind.com/prompts — «потыкаться» [[ai-c0023]]. Готовые промпты оттуда не используются как есть: их копируют и подгоняют под себя, меняя данные и детали под конкретную задачу; адаптировать удобно через ИИ — сам он для этого использует DeepSeek [[ai-c0024]].

Для сравнения, по словам FroZi, GLM 5.2 дешевле Opus 4.8 примерно в 5 раз (на 7 июля 2026), и «хуже чем GLM 5.2 уже не будет» — то есть дешёвая модель как раз для таких вспомогательных правок [[ai-c0083]].

## Референс-листы персонажей (Nano Banana)

Схема от batrakan для консистентности персонажа между кадрами [[ai-c0025]]:

1. Подготовить референсы или референс-листы каждого персонажа.
2. Загрузить исходник (лицо, тело с одеждой или несколько исходников) в Nano Banana — на выходе лист с ракурсами.
3. Кидать референс-листы всех персонажей под один промпт и распределять роли словами: «персонаж с изображения 1 сидит на стуле, персонаж с изображения 2 стоит» и т.д.

Именно так, по его словам, сохраняется консистентность персонажа [[ai-c0025]].

Промпт для референс-листа «от Higgsfield AI» (технический turnaround: два горизонтальных ряда, сверху 4 полных ракурса — фронт, левый профиль, правый профиль, спина; снизу 3 крупных портрета; единый свет, масштаб, A-pose) [[ai-c0025]]:

```
Create a professional character reference sheet based strictly on the uploaded reference image. Use a clean, neutral plain background and present the sheet as a technical model turnaround while matching the exact realistic visual style of the reference (same realism level, rendering approach, texture, color treatment, and overall aesthetic). Arrange the composition into two horizontal rows. Top row: four full-body standing views placed side-by-side in this order: front view, left profile view (facing left), right profile view (facing right), back view. Bottom row: three highly detailed close-up portraits aligned beneath the full-body row in this order: front portrait, left profile portrait (facing left), right profile portrait (facing right). Maintain perfect identity consistency across every panel. Keep the subject in a relaxed A-pose and with consistent scale and alignment between views, accurate anatomy, and clear silhouette; ensure even spacing and clean panel separation, with uniform framing and consistent head height across the full-body lineup and consistent facial scale across the portraits. Lighting should be consistent across all panels (same direction, intensity, and softness), with natural, controlled shadows that preserve detail without dramatic mood shifts. Output a crisp, print-ready reference sheet look, sharp details.
```

Вариант «от Sirio» (видео https://youtu.be/mCavxK7UJ84, ~118-я секунда) — шаблон под переиспользование: белый студийный фон, 4 полных ракурса плюс панели с деталями (глаза, верх/низ лица, губы, текстура кожи, волосы, деталь одежды), всё специфичное упрощается до нейтрального, одежда — базовый комплект [[ai-c0025]]:

```
Create a professional character turnaround and reference sheet based on the reference image. Use the uploaded image as the primary visual reference for the character’s identity, proportions, facial features, body shape, hairstyle, and overall design language, while translating it into a clean, neutral, reusable presentation board. The final image should be arranged like a polished concept art sheet on a pure white studio background. Show the same character in four full body views: front view, side profile, back view, and three quarter view. On the right side, include multiple clean detail panels with close ups of the eyes, upper face, lower face, lips, skin texture, hair detail, and one small clothing or material detail. Keep the styling neutral and generic so the sheet can be reused as a base template for future adaptations. Simplify anything overly specific, thematic, fantasy based, branded, culturally tied, or heavily ornamental from the source image into a more universal version while preserving the essence of the character. The outfit should become a clean neutral base outfit with minimal detailing, soft solid tones, and a refined silhouette. No excessive accessories, no dramatic headpieces, no strong lore specific elements, no heavy decoration unless they are essential to the base identity. The character should feel balanced, elegant, realistic, and adaptable. Expression should be calm and neutral. Makeup should be subtle and natural. Lighting should be soft, even, and studio clean. The layout should feel like a premium design presentation board used for model sheets, character development, or production reference. Preserve the core identity from @Image1, but present it in a simplified, neutral, production ready format that can serve as a universal template for future redesigns.
```

Осторожно с рейтингами: FroZi 7 июля 2026 утверждал, что в рейтинге редактирования изображений Nano Banana «даже не в топ-5», хотя сам давно пользуется только GPT и Banana [[ai-c0083]]. Это противоречит основной практике чата, где Banana — рабочий инструмент под референс-листы [[ai-c0025]].

## Images 2.5 и «image-first» воркфлоу

23 августа на арене (по пересланному посту, официального анонса не было) засветился чекпойнт OpenAI luna-lisa-alpha с обновлённым knowledge cutoff; Max предположил, что если генерация картинок выйдет в релиз в таком духе — это сильный шаг вперёд [[ai-c0198]].

8 сентября 2026 ChatGPT Images 2.5 вышел официально (пересланный анонс): генерация на 50% быстрее, улучшена детализация — естественность цветов, текстур, преломление света, точечное редактирование картинок, режим Sketch (рисунок от руки задаёт композицию), шаблоны под типовые задачи; модель раскатывали сразу на всех пользователей [[ai-c0260]].

Воркфлоу «image-first» из пересланного поста Vox от Neko (20 сентября) [[ai-c0295]]: в Codex встроен Images 2.5, поэтому сначала генерят визуал, дорабатывают его глазами, и только потом Sol или Astra реализуют дизайн в коде. Схема со слайда — прямое кодирование против image-first:

- **Direct coding:** Prompt → Sol / Astra → страница/игра (пишут код, дизайн не видно заранее).
- **Image-first:** Prompt → Images 2.5 → Sol / Astra → страница/игра (сначала видно и можно уточнить дизайн, потом код).

Промпт-формула оттуда дословно: «Use imagegen to reimagine this page, then implement it» [[ai-c0295]].

## Когда модель делает «с нуля» — плохо

Max, 15 сентября 2026: «говори ему чтобы не сам генерировал хуйню, а брал готовые варианты и/или сразу с референсами ему закидывать и он копию всего что хочет сделает» [[ai-c0278]]. Разделение инструментов по задачам, по его мнению:

- фон под текст / вёрстка без лиц — хорошо заходит Image 2.5;
- дизайн с лицами — Claude не подойдёт, «точно так не нахуевертит» [[ai-c0278]].

Антипример: Max попросил Astra сгенерировать 40 разных логотипов для автосервиса и собрать их в одну HTML-страницу для удобного выбора. Astra собирала лого прямо на HTML-разметке, потратила час, результат — «детские кривые» логотипы [[ai-c0309]].

## Игровые ассеты и манга

Ассеты для игр (17 июня 2026): свои спрайты делать долго — Max советует искать готовые паки от художников, бесплатные или дешёвые, в том числе на itch.io; по словам wavyluv, Blender MCP даёт слабые ассеты [[ai-c0005]]. По словам Max, с Claude ассеты выходили лучше, чем с Gemini и GPT, но вопрос лицензий на сгенерированное не обсуждался [[ai-c0005]].

Манга и комиксы (25 июля 2026). ArteMm.baka: главная проблема — сохранить один стиль на всех страницах (внешность персонажей и детали); вариант — обрисовывать то, что дал ИИ, и фиксить за ним, это проще, если не умеешь рисовать. Сам он ИИ в финале не хочет: лучше нанять артиста в Twitter «за три копейки» и платить роялти, а ИИ использовать максимум для черновиков и референсов, которые отдаются артисту; такие штуки делаются через ComfyUI [[life-c0238]]. По его словам, автор комикса hpmorcomic использует один референс и серию из трёх промптов, генерации — в ComfyUI [[life-c0238]].

## Выводы и правила

- Не проси модель «сделать дизайн» — дай готовый вариант или референс и проси копию [[ai-c0278]].
- Для персонажей сначала собери референс-лист в Nano Banana (исходник + шаблон Higgsfield/Sirio), потом сшивай листы нескольких персонажей в одном промпте, распределяя роли по номерам изображений [[ai-c0025]].
- Промпты бери из библиотек (youmind.com/prompts) и адаптируй под задачу через ИИ — например, DeepSeek, а не пиши с нуля [[ai-c0023]] [[ai-c0024]].
- Дизайн страницы или игры делай image-first: Images 2.5 → правки визуала → Sol/Astra реализуют код [[ai-c0295]].
- Фон и вёрстку без лиц отдавай Image 2.5; лица и сложный дизайн — не Claude [[ai-c0278]].
- Не проси LLM «сгенерировать N вариантов логотипов» через HTML-разметку: получишь кривые картинки и потерю времени (кейс: 40 логотипов, час, брак) [[ai-c0309]].
- Спрайты и ассеты для игр — готовые паки с itch.io; Blender MCP даёт слабый результат [[ai-c0005]]. Лицензии на AI-ассеты никто не проверял [[ai-c0005]].
- Для манги/комикса ИИ — черновики и референсы для артиста; стабильный стиль держится на одном референсе + серии из трёх промптов, пайплайн — ComfyUI [[life-c0238]].
- Рейтинги моделей редактирования расходятся с практикой чата (FroZi: Banana не в топ-5 [[ai-c0083]]) — проверяй на своих задачах.
