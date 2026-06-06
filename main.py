"""
Betting Bot RECOVERY — based on last working v8.0 file.
Purpose:
- Restore Telegram polling reliably on Railway.
- No scan spam on startup by default.
- Uses /scan manually.
- Uses Railway venv via railway_start.sh.
"""

from __future__ import annotations

print("BOOT: recovery main.py imported", flush=True)

import os
import json
import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ContextTypes, CommandHandler, CallbackQueryHandler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", force=True)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logger = logging.getLogger("betting-bot-recovery")

UTC = timezone.utc

def env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "y", "on"}

def env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except Exception:
        logger.warning("Bad int env %s=%r; using %s", name, raw, default)
        return default

def env_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        return float(raw)
    except Exception:
        logger.warning("Bad float env %s=%r; using %s", name, raw, default)
        return default

def parse_chat_id(raw: str) -> Optional[int]:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except Exception:
        logger.warning("CHAT_ID is not int: %r", raw)
        return None

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = parse_chat_id(os.getenv("CHAT_ID", ""))
ODDS_KEY = os.getenv("ODDS_API_KEY", "").strip()

INITIAL_BANK = env_float("INITIAL_BANK", 1019.98)
DAILY_SCAN_ENABLED = env_bool("DAILY_SCAN_ENABLED", "false")
STARTUP_MESSAGE_ENABLED = env_bool("STARTUP_MESSAGE_ENABLED", "false")
DAILY_SCAN_HOUR_UTC = env_int("DAILY_SCAN_HOUR_UTC", 8)
MAX_MATCHES_TO_SEND = env_int("MAX_MATCHES_TO_SEND", 10)
MAX_OPTIONS_PER_MATCH = env_int("MAX_OPTIONS_PER_MATCH", 2)

DEFAULT_ALLOWED_SPORTS = (
    "basketball_nba",
    "basketball_wnba",
    "icehockey_nhl",
    "tennis_atp_french_open",
    "tennis_wta_french_open",
)
ALLOWED_SPORTS = {
    s.strip()
    for s in os.getenv("ALLOWED_SPORTS", ",".join(DEFAULT_ALLOWED_SPORTS)).split(",")
    if s.strip()
}

# Adaptive filter thresholds by odds tier
TIERS = [
    # (odds_min, odds_max, min_ev, min_kelly, min_conf)
    (1.80, 2.00, 0.10, 0.02, 0.55),
    (2.00, 3.00, 0.08, 0.015, 0.52),
    (3.00, 5.01, 0.05, 0.01, 0.50),
]

KELLY_FRACTION = 0.25
BOOTSTRAP_THRESHOLD = 100

_pending: dict = {}
_open_bets: dict = {}
_results: list = []
_bet_counter = 0
_bank = INITIAL_BANK

SPORT_CONF = {
    "soccer": {"h2h": 0.52, "spreads": 0.51, "totals": 0.53, "btts": 0.52},
    "basketball": {"h2h": 0.54, "spreads": 0.53, "totals": 0.54},
    "tennis": {"h2h": 0.53, "spreads": 0.52, "totals": 0.52},
    "mma": {"h2h": 0.51},
    "baseball": {"h2h": 0.52, "totals": 0.52},
    "hockey": {"h2h": 0.52, "totals": 0.53},
    "icehockey": {"h2h": 0.52, "spreads": 0.51, "totals": 0.53},
    "americanfootball": {"h2h": 0.53, "spreads": 0.54, "totals": 0.53},
    "default": {"default": 0.51},
}

ALLOWED_MARKETS = ("h2h", "spreads", "totals")


def _settled_count() -> int:
    return len(_results)


def _get_tier(odds: float):
    for lo, hi, min_ev, min_kelly, min_conf in TIERS:
        if lo <= odds < hi:
            return min_ev, min_kelly, min_conf
    return None


def _calc(conf: float, odds: float):
    ev = conf * (odds - 1) - (1 - conf)
    raw_k = (conf * odds - 1) / (odds - 1) if odds > 1 else 0
    kelly = max(raw_k * KELLY_FRACTION, 0)
    return ev, kelly


def _stake(kelly: float) -> float:
    raw = kelly * _bank
    return max(round(min(raw, _bank * 0.10), 0), 10.0)


