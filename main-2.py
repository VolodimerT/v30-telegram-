"""
Betting Bot v8.0 — Pure Mathematical Approach
No external ML until 100+ settled bets.
"""
import os, json, logging, asyncio
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ContextTypes, CommandHandler, CallbackQueryHandler
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID    = int(os.getenv("CHAT_ID", "0")) or None
ODDS_KEY   = os.getenv("ODDS_API_KEY", "")
UTC        = timezone.utc

# ── Bank ─────────────────────────────────────────────────────────────────────
INITIAL_BANK = 1000.0          # UAH demo budget

# ── Adaptive filter thresholds by odds tier ───────────────────────────────────
TIERS = [
    # (odds_min, odds_max, min_ev, min_kelly, min_conf)
    (1.80, 2.00, 0.10, 0.02, 0.55),
    (2.00, 3.00, 0.08, 0.015, 0.52),
    (3.00, 5.01, 0.05, 0.01, 0.50),
]

# ── Kelly quarter-fraction ────────────────────────────────────────────────────
KELLY_FRACTION = 0.25

# ── Bootstrap: use pure-math until this many settled bets ────────────────────
BOOTSTRAP_THRESHOLD = 100

# ── In-memory state ───────────────────────────────────────────────────────────
_pending: dict   = {}   # msg_id -> pick info
_open_bets: dict = {}   # bet_id -> bet
_results: list   = []
_bet_counter     = 0
_bank            = INITIAL_BANK


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _settled_count():
    return len(_results)


def _get_tier(odds: float):
    for lo, hi, min_ev, min_kelly, min_conf in TIERS:
        if lo <= odds < hi:
            return min_ev, min_kelly, min_conf
    return None  # outside supported range


def _calc(conf: float, odds: float):
    ev     = conf * (odds - 1) - (1 - conf)
    raw_k  = (conf * odds - 1) / (odds - 1) if odds > 1 else 0
    kelly  = max(raw_k * KELLY_FRACTION, 0)
    return ev, kelly


def _stake(kelly: float):
    global _bank
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


# ─────────────────────────────────────────────────────────────────────────────
# KEYBOARDS
# ─────────────────────────────────────────────────────────────────────────────

def _kb_pick(mid: int, stake: float):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"✅ ACCEPT  {int(stake)} UAH", callback_data=f"acc:{mid}:{int(stake)}"),
        InlineKeyboardButton("❌ SKIP",                     callback_data=f"skip:{mid}"),
    ]])


def _kb_settle(bet_id: int):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("WIN",     callback_data=f"win:{bet_id}"),
        InlineKeyboardButton("LOSS",    callback_data=f"loss:{bet_id}"),
        InlineKeyboardButton("PENDING", callback_data=f"pend:{bet_id}"),
    ]])


def _kb_done(label: str):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(label, callback_data="noop"),
    ]])


# ─────────────────────────────────────────────────────────────────────────────
# ODDS API
# ─────────────────────────────────────────────────────────────────────────────

# Confidence calibration per sport/market (empirical estimates)
SPORT_CONF = {
    "soccer":      {"h2h": 0.52, "spreads": 0.51, "totals": 0.53, "btts": 0.52},
    "basketball":  {"h2h": 0.54, "spreads": 0.53, "totals": 0.54},
    "tennis":      {"h2h": 0.53, "spreads": 0.52},
    "mma":         {"h2h": 0.51},
    "baseball":    {"h2h": 0.52, "totals": 0.52},
    "hockey":      {"h2h": 0.52, "totals": 0.53},
    "americanfootball": {"h2h": 0.53, "spreads": 0.54, "totals": 0.53},
    "default":     {"default": 0.51},
}

ALLOWED_MARKETS = ("h2h", "spreads", "totals")


def _conf_for(sport_key: str, market_key: str) -> float:
    sport_base = sport_key.split("_")[0]
    table = SPORT_CONF.get(sport_base, SPORT_CONF["default"])
    return table.get(market_key, table.get("default", 0.51))


