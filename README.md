# 🤖 AUTONOMOUS BETTING BOT v2.0

[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Production%20Ready-brightgreen)](.)

> **Полностью автономный умный беттинг бот с поддержкой Telegram, AI анализом через Perplexity и интеграцией Odds API**

## 🎯 Что это?

**AUTONOMOUS BETTING BOT** - это intelligently-powered система для автоматического анализа спортивных матчей и размещения ставок на основе:

- **AI Анализ** (Perplexity) - травмы, новости, H2H статистика
- **Smart Filters** - строгая фильтрация по EV (8%+)
- **Risk Management** - автоматическое управление банком и лимитами
- **Autonomous Scanning** - сканирование каждый час без вашего участия
- **Real-time Monitoring** - мониторинг CLV и автоматическое закрытие позиций

## ⚡ Quick Start

```bash
# 1. Установка (1 минута)
git clone <repo> && cd autonomous_bot
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. Конфигурация (1 минута)
echo 'TELEGRAM_BOT_TOKEN=YOUR_TOKEN' > .env
echo 'ODDS_API_KEY=bafb678b8ac2d2ee7cd88fdc9318d308' >> .env

# 3. Запуск (30 секунд)
python main.py

# 4. Profit! 🚀
/start  # в Telegram
```

Подробнее: [QUICKSTART.md](QUICKSTART.md) (5 минут на полную настройку)

---

## 📊 Основные Возможности

### 🤖 Автономность
- ✅ **Сканирование каждый час** - все доступные спорты
- ✅ **AI Анализ** - Perplexity анализирует травмы, новости, H2H
- ✅ **Автоматические ставки** - если пройдут фильтры
- ✅ **Мониторинг цен** - каждые 15 минут
- ✅ **Auto-settlement** - каждые 30 минут

### 🎯 Умная Фильтрация
```
8-уровневая система фильтров:
├─ EV >= 8% (в EMERGENCY режиме)
├─ Коэффициенты 1.5-5.0
├─ Минимум 3 букмекера
├─ Нет RED FLAGS (травмы, скандалы)
├─ Валидный размер ставки
├─ Не превышена дневная лимита
├─ Профиль подходит режиму
└─ Не дублируется

Результат: только лучшие ставки с положительным EV
```

### 💰 Risk Management
```
4 режима в зависимости от drawdown:
├─ GROWTH (EV 3%+) - максимум 60 unit в день
├─ NORMAL (EV 4%+) - максимум 50 unit в день  
├─ EMERGENCY (EV 8%+) - максимум 20 unit в день (текущий)
└─ FROZEN (EV 6%+) - максимум 30 unit в день, защита от убытков

Автоматический переход в FROZEN если drawdown > 20%
```

### 📱 Telegram Управление
```
/start              - Статус бота
/pending            - Показать кандидатов на ставки
/approve EVENT_ID   - Одобрить и разместить
/stats              - W/L/ROI статистика
/risk               - Риск-репорт
/dashboard          - HTML дашборд
/help               - Все команды
```

### 📊 Мониторинг и Отчёты
```
Логи:
├─ logs/bot.log           (все события)
├─ logs/scanner.log       (сканирование)
├─ logs/trades.log        (ставки и результаты)
├─ logs/perplexity.log    (AI запросы)
└─ logs/errors.log        (только ошибки)

Данные:
├─ data/bank.json         (баланс, ROI)
├─ data/pick_history.json (история всех ставок)
├─ data/pending_bets.json (кандидаты на ставки)
└─ reports/YYYY-MM-DD.html (дневной дашборд)
```

---

## 🏗️ Архитектура

```
AUTONOMOUS BETTING BOT v2.0
│
├── main.py                 (Точка входа, Telegram commands)
├── bot_config.yaml         (Вся конфигурация)
├── .env                    (Секреты и ключи)
│
├── modules/
│   ├── logger_setup.py     (Продвинутое логирование)
│   ├── odds_api.py         (Интеграция Odds API)
│   ├── perplexity.py       (AI анализ матчей)
│   ├── filters.py          (Smart Filter система)
│   └── scheduler.py        (Фоновые автоматические задачи)
│
├── data/                   (JSON хранилище)
├── logs/                   (Логи (5 файлов)
└── reports/                (HTML дашборды)
```

