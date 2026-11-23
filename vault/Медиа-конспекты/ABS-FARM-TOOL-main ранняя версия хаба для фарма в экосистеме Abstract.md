# ABS-FARM-TOOL-main: ранняя версия хаба для фарма в экосистеме Abstract

- Источник: ветка крипта_работа_проекты, сообщение #13735, files
- Категория: tools
- Теги: abstract, фарм, мультиаккаунт, adspower, webshare, gigaverse, tollan, dyli

## Кратко
Архив ABS-FARM-TOOL-main.zip (171 файл) — более ранняя версия того же хаба, что и в #35332 (190 файлов). Главный вывод: набор тот же, но без модулей Gigaverse-инвентаря, автопилота и API-ключей Dyli, которые появились позже.

## Главное
По именам файлов структура совпадает с #35332: hub_*.js (аккаунты, прокси, учётные данные, расписание, Telegram, подписи), dashboard-ui, projects/abstract-upvote (сессии AGW, голосования, чек-ины Lingo, Spellborne, Portal, gacha_claim, promotion royale, AdsPower-интеграция, webshare_proxy), projects/giga (gigaverse_bot и части, run_all, token_reader, web_ui), projects/tollan (аккаунты, прокси, запуск действий). Отличия от #35332 по именам: нет giga_autopilot.py, hub_giga_credentials.js, hub_giga_inventory.js, giga_inventory_filters.js, hub_dyli_api_keys.js и hub_dyli_api_key_provision.js; нет promotion_royale_selected.js и agw_session_renew_selected.js; нет docs/SPR1NTRAY_GIGAVERSE_BOT_AUDIT. Смысл тот же: управление фермой аккаунтов с отдельными прокси и профилями.
