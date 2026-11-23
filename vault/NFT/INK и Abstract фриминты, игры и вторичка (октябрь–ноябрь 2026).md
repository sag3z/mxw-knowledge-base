---
категория: nft
ветки: [nft, soft]
период: 2026-10-14 — 2026-11-02
источников: 6
модель: deepseek-v4.1-flash
---
# INK и Abstract: фриминты, игры и вторичка (октябрь–ноябрь 2026)

**Кратко.** В октябре 2026 на INK и Solana прошла серия форм: CXNFT на Solana, Inkbots на 888, безымянный проект на 999, Inktropy на 1111 и фриминт Inkpebble на 1 000. По Inktropy Max Wayld оценил шанс нормального выхода как маленький, но отметил, что за 2 дня через форму «проабузили только 10К акков». В ноябре внимание сместилось к Hamieverse с нераскрытыми supply/MP, Boinker Agent NFTs, где из 4К возможных минтов забрали около 700, и вторичке Boinkorage: флор $12.71, топ-оффер $11.05, 627 владельцев. C.Rollins по Boinkorage ждал анонс и предполагал уменьшение саплая «в течении 10 минут».

## Октябрь: формы, саплаи и условия

- **14.10 — CXNFT (Solana).** hh дал ссылки `https://x.com/cxnfts` и `https://cxnft.world/`, назвал проект «ерли нфт на солане» и написал: «завтра чет будет, возможно закроют форму». К сообщению приложен архив `cxnfts-bot-hh-public.zip` на 35 файлов [[soft-c0032]].
- **14.10 — Inkbots (INK).** hh дал `https://x.com/InkbotsNFT` и форму `https://inkbots.fun/whitelist`; саплай — 888, по его словам, «возможно будет какой то стейк или p2e». По выжимке ветки проект на INK. Архив — `inkbots-bot-hh-public.zip`, 34 файла [[soft-c0032]].
- **17.10 — безымянный проект на INK, 999 supply.** hh: «форма должна была уже закрыться но никакого анонса небыло». Архив — `unruly-bot-hh-public.zip`, 36 файлов [[soft-c0035]].
- **20.10 — Inktropy (INK), 1111 supply.** Max Wayld 福 дал `https://inktropy.xyz/` и `https://x.com/inktropynft`. Его оценка: «На INK 1111 говна. Маленький шанс что будет норм, но аудиторию лутают норм а за 2 дня проабузили только 10К акков через форму». Архив — `inktropy-bot-MXW.zip`, 34 файла [[soft-c0038]].
- **25.10 — Inkpebble (INK), 1К supply, фриминт.** hh дал `https://x.com/inkpebblexyz` и форму `https://inkpebble.xyz/apply`; описание: «1к сап / инк / фришка». Архив — `inkpebble-bot-hh-public.zip`, 38 файлов [[soft-c0041]].

## Софт: каркас под мультиаккаунтные формы

Во всех октябрьских дропах к сообщению прикладывали публичный архив бота. Общий набор повторяется: `main.py`, `config.py`, `requirements.txt`, `examples/proxies.txt.example`, `examples/wallets.txt.example`, пакет с `runner.py`, `submit.py`, `transport.py`, `ledger.py`, `run_lock.py`, `slot_pool.py`, тесты и `tools/secret_scan.py`. По составу это схема с пулом кошельков и прокси, очередью слотов, учётом отправок и блокировкой повторного запуска [[soft-c0032]] [[soft-c0035]] [[soft-c0038]] [[soft-c0041]].

- **CXNFT:** пакет `cxnft`, отдельные `token_store.py`, `archive_style_fields.py`, `docs/references/cxnft-api.md`, `tools/public_release.py`; всего 35 файлов [[soft-c0032]].
- **Inkbots:** пакет `inkbots`, `docs/references/inkbots-api.md`; 34 файла [[soft-c0032]].
- **Безымянный INK-проект / unruly:** `lab-project.json`, `gate.py`, `tools/audit_release.py`, `tools/queued_run.py`; 36 файлов [[soft-c0035]].
- **Inktropy:** `inktropy/answers.py`, `inktropy/submit.py` и модуль `mxw/` (`app.py`, `cfg.py`, `identity.py`, `launch.py`, `proxies.py`, `runner.py`, `wallets.py`, `ui.py`), а также `START.bat`, `start.command`, `start.sh`, `RECON.md`, `CLAUDE.md`, `GEMINI.md`, `QWEN.md`; 34 файла [[soft-c0038]].
- **Inkpebble:** в пакете есть `protocol.py`, `proxy_manager.py`, `routing.py`; 38 файлов [[soft-c0041]].

## Вторичка: Boinkorage (INK)

