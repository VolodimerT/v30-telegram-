"""
Autonomous Betting System v9.0
Pure-math EV/Kelly approach, adaptive stats, full risk controls.
Railway-ready — single file, no database needed.
"""
import os, json, logging, asyncio, time, math
from datetime import datetime, timezone, timedelta
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ContextTypes, CommandHandler, CallbackQueryHandler
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import httpx

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# ── Config (env-driven) ───────────────────────────────────────────────────────
TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID  = int(os.getenv("CHAT_ID", "0")) or None
ODDS_KEY = os.getenv("ODDS_API_KEY", "")
UTC      = timezone.utc
DATA_FILE = Path("state.json")

# ── Constants ─────────────────────────────────────────────────────────────────
INITIAL_BANK        = 1000.0   # UAH demo
KELLY_FRACTION      = 0.25     # quarter Kelly
MAX_BET_PCT         = 0.10     # never more than 10% of bank per bet
MIN_STAKE           = 10.0     # UAH floor
STOP_LOSS_PCT       = 0.15     # bank -15% → emergency mode
HALF_STAKE_FACTOR   = 0.50     # emergency mode multiplier
BOOTSTRAP_THRESHOLD = 100      # bets before adaptive kicks in
MAX_PICKS_PER_SCAN  = 20

# Adaptive filter tiers (odds_min, odds_max, min_ev, min_kelly, min_conf)
TIERS = [
    (1.80, 2.00, 0.10, 0.02, 0.55),
    (2.00, 3.00, 0.08, 0.015, 0.52),
    (3.00, 5.01, 0.05, 0.01, 0.50),
]

# Sport-level baseline confidence
SPORT_CONF = {
    "soccer":           {"h2h": 0.52, "spreads": 0.51, "totals": 0.53},
    "basketball":       {"h2h": 0.54, "spreads": 0.53, "totals": 0.54},
    "tennis":           {"h2h": 0.53, "spreads": 0.52},
    "mma":              {"h2h": 0.51},
    "baseball":         {"h2h": 0.52, "totals": 0.52},
    "hockey":           {"h2h": 0.52, "totals": 0.53},
    "americanfootball": {"h2h": 0.53, "spreads": 0.54, "totals": 0.53},
    "default":          {"default": 0.51},
}

ALLOWED_MARKETS = ("h2h", "spreads", "totals")  # btts not supported on /sports endpoint


# ═════════════════════════════════════════════════════════════════════════════
# PERSISTENCE
# ═════════════════════════════════════════════════════════════════════════════

def _load_state() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except Exception:
            pass
    return {
        "bank": INITIAL_BANK,
        "bet_counter": 0,
        "open_bets": {},
        "results": [],
        # Adaptive stats: {sport_market_tier: {n, wins}}
        "stats": {},
        # Odds history for line-movement: {event_key: [{"ts": float, "odds": float}]}
        "odds_history": {},
    }


def _save_state(st: dict):
    try:
        DATA_FILE.write_text(json.dumps(st, indent=2))
    except Exception as e:
        logger.error(f"State save error: {e}")


# Global mutable state
_S: dict = _load_state()

# In-memory pending (not persisted — ephemeral keyboard state)
_pending: dict = {}


# ═════════════════════════════════════════════════════════════════════════════
# MATH CORE
# ═════════════════════════════════════════════════════════════════════════════

def _calc_ev_kelly(conf: float, odds: float):
    ev    = conf * (odds - 1) - (1 - conf)
    raw_k = (conf * odds - 1) / (odds - 1) if odds > 1 else 0.0
    kelly = max(raw_k * KELLY_FRACTION, 0.0)
    return ev, kelly


def _get_tier(odds: float):
    for lo, hi, min_ev, min_kelly, min_conf in TIERS:
        if lo <= odds < hi:
            return min_ev, min_kelly, min_conf
    return None


def _conf_for(sport_key: str, market_key: str) -> float:
    base = sport_key.split("_")[0]
    table = SPORT_CONF.get(base, SPORT_CONF["default"])
    return table.get(market_key, table.get("default", 0.51))


