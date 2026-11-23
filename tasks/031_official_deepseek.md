# Задача: подключить официальный DeepSeek API и перевести на него проверку фактов и чат

Не читай и не печатай `.llm_env` целиком (там ключи) — `llm.py` сам читает его построчно (`ENV = dict(...)`); ключи никогда не выводить в лог/консоль. Не читай `tasks/*.log`, НЕ открывай PNG. Меняй `llm.py`, `factcheck.py`, `app/server.py`.

## Факты (замерил ревьюер)
- В `.llm_env` теперь есть `DEEPSEEK_API_KEY` и `DEEPSEEK_BASE_URL=https://api.deepseek.com/v1` (официальный провайдер). Модели: `deepseek-flash` (быстрая, ~1.8 с на короткий запрос), `deepseek-v4-pro`.
- Официальный выдерживает 24 параллельных запроса без 429; reformboss (`LLM_BASE_URL`/`LLM_API_KEY`) — только ~3, на 8 параллельных 7 получают 429.
- Проверка фактов сейчас: судья `qwen3.8-max` (reformboss) отвечает в среднем 8k, до 25k токенов → 1.5–5 часов на статью.

## 1. `llm.py` — два провайдера
- Маршрутизация по префиксу модели: `"ds:<model>"` → официальный DeepSeek (`DEEPSEEK_BASE_URL`/`DEEPSEEK_API_KEY`), без префикса → reformboss как сейчас. Сделай это в `_stream` (и `chat_stream`), не ломая существующие вызовы. Если в `.llm_env` нет DEEPSEEK_* — `ds:` модели падают понятной ошибкой, и срабатывает fallback.
- Отдельные семафоры параллельности на провайдера: reformboss — 3, официальный — 16 (константы `LIMIT_REFORMBOSS`, `LIMIT_DEEPSEEK`). Все вызовы `chat`/`chat_json`/`chat_stream` проходят через свой семафор — тогда параллельные потоки в скриптах не устраивают 429.
- Логирование в `data/llm_calls.log` — с префиксом модели как есть (`ds:deepseek-flash`).
- Константы: `FAST = "ds:deepseek-flash"`, `PRO = "ds:deepseek-v4-pro"`.

## 2. `factcheck.py`
- Судья по умолчанию `ds:deepseek-flash` (константа JUDGE, флаг `--judge`), выделение утверждений и поиск настоящего источника — тоже им. Второе мнение (только для `unsupported`/`contradicted`) — `qwen3.8-max` (reformboss, другая модель-семейство; флаг `--second`), fallback второго мнения — `ds:deepseek-v4-pro`.
- Параллельность: общий пул задач на все разделы всех статей, 16 потоков (семафоры в llm.py сами ограничат reformboss до 3).
- Ограничь размер ответа судьи: `max_tokens` 6000, проси компактный JSON по номерам утверждений (без повторения текста), пачки по 20 утверждений.
- Флаг `--all` — проверить ВСЕ статьи хранилища (папки категорий, `Схемы/` с подпапками, `Проекты и инструменты/`; кроме `Источники/`, `Медиа-конспекты/`, `00 Главная.md`, «Календарь активностей»), resume (готовые не трогать без `--force`), прогресс в консоль: «готово N/M статей, ~X мин осталось».

## 3. `app/server.py` — чат
- Генерация ответа чата, переформулировка/ключевые слова и дайджест — через `ds:deepseek-flash`, fallback на текущий reformboss `deepseek-v4.1-flash`. Таймаут расширения ключевых слов (8 с) оставить.

## Проверка
- `python -c "import llm; print(llm.chat('ds:deepseek-flash','','ответь: ок',max_tokens=20))"` (ключ не печатать).
- Останови текущий процесс `factcheck.py --pilot` (если ещё идёт; он на старом судье) и запусти `python factcheck.py --pilot --force` отвязанно (`Start-Process -WindowStyle Hidden python -ArgumentList 'factcheck.py','--pilot','--force' -RedirectStandardOutput data/factcheck_run3.log -RedirectStandardError data/factcheck_err3.log` из папки проекта). Дождись (проверяй раз в минуту, до 40 минут). Цель — ≤ 10 минут на статью в среднем.
- Перезапусти сервер отвязанно (`Start-Process -WindowStyle Hidden python -ArgumentList '-u','app/server.py','8765'`; старый останови) и проверь чат: `python tasks/020_check.py` — время до первого delta.
- В отчёте: таблица 10 статей (утверждений, supported %, partial, unsupported, contradicted, wrong_citation, no_source, время, вызовов) + сравнение времени с прошлым прогоном; время ответа чата до/после.