def _passes(odds: float, conf: float):
    tier = _get_tier(odds)
    if tier is None:
        return False, "odds out of range"
    min_ev, min_kelly, min_conf = tier
    ev, kelly = _calc(conf, odds)
    if conf < min_conf:
        return False, f"conf {conf:.0%} < {min_conf:.0%}"
    if ev < min_ev:
        return False, f"EV {ev:.3f} < {min_ev}"
    if kelly < min_kelly:
        return False, f"Kelly {kelly*100:.1f}% < {min_kelly*100:.1f}%"
    return True, f"EV={ev:.3f} Kelly={kelly*100:.1f}% conf={conf:.0%}"


def _kb_pick(mid: int, stake: float):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"ACCEPT {int(stake)} UAH", callback_data=f"acc:{mid}:{int(stake)}"),
        InlineKeyboardButton("SKIP", callback_data=f"skip:{mid}"),
    ]])


def _kb_settle(bet_id: int):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("WIN", callback_data=f"win:{bet_id}"),
        InlineKeyboardButton("LOSS", callback_data=f"loss:{bet_id}"),
        InlineKeyboardButton("PENDING", callback_data=f"pend:{bet_id}"),
    ]])


def _kb_done(label: str):
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="noop")]])


def _conf_for(sport_key: str, market_key: str) -> float:
    sport_base = sport_key.split("_")[0]
    table = SPORT_CONF.get(sport_base, SPORT_CONF["default"])
    return table.get(market_key, table.get("default", 0.51))


async def safe_send(bot, chat_id: int, text: str, **kwargs):
    try:
        return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except Exception as e:
        logger.exception("Send failed: %s", e)
        return None


async def fetch_picks() -> list:
    if not ODDS_KEY:
        logger.warning("No ODDS_API_KEY — returning empty list")
        return []

    picks = []
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get("https://api.the-odds-api.com/v4/sports", params={"apiKey": ODDS_KEY})
        if r.status_code != 200:
            logger.error("Sports list error: %s %s", r.status_code, r.text[:300])
            return []

        active = [s["key"] for s in r.json() if s.get("active")]
        if ALLOWED_SPORTS:
            active = [s for s in active if s in ALLOWED_SPORTS]
        logger.info("Active allowed sports: %s", active)

        for sport_key in active:
            try:
                r2 = await client.get(
                    f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds",
                    params={
                        "apiKey": ODDS_KEY,
                        "regions": "eu,uk",
                        "markets": ",".join(ALLOWED_MARKETS),
                        "oddsFormat": "decimal",
                    },
                    timeout=10.0,
                )
                if r2.status_code != 200:
                    logger.warning("%s odds error: %s", sport_key, r2.status_code)
                    continue
                events = r2.json()
            except Exception as e:
                logger.warning("%s: %s", sport_key, e)
                continue

            for event in events:
                home = event.get("home_team", "")
                away = event.get("away_team", "")
                if not home or not away:
                    continue
                match_name = f"{home} vs {away}"
                bookmakers = event.get("bookmakers", [])
                if not bookmakers:
                    continue

                per_match = 0
                for market in bookmakers[0].get("markets", []):
                    mk = market.get("key", "")
                    if mk not in ALLOWED_MARKETS:
                        continue
                    conf = _conf_for(sport_key, mk)
                    for outcome in market.get("outcomes", []):
                        if per_match >= MAX_OPTIONS_PER_MATCH:
                            break
                        odds = float(outcome.get("price", 0.0) or 0.0)
                        if not (1.80 <= odds <= 5.00):
                            continue
                        name = outcome.get("name", "")
                        ev, kelly = _calc(conf, odds)
                        picks.append({
                            "match": match_name,
                            "sport": sport_key,
                            "market": mk,
                            "selection": name,
                            "odds": odds,
                            "conf": conf,
                            "ev": ev,
                            "kelly": kelly,
                        })
                        per_match += 1
                    if per_match >= MAX_OPTIONS_PER_MATCH:
                        break

    picks.sort(key=lambda p: (p["ev"], p["kelly"]), reverse=True)
    logger.info("Total raw picks: %d", len(picks))
    return picks