# ═════════════════════════════════════════════════════════════════════════════
# ADAPTIVE STATS
# ═════════════════════════════════════════════════════════════════════════════

def _stat_key(sport: str, market: str, odds: float) -> str:
    base  = sport.split("_")[0]
    tier  = "low" if odds < 2.0 else ("mid" if odds < 3.0 else "high")
    return f"{base}.{market}.{tier}"


def _record_stat(sport: str, market: str, odds: float, won: bool):
    k = _stat_key(sport, market, odds)
    st = _S["stats"].setdefault(k, {"n": 0, "wins": 0})
    st["n"] += 1
    if won:
        st["wins"] += 1
    _save_state(_S)


def _adaptive_boost(sport: str, market: str, odds: float) -> float:
    """Return confidence adjustment based on historical win-rate.
    Only applied after BOOTSTRAP_THRESHOLD bets."""
    if len(_S["results"]) < BOOTSTRAP_THRESHOLD:
        return 0.0
    k  = _stat_key(sport, market, odds)
    st = _S["stats"].get(k, {})
    n  = st.get("n", 0)
    if n < 10:
        return 0.0
    wins      = st.get("wins", 0)
    obs_wr    = wins / n
    implied   = 1 / odds
    boost     = (obs_wr - implied) * 0.5   # half the edge as boost
    return max(min(boost, 0.10), -0.10)    # clamp ±10%


# ═════════════════════════════════════════════════════════════════════════════
# RISK MANAGEMENT
# ═════════════════════════════════════════════════════════════════════════════

def _is_emergency() -> bool:
    return _S["bank"] < INITIAL_BANK * (1 - STOP_LOSS_PCT)


def apply_risk_controls(kelly: float) -> float:
    """Return final stake enforcing Kelly, position sizing, stop-loss."""
    raw   = kelly * _S["bank"]
    raw   = min(raw, _S["bank"] * MAX_BET_PCT)  # position limit
    raw   = max(raw, MIN_STAKE)
    raw   = min(raw, _S["bank"])                # can't bet more than bank
    if _is_emergency():
        raw *= HALF_STAKE_FACTOR
    return round(raw, 0)


def _passes(sport: str, market: str, odds: float, conf: float):
    tier = _get_tier(odds)
    if tier is None:
        return False, f"odds {odds} outside [1.80–5.00]"
    min_ev, min_kelly, min_conf = tier
    boost = _adaptive_boost(sport, market, odds)
    conf_adj = conf + boost
    ev, kelly = _calc_ev_kelly(conf_adj, odds)
    if conf_adj < min_conf:
        return False, f"conf {conf_adj:.0%} < {min_conf:.0%}"
    if ev < min_ev:
        return False, f"EV {ev:.3f} < {min_ev}"
    if kelly < min_kelly:
        return False, f"Kelly {kelly*100:.1f}% < {min_kelly*100:.1f}%"
    return True, f"EV={ev:+.3f} Kelly={kelly*100:.1f}% conf_adj={conf_adj:.0%} boost={boost:+.1%}"


# ═════════════════════════════════════════════════════════════════════════════
# LINE MOVEMENT
# ═════════════════════════════════════════════════════════════════════════════

def _update_odds_history(event_key: str, current_odds: float):
    hist = _S["odds_history"].setdefault(event_key, [])
    hist.append({"ts": time.time(), "odds": current_odds})
    # keep last 24 h
    cutoff = time.time() - 86400
    _S["odds_history"][event_key] = [h for h in hist if h["ts"] > cutoff]


def _detect_line_movement(event_key: str, current_odds: float) -> str | None:
    """Return alert string if odds moved ≥5% vs 1h-ago snapshot."""
    hist = _S["odds_history"].get(event_key, [])
    cutoff = time.time() - 3600
    old = [h["odds"] for h in hist if h["ts"] < cutoff]
    if not old:
        return None
    prev = old[-1]
    if prev == 0:
        return None
    move = (current_odds - prev) / prev
    if abs(move) >= 0.05:
        direction = "📈" if move > 0 else "📉"
        return f"{direction} Line moved {move:+.1%} (was {prev:.2f})"
    return None


# ═════════════════════════════════════════════════════════════════════════════
# ODDS API
# ═════════════════════════════════════════════════════════════════════════════