async def fetch_picks() -> list:
    """Fetch all active sports → odds → build pick list."""
    if not ODDS_KEY:
        logger.warning("No ODDS_API_KEY — returning empty list")
        return []

    picks = []
    async with httpx.AsyncClient(timeout=15.0) as client:
        # 1. Get active sports
        r = await client.get("https://api.the-odds-api.com/v4/sports",
                             params={"apiKey": ODDS_KEY})
        if r.status_code != 200:
            logger.error(f"Sports list error: {r.status_code}")
            return []

        active = [s["key"] for s in r.json() if s.get("active")]
        logger.info(f"Active sports: {len(active)}")

        for sport_key in active:
            try:
                r2 = await client.get(
                    f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds",
                    params={
                        "apiKey": ODDS_KEY,
                        "regions": "us,eu,uk,au",
                        "markets": ",".join(ALLOWED_MARKETS),
                    },
                    timeout=10.0,
                )
                if r2.status_code != 200:
                    continue
                events = r2.json()
            except Exception as e:
                logger.warning(f"{sport_key}: {e}")
                continue

            for event in events:
                home = event.get("home_team", "")
                away = event.get("away_team", "")
                if not home or not away:
                    continue
                match_name = f"{home} vs {away}"
                bks = event.get("bookmakers", [])
                if not bks:
                    continue

                for market in bks[0].get("markets", []):
                    mk = market.get("key", "")
                    if mk not in ALLOWED_MARKETS:
                        continue
                    conf = _conf_for(sport_key, mk)
                    for outcome in market.get("outcomes", []):
                        odds = outcome.get("price", 0.0)
                        if not (1.80 <= odds <= 5.00):
                            continue
                        name = outcome.get("name", "")
                        ev, kelly = _calc(conf, odds)
                        picks.append({
                            "match":     match_name,
                            "sport":     sport_key,
                            "market":    mk,
                            "selection": name,
                            "odds":      odds,
                            "conf":      conf,
                            "ev":        ev,
                            "kelly":     kelly,
                        })

    logger.info(f"Total raw picks: {len(picks)}")
    return picks


# ─────────────────────────────────────────────────────────────────────────────
# SCAN — filter + send
# ─────────────────────────────────────────────────────────────────────────────

async def scan(app=None):
    global _bank
    logger.info("🔍 SCAN START")
    raw = await fetch_picks()

    passed, skipped = [], []
    for p in raw:
        ok, reason = _passes(p["odds"], p["conf"])
        if ok:
            passed.append((p, reason))
        else:
            skipped.append((p, reason))

    logger.info(f"Passed: {len(passed)}  Skipped: {len(skipped)}")

    if not passed:
        msg = (f"🔍 Scan complete: 0 picks passed filters\n"
               f"Skipped {len(skipped)} | Bank: {_bank:.0f} UAH")
        if app and CHAT_ID:
            await app.bot.send_message(chat_id=CHAT_ID, text=msg)
        return

    # Send header
    n = _settled_count()
    mode = "BOOTSTRAP" if n < BOOTSTRAP_THRESHOLD else "TRAINED"
    header = (
        f"📊 *Daily Scan — {datetime.now(UTC).strftime('%d %b %Y %H:%M')} UTC*\n"
        f"Mode: {mode} ({n}/{BOOTSTRAP_THRESHOLD} settled)\n"
        f"Picks: {len(passed)} passed / {len(skipped)} filtered\n"
        f"Bank: {_bank:.0f} UAH"
    )
    if app and CHAT_ID:
        await app.bot.send_message(chat_id=CHAT_ID, text=header, parse_mode="Markdown")

    # Send each pick card
    for pick, reason in passed[:15]:   # cap at 15 per scan
        odds  = pick["odds"]
        conf  = pick["conf"]
        ev    = pick["ev"]
        kelly = pick["kelly"]
        stake = _stake(kelly)

        text = (
            f"🏟 *{pick['sport'].upper()}*\n"
            f"{pick['match']}\n"
            f"_{pick['market'].upper()}: {pick['selection']}_\n\n"
            f"Odds: `{odds}`   Conf: `{conf:.0%}`\n"
            f"EV: `{ev:+.3f}`   Kelly: `{kelly*100:.1f}%`\n"
            f"Stake: `{int(stake)} UAH`"
        )

        if app and CHAT_ID:
            try:
                sent = await app.bot.send_message(
                    chat_id=CHAT_ID,
                    text=text,
                    parse_mode="Markdown",
                    reply_markup=_kb_pick(0, stake),
                )
                mid = sent.message_id
                _pending[mid] = {**pick, "stake": stake}
                await sent.edit_reply_markup(reply_markup=_kb_pick(mid, stake))
            except Exception as e:
                logger.error(f"Send error: {e}")
        await asyncio.sleep(0.15)

    logger.info("🔍 SCAN DONE")