async def scan(app=None):
    logger.info("SCAN START")
    raw = await fetch_picks()

    passed, skipped = [], []
    for p in raw:
        ok, reason = _passes(p["odds"], p["conf"])
        if ok:
            passed.append((p, reason))
        else:
            skipped.append((p, reason))

    logger.info("Passed: %d Skipped: %d", len(passed), len(skipped))

    if not CHAT_ID:
        logger.warning("CHAT_ID missing; scan cannot send messages")
        return

    if not passed:
        msg = f"Scan complete: 0 picks passed filters\nSkipped {len(skipped)} | Bank: {_bank:.0f} UAH"
        if app:
            await safe_send(app.bot, CHAT_ID, msg)
        return

    n = _settled_count()
    mode = "BOOTSTRAP" if n < BOOTSTRAP_THRESHOLD else "TRAINED"
    header = (
        f"Daily Scan — {datetime.now(UTC).strftime('%d %b %Y %H:%M')} UTC\n"
        f"Mode: {mode} ({n}/{BOOTSTRAP_THRESHOLD} settled)\n"
        f"Picks: {len(passed)} passed / {len(skipped)} filtered\n"
        f"Bank: {_bank:.0f} UAH"
    )
    if app:
        await safe_send(app.bot, CHAT_ID, header)

    for pick, reason in passed[:MAX_MATCHES_TO_SEND]:
        odds = pick["odds"]
        conf = pick["conf"]
        ev = pick["ev"]
        kelly = pick["kelly"]
        stake = _stake(kelly)
        text = (
            f"{pick['sport'].upper()}\n"
            f"{pick['match']}\n"
            f"{pick['market'].upper()}: {pick['selection']}\n\n"
            f"Odds: {odds} | Conf: {conf:.0%}\n"
            f"EV: {ev:+.3f} | Kelly: {kelly*100:.1f}%\n"
            f"Stake: {int(stake)} UAH"
        )
        if app:
            sent = await safe_send(app.bot, CHAT_ID, text, reply_markup=_kb_pick(0, stake))
            if sent:
                mid = sent.message_id
                _pending[mid] = {**pick, "stake": stake}
                try:
                    await sent.edit_reply_markup(reply_markup=_kb_pick(mid, stake))
                except Exception as e:
                    logger.warning("edit markup failed: %s", e)
        await asyncio.sleep(0.25)

    logger.info("SCAN DONE")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _bank, _bet_counter
    q = update.callback_query
    data = (q.data or "").strip()
    try:
        await q.answer(cache_time=0)
    except Exception:
        pass

    if data == "noop":
        return

    if data.startswith("acc:"):
        parts = data.split(":")
        mid = int(parts[1])
        stake = float(parts[2])
        info = _pending.pop(mid, None)
        if not info:
            await q.message.reply_text("Pick expired. Run /scan.")
            return

        try:
            await q.message.edit_reply_markup(reply_markup=_kb_done(f"Accepted {int(stake)} UAH"))
        except Exception:
            pass

        _bet_counter += 1
        bid = _bet_counter
        _open_bets[bid] = {
            **info,
            "id": bid,
            "placed_at": datetime.now(UTC).isoformat(),
            "status": "OPEN",
        }
        await q.message.reply_text(
            f"Bet #{bid} registered\n"
            f"{info['match']}\n"
            f"{info['market'].upper()}: {info['selection']}\n"
            f"Odds: {info['odds']} | Stake: {int(stake)} UAH\n\n"
            f"Settle when done:",
            reply_markup=_kb_settle(bid),
        )
        return

    if data.startswith("skip:"):
        mid = int(data.split(":")[1])
        _pending.pop(mid, None)
        try:
            await q.message.edit_reply_markup(reply_markup=_kb_done("Skipped"))
        except Exception:
            pass
        return

    if data.startswith("win:"):
        bid = int(data.split(":")[1])
        bet = _open_bets.get(bid)
        if not bet:
            await q.message.reply_text("Bet not found.")
            return
        profit = round((bet["odds"] - 1.0) * bet["stake"], 2)
        _bank += profit
        _results.append({**bet, "result": "WON", "profit": profit, "settled_at": datetime.now(UTC).isoformat()})
        bet["status"] = "SETTLED"
        try:
            await q.message.edit_reply_markup(reply_markup=_kb_done(f"WIN +{profit} UAH"))
        except Exception:
            pass
        await q.message.reply_text(
            f"WIN — Bet #{bid}\n{bet['match']}\nProfit: +{profit} UAH\nBank: {_bank:.0f} UAH"
        )
        return

    if data.startswith("loss:"):
        bid = int(data.split(":")[1])
        bet = _open_bets.get(bid)
        if not bet:
            await q.message.reply_text("Bet not found.")
            return
        loss = -round(bet["stake"], 2)
        _bank += loss
        _results.append({**bet, "result": "LOST", "profit": loss, "settled_at": datetime.now(UTC).isoformat()})
        bet["status"] = "SETTLED"
        try:
            await q.message.edit_reply_markup(reply_markup=_kb_done(f"LOSS {loss} UAH"))
        except Exception:
            pass
        await q.message.reply_text(
            f"LOSS — Bet #{bid}\n{bet['match']}\nLoss: {loss} UAH\nBank: {_bank:.0f} UAH"
        )
        return

    if data.startswith("pend:"):
        try:
            await q.message.edit_reply_markup(reply_markup=_kb_done("Pending"))
        except Exception:
            pass
        return


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Betting Bot RECOVERY is alive.\n\n"
        "Commands:\n"
        "/scan — run scan now\n"
        "/stats — performance stats\n"
        "/bank — current bankroll\n"
        "/bets — open bets\n"
        "/help — this message"
    )


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Scanning...")
    await scan(context.application)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    n = len(_results)
    if n == 0:
        await update.message.reply_text("No settled bets yet.")
        return
    wins = sum(1 for r in _results if r["result"] == "WON")
    profit = sum(r["profit"] for r in _results)
    staked = sum(r["stake"] for r in _results)
    roi = profit / staked * 100 if staked else 0
    await update.message.reply_text(
        f"Stats\n"
        f"Settled: {n} | Wins: {wins} | WR: {wins/n*100:.1f}%\n"
        f"Profit: {profit:+.0f} UAH\n"
        f"ROI: {roi:+.1f}%\n"
        f"Bank: {_bank:.0f} UAH"
    )