async def fetch_picks() -> list:
    if not ODDS_KEY:
        logger.warning("ODDS_API_KEY not set")
        return []

    all_picks = []
    async with httpx.AsyncClient(timeout=20.0) as client:
        # 1. Active sports list
        try:
            r = await client.get(
                "https://api.the-odds-api.com/v4/sports",
                params={"apiKey": ODDS_KEY},
            )
            if r.status_code != 200:
                logger.error(f"Sports list: {r.status_code}")
                return []
            active = [s["key"] for s in r.json() if s.get("active")]
            logger.info(f"Active sports: {len(active)}")
        except Exception as e:
            logger.error(f"Sports list error: {e}")
            return []

        # 2. Odds per sport
        for sport_key in active:
            try:
                r2 = await client.get(
                    f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds",
                    params={
                        "apiKey": ODDS_KEY,
                        "regions": "us,eu,uk,au",
                        "markets": "h2h,spreads,totals",
                    },
                    timeout=12.0,
                )
                if r2.status_code != 200:
                    logger.warning(f"{sport_key}: HTTP {r2.status_code}")
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
                        name = outcome.get("name", "")
                        ev_key = f"{event.get('id','')}.{mk}.{name}"
                        _update_odds_history(ev_key, odds)
                        line_alert = _detect_line_movement(ev_key, odds)
                        ev, kelly = _calc_ev_kelly(conf, odds)
                        all_picks.append({
                            "match":      match_name,
                            "sport":      sport_key,
                            "market":     mk,
                            "selection":  name,
                            "odds":       odds,
                            "conf":       conf,
                            "ev":         ev,
                            "kelly":      kelly,
                            "line_alert": line_alert,
                        })

    _save_state(_S)   # persist updated odds history
    logger.info(f"Total raw picks: {len(all_picks)}")
    return all_picks


# ═════════════════════════════════════════════════════════════════════════════
# KEYBOARDS
# ═════════════════════════════════════════════════════════════════════════════

def _kb_pick(mid: int, stake: float):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"✅ ACCEPT  {int(stake)} UAH", callback_data=f"acc:{mid}:{int(stake)}"),
        InlineKeyboardButton("❌ SKIP",                      callback_data=f"skip:{mid}"),
    ]])


def _kb_settle(bet_id: int):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("WIN",     callback_data=f"win:{bet_id}"),
        InlineKeyboardButton("LOSS",    callback_data=f"loss:{bet_id}"),
        InlineKeyboardButton("PENDING", callback_data=f"pend:{bet_id}"),
    ]])


def _kb_done(label: str):
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="noop")]])


# ═════════════════════════════════════════════════════════════════════════════
# REPORTING
# ═════════════════════════════════════════════════════════════════════════════

def _roi_block(results: list, label: str = "Overall") -> list[str]:
    if not results:
        return [f"{label}: no settled bets yet"]
    n      = len(results)
    wins   = sum(1 for r in results if r["result"] == "WON")
    profit = sum(r["profit"] for r in results)
    staked = sum(r["stake"] for r in results)
    roi    = profit / staked * 100 if staked else 0
    return [
        f"{label}: {n} bets  WR={wins/n*100:.1f}%  "
        f"Profit={profit:+.0f} UAH  ROI={roi:+.1f}%"
    ]