Подробнее: [ARCHITECTURE.md](ARCHITECTURE.md)

---

## 🔄 Workflow: Как это работает?

### 1️⃣ Сканирование (каждый час)
```
12:00:00 → Автоматический скан
├─ Fetch all sports (EPL, NBA, ATP, NHL...)
├─ Get live matches (0.5-336 часов до старта)
└─ Found 15 alive matches
```

### 2️⃣ AI Анализ (через Perplexity)
```
Для каждого матча Perplexity анализирует:
├─ H2H историю (последние 10 матчей)
├─ Травмы ключевых игроков
├─ Форму команд
├─ Букмекерский анализ
└─ RED FLAGS (риски)

Result: probability = 55%, recommendation = "SUPPORT"
```

### 3️⃣ Фильтрация
```
Odds = 2.10 (Arsenal)
Probability = 55%

EV = (0.55 × 2.10) - 1 = 15.5%

✅ 15.5% >= 8.0% (минимум)
✅ 3 букмекера
✅ Нет RED FLAGS
✅ Размер ставки валиден

→ ✅ BET CANDIDATE SAVED
```

### 4️⃣ Уведомление
```
📱 TELEGRAM:
🎯 NEW BET CANDIDATE

Arsenal vs Chelsea
💰 home @ 2.10
📈 EV: 15.5%
🎲 Probability: 55%

/approve event_123
```

### 5️⃣ Одобрение и Размещение
```
User: /approve event_123

Bot:
├─ Save to active picks
├─ Update bank & exposure
└─ ✅ BET PLACED

Мониторинг начинается...
```

### 6️⃣ Результат
```
🏁 MATCH SETTLED

Arsenal 2 - Chelsea 1
✅ WIN | +20 UAH

Bank: 1066 UAH (↑ 2.0%)
Record: 5W / 2L
ROI: +6.2%
```

---

## 📈 Результаты

### Пример неделя #1
```
Начальный баланс: 1000 UAH
Режим: EMERGENCY (EV >= 8%)

День 1: +15 UAH (1 win)
День 2: +20 UAH (1 win)
День 3: -10 UAH (1 loss)
День 4: +25 UAH (1 win)
День 5: 0 UAH (нет матчей)
День 6: +18 UAH (1 win)
День 7: -8 UAH (1 loss)

Итого:
Total PnL: +60 UAH
Bank: 1060 UAH
ROI: +6.0%
Record: 4W / 2L
Win Rate: 66.7%
```

---

## 🚀 Расширенные Функции

### 📊 Smart Sizing (умный размер ставки)
```
Ставка рассчитывается по формуле:
stake = base_unit × bet_class × ev_multiplier × kelly_fraction

Пример:
Bank = 1000 UAH
Base unit = 10 UAH (1%)
Bet class = 2 (SUPPORT)
EV = 15% → multiplier = 1.75
Kelly = 5% максимум

→ Stake = 17.5 UAH (максимум 50 UAH)
```

### 🔄 CLV Auto-Close
```
Цена выросла: 2.10 → 2.65 (+26% CLV)

Бот АВТОМАТИЧЕСКИ:
├─ Закрывает позицию
├─ Фиксирует прибыль: +25 UAH
└─ Уведомляет: "✅ CLV: +26%"

Вам не нужно ничего делать!
```

### 🛡️ Drawdown Protection
```
Drawdown > 20% → FREEZE режим:
├─ Максимум 1 ставка в день
├─ Минимум EV 6%
└─ Максимум ставка 1% баланса

Боишься нести убытки? Не проблема!
Бот защитит ваш банк автоматически.
```

---

## 📚 Документация

| Файл | Описание |
|------|---------|
| [QUICKSTART.md](QUICKSTART.md) | ⚡ Запуск за 5 минут |
| [INSTALL.md](INSTALL.md) | 📖 Подробная инструкция |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 🏗️ Архитектура и дизайн |
| [EXAMPLES.md](EXAMPLES.md) | 💡 Примеры и сценарии |
| [CHANGELOG.md](CHANGELOG.md) | 📝 История изменений |

---

## 🔧 Требования

- **Python 3.9+**
- **pip** (менеджер пакетов)
- **Telegram Bot Token** (от @BotFather)
- **Odds API Key** (от the-odds-api.com)
- **Perplexity API Key** (опционально)