async def cmd_bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Bank: {_bank:.0f} UAH\n"
        f"Initial: {INITIAL_BANK:.0f} UAH\n"
        f"P&L: {_bank - INITIAL_BANK:+.0f} UAH\n"
        f"Settled bets: {_settled_count()}"
    )


async def cmd_bets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    open_b = [b for b in _open_bets.values() if b.get("status") == "OPEN"]
    if not open_b:
        await update.message.reply_text("No open bets.")
        return
    lines = [f"Open bets ({len(open_b)})"]
    for b in open_b:
        lines.append(f"#{b['id']} {b['match']} | {b['selection']} | @{b['odds']} stake {b['stake']:.0f}")
    await update.message.reply_text("\n".join(lines))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


scheduler = AsyncIOScheduler(timezone="UTC")


async def post_init(app: Application):
    print("BOOT: post_init called", flush=True)
    me = await app.bot.get_me()
    print(f"BOOT: get_me OK @{me.username} id={me.id}", flush=True)
    logger.info("Bot init complete. Daily scan enabled=%s", DAILY_SCAN_ENABLED)

    if DAILY_SCAN_ENABLED:
        if not scheduler.running:
            scheduler.start()
        scheduler.add_job(
            scan,
            "cron",
            hour=DAILY_SCAN_HOUR_UTC,
            minute=0,
            kwargs={"app": app},
            id="daily_scan",
            replace_existing=True,
            max_instances=1,
        )
        logger.info("Scheduler started — daily scan @ %02d:00 UTC", DAILY_SCAN_HOUR_UTC)
    else:
        logger.info("Daily scan disabled. Use /scan manually.")

    if STARTUP_MESSAGE_ENABLED and CHAT_ID:
        await safe_send(app.bot, CHAT_ID, "Betting Bot RECOVERY started. Use /start or /bank.")


async def post_stop(app: Application):
    if scheduler.running:
        scheduler.shutdown(wait=False)


def main():
    print("BOOT: main() entered", flush=True)
    print(f"BOOT: TOKEN_SET={bool(TOKEN)} ODDS_KEY_SET={bool(ODDS_KEY)} CHAT_ID={CHAT_ID}", flush=True)
    print(f"BOOT: ALLOWED_SPORTS={sorted(ALLOWED_SPORTS)}", flush=True)

    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

    builder = Application.builder().token(TOKEN).post_init(post_init).post_stop(post_stop)
    app = builder.build()
    print("BOOT: application built", flush=True)

    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("bank", cmd_bank))
    app.add_handler(CommandHandler("bets", cmd_bets))
    app.add_handler(CommandHandler("help", cmd_help))

    print("BOOT: handlers registered", flush=True)
    logger.info("Polling started")
    print("BOOT: run_polling starting", flush=True)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    print("BOOT: __main__ block", flush=True)
    main()
    