# ─────────────────────────────────────────────────────────────────────────────
# CALLBACK HANDLER
# ─────────────────────────────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _bank, _bet_counter
    q    = update.callback_query
    data = (q.data or "").strip()
    try:
        await q.answer(cache_time=0)
    except Exception:
        pass

    if data == "noop":
        return

    # ── ACCEPT ───────────────────────────────────────────────────────────────
    if data.startswith("acc:"):
        parts = data.split(":")
        mid   = int(parts[1])
        stake = float(parts[2])
        info  = _pending.pop(mid, None)
        if not info:
            await q.message.reply_text("Pick expired. Run /scan.")
            return
        try:
            await q.message.edit_reply_markup(
                reply_markup=_kb_done(f"Accepted {int(stake)} UAH"))
        except Exception:
            pass
        _bet_counter += 1
        bid = _bet_counter
        _open_bets[bid] = {**info, "id": bid,
                           "placed_at": datetime.now(UTC).isoformat(),
                           "status": "OPEN"}
        await q.message.reply_text(
            f"✅ Bet #{bid} registered\n"
            f"{info['match']}\n"
            f"{info['market'].upper()}: {info['selection']}\n"
            f"Odds: {info['odds']}  Stake: {int(stake)} UAH\n\n"
            f"Settle when done:",
            reply_markup=_kb_settle(bid),
        )
        return

    # ── SKIP ─────────────────────────────────────────────────────────────────
    if data.startswith("skip:"):
        mid = int(data.split(":")[1])
        _pending.pop(mid, None)
        try:
            await q.message.edit_reply_markup(reply_markup=_kb_done("Skipped"))
        except Exception:
            pass
        return

    # ── WIN ──────────────────────────────────────────────────────────────────
    if data.startswith("win:"):
        bid = int(data.split(":")[1])
        bet = _open_bets.get(bid)
        if not bet:
            await q.message.reply_text("Bet not found.")
            return
        profit = round((bet["odds"] - 1.0) * bet["stake"], 2)
        _bank += profit
        _results.append({**bet, "result": "WON", "profit": profit,
                         "settled_at": datetime.now(UTC).isoformat()})
        bet["status"] = "SETTLED"
        try:
            await q.message.edit_reply_markup(
                reply_markup=_kb_done(f"WIN +{profit} UAH"))
        except Exception:
            pass
        await q.message.reply_text(
            f"🏆 WIN — Bet #{bid}\n"
            f"{bet['match']}\n"
            f"Profit: +{profit} UAH\n"
            f"Bank: {_bank:.0f} UAH  ({_settled_count()} settled)"
        )
        return

    # ── LOSS ─────────────────────────────────────────────────────────────────
    if data.startswith("loss:"):
        bid = int(data.split(":")[1])
        bet = _open_bets.get(bid)
        if not bet:
            await q.message.reply_text("Bet not found.")
            return
        loss = -round(bet["stake"], 2)
        _bank += loss
        _results.append({**bet, "result": "LOST", "profit": loss,
                         "settled_at": datetime.now(UTC).isoformat()})
        bet["status"] = "SETTLED"
        try:
            await q.message.edit_reply_markup(
                reply_markup=_kb_done(f"LOSS {loss} UAH"))
        except Exception:
            pass
        await q.message.reply_text(
            f"❌ LOSS — Bet #{bid}\n"
            f"{bet['match']}\n"
            f"Loss: {loss} UAH\n"
            f"Bank: {_bank:.0f} UAH  ({_settled_count()} settled)"
        )
        return

    # ── PENDING ───────────────────────────────────────────────────────────────
    if data.startswith("pend:"):
        try:
            await q.message.edit_reply_markup(reply_markup=_kb_done("⏳ Pending"))
        except Exception:
            pass
        return