def generate_daily_report() -> str:
    now   = datetime.now(UTC)
    day   = timedelta(days=1)
    week  = timedelta(weeks=1)
    month = timedelta(days=30)

    today_r  = [r for r in _S["results"] if (now - datetime.fromisoformat(r["settled_at"])) < day]
    week_r   = [r for r in _S["results"] if (now - datetime.fromisoformat(r["settled_at"])) < week]
    month_r  = [r for r in _S["results"] if (now - datetime.fromisoformat(r["settled_at"])) < month]

    lines = [
        f"📊 *Daily Report — {now.strftime('%d %b %Y %H:%M')} UTC*",
        f"Bank: {_S['bank']:.0f} UAH  "
        f"(Initial: {INITIAL_BANK:.0f}  P&L: {_S['bank']-INITIAL_BANK:+.0f})",
        f"Emergency mode: {'⚠️ YES' if _is_emergency() else '✅ No'}",
        "",
        *_roi_block(today_r,  "Today"),
        *_roi_block(week_r,   "Week"),
        *_roi_block(month_r,  "Month"),
        *_roi_block(_S["results"], "All-time"),
        "",
        "📈 *Top combos (≥5 bets)*:",
    ]

    # Best adaptive combos
    combos = [
        (k, v) for k, v in _S["stats"].items()
        if v["n"] >= 5
    ]
    combos_sorted = sorted(
        combos,
        key=lambda x: x[1]["wins"] / x[1]["n"],
        reverse=True,
    )
    if combos_sorted:
        for k, v in combos_sorted[:5]:
            wr = v["wins"] / v["n"] * 100
            lines.append(f"  {k}: {wr:.0f}% WR ({v['n']} bets)")
    else:
        lines.append("  Not enough data yet")

    # Recommendation
    lines.append("")
    lines.append("💡 *Recommendation*:")
    n_total = len(_S["results"])
    if n_total < 50:
        lines.append(f"  Bootstrap phase ({n_total}/50) — place bets, build data")
    elif n_total < 100:
        lines.append(f"  Learning phase ({n_total}/100) — adaptive stats accumulating")
    else:
        top = combos_sorted[0][0] if combos_sorted else "none"
        lines.append(f"  Focus on best combo: {top}")

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# SCAN
# ═════════════════════════════════════════════════════════════════════════════

async def scan(app=None):
    logger.info("🔍 SCAN START")
    raw = await fetch_picks()

    # Filter
    passed, reject_odds, reject_conf, reject_ev, reject_kelly = [], 0, 0, 0, 0
    for p in raw:
        ok, reason = _passes(p["sport"], p["market"], p["odds"], p["conf"])
        if ok:
            # Attach reason string and final stake
            ev, kelly = _calc_ev_kelly(
                p["conf"] + _adaptive_boost(p["sport"], p["market"], p["odds"]),
                p["odds"],
            )
            p["reason"] = reason
            p["kelly_final"] = kelly
            p["stake"] = apply_risk_controls(kelly)
            passed.append(p)
        else:
            if "odds"  in reason: reject_odds  += 1
            elif "conf" in reason: reject_conf  += 1
            elif "EV"   in reason: reject_ev    += 1
            else:                  reject_kelly += 1
            logger.info(f"  ✗ {p['match'][:30]} | {reason}")

    # Sort by EV descending, cap
    passed.sort(key=lambda x: x["ev"], reverse=True)
    passed = passed[:MAX_PICKS_PER_SCAN]

    logger.info(f"Passed: {len(passed)}  Skipped: {len(raw)-len(passed)}")

    if not passed:
        msg = (
            f"🔍 Scan: 0 picks passed\n"
            f"Fetched {len(raw)} | Odds:{reject_odds} Conf:{reject_conf} "
            f"EV:{reject_ev} Kelly:{reject_kelly} filtered\n"
            f"Bank: {_S['bank']:.0f} UAH"
        )
        if app and CHAT_ID:
            await app.bot.send_message(chat_id=CHAT_ID, text=msg)
        return

    # ── Header ────────────────────────────────────────────────────────────────
    n    = len(_S["results"])
    mode = "BOOTSTRAP" if n < BOOTSTRAP_THRESHOLD else "ADAPTIVE"
    emrg = "⚠️ EMERGENCY" if _is_emergency() else ""
    hdr  = (
        f"📊 *Scan {datetime.now(UTC).strftime('%d %b %H:%M')} UTC* {emrg}\n"
        f"Mode: {mode} ({n}/{BOOTSTRAP_THRESHOLD})  "
        f"Bank: {_S['bank']:.0f} UAH\n"
        f"Picks: {len(passed)} passed / {len(raw)-len(passed)} filtered"
    )
    if app and CHAT_ID:
        await app.bot.send_message(chat_id=CHAT_ID, text=hdr, parse_mode="Markdown")

    # ── Pick cards ────────────────────────────────────────────────────────────
    for p in passed:
        la_line = f"\n{p['line_alert']}" if p.get("line_alert") else ""
        text = (
            f"🏟 *{p['sport'].upper()}*\n"
            f"{p['match']}\n"
            f"_{p['market'].upper()}: {p['selection']}_\n\n"
            f"Odds: `{p['odds']}`  Conf: `{p['conf']:.0%}`\n"
            f"EV: `{p['ev']:+.3f}`  Kelly: `{p['kelly_final']*100:.1f}%`\n"
            f"Stake: `{int(p['stake'])} UAH`{la_line}"
        )
        if app and CHAT_ID:
            try:
                sent = await app.bot.send_message(
                    chat_id=CHAT_ID,
                    text=text,
                    parse_mode="Markdown",
                    reply_markup=_kb_pick(0, p["stake"]),
                )
                mid = sent.message_id
                _pending[mid] = p
                await sent.edit_reply_markup(reply_markup=_kb_pick(mid, p["stake"]))
            except Exception as e:
                logger.error(f"Send error: {e}")
        await asyncio.sleep(0.15)

    logger.info("🔍 SCAN DONE")


