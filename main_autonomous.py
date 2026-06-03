"""
Betting Bot v11.0 — Hybrid Final
Hermès confidence calibration  +  clean pipeline
No learning loops, no bootstrap mode. Pure math.
"""
import os, json, logging, asyncio
from datetime import datetime, timezone
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ContextTypes, CommandHandler, CallbackQueryHandler
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID  = int(os.getenv("CHAT_ID", "0")) or None
ODDS_KEY = os.getenv("ODDS_API_KEY", "")
UTC      = timezone.utc

INITIAL_BANK   = 1000.0
KELLY_FRACTION = 0.25
MAX_BET_PCT    = 0.10
STOP_LOSS_PCT  = 0.15
MIN_STAKE      = 10.0
MAX_PICKS      = 30

STATE_FILE = Path("state.json")

def _load():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"bank": INITIAL_BANK, "bet_ctr": 0, "bets": {}, "results": []}

def _save():
    STATE_FILE.write_text(json.dumps(_S, indent=2))

_S       = _load()
_pending: dict = {}

# ─── Hermès confidence layer ─────────────────────────────────────────────────
_SPORT_BASE = {
    "soccer":     0.52,
    "tennis":     0.51,
    "basketball": 0.50,
    "baseball":   0.51,
    "hockey":     0.50,
    "boxing":     0.51,
    "mma":        0.51,
}
_MARKET_DELTA = {"h2h": 0.00, "spreads": -0.02, "totals": -0.02}

def hermes_conf(sport_key: str, market: str) -> float:
    base = _SPORT_BASE.get(sport_key.split("_")[0], 0.50)
    adj  = _MARKET_DELTA.get(market, -0.02)
    return round(base + adj, 4)

# ─── Per-market thresholds ───────────────────────────────────────────────────
# (odds_lo, odds_hi, min_conf, min_ev)
_TIERS = {
    "h2h": [
        (1.50, 2.00, 0.60, 0.10),
        (2.00, 3.00, 0.51, 0.05),
        (3.00, 5.01, 0.50, 0.03),
    ],
    "spreads": [
        (1.50, 2.00, 0.57, 0.05),
        (2.00, 3.00, 0.49, 0.02),
        (3.00, 5.01, 0.48, 0.01),
    ],
    "totals": [
        (1.50, 2.00, 0.58, 0.04),
        (2.00, 3.00, 0.49, 0.01),
        (3.00, 5.01, 0.48, 0.01),
    ],
}

def _get_tier(market: str, odds: float):
    for lo, hi, min_conf, min_ev in _TIERS.get(market, _TIERS["h2h"]):
        if lo <= odds < hi:
            return min_conf, min_ev
    return None, None

# ─── Math ─────────────────────────────────────────────────────────────────────
def calc_ev_kelly(conf: float, odds: float):
    ev    = conf * (odds - 1) - (1 - conf)
    raw_k = (conf * odds - 1) / (odds - 1) if odds > 1 else 0.0
    kelly = max(raw_k * KELLY_FRACTION, 0.0)
    return ev, kelly

def calc_stake(kelly: float) -> float:
    raw = kelly * _S["bank"]
    raw = min(raw, _S["bank"] * MAX_BET_PCT)
    raw = max(raw, MIN_STAKE)
    if _S["bank"] < INITIAL_BANK * (1 - STOP_LOSS_PCT):
        raw *= 0.5
    return round(min(raw, _S["bank"]), 0)

def passes(sport: str, market: str, odds: float):
    if not (1.50 <= odds <= 5.00):
        return False, f"odds {odds:.2f} outside [1.50-5.00]", 0.0, 0.0
    conf = hermes_conf(sport, market)
    ev, kelly = calc_ev_kelly(conf, odds)
    min_conf, min_ev = _get_tier(market, odds)
    if min_conf is None:
        return False, f"no tier for odds {odds:.2f}", ev, kelly
    if kelly <= 0:
        return False, f"Kelly<=0 (breakeven or negative)", ev, kelly
    if conf < min_conf:
        return False, f"conf {conf:.0%} < {min_conf:.0%}", ev, kelly
    if ev < min_ev:
        return False, f"EV {ev:.3f} < {min_ev:.3f}", ev, kelly
    return True, f"EV={ev:+.3f} K={kelly*100:.1f}% conf={conf:.0%}", ev, kelly