# ─────────────────────────────────────────────────────────────────────────────
# COMMANDS
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Betting Bot v8.0*\n\n"
        "Commands:\n"
        "/scan — run scan now\n"
        "/stats — performance stats\n"
        "/bank — current bankroll\n"
        "/bets — open bets\n"
        "/help — this message",
        parse_mode="Markdown",
    )


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Scanning...")
    await scan(context.application)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    n = len(_results)
    if n == 0:
        await update.message.reply_text("No settled bets yet.")
        return
    wins   = sum(1 for r in _results if r["result"] == "WON")
    profit = sum(r["profit"] for r in _results)
    staked = sum(r["stake"] for r in _results)
    roi    = profit / staked * 100 if staked else 0
    await update.message.reply_text(
        f"📊 *Stats*\n"
        f"Settled: {n}  Wins: {wins}  WR: {wins/n*100:.1f}%\n"
        f"Profit: {profit:+.0f} UAH\n"
        f"ROI: {roi:+.1f}%\n"
        f"Bank: {_bank:.0f} UAH",
        parse_mode="Markdown",
    )


async def cmd_bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"💰 Bank: *{_bank:.0f} UAH*\n"
        f"Initial: {INITIAL_BANK:.0f} UAH\n"
        f"P&L: {_bank - INITIAL_BANK:+.0f} UAH\n"
        f"Settled bets: {_settled_count()}",
        parse_mode="Markdown",
    )


async def cmd_bets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    open_b = [b for b in _open_bets.values() if b.get("status") == "OPEN"]
    if not open_b:
        await update.message.reply_text("No open bets.")
        return
    lines = [f"*Open bets ({len(open_b)})*"]
    for b in open_b:
        lines.append(
            f"#{b['id']} {b['match']} | {b['selection']} | "
            f"@{b['odds']} stake {b['stake']:.0f}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


# ─────────────────────────────────────────────────────────────────────────────
# STARTUP / SCHEDULER
# ─────────────────────────────────────────────────────────────────────────────

scheduler = AsyncIOScheduler()


async def post_init(app):
    logger.info("Bot initializing…")
    scheduler.start()
    scheduler.add_job(scan, "cron", hour=8, minute=0,
                      kwargs={"app": app}, id="daily_scan")
    logger.info("Scheduler started — daily scan @ 08:00 UTC")
    try:
        await app.bot.send_message(chat_id=CHAT_ID,
                                   text="✅ Betting Bot v8.0 started")
    except Exception:
        pass
    await scan(app)


async def post_stop(app):
    if scheduler.running:
        scheduler.shutdown(wait=False)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

    app = Application.builder().token(TOKEN).build()
    app.post_init = post_init
    app.post_stop = post_stop

    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("scan",  cmd_scan))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("bank",  cmd_bank))
    app.add_handler(CommandHandler("bets",  cmd_bets))
    app.add_handler(CommandHandler("help",  cmd_help))

    logger.info("🚀 Polling…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
