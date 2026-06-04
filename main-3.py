"""
Betting Bot — Final Hybrid v12.0
Market-specific thresholds + All regions + No BTTS
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

# ── Bank ──────────────────────────────────────────────────────────────────────
INITIAL_BANK = 1000.0

# ── Kelly ─────────────────────────────────────────────────────────────────────
KELLY_FRACTION = 0.25

# ── Market-specific thresholds ────────────────────────────────────────────────
# (odds_min, odds_max, min_ev, min_kelly, min_conf)
TIERS_BY_MARKET = {
    "h2h": [
        (1.50, 2.00, 0.08, 0.010, 0.55),
        (2.00, 3.00, 0.06, 0.008, 0.52),
        (3.00, 5.01, 0.04, 0.005, 0.50),
    ],
    "spreads": [
        (1.50, 2.00, 0.04, 0.005, 0.51),
        (2.00, 3.00, 0.02, 0.003, 0.50),
        (3.00, 5.01, 0.01, 0.002, 0.49),
    ],
    "totals": [
        (1.50, 2.00, 0.03, 0.005, 0.51),
        (2.00, 3.00, 0.01, 0.003, 0.50),
        (3.00, 5.01, 0.005,0.002, 0.49),
    ],
}

# ── Sport/Market confidence calibration ───────────────────────────────────────
SPORT_CONF = {
    "soccer":           {"h2h": 0.52, "spreads": 0.51, "totals": 0.52},
    "basketball":       {"h2h": 0.53, "spreads": 0.52, "totals": 0.53},
    "tennis":           {"h2h": 0.52, "spreads": 0.51, "totals": 0.51},
    "baseball":         {"h2h": 0.51, "spreads": 0.51, "totals": 0.52},
    "hockey":           {"h2h": 0.51, "spreads": 0.50, "totals": 0.52},
    "americanfootball": {"h2h": 0.52, "spreads": 0.53, "totals": 0.52},
    "mma":              {"h2h": 0.51, "spreads": 0.50, "totals": 0.50},
    "boxing":           {"h2h": 0.51, "spreads": 0.50, "totals": 0.50},
    "cricket":          {"h2h": 0.51, "spreads": 0.50, "totals": 0.51},
    "rugby":            {"h2h": 0.51, "spreads": 0.50, "totals": 0.51},
    "default":          {"h2h": 0.51, "spreads": 0.50, "totals": 0.50},
}

ALLOWED_MARKETS = ("h2h", "spreads", "totals")

# ── In-memory state ───────────────────────────────────────────────────────────
_pending: dict   = {}
_open_bets: dict = {}
_results: list   = []
_bet_counter     = 0
_bank            = INITIAL_BANK


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _settled_count():
    return len(_results)


def _conf_for(sport_key: str, market_key: str) -> float:
    sport_base = sport_key.split("_")[0]
    table = SPORT_CONF.get(sport_base, SPORT_CONF["default"])
    return table.get(market_key, SPORT_CONF["default"].get(market_key, 0.50))


def _get_tier(odds: float, market: str):
    tiers = TIERS_BY_MARKET.get(market, TIERS_BY_MARKET["h2h"])
    for lo, hi, min_ev, min_kelly, min_conf in tiers:
        if lo <= odds < hi:
            return min_ev, min_kelly, min_conf
    return None


def _calc(conf: float, odds: float):
    ev    = conf * (odds - 1) - (1 - conf)
    raw_k = (conf * odds - 1) / (odds - 1) if odds > 1 else 0
    kelly = max(raw_k * KELLY_FRACTION, 0)
    return ev, kelly


def _stake(kelly: float):
    raw = kelly * _bank
    return max(round(min(raw, _bank * 0.10), 0), 10.0)


def _passes(odds: float, conf: float, market: str):
    if not (1.50 <= odds <= 5.00):
        return False, "odds out of range"
    tier = _get_tier(odds, market)
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

async def fetch_picks() -> list:
    if not ODDS_KEY:
        logger.warning("No ODDS_API_KEY — returning empty list")
        return []

    picks = []
    mkt_counts = {}
    async with httpx.AsyncClient(timeout=15.0) as client:
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
                        "apiKey":   ODDS_KEY,
                        "regions":  "us,eu,uk,au",
                        "markets":  ",".join(ALLOWED_MARKETS),
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
                    mkt_counts[mk] = mkt_counts.get(mk, 0) + 1
                    conf = _conf_for(sport_key, mk)
                    for outcome in market.get("outcomes", []):
                        odds = outcome.get("price", 0.0)
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

    logger.info(f"Total raw picks: {len(picks)}  Markets: {mkt_counts}")
    return picks


# ─────────────────────────────────────────────────────────────────────────────
# SCAN
# ─────────────────────────────────────────────────────────────────────────────

MAX_PICKS = 30

async def scan(app=None):
    global _bank
    logger.info("🔍 SCAN START")
    raw = await fetch_picks()

    passed, skipped = [], []
    for p in raw:
        ok, reason = _passes(p["odds"], p["conf"], p["market"])
        if ok:
            passed.append((p, reason))
        else:
            skipped.append((p, reason))

    logger.info(f"Passed: {len(passed)}  Skipped: {len(skipped)}")

    if not passed:
        msg = (f"🔍 Scan complete: 0 picks passed\n"
               f"Skipped {len(skipped)} | Bank: {_bank:.0f} UAH")
        if app and CHAT_ID:
            await app.bot.send_message(chat_id=CHAT_ID, text=msg)
        logger.info("🔍 SCAN DONE — 0 picks")
        return

    # Sort by EV descending, take top MAX_PICKS
    passed.sort(key=lambda x: x[0]["ev"], reverse=True)
    top = passed[:MAX_PICKS]

    mkt_dist = {}
    for p, _ in top:
        mkt_dist[p["market"]] = mkt_dist.get(p["market"], 0) + 1

    mkt_str = "  ".join(f"{k}={v}" for k, v in mkt_dist.items())
    header = (
        f"📊 Scan {datetime.now(UTC).strftime('%d %b %H:%M')} UTC\n"
        f"Picks: {len(top)} passed / {len(skipped)} filtered\n"
        f"Markets: {mkt_str}  Bank: {_bank:.0f} UAH"
    )
    if app and CHAT_ID:
        await app.bot.send_message(chat_id=CHAT_ID, text=header)

    for p, reason in top:
        ev, kelly = _calc(p["conf"], p["odds"])
        stake = _stake(kelly)
        p["stake"] = stake
        mid = id(p) & 0xFFFFFF
        _pending[mid] = p

        sport_label = p["sport"].upper().replace("_", " ")
        market_label = p["market"].upper()
        text = (
            f"🏟 {sport_label}\n"
            f"{p['match']}\n"
            f"{market_label}: {p['selection']}\n\n"
            f"Odds: {p['odds']}  Conf: {p['conf']:.0%}\n"
            f"EV: {ev:+.3f}   Kelly: {kelly*100:.1f}%\n"
            f"Stake: {int(stake)} UAH"
        )
        if app and CHAT_ID:
            await app.bot.send_message(
                chat_id=CHAT_ID, text=text,
                reply_markup=_kb_pick(mid, stake)
            )

    logger.info("🔍 SCAN DONE")


# ─────────────────────────────────────────────────────────────────────────────
# CALLBACK
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

    if data.startswith("acc:"):
        parts = data.split(":")
        mid   = int(parts[1])
        stake = float(parts[2])
        info  = _pending.pop(mid, None)
        if not info:
            await q.message.reply_text("Pick expired. Run /scan.")
            return
        try:
            await q.message.edit_reply_markup(reply_markup=_kb_done(f"Accepted {int(stake)} UAH"))
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
        _results.append({**bet, "result": "WON", "profit": profit,
                         "settled_at": datetime.now(UTC).isoformat()})
        bet["status"] = "SETTLED"
        try:
            await q.message.edit_reply_markup(reply_markup=_kb_done(f"WIN +{profit} UAH"))
        except Exception:
            pass
        await q.message.reply_text(
            f"🏆 WIN — Bet #{bid}\n{bet['match']}\n"
            f"Profit: +{profit} UAH\nBank: {_bank:.0f} UAH"
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
        _results.append({**bet, "result": "LOST", "profit": loss,
                         "settled_at": datetime.now(UTC).isoformat()})
        bet["status"] = "SETTLED"
        try:
            await q.message.edit_reply_markup(reply_markup=_kb_done(f"LOSS {loss} UAH"))
        except Exception:
            pass
        await q.message.reply_text(
            f"❌ LOSS — Bet #{bid}\n{bet['match']}\n"
            f"Loss: {loss} UAH\nBank: {_bank:.0f} UAH"
        )
        return

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
        "🤖 *Betting Bot v12.0*\n\n"
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

    mkt_stats = {}
    for r in _results:
        mk = r.get("market", "h2h")
        if mk not in mkt_stats:
            mkt_stats[mk] = {"n": 0, "wins": 0, "profit": 0}
        mkt_stats[mk]["n"] += 1
        mkt_stats[mk]["wins"] += 1 if r["result"] == "WON" else 0
        mkt_stats[mk]["profit"] += r["profit"]

    mkt_lines = "\n".join(
        f"  {mk}: {v['n']} bets | WR {v['wins']/v['n']*100:.0f}% | P&L {v['profit']:+.0f}"
        for mk, v in mkt_stats.items()
    )

    await update.message.reply_text(
        f"📊 *Stats*\n"
        f"Settled: {n}  Wins: {wins}  WR: {wins/n*100:.1f}%\n"
        f"Profit: {profit:+.0f} UAH\n"
        f"ROI: {roi:+.1f}%\n"
        f"Bank: {_bank:.0f} UAH\n\n"
        f"*By market:*\n{mkt_lines}",
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
            f"#{b['id']} {b['match']} | {b['market'].upper()} {b['selection']} | "
            f"@{b['odds']} stake {b['stake']:.0f} UAH"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


# ─────────────────────────────────────────────────────────────────────────────
# STARTUP
# ─────────────────────────────────────────────────────────────────────────────

scheduler = AsyncIOScheduler()


async def post_init(app):
    logger.info("Bot initializing…")
    scheduler.start()
    scheduler.add_job(scan, "cron", hour=8, minute=0,
                      kwargs={"app": app}, id="daily_scan")
    logger.info("Scheduler started — daily scan @ 08:00 UTC")
    try:
        await app.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"✅ Betting Bot v12.0 started\n"
                f"Bank: {INITIAL_BANK:.0f} UAH\n"
                f"Markets: h2h, spreads, totals\n"
                f"Regions: us, eu, uk, au"
            )
        )
    except Exception:
        pass
    await scan(app)


async def post_stop(app):
    if scheduler.running:
        scheduler.shutdown(wait=False)


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