# ─── Odds API ─────────────────────────────────────────────────────────────────
async def fetch_picks() -> list:
    if not ODDS_KEY:
        logger.warning("ODDS_API_KEY not set")
        return []
    all_picks = []
    mkt_counts = {}
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            r = await client.get("https://api.the-odds-api.com/v4/sports",
                                 params={"apiKey": ODDS_KEY})
            if r.status_code != 200:
                logger.error(f"Sports list HTTP {r.status_code}")
                return []
            active = [s["key"] for s in r.json() if s.get("active")]
            logger.info(f"Active sports: {len(active)}")
        except Exception as e:
            logger.error(f"Sports list error: {e}")
            return []

        for sport_key in active:
            try:
                r2 = await client.get(
                    f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds",
                    params={"apiKey": ODDS_KEY, "regions": "us,eu,uk,au",
                            "markets": "h2h,spreads,totals"},
                    timeout=12.0,
                )
                if r2.status_code != 200:
                    continue
                events = r2.json()
            except Exception as e:
                logger.debug(f"{sport_key}: {e}")
                continue

            for event in events:
                home = event.get("home_team", "")
                away = event.get("away_team", "")
                if not home or not away:
                    continue
                bks = event.get("bookmakers", [])
                if not bks:
                    continue
                for mobj in bks[0].get("markets", []):
                    mk = mobj.get("key", "")
                    if mk not in ("h2h", "spreads", "totals"):
                        continue
                    mkt_counts[mk] = mkt_counts.get(mk, 0) + 1
                    for outcome in mobj.get("outcomes", []):
                        odds = outcome.get("price", 0.0)
                        ok, reason, ev, kelly = passes(sport_key, mk, odds)
                        all_picks.append({
                            "match":     f"{home} vs {away}",
                            "sport":     sport_key,
                            "market":    mk,
                            "selection": outcome.get("name", ""),
                            "odds":      odds,
                            "conf":      hermes_conf(sport_key, mk),
                            "ev":        ev,
                            "kelly":     kelly,
                            "ok":        ok,
                            "reason":    reason,
                        })

    logger.info(f"Raw picks: {len(all_picks)}  Market counts: {mkt_counts}")
    return all_picks

# ─── Keyboards ───────────────────────────────────────────────────────────────
def kb_pick(mid, stake):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"ACCEPT {int(stake)} UAH", callback_data=f"acc:{mid}:{int(stake)}"),
        InlineKeyboardButton("SKIP",                     callback_data=f"skip:{mid}"),
    ]])

def kb_settle(bid):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("WIN",     callback_data=f"win:{bid}"),
        InlineKeyboardButton("LOSS",    callback_data=f"loss:{bid}"),
        InlineKeyboardButton("PENDING", callback_data=f"pend:{bid}"),
    ]])

def kb_done(label):
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="noop")]])

