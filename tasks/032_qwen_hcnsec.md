# Задача: вся обработка — на Qwen через HCNSEC; официальный DeepSeek — только чат сайта

Не печатай ключи, не читай `.llm_env` целиком, не читай `tasks/*.log`, НЕ открывай PNG. Меняй `llm.py`, `factcheck.py` (и другие скрипты, если они используют `ds:`).

## Решение пользователя
- Официальный DeepSeek (`ds:` в llm.py, `llm.FAST`/`llm.PRO`) — **только** для чата на сайте (`app/server.py`: чат, переформулировка/ключевые слова, дайджест). Нигде больше его не использовать — ни в проверке фактов, ни в других пакетных скриптах, ни как fallback в них.
- Пакетная обработка (проверка фактов и т.п.) — на Qwen.

## Замеры ревьюера (HCNSEC, локальный шлюз OpenCode API Manager, `http://127.0.0.1:43117/p/ocam-1d85357dbaed/v1`, ключ не нужен — шлюз подставляет сам)
- `Qwen3.8-27B` — 4 с на короткий запрос, 12 параллельных — все ок за 4 с.
- `Qwen3.8-Flash-Next` — ~11 с; `MiniMax-M3.1-Flash` — ~13 с.
- DeepSeek/GLM/Kimi на HCNSEC — не отвечают или 60–90 с с пустым текстом (не использовать).
- Шлюз должен быть запущен (`OpenCodeAPIManager.exe`); если 127.0.0.1:43117 не отвечает — скрипт сразу пишет понятную ошибку «запусти OpenCode API Manager», а не висит.

## 1. `llm.py`
- Третий провайдер: префикс `"hc:<model>"` → HCNSEC-шлюз (base выше, без заголовка Authorization). Свой семафор `LIMIT_HCNSEC = 12`. Стриминг как у остальных (SSE-разбор тот же).
- Константы: `QWEN = "hc:Qwen3.8-27B"`, `QWEN_ALT = "hc:Qwen3.8-Flash-Next"`.
- Модели с рассуждениями могут тратить токены на reasoning — если ответ пустой при `finish_reason=length`, повторить один раз с удвоенным `max_tokens`.

## 2. `factcheck.py`
- Судья, выделение утверждений, поиск настоящего источника — `llm.QWEN`; fallback — `llm.QWEN_ALT`.
- Второе мнение (только unsupported/contradicted) — `qwen3.8-max` (reformboss), fallback — `llm.QWEN_ALT`. Никаких `ds:`.
- Параллельность пула — 12; проверь, что судья не упирается в лимит ответа (компактный JSON по номерам, пачки по 20; если Qwen3.8-27B часто режет ответ — пачки по 12).
- Перед стартом — проверка доступности шлюза (п. выше).
- `grep -rn "ds:\|llm.FAST\|llm.PRO"` по проекту (кроме backup/, tasks/) — должно остаться только в `app/server.py` и определениях в `llm.py`.

## Проверка
- Быстро: `python factcheck.py "<самая короткая статья пилота>" --force` — время, вердикты адекватные (посмотри 5 утверждений с цитатами).
- Пилот: `python factcheck.py --pilot --force` отвязанно (`Start-Process -WindowStyle Hidden python -ArgumentList 'factcheck.py','--pilot','--force' -RedirectStandardOutput data/factcheck_run4.log -RedirectStandardError data/factcheck_err4.log` из папки проекта), жди до 45 минут (проверяй раз в минуту).
- В отчёте: таблица 10 статей (утверждений, supported %, partial, unsupported, contradicted, wrong_citation, no_source, время, вызовов) и распределение вызовов по моделям из `data/llm_calls.log` за прогон (должно быть 0 вызовов `ds:`).
