---
ветка: "Щитки, Мемы, Деген плеи"
разговор: c0035
дата: 2026-09-16
категория: security
оценка: partial
теги: ["arc", "bridge-scam"]
---
# Щитки, Мемы, Деген плеи · c0035 · 2026-09-16 01:30

**Выжимка:** Max: при свопе USDG→USDC на мосту ARC потеря ~1.1% (price impact −1.12%). Пост GuarEmperor (переслан): на ARC много фейковых мостов — фронтенд ведёт на адрес мошенника, а не на контракт (transfer USDC на EOA вместо контракта). Адреса из поста не переписываю.

## Сообщения

**#48827 2026-09-16 01:30 Max Wayld 福: Нихуя, даже не в -50% за депозит в ARC ⟪photos: ТИП: интерфейс кошелька или биржи ТЕКСТ: Swap & Bridge | Private | Gas | Send | 1000 | USDG | $1,000.97 | 0.157973 | Receive | 989.999999 | USDC | $989.73 | You don't have enough funds to complete the transaction. | Review bridge | Receive | Best Return | 989.999999 | $989.73 + USDC on Arc | Route | Destination | 0xec3D6...183c3 | Network cost | $0.04 | Price impact | -1.12% | Max. slippage | 2.28% - Auto | Min. received | 989.99999 USDC | Exchange rate | 1 USDG = 0.989999 USDC | Estimated time | 30s СУТЬ: Скриншот интерфейса крипто-моста/свопа с расчётом обмена 1000 USDG на ~989.999999 USDC (курс 1 USDG = 0.989999 USDC), где система выдаёт ошибку «недостаточно средств» для завершения транзакции. Могли скинуть в чат, чтобы обсудить курс/потери (price impact −1.12%) или пожаловаться на то, что обмен не проходит из-за нехватки баланса.⟫**

#48828 2026-09-16 01:30 Max Wayld 福: Технологии

**#48829 2026-09-16 01:33 Max Wayld 福: А там всё ещё челы ищут мосты и какую-то хуйню изобретают когда джампер работает ⟪photos: ТИП: скрин твита ТЕКСТ: GE @GuarEmperor · 12m Just reminder people Many bridge scammers on ARC If you see a lot of Bridge Arc links on your timeline, be sure to check the frontend and also make sure sending to the contract not address EOA. These scammers typically use the Pair usdc ETH/Base via Circle CCTP and they say "we're using CCTP Arc" this larp Example like this : Frontend bridgeappARC (web example) call contract USDC: - Ethereum USDC: 0xA0.... - Base USDC: 0x83.... - Function call: transfer(to, value) - to fixed: 0x5d.... so if u click bridge, wallet sign tx: USDC.transfer(0x5d..., amount) this fake not to contract but send addy scammer, fake and larp bridge router arc [вложенный скрин интерфейса с транзакциями Receive]: 10secs ago 0xb47c...d87b | Receive 0xa7dd...3c34 | +4.4000 USDC ($4.40) 34secs ago 0xa1bd...2367 | Receive 0xcd3a...c543 | +9.5000 USDC ($9.33) 38secs ago 0xd09b...bef8 | Receive 0x9e04...354d | +91.6100 USDC ($61.66) 40secs ago 0x82bf...45d1 | Receive 0xf49a...90cd | +20.0000 USDC ($20.07) 48secs ago 0xb8d7...3376 | Receive 0x4bbd...df36 | +5.0000 USDC ($5.00) 1min 12secs ago 0xe64e...Dff | Receive 0x1654...faa6 | +1.0000 USDC ($1.00) 1min 16secs ago 0xa7c2...D4C7 | Receive 0x2776...8128 | +1.0000 USDC ($1.00) СУТЬ: Предупреждение от @GuarEmperor о мошенниках, которые под видом моста Bridge/ARC/CCTP заставляют подписывать транзакцию transfer() на кошелёк скамера вместо контракта; в чате это скинули как рассылку-осторожность для тех, кто видит такие ссылки в таймлайне.⟫**

#48830 2026-09-16 01:44 Max Wayld 福 (переслано от Таверна КрэгХэка 16.09.2026 01:43:46): началось ⟪photos: ТИП: интерфейс кошелька или биржи ТЕКСТ: frankdegods Buy now / ARCHITECTS $3K at $343.8K MC / rasmr Buy 15s / ARGUS $20K at $19.6M MC / wealthmaxxer Buy 23s / ARGUS $3.5K at $18.2M MC / rasmr Buy 29s / ARGUS $10K at $16.7M MC СУТЬ: Скрин ленты покупок мемкоинов в реальном времени (таймстампы now/15s/23s/29s), где видны ники покупателей, суммы в USD и текущий market cap токенов ARCHITECTS и ARGUS. Такое обычно кидают в чат как сигнал «умные деньги/киты заходят» — для фомо, обоснования покупки или аналитики активности крупных игроков.⟫

#48831 2026-09-16 01:44 Max Wayld 福: 

#48832 2026-09-16 01:47 Nikita Asati: тренчить)