# ─── Scan ─────────────────────────────────────────────────────────────────────
async def scan(app=None):
    logger.info("=== SCAN START ===")
    raw = await fetch_picks()
    if not raw:
        if app and CHAT_ID:
            await app.bot.send_message(CHAT_ID, "No data from Odds API. Check ODDS_API_KEY.")
        return

    ok_picks = [p for p in raw if p["ok"]]
    rejected = len(raw) - len(ok_picks)
    rej_odds  = sum(1 for p in raw if not p["ok"] and "outside"  in p["reason"])
    rej_kelly = sum(1 for p in raw if not p["ok"] and "Kelly"    in p["reason"])
    rej_conf  = sum(1 for p in raw if not p["ok"] and "conf"     in p["reason"])
    rej_ev    = sum(1 for p in raw if not p["ok"] and "EV"       in p["reason"])

    logger.info(f"Passed: {len(ok_picks)}/{len(raw)}  "
                f"rej odds:{rej_odds} kelly:{rej_kelly} conf:{rej_conf} ev:{rej_ev}")
    for i, p in enumerate(raw[:30], 1):
        tag = "V" if p["ok"] else "X"
        logger.info(f"  {tag} [{i:03d}] {p['market']:7} odds={p['odds']:.2f} "
                    f"conf={p['conf']:.0%} ev={p['ev']:+.3f} | {p['reason']}")

    ok_picks.sort(key=lambda x: x["ev"], reverse=True)
    top = ok_picks[:MAX_PICKS]

    if not top:
        mkt_dist = {}
        for p in raw:
            mkt_dist[p["market"]] = mkt_dist.get(p["market"], 0) + 1
        dist = "  ".join(f"{k}={v}" for k, v in sorted(mkt_dist.items())) or "none"
        msg = (
            f"Scan: 0 picks passed\n"
            f"Raw: {len(raw)}  Markets: {dist}\n"
            f"rej odds:{rej_odds} kelly:{rej_kelly} conf:{rej_conf} ev:{rej_ev}\n"
            f"Bank: {_S['bank']:.0f} UAH\nSee Railway logs for details."
        )
        if app and CHAT_ID:
            await app.bot.send_message(CHAT_ID, msg)
        return

    emrg = " EMERGENCY" if _S["bank"] < INITIAL_BANK * (1 - STOP_LOSS_PCT) else ""
    mkt_pass = {}
    for p in top:
        mkt_pass[p["market"]] = mkt_pass.get(p["market"], 0) + 1
    mkt_str = "  ".join(f"{k}={v}" for k, v in sorted(mkt_pass.items()))
    hdr = (
        f"Scan {datetime.now(UTC).strftime('%d %b %H:%M')} UTC{emrg}\n"
        f"Picks: {len(top)} passed / {rejected} filtered\n"
        f"Markets: {mkt_str}  Bank: {_S['bank']:.0f} UAH"
    )
    if app and CHAT_ID:
        await app.bot.send_message(CHAT_ID, hdr)

    for p in top:
        stake = calc_stake(p["kelly"])
        lbl   = {"h2h": "H2H", "spreads": "SPREAD", "totals": "TOTAL"}.get(p["market"], p["market"])
        text  = (
            f"{p['sport'].upper()}\n"
            f"{p['match']}\n"
            f"{lbl}: {p['selection']}\n"
            f"Odds: {p['odds']}  Conf: {p['conf']:.0%}  EV: {p['ev']:+.3f}\n"
            f"Kelly: {p['kelly']*100:.1f}%  Stake: {int(stake)} UAH"
        )
        if app and CHAT_ID:
            try:
                sent = await app.bot.send_message(CHAT_ID, text,
                                                  reply_markup=kb_pick(0, stake))
                mid  = sent.message_id
                _pending[mid] = {**p, "stake": stake}
                await sent.edit_reply_markup(reply_markup=kb_pick(mid, stake))
            except Exception as e:
                logger.error(f"Send error: {e}")
        await asyncio.sleep(0.12)
    logger.info("=== SCAN DONE ===")

# ─── Callbacks ───────────────────────────────────────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        pick  = _pending.pop(mid, None)
        if not pick:
            await q.message.reply_text("Pick expired — run /scan again.")
            return
        try:
            await q.message.edit_reply_markup(kb_done(f"Accepted {int(stake)} UAH"))
        except Exception:
            pass
        _S["bet_ctr"] += 1
        bid = _S["bet_ctr"]
        _S["bets"][str(bid)] = {
            "id": bid, "match": pick["match"], "sport": pick["sport"],
            "market": pick["market"], "selection": pick["selection"],
            "odds": pick["odds"], "stake": stake,
            "placed_at": datetime.now(UTC).isoformat(), "status": "OPEN",
        }
        _save()
        await q.message.reply_text(
            f"Bet #{bid}\n{pick['match']}\n"
            f"{pick['market']}: {pick['selection']}  @{pick['odds']}\n"
            f"Stake: {int(stake)} UAH",
            reply_markup=kb_settle(bid),
        )
        return

    if data.startswith("skip:"):
        _pending.pop(int(data.split(":")[1]), None)
        try:
            await q.message.edit_reply_markup(kb_done("Skipped"))
        except Exception:
            pass
        return

    if data.startswith("win:"):
        bid = int(data.split(":")[1])
        bet = _S["bets"].get(str(bid))
        if not bet:
            await q.message.reply_text("Bet not found.")
            return
        profit = round((bet["odds"] - 1.0) * bet["stake"], 2)
        _S["bank"] += profit
        _S["results"].append({**bet, "result": "WON", "profit": profit,
                               "settled_at": datetime.now(UTC).isoformat()})
        bet["status"] = "SETTLED"
        _save()
        try:
            await q.message.edit_reply_markup(kb_done(f"WIN +{profit:.0f}"))
        except Exception:
            pass
        await q.message.reply_text(
            f"WIN #{bid}  +{profit:.0f} UAH\nBank: {_S['bank']:.0f} UAH  ({len(_S['results'])} settled)"
        )
        return

    if data.startswith("loss:"):
        bid = int(data.split(":")[1])
        bet = _S["bets"].get(str(bid))
        if not bet:
            await q.message.reply_text("Bet not found.")
            return
        loss = -round(bet["stake"], 2)
        _S["bank"] += loss
        _S["results"].append({**bet, "result": "LOST", "profit": loss,
                               "settled_at": datetime.now(UTC).isoformat()})
        bet["status"] = "SETTLED"
        _save()
        try:
            await q.message.edit_reply_markup(kb_done(f"LOSS {loss:.0f}"))
        except Exception:
            pass
        emrg = "\nEMERGENCY: stakes halved" if _S["bank"] < INITIAL_BANK * (1 - STOP_LOSS_PCT) else ""
        await q.message.reply_text(
            f"LOSS #{bid}  {loss:.0f} UAH\nBank: {_S['bank']:.0f} UAH{emrg}"
        )
        return

    if data.startswith("pend:"):
        try:
            await q.message.edit_reply_markup(kb_done("Pending"))
        except Exception:
            pass
        return