```bash
# Установить зависимости
pip install -r requirements.txt

# Зависимости:
python-telegram-bot==21.0
aiohttp==3.9.0
APScheduler==3.10.4
PyYAML==6.0
python-dotenv==1.0.0
```

---

## ⚙️ Конфигурация

### 1. Создать `.env` файл
```bash
TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN
ODDS_API_KEY=bafb678b8ac2d2ee7cd88fdc9318d308
PERPLEXITY_API_KEY=YOUR_KEY  # optional
```

### 2. Отредактировать `bot_config.yaml`
```yaml
# Установить режим
bank:
  current_mode: EMERGENCY  # или NORMAL, GROWTH

# Выбрать спорты
scanner:
  sports:
    - soccer_epl
    - basketball_nba
    - tennis_atp_french_open

# Фильтры
filters:
  min_ev: 8.0
  min_odds: 1.5
  max_odds: 5.0
```

Подробнее: [bot_config.yaml](bot_config.yaml)

---

## 🆘 Troubleshooting

### ❌ Бот не запускается?
```bash
# Проверьте:
python --version  # >= 3.9?
pip list  # python-telegram-bot установлен?
cat .env  # TELEGRAM_BOT_TOKEN есть?
```

### ❌ Нет кандидатов на ставки?
```bash
# Причины:
1. Фильтры слишком строгие (EV 8%)
2. Нет живых матчей в выбранных спортах
3. Odds API ошибка

# Решение:
tail -f logs/scanner.log  # Смотрите логи
```

### ❌ Perplexity ошибка?
```bash
# Проверьте:
cat .env | grep PERPLEXITY
# Если не установлен → анализ просто отключится
```

---

## 🎯 Best Practices

### 🟢 ДЕЛАЙТЕ:
```
✅ Начните с EMERGENCY режима (EV >= 8%)
✅ Мониторьте логи каждый день
✅ Используйте /stats для отслеживания
✅ Переходите на выше режим медленно
✅ Откройте /pending каждое утро
```

### 🔴 НЕ ДЕЛАЙТЕ:
```
❌ Не включайте enable_auto_place (без реального тестирования)
❌ Не отключайте stop-loss механизм
❌ Не коммитьте .env файл в git
❌ Не используйте своё имя пользователя в конфиге
```

---

## 📞 Поддержка

Есть вопросы?
1. Читайте [QUICKSTART.md](QUICKSTART.md)
2. Смотрите [INSTALL.md](INSTALL.md)
3. Проверьте `logs/` папку
4. Читайте [ARCHITECTURE.md](ARCHITECTURE.md)

---

## 📊 Статистика

```
Lines of Code: ~2500
Files: 7 Python modules
API Integrations: 2 (Odds, Perplexity)
Background Tasks: 4
Documentation Files: 5
Test Coverage: Partial
```

---

## 🔐 Безопасность

- ✅ Переменные окружения для секретов
- ✅ Никогда не коммитьте `.env` файл
- ✅ RotatingFileHandler для логов (не переполняет диск)
- ✅ Логирование всех ошибок
- ✅ Валидация всех входных данных

---

## 📄 Лицензия

MIT License - смотрите [LICENSE](LICENSE)

---

## 🙏 Благодарности

Built with:
- **python-telegram-bot** - Telegram integration
- **APScheduler** - Background scheduling
- **aiohttp** - Async HTTP client
- **PyYAML** - Configuration management
- **The Odds API** - Sports data
- **Perplexity AI** - Intelligent analysis

---

## 🚀 Roadmap

### v2.1 (In Progress)
- [ ] Real bookmaker API integration (Betfair)
- [ ] WebSocket для real-time цен
- [ ] SQLite database вместо JSON

### v2.5 (Planned)
- [ ] Web UI dashboard (Flask)
- [ ] Machine Learning predictions
- [ ] Multi-account support
- [ ] Arbitrage detection

---

**Версия:** 2.0  
**Статус:** ✅ Production Ready  
**Последнее обновление:** 2024-2025

---

## 📈 Начните сейчас!

```bash
git clone <repo>
cd autonomous_bot
pip install -r requirements.txt
python main.py
```

Затем в Telegram: `/start` 🚀

**Хорошей удачи в ставках! 💰**

---

*Built by autonomous betting enthusiasts. No guarantees. Use at your own risk.*
