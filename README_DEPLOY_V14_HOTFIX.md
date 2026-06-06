# Telegram Betting Bot v14 HOTFIX

## What it fixes

- Startup scan is disabled by default.
- Scan uses strict `ALLOWED_SPORTS` whitelist only.
- Telegram `callback_data` uses short IDs, so `Button_data_invalid` is fixed.
- Output is capped by `MAX_MATCHES_TO_SEND` and `MAX_OPTIONS_PER_MATCH`.
- `ACCEPT` now creates a real open bet in `data/state.json`.
- `/settle <id> win|loss|push` updates bank and stats.
- `safe_send()` prevents one Telegram error from crashing the bot.
- HTTP logs are reduced.

## Critical security action

Your old Telegram token and Odds API key appeared in logs. Rotate them before redeploying:

1. BotFather → revoke/regenerate Telegram token.
2. Odds API dashboard → regenerate API key.
3. Update Railway Variables.
4. Do not put real keys into GitHub files.

## Railway variables

Set these in Railway → Service → Variables:

```env
TELEGRAM_BOT_TOKEN=NEW_TOKEN_HERE
ODDS_API_KEY=NEW_ODDS_KEY_HERE
CHAT_ID=YOUR_CHAT_ID
DATA_FILE=/app/data/state.json
INITIAL_BANK=1019.98
MAX_MATCHES_TO_SEND=10
MAX_OPTIONS_PER_MATCH=2
DAILY_SCAN_ENABLED=false
STARTUP_MESSAGE_ENABLED=false
ALLOWED_SPORTS=basketball_nba,basketball_wnba,icehockey_nhl,tennis_atp_french_open,tennis_wta_french_open
```

## Deployment

Safe path:

1. Stop Railway service.
2. Replace current `main.py` with this `main.py`.
3. Commit to GitHub.
4. Check Railway variables.
5. Deploy.
6. In Telegram, run `/start`, then `/scan` manually.

Do not enable `DAILY_SCAN_ENABLED=true` until manual `/scan` works cleanly.

## Commands

- `/scan` — manual scan with limited candidates.
- `/bank` — current test bank.
- `/bets` — open bets.
- `/stats` — settled stats.
- `/settle <id> win|loss|push` — settle a bet.

## Important note

Rough EV in scan output is not a final betting recommendation. It is only a sorting hint. Final decision still requires v29.1 analysis: scenario, ENV, line movement, injury/context check, EV_CI_Low and stake gate.