# ─── Commands ─────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Betting Bot v11.0\n\n/scan\n/stats\n/bank\n/bets"
    )

async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Scanning...")
    await scan(context.application)

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    r = _S["results"]
    if not r:
        await update.message.reply_text("No settled bets yet.")
        return
    n = len(r)
    wins   = sum(1 for x in r if x["result"] == "WON")
    profit = sum(x["profit"] for x in r)
    staked = sum(x["stake"]  for x in r)
    roi    = profit / staked * 100 if staked else 0
    lines  = [f"Bets: {n}  WR: {wins/n*100:.1f}%  Profit: {profit:+.0f} UAH  ROI: {roi:+.1f}%",
              f"Bank: {_S['bank']:.0f} UAH", "", "By market:"]
    for mk in ("h2h", "spreads", "totals"):
        sub = [x for x in r if x.get("market") == mk]
        if sub:
            sw = sum(1 for x in sub if x["result"] == "WON")
            lines.append(f"  {mk}: {sw}/{len(sub)} WR={sw/len(sub)*100:.0f}%")
    await update.message.reply_text("\n".join(lines))

async def cmd_bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    emrg = "\nEMERGENCY MODE" if _S["bank"] < INITIAL_BANK * (1 - STOP_LOSS_PCT) else ""
    await update.message.reply_text(
        f"Bank: {_S['bank']:.0f} UAH\n"
        f"P&L: {_S['bank'] - INITIAL_BANK:+.0f} UAH  |  Settled: {len(_S['results'])}{emrg}"
    )

async def cmd_bets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    open_b = [b for b in _S["bets"].values() if b.get("status") == "OPEN"]
    if not open_b:
        await update.message.reply_text("No open bets.")
        return
    lines = [f"Open bets ({len(open_b)})"]
    for b in open_b:
        lines.append(f"#{b['id']} {b['match']} | {b['market']} {b['selection']} @{b['odds']} - {b['stake']:.0f} UAH")
    await update.message.reply_text("\n".join(lines))

# ─── Scheduler / startup ─────────────────────────────────────────────────────
scheduler = AsyncIOScheduler()

async def post_init(app):
    logger.info("Bot starting...")
    scheduler.start()
    scheduler.add_job(scan, "cron", hour=8, minute=0, kwargs={"app": app}, id="scan")
    logger.info("Scheduler: scan @ 08:00 UTC daily")
    try:
        await app.bot.send_message(
            CHAT_ID,
            f"Betting Bot v11.0 started\nBank: {_S['bank']:.0f} UAH  Settled: {len(_S['results'])} bets"
        )
    except Exception:
        pass
    await scan(app)

async def post_stop(app):
    if scheduler.running:
        scheduler.shutdown(wait=False)

# ─── Main ─────────────────────────────────────────────────────────────────────
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
    app.add_handler(CommandHandler("help",  cmd_start))
    logger.info("Polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
