# ABS-FARM-TOOL (safe): хаб для многоаккаунтного фарма в экосистеме Abstract

- Источник: ветка софты, сообщение #35332, files
- Категория: tools
- Теги: abstract, фарм, мультиаккаунт, adspower, webshare, gigaverse, tollan, dyli

## Кратко
Архив из 190 файлов (ABS-FARM-TOOL-safe.zip), судя по именам — локальная панель-хаб для управления большим числом аккаунтов в проектах экосистемы Abstract: Gigaverse, Tollan, Dyli, abstract-upvote. Главный вывод: инструмент автоматизирует ежедневные активности с ротацией прокси и браузерных профилей. Список файлов совпадает с архивом #30160.

## Главное
Ядро — модули hub_*.js: hub_accounts (аккаунты), hub_proxy и hub_signer_profiles (прокси и профили подписи), hub_credentials (учётные данные, есть также .py-версия), hub_dyli_api_keys и hub_dyli_auth (ключи и авторизация Dyli), hub_giga_credentials и hub_giga_inventory (аккаунты и инвентарь Gigaverse), hub_balances (балансы), hub_bulk_import (массовый импорт аккаунтов), hub_scheduler и hub_runner (расписание и запуск), hub_telegram (уведомления в Telegram), hub_security. Папка projects/abstract-upvote содержит скрипты сессий AGW (agw_session_setup, agw_session_renewal), голосования (agw_vote_all, agw_vote_selected), ежедневных заданий Dyli (dyli_daily_actions, dyli_rewards), чек-инов (lingo_checkin, spellborne_checkin, portal_streak), gacha_claim, promotion_royale, интеграцию с AdsPower (agw_adspower_setup, agw_adspower_proxy) и webshare_proxy. Папка projects/giga — gigaverse_bot (части part_01…part_99), giga_autopilot, token_reader, telegram_notifier и веб-интерфейс web_ui. Папка projects/tollan — массовые действия по аккаунтам с прокси (tollan_accounts, tollan_proxy, tollan_run). Есть тесты (tests/), wiki с project-status и codebase-map, а также docs/SPR1NTRAY_GIGAVERSE_BOT_AUDIT_2026-07-24.md (аудит бота Gigaverse). Судя по структуре, это фермерский инструмент: один аккаунт — свой прокси и свой профиль.
