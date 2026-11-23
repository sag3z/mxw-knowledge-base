# MXW-FormLab: инструмент для массовой отправки форм (Google Forms и Apps Script)

- Источник: ветка софты, сообщение #56023, files
- Категория: tools
- Теги: google-forms, apps-script, форм-фарм, прокси, кошельки, капча, вайтлист

## Кратко
Архив MXW-FormLab-new.zip (625 файлов, в файле перечислено 205) — набор примеров и инструкций для автоматической отправки анкет и форм. По документации skill form-farm, это заявки на участие и вайтлисты с привязкой адреса кошелька, прокси и обходом капчи. Главный вывод: инструмент построен под массовую подачу форм, а не под одну заявку.

## Главное
Папка .agents/skills/form-farm — инструкция для ИИ-агента. В references: 01-recon (разведка формы), 02-submit-contract (контракт отправки), 03-live-proof, 04-identity, 05-proxies-and-limits (прокси и лимиты), 06-answers, 07-packaging, 08-captcha, 09-gotchas. В cases разобраны ошибки: apps-script-404, closed-whitelist-410, two-opposite-409s, decorative-vs-real-field (декоративное поле вместо реального), free-captcha-fallback. Пять примеров в examples: 01-basic-json-api, 02-google-forms, 03-apps-script, 04-session-handshake, 05-captcha-fallback — у каждого свой config.py, main.py и пакет (basic, gform, script, shake, mathcap), плюс общий набор mxw/ (identity, proxies, wallets, ledger, launch, runner, ui). В data — шаблоны my-proxies.txt и my-wallets.txt. В docs — 01-start, 02-first-bot, 03-mistakes, 04-safety. Файлы CLAUDE.md, GEMINI.md, QWEN.md, AGENTS.md и RECON.md — инструкции для разных ИИ-агентов в каждом примере. Судя по именам, ядро — массовая отправка форм с разных кошельков и прокси.