# ═════════════════════════════════════════════════════════════════════════════
# CALLBACK HANDLER
# ═════════════════════════════════════════════════════════════════════════════

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    data = (q.data or "").strip()
    try:
        await q.answer(cache_time=0)
    except Exception:
        pass

    if data == "noop":
        return

    # ── ACCEPT ────────────────────────────────────────────────────────────────
    if data.startswith("acc:"):
        parts  = data.split(":")
        mid    = int(parts[1])
        stake  = float(parts[2])
        pick   = _pending.pop(mid, None)
        if not pick:
            await q.message.reply_text("Pick expired.")
            return
        try:
            await q.message.edit_reply_markup(_kb_done(f"Accepted {int(stake)} UAH"))
        except Exception:
            pass
        _S["bet_counter"] += 1
        bid = _S["bet_counter"]
        _S["open_bets"][str(bid)] = {
            **{k: v for k, v in pick.items() if k not in ("reason", "line_alert")},
            "id": bid,
            "stake": stake,
            "placed_at": datetime.now(UTC).isoformat(),
            "status": "OPEN",
        }
        _save_state(_S)
        await q.message.reply_text(
            f"✅ Bet #{bid}\n{pick['match']}\n"
            f"{pick['market'].upper()}: {pick['selection']}  @{pick['odds']}\n"
            f"Stake: {int(stake)} UAH",
            reply_markup=_kb_settle(bid),
        )
        return

    # ── SKIP ──────────────────────────────────────────────────────────────────
    if data.startswith("skip:"):
        _pending.pop(int(data.split(":")[1]), None)
        try:
            await q.message.edit_reply_markup(_kb_done("Skipped"))
        except Exception:
            pass
        return

    # ── WIN ───────────────────────────────────────────────────────────────────
    if data.startswith("win:"):
        bid = int(data.split(":")[1])
        bet = _S["open_bets"].get(str(bid))
        if not bet:
            await q.message.reply_text("Bet not found.")
            return
        profit = round((bet["odds"] - 1.0) * bet["stake"], 2)
        _S["bank"] += profit
        result = {**bet, "result": "WON", "profit": profit,
                  "settled_at": datetime.now(UTC).isoformat()}
        _S["results"].append(result)
        bet["status"] = "SETTLED"
        _record_stat(bet["sport"], bet["market"], bet["odds"], won=True)
        try:
            await q.message.edit_reply_markup(_kb_done(f"WIN +{profit:.0f} UAH"))
        except Exception:
            pass
        await q.message.reply_text(
            f"🏆 WIN #{bid}  +{profit:.0f} UAH\n"
            f"Bank: {_S['bank']:.0f} UAH  ({len(_S['results'])} settled)"
        )
        return

    # ── LOSS ──────────────────────────────────────────────────────────────────
    if data.startswith("loss:"):
        bid = int(data.split(":")[1])
        bet = _S["open_bets"].get(str(bid))
        if not bet:
            await q.message.reply_text("Bet not found.")
            return
        loss = -round(bet["stake"], 2)
        _S["bank"] += loss
        result = {**bet, "result": "LOST", "profit": loss,
                  "settled_at": datetime.now(UTC).isoformat()}
        _S["results"].append(result)
        bet["status"] = "SETTLED"
        _record_stat(bet["sport"], bet["market"], bet["odds"], won=False)
        try:
            await q.message.edit_reply_markup(_kb_done(f"LOSS {loss:.0f} UAH"))
        except Exception:
            pass
        await q.message.reply_text(
            f"❌ LOSS #{bid}  {loss:.0f} UAH\n"
            f"Bank: {_S['bank']:.0f} UAH  ({len(_S['results'])} settled)\n"
            + ("⚠️ EMERGENCY MODE ACTIVATED" if _is_emergency() else "")
        )
        return

    # ── PENDING ───────────────────────────────────────────────────────────────
    if data.startswith("pend:"):
        try:
            await q.message.edit_reply_markup(_kb_done("⏳ Pending"))
        except Exception:
            pass
        return


