# Задача: оригиналы медиа в панели разговора

Сейчас в панели разговора (`openConv` в `app/index.html`) вместо медиа видны только расшифровки/описания (`media_text`) или метки вида `[фото]`. Нужно показывать сами файлы из экспорта Telegram.

Меняй только `app/server.py` и `app/index.html`. Не читай `.llm_env`/`.openrouter_key`, не читай `tasks/*.log`, НЕ открывай PNG/медиа инструментом Read (сессия падает). LLM не вызывать.

## Данные
`data/media_index.jsonl` — 6213 строк `{"topic": "<slug>", "msg": <id>, "kind": "photos|video_files|voice_messages|round_video_messages|files", "path": "C:\\Users\\fl3xx\\Desktop\\MXW Data Base\\ChatExport_2026-11-16 (N)\\chats\\...\\<file>"}`. У одного сообщения может быть несколько файлов. Рядом с фото в экспорте Telegram часто лежит миниатюра `<имя>_thumb.jpg` — проверь на диске и используй, если есть.

## Сервер
- При старте загрузи индекс в словарь `(topic, msg) → [{"i": <номер строки>, "kind", "name", "size"}]`; сам путь клиенту НЕ отдавать.
- В `conv_detail` добавь каждому сообщению `files: [...]` из словаря.
- `GET /api/media?i=<номер>[&thumb=1]` — отдаёт файл по номеру строки индекса. Безопасность: только пути из индекса; дополнительно проверь, что resolved-путь лежит внутри `C:\Users\fl3xx\Desktop\MXW Data Base`. Content-Type по расширению (`mimetypes`, для `.ogg` → `audio/ogg`, `.mp4` → `video/mp4`). Поддержи HTTP Range (`206 Partial Content`, `Accept-Ranges: bytes`, `Content-Range`) — без этого не работает перемотка аудио/видео. Отдавай файл потоково кусками (не читать 100 МБ видео целиком в память). `Cache-Control: max-age=86400`. Для `files` (zip и т.п.) — `Content-Disposition: attachment; filename*=UTF-8''<имя>`.
- Обработка обрыва соединения браузером (ConnectionResetError/BrokenPipe при перемотке) — молча.

## Фронт (панель разговора)
- photos: сетка миниатюр (thumb, если есть, иначе сам файл, `loading="lazy"`, высота ~140px, object-fit cover); клик — полноэкранный просмотр (оверлей с затемнением, картинка по центру, закрытие по клику/Esc, стрелки ←/→ между фото этого разговора).
- voice_messages: `<audio controls preload="none">`.
- round_video_messages: круглое видео 200×200 (`border-radius:50%`), `controls preload="none"`.
- video_files: `<video controls preload="none">` шириной до 100% панели, maxHeight 320px; если это GIF-анимация (в метке `media` есть «Animation») — `autoplay muted loop playsinline`.
- files: строка «📎 имя (размер) — скачать» ссылкой на `/api/media?i=`; для .zip/.exe/.bat/.js/.apk/.msi пометка «⚠ архив/программа из чата — не запускай без проверки».
- Расшифровка (`media_text`) остаётся под медиа, но свёрнута в `<details><summary>расшифровка</summary>…</details>` для голосовых/видео; для фото — описание показывать, как сейчас, но компактнее (первые 2 строки, остальное по клику). Если расшифровка содержит найденные поиском слова (hit) — разворачивать.
- Режимы «Главное/Без флуда/Всё» и стример-мод не сломать. Сообщение только с фото без текста — не noise, если у него есть файлы (проверь логику noise на сервере: файлы = содержательное сообщение, кроме стикеров).

## Проверка
- Перезапусти сервер (останови процесс `python app/server.py` на 8765, запусти заново в фоне).
- curl: возьми из индекса одно фото, одно голосовое и один файл; `curl -sI` на `/api/media?i=N` — тип и длина; `curl -s -H "Range: bytes=0-99" -o NUL -w "%{http_code} %{size_download}"` → `206 100`; попытка `i=999999` → 404.
- Сколько сообщений в `nft-c0083-01` имеют files.
- `node --check` на инлайн-скрипте; скриншот headless Edge `http://127.0.0.1:8765/#conv=<разговор с фото и голосовым>&mode=all` → `data/map_media.png` (не открывать; напиши в отчёте, какой разговор взял).
