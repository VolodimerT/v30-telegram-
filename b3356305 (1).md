# BettingBot — Phases 1-6

## Структура
```
bot/
├── gates.py          # Hard gates, EV, CI, Kelly, calibration
├── sport_models.py   # Phase 4: football xG / basketball / tennis / hockey
├── edge_engine.py    # Candidate fetch, finalize, sort
├── brm.py            # Bankroll, pick history, stats, reports
├── pipeline.py       # auto + live pipelines, request parser, watchlist
├── main.py           # Telegram handlers + bot entry point
└── requirements.txt
```

## Новые команды (Phase 5 + 6)

| Команда | Описание |
|---|---|
| `/live epl` | Phase 5 — in-play сканер с тайтер-гейтами (book_count≥4, age≤60мин, cap SUPPORT) |
| `/live nba` | Live NBA |
| `/model football home_xg=1.6 away_xg=0.9 selection=home` | Phase 4 ручной тест модели |
| `/model basketball home_ortg=114 home_drtg=108 away_ortg=110 away_drtg=112 selection=home` | |
| `/model tennis p1_surface_winrate=0.62 p1_hold_rate=0.72 selection=p1` | |
| `/model hockey home_goalie_sv=0.920 away_b2b=true selection=over total_line=5.5` | |

## Деплой на Railway
1. Загрузи все 6 файлов в корень проекта
2. ENV: `TELEGRAM_BOT_TOKEN`, `ODDS_API_KEY`
3. Start command: `python main.py`

## Live mode (Phase 5) — отличия от /auto
- `max_odds_age = 60 мин` (vs 180)
- `min_book_count = 4` (vs 3)
- Максимальный класс = SUPPORT (CORE блокируется `LIVE_CAP`)
- Ставки уменьшены через `LIVE_MODE_RULES`
- API параметр `inPlay=true`
