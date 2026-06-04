"""
Betting Bot v13.0 — Final
All markets per match + copyable list for Perplexity
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

INITIAL_BANK = 1000.0
KELLY_FRACTION = 0.25

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

_pending: dict   = {}
_open_bets: dict = {}
_results: list   = []
_bet_counter     = 0
_bank            = INITIAL_BANK


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
        return False
    tier = _get_tier(odds, market)
    if tier is None:
        return False
    min_ev, min_kelly, min_conf = tier
    ev, kelly = _calc(conf, odds)
    if conf < min_conf or ev < min_ev or kelly < min_kelly:
        return False
    return True


def _kb_accept_skip(mid: str):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ ACCEPT", callback_data=f"acc:{mid}"),
        InlineKeyboardButton("❌ SKIP",   callback_data=f"skip:{mid}"),
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


async def fetch_picks() -> dict:
    """Return dict: match_name -> list of pick options"""
    if not ODDS_KEY:
        return {}

    matches = {}  # match_name -> {sport, options: []}
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get("https://api.the-odds-api.com/v4/sports",
                             params={"apiKey": ODDS_KEY})
        if r.status_code != 200:
            return {}

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
                
                if match_name not in matches:
                    matches[match_name] = {"sport": sport_key, "options": []}

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
                        name = outcome.get("name", "")
                        point = outcome.get("point")
                        
                        if not _passes(odds, conf, mk):
                            continue
                        
                        ev, kelly = _calc(conf, odds)
                        
                        # Format selection name with point if present
                        if point:
                            sel_name = f"{name} {point:+.1f}" if isinstance(point, float) else f"{name} {point}"
                        else:
                            sel_name = name
                        
                        matches[match_name]["options"].append({
                            "market":    mk,
                            "selection": sel_name,
                            "odds":      odds,
                            "conf":      conf,
                            "ev":        ev,
                            "kelly":     kelly,
                        })

    logger.info(f"Loaded {len(matches)} matches with options")
    return matches


async def scan(app=None):
    global _bank
    logger.info("🔍 SCAN START")
    matches = await fetch_picks()

    if not matches:
        msg = f"🔍 Scan: no picks found | Bank: {_bank:.0f} UAH"
        if app and CHAT_ID:
            await app.bot.send_message(chat_id=CHAT_ID, text=msg)
        return

    logger.info(f"Passed: {sum(len(m['options']) for m in matches.values())}  Matches: {len(matches)}")

    # Send header
    header = (
        f"📊 Scan {datetime.now(UTC).strftime('%d %b %H:%M')} UTC\n"
        f"Matches: {len(matches)}  Bank: {_bank:.0f} UAH\n\n"
        f"📋 COPYABLE LIST FOR PERPLEXITY:\n"
    )
    
    # Build copyable list
    copy_list = []
    for match_name, data in sorted(matches.items()):
        for opt in sorted(data["options"], key=lambda x: x["ev"], reverse=True):
            line = (
                f"{match_name} - {opt['market'].upper()}: {opt['selection']} @ {opt['odds']} | "
                f"EV {opt['ev']:+.3f} | Kelly {opt['kelly']*100:.1f}%"
            )
            copy_list.append(line)
    
    if copy_list:
        if app and CHAT_ID:
            # Send header with copyable list
            msg = header + "\n".join(copy_list)
            if len(msg) > 4000:  # Telegram limit
                # Split into chunks
                chunks = []
                current = header
                for line in copy_list:
                    if len(current) + len(line) + 1 > 3900:
                        chunks.append(current)
                        current = line
                    else:
                        current += "\n" + line
                if current:
                    chunks.append(current)
                for chunk in chunks:
                    await app.bot.send_message(chat_id=CHAT_ID, text=chunk)
            else:
                await app.bot.send_message(chat_id=CHAT_ID, text=msg)
    
    # Now send formatted picks with ACCEPT/SKIP buttons
    msg_count = 0
    for match_name, data in sorted(matches.items()):
        sport_label = data["sport"].upper().replace("_", " ")
        text = f"🏟 {sport_label}\n{match_name}\n\n"
        
        for opt in sorted(data["options"], key=lambda x: x["ev"], reverse=True):
            text += (
                f"{opt['market'].upper()}: {opt['selection']}\n"
                f"  Odds {opt['odds']} | Conf {opt['conf']:.0%} | "
                f"EV {opt['ev']:+.3f} | Kelly {opt['kelly']*100:.1f}%\n\n"
            )
        
        mid = f"{match_name}_{msg_count}"
        _pending[mid] = {"match": match_name, "data": data, "sport": sport_label}
        
        if app and CHAT_ID:
            await app.bot.send_message(
                chat_id=CHAT_ID, text=text,
                reply_markup=_kb_accept_skip(mid)
            )
        msg_count += 1

    logger.info("🔍 SCAN DONE")


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
        mid = data.split(":")[1]
        info = _pending.pop(mid, None)
        if not info:
            await q.message.reply_text("Expired. Run /scan.")
            return
        try:
            await q.message.edit_reply_markup(reply_markup=_kb_done("✅ PENDING ANALYSIS"))
        except Exception:
            pass
        return

    if data.startswith("skip:"):
        mid = data.split(":")[1]
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
        await q.message.reply_text(f"🏆 WIN #{bid}\nProfit: +{profit} UAH\nBank: {_bank:.0f} UAH")
        return

    if data.startswith("loss:"):
        bid = int(data.split(":")[1])
        bet = _open_bets.get(bid)
        if not bet:
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
        await q.message.reply_text(f"❌ LOSS #{bid}\nLoss: {loss} UAH\nBank: {_bank:.0f} UAH")
        return

    if data.startswith("pend:"):
        try:
            await q.message.edit_reply_markup(reply_markup=_kb_done("⏳ Pending"))
        except Exception:
            pass
        return


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Bot v13.0*\nAll markets + Perplexity analysis\n\n"
        "/scan, /stats, /bank, /bets, /help",
        parse_mode="Markdown",
    )


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Scanning...")
    await scan(context.application)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    n = len(_results)
    if n == 0:
        await update.message.reply_text("No bets yet.")
        return
    wins = sum(1 for r in _results if r["result"] == "WON")
    profit = sum(r["profit"] for r in _results)
    roi = profit / sum(r["stake"] for r in _results) * 100 if n else 0
    await update.message.reply_text(
        f"📊 *Stats*\nSettled: {n} | WR: {wins/n*100:.0f}% | ROI: {roi:+.1f}%\nProfit: {profit:+.0f} UAH\nBank: {_bank:.0f} UAH",
        parse_mode="Markdown",
    )


async def cmd_bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"💰 *{_bank:.0f} UAH*\nP&L: {_bank - INITIAL_BANK:+.0f}\nSettled: {_settled_count()}",
        parse_mode="Markdown",
    )


async def cmd_bets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    open_b = [b for b in _open_bets.values() if b.get("status") == "OPEN"]
    if not open_b:
        await update.message.reply_text("No open bets.")
        return
    lines = [f"*Open ({len(open_b)})*"]
    for b in open_b[:10]:
        lines.append(f"#{b['id']} {b['match'][:25]} @{b['odds']}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


scheduler = AsyncIOScheduler()


async def post_init(app):
    logger.info("Bot init…")
    scheduler.start()
    scheduler.add_job(scan, "cron", hour=8, minute=0, kwargs={"app": app}, id="daily_scan")
    logger.info("Daily scan @ 08:00 UTC")
    try:
        await app.bot.send_message(chat_id=CHAT_ID, text="✅ Bot v13.0 started\nAll markets + copyable list")
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
        