2 ноября 2026 hh скинул скрин страницы коллекции «Boinkorage | INK | BY THEBOINKERS | 920 | NOV 2026 | 0% CREATOR FEE | LISTING NOW». На скрине: флор $12.71, изменение флора за 1 день 0%, топ-оффер $11.05, объём за 24 часа $8,702.50, общий объём $8,702.50, выставлено 44 лота (4.8%), уникальных владельцев 627 (68.2%), 76 офферов, сумма всех офферов по коллекции $290.64 [[nft-c0716]].

В попапе «Bidders» показан оффер $10.82: 2 оффера, объём $21.64. В списке — «You» с 1 предметом, 2 офферами и 1 бидом, а также «Mercenary322» с 1 предметом, 1 оффером и 1 бидом. hh прокомментировал: «и вот остались только наши биды» [[nft-c0716]].

C.Rollins по этой же коллекции написал: «Announcement is coming soon», а затем: «кароче уменьшит саплай думаю в течении 10 минут». Вторая фраза — его предположение, а не подтверждённый анонс [[nft-c0716]].

На скрине верхний оффер $11.05, а в попапе — $10.82; это разные уровни/лоты, а не обязательно один и тот же оффер [[nft-c0716]].

## Hamieverse, Boinker Agent и Abstract

- **Hamieverse.** hh переслал пост AR Alpha Circle от 02.11.2026 16:42:37: «Keeping an eye on @hamieverse». Указано: supply и MP — TBA; Hamieverse — entertainment-бренд с играми, комиксами и коллекционными предметами; doxxed-команда 40+ человек, строят 2+ года; 23+ успешные благотворительные миссии по миру. Предыдущая коллекция — по ссылке `https://x.com/tenacious_ar/status/2117278692473024951` [[nft-c0715]].
- **Boinker Agent NFTs (по выжимке — на Ink).** Max Wayld переслал сообщение из «Возрождение бубылды 🐼» от 02.11.2026 18:05:39 со ссылкой `https://opensea.io/collection/boinkeragenfts/overview` и словами «Следите за минтом». Его мнение: «солд на фкфс или даже до паблика, флор будет около мп, завтра x2»; отдельно — «На инке порой деляют необъяснимую хуйню». Max Wayld также написал: «Из 4К возможных минтов забрали 700» [[nft-c0715]].
- **Abstract.** C.Rollins скинул `https://opensea.io/collection/abstract-workers2/activity` без комментария. Других условий, цен или саплая по Abstract в сообщениях нет [[nft-c0715]].
- По заметке к ветке, C.Rollins отметил, что минт на Ink может держаться дольше, чем на Robinhood [[nft-c0715]].

## Выводы и правила

- **Не путать сети.** CXNFT — Solana; Inkbots, Inktropy, Inkpebble и Boinkorage — INK; Boinker Agent NFTs по выжимке — Ink; по Hamieverse сеть в сообщении не указана [[soft-c0032]] [[soft-c0038]] [[soft-c0041]] [[nft-c0715]] [[nft-c0716]].
- **На INK формы могут закрыться без анонса.** 17.10 форма на 999 supply должна была закрыться, но анонса не было; по CXNFT 14.10 hh ждал, что форму «возможно закроют» уже завтра. Отправлять форму сразу, не ожидая подтверждения [[soft-c0035]] [[soft-c0032]].
- **Малый саплай не равен лёгкому выходу.** Inktropy — 1111 supply, но за 2 дня через форму прошло около 10К аккаунтов; Max Wayld оценил шанс нормального выхода как маленький [[soft-c0038]].
- **Фриминт из октябрьской серии — Inkpebble.** 1К supply, форма `https://inkpebble.xyz/apply`, описание hh — «фришка». CXNFT — отдельный Solana-дроп, не INK [[soft-c0041]] [[soft-c0032]].
- **По Boinkorage смотреть на стакан.** Флор $12.71, топ-оффер $11.05, 76 офферов на $290.64, 627 владельцев на 920 предметов, выставлено 4.8%. Уменьшение саплая C.Rollins называл предположительно, со сроком «в течении 10 минут» [[nft-c0716]].
- **Boinker Agent — прогноз, не факт.** 700 из 4К возможных минтов; ожидания Max Wayld — флор около MP и «завтра x2». Это его мнение, а не подтверждённые данные [[nft-c0715]].
- **В архивах — готовые каркасы под мультиаккаунты.** Есть `wallets.txt.example`, `proxies.txt.example`, `ledger.py`, `slot_pool.py`, `submit.py`; в наборах также `tools/secret_scan.py` для проверки на утечки секретов [[soft-c0032]] [[soft-c0038]] [[soft-c0041]].