# ═════════════════════════════════════════════════════════════════════════════
# COMMANDS
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Betting Bot v9.0*\n\n"
        "/scan    — run scan now\n"
        "/stats   — performance report\n"
        "/bank    — bankroll status\n"
        "/bets    — open bets\n"
        "/report  — daily report\n"
        "/help    — this message",
        parse_mode="Markdown",
    )


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Scanning…")
    await scan(context.application)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        generate_daily_report(),
        parse_mode="Markdown",
    )


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        generate_daily_report(),
        parse_mode="Markdown",
    )


async def cmd_bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    emrg = "\n⚠️ EMERGENCY MODE — stakes halved" if _is_emergency() else ""
    await update.message.reply_text(
        f"💰 Bank: *{_S['bank']:.0f} UAH*\n"
        f"Initial: {INITIAL_BANK:.0f} UAH\n"
        f"P&L: {_S['bank']-INITIAL_BANK:+.0f} UAH\n"
        f"Settled bets: {len(_S['results'])}{emrg}",
        parse_mode="Markdown",
    )


async def cmd_bets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    open_b = [b for b in _S["open_bets"].values() if b.get("status") == "OPEN"]
    if not open_b:
        await update.message.reply_text("No open bets.")
        return
    lines = [f"*Open bets ({len(open_b)})*"]
    for b in open_b:
        lines.append(
            f"#{b['id']} {b['match']} | {b['selection']} | "
            f"@{b['odds']} stake {b['stake']:.0f} UAH"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


# ═════════════════════════════════════════════════════════════════════════════
# SCHEDULER + STARTUP
# ═════════════════════════════════════════════════════════════════════════════

scheduler = AsyncIOScheduler()


async def _daily_report_job(app):
    """Send daily report at 21:00 UTC."""
    try:
        await app.bot.send_message(
            chat_id=CHAT_ID,
            text=generate_daily_report(),
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"Daily report error: {e}")


async def post_init(app):
    logger.info("Bot initializing…")
    scheduler.start()
    scheduler.add_job(scan, "cron", hour=8,  minute=0,
                      kwargs={"app": app}, id="daily_scan")
    scheduler.add_job(_daily_report_job, "cron", hour=21, minute=0,
                      kwargs={"app": app}, id="daily_report")
    logger.info("Scheduler: scan@08:00 UTC  report@21:00 UTC")
    try:
        await app.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"✅ *Betting Bot v9.0 started*\n"
                f"Bank: {_S['bank']:.0f} UAH  "
                f"Settled: {len(_S['results'])} bets"
            ),
            parse_mode="Markdown",
        )
    except Exception:
        pass
    await scan(app)


async def post_stop(app):
    if scheduler.running:
        scheduler.shutdown(wait=False)


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")
    app = Application.builder().token(TOKEN).build()
    app.post_init = post_init
    app.post_stop = post_stop

    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("scan",   cmd_scan))
    app.add_handler(CommandHandler("stats",  cmd_stats))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("bank",   cmd_bank))
    app.add_handler(CommandHandler("bets",   cmd_bets))
    app.add_handler(CommandHandler("help",   cmd_help))

    logger.info("🚀 Polling…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
