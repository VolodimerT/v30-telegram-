"""
main_autonomous_hermes.py — BETTING BOT v7.0 + HERMÈS

All parameters (confidence, Kelly, EV thresholds) are read from
HermesManager instead of hard-coded constants.

Commands:
  /scan  /refresh  /place_bet  /settle  /bets  /bank  /help
  /hermes_status   — full parameter snapshot
  /hermes_stats    — W/L/ROI per market & sport
  /hermes_update   — force learning cycle now
  /hermes_updates  — changelog of parameter changes
  /hermes_reset    — reset to defaults (with confirmation)
"""

import os, json, logging, asyncio, copy
from datetime import datetime, timezone, timedelta

import httpx
from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from hermes_manager     import HermesManager, DEFAULT_STATE
from feedback_tracker   import FeedbackTracker
from learning_algorithm import LearningAlgorithm

# ─── LOGGING ────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
UTC    = timezone.utc

# ─── ENV ────────────────────────────────────────────────────────────────────
TOKEN        = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID_STR  = os.getenv("CHAT_ID", "")
CHAT_ID      = int(CHAT_ID_STR) if CHAT_ID_STR else None
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")

# ─── HERMÈS ──────────────────────────────────────────────────────────────────
hermes   = HermesManager()
tracker  = FeedbackTracker()
learner  = LearningAlgorithm(hermes, tracker)

TOP_N        = 50
INITIAL_BANK = hermes.state.get("initial_bank", 1019.0)
scheduler    = AsyncIOScheduler()
ALL_MATCHES  = None

# ─── MARKET CONFIG ────────────────────────────────────────────────────────────
SPORT_MARKETS = {
    "soccer":     {"featured": "h2h,spreads,totals", "additional": "btts,draw_no_bet",
                   "accept": {"h2h","spreads","totals","btts","draw_no_bet"}},
    "basketball": {"featured": "h2h,spreads,totals,h2h_h1,totals_h1", "additional": None,
                   "accept": {"h2h","spreads","totals","h2h_h1","totals_h1"}},
    "icehockey":  {"featured": "h2h,spreads,totals,h2h_p1,h2h_p2,h2h_p3", "additional": None,
                   "accept": {"h2h","spreads","totals","h2h_p1","h2h_p2","h2h_p3"}},
    "tennis":     {"featured": "h2h", "additional": None, "accept": {"h2h"}},
    "mma":        {"featured": "h2h", "additional": None, "accept": {"h2h"}},
    "boxing":     {"featured": "h2h", "additional": None, "accept": {"h2h"}},
    "baseball":   {"featured": "h2h,spreads,totals", "additional": None,
                   "accept": {"h2h","spreads","totals"}},
    "default":    {"featured": "h2h,spreads,totals", "additional": None,
                   "accept": {"h2h","spreads","totals"}},
}
MK_BONUS = {"btts": 0.07, "spreads": 0.05, "totals": 0.05, "draw_no_bet": 0.04,
            "h2h_p1": 0.04, "h2h_p2": 0.04, "h2h_p3": 0.04, "h2h_h1": 0.03}
MK_EMOJI = {"h2h": "🏆", "spreads": "➕", "totals": "📊", "btts": "⚽",
            "draw_no_bet": "🛡", "h2h_h1": "½🏆", "totals_h1": "½📊",
            "h2h_p1": "P1🏒", "h2h_p2": "P2🏒", "h2h_p3": "P3🏒"}


def sport_cat(sk):
    for prefix in ("soccer","basketball","icehockey","tennis","mma","boxing","baseball"):
        if prefix in sk: return prefix
    return "default"


def market_label(mk, name, point):
    if point is not None:
        try:    p = f"{float(point):+.1f}"
        except: p = ""
    else: p = ""
    return {"h2h":         f"h2h: {name}",
            "spreads":     f"hcap{p}: {name}",
            "totals":      f"total{p}: {name}",
            "btts":        f"btts: {name}",
            "draw_no_bet": f"dnb: {name}",
            "h2h_h1":      f"1H h2h: {name}",
            "totals_h1":   f"1H total{p}: {name}",
            "h2h_p1":      f"P1 h2h: {name}",
            "h2h_p2":      f"P2 h2h: {name}",
            "h2h_p3":      f"P3 h2h: {name}"}.get(mk, f"{mk}: {name}")


# ─── KELLY + EV ──────────────────────────────────────────────────────────────

def kelly_fraction(conf, odds):
    b = odds - 1.0
    if b <= 0: return 0.0
    raw = (b * conf - (1.0 - conf)) / b
    return max(0.0, round(raw * hermes.state["kelly_multiplier"] * 0.25, 5))


def calc_ev(odds, conf):
    return round(conf * (odds - 1.0) - (1.0 - conf), 4)


def bet_size(bank, kf, min_bet=10.0, max_frac=0.08):
    if kf <= 0: return min_bet
    return max(min_bet, min(bank * kf, bank * max_frac))


def rec_label(ev):
    if ev >= hermes.get_ev_accept():    return "✅ ACCEPT"
    if ev >= hermes.get_ev_consider():  return "⚠️ CONSIDER"
    return "♻️ RECONSIDER"


# ─── STORAGE ─────────────────────────────────────────────────────────────────

class Storage:
    def __init__(self):
        self.bank = INITIAL_BANK
        self.bets = self._load("user_bets.json")
        self.app  = None

    @staticmethod
    def _load(p):
        try:    return json.load(open(p))
        except: return []

    def save(self):
        json.dump(self.bets, open("user_bets.json", "w"), indent=2)

    def set_app(self, a): self.app = a

    def add_bet(self, match, market, mk, scat, odds, stake, ev, conf, kf):
        bet = {"id": len(self.bets) + 1, "match": match, "market": market,
               "mk": mk, "scat": scat, "odds": odds, "stake": stake,
               "ev": ev, "conf": conf, "kf": kf,
               "timestamp": datetime.now(UTC).isoformat(), "status": "OPEN"}
        self.bets.append(bet)
        self.save()
        tracker.record_pick(bet["id"], match, market, mk, scat, odds, stake, ev, conf, kf)
        return bet

    async def tg(self, text, parse_mode="Markdown"):
        if not (self.app and CHAT_ID): return
        try:
            await self.app.bot.send_message(
                chat_id=CHAT_ID, text=text, parse_mode=parse_mode)
        except Exception as e:
            logger.error("TG send error: %s", e)


storage = Storage()


# ─── DATE WINDOW ─────────────────────────────────────────────────────────────

def today_window():
    now   = datetime.now(UTC)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end   = start + timedelta(hours=47)
    fmt   = "%Y-%m-%dT%H:%M:%SZ"
    return start.strftime(fmt), end.strftime(fmt)


# ─── BTTS FETCH ──────────────────────────────────────────────────────────────

async def fetch_btts(client, sk, date_from, date_to):
    result = {}
    try:
        r = await client.get(
            f"https://api.the-odds-api.com/v4/sports/{sk}/events",
            params={"apiKey": ODDS_API_KEY, "commenceTimeFrom": date_from,
                    "commenceTimeTo": date_to}, timeout=10.0)
        if r.status_code != 200: return result
        for ev in r.json()[:12]:
            ev_id = ev.get("id")
            if not ev_id: continue
            try:
                ro = await client.get(
                    f"https://api.the-odds-api.com/v4/sports/{sk}/events/{ev_id}/odds",
                    params={"apiKey": ODDS_API_KEY, "regions": "eu,uk",
                            "markets": "btts,draw_no_bet", "oddsFormat": "decimal"},
                    timeout=8.0)
                if ro.status_code == 200:
                    key = (ev.get("home_team", ""), ev.get("away_team", ""))
                    result[key] = ro.json().get("bookmakers", [])
                await asyncio.sleep(0.08)
            except Exception as e:
                logger.debug("BTTS ev %s: %s", ev_id, e)
    except Exception as e:
        logger.error("fetch_btts %s: %s", sk, e)
    return result


# ─── FETCH ALL ───────────────────────────────────────────────────────────────

async def fetch_all_matches():
    if not ODDS_API_KEY: return {}
    date_from, date_to = today_window()
    matches = {}
    async with httpx.AsyncClient(timeout=25.0) as client:
        sr = await client.get("https://api.the-odds-api.com/v4/sports",
                               params={"apiKey": ODDS_API_KEY})
        if sr.status_code != 200: return {}
        for sport in [s for s in sr.json() if s.get("active")]:
            sk   = sport.get("key", "")
            scat = sport_cat(sk)
            cfg  = SPORT_MARKETS[scat]
            if not sk: continue
            try:
                r = await client.get(
                    f"https://api.the-odds-api.com/v4/sports/{sk}/odds",
                    params={"apiKey": ODDS_API_KEY, "regions": "eu,uk",
                            "markets": cfg["featured"],
                            "commenceTimeFrom": date_from,
                            "commenceTimeTo": date_to,
                            "oddsFormat": "decimal"}, timeout=12.0)
                if r.status_code != 200: continue
                events = r.json()
                if not events: continue
                processed = []
                for ev in events:
                    home = ev.get("home_team", "")
                    away = ev.get("away_team", "")
                    if not home or not away: continue
                    processed.append({
                        "match": f"{home} vs {away}", "sport": sk,
                        "scat": scat, "home": home, "away": away,
                        "commence": ev.get("commence_time", ""),
                        "bookmakers": ev.get("bookmakers", [])})
                if scat == "soccer" and processed:
                    btts_map = await fetch_btts(client, sk, date_from, date_to)
                    for ev in processed:
                        key = (ev["home"], ev["away"])
                        if key in btts_map:
                            ev["bookmakers"] += btts_map[key]
                matches[sk] = processed
            except Exception as e:
                logger.error("%s: %s", sk, e)
    return matches


# ─── SCAN ────────────────────────────────────────────────────────────────────

async def scan():
    global ALL_MATCHES
    if not ALL_MATCHES:
        await storage.tg("⚠️ No match data. Run /refresh first.")
        return

    if hermes.is_paused():
        await storage.tg(
            "🔴 *HERMÈS STOP-LOSS ACTIVE* — scanning paused.\n"
            "Use /hermes\\_status for details.")
        return

    MIN_EV    = hermes.get_min_ev()
    MIN_KELLY = hermes.get_min_kelly()
    MIN_ODDS  = hermes.get_min_odds()
    MAX_ODDS  = hermes.get_max_odds()

    raw = []
    for sk, events in ALL_MATCHES.items():
        scat   = sport_cat(sk)
        accept = SPORT_MARKETS[scat]["accept"]
        for event in events:
            seen = set()
            for bk in event.get("bookmakers", [])[:4]:
                for market in bk.get("markets", []):
                    mk = market.get("key", "")
                    if mk not in accept: continue
                    for out in market.get("outcomes", []):
                        odds  = out.get("price", 0.0)
                        name  = out.get("name", "")
                        point = out.get("point", None)
                        if not (MIN_ODDS <= odds <= MAX_ODDS): continue
                        label = market_label(mk, name, point)
                        key   = (event["match"], label)
                        if key in seen: continue
                        seen.add(key)
                        conf = hermes.get_confidence(mk, scat)
                        ev_  = calc_ev(odds, conf)
                        kf   = kelly_fraction(conf, odds)
                        if ev_ < MIN_EV or kf < MIN_KELLY: continue
                        raw.append({
                            "match": event["match"], "sport": sk, "scat": scat,
                            "mk": mk, "label": label, "odds": odds,
                            "conf": conf, "ev": ev_, "kf": kf})

    if not raw:
        await storage.tg("⚠️ 0 value picks after Hermès filters.")
        return

    picks = sorted(raw,
                   key=lambda p: p["ev"] + MK_BONUS.get(p["mk"], 0),
                   reverse=True)[:TOP_N]
    mk_counter = {}
    for p in picks:
        mk_counter[p["mk"]] = mk_counter.get(p["mk"], 0) + 1

    for p in picks:
        emoji = MK_EMOJI.get(p["mk"], "🎯")
        rec   = rec_label(p["ev"])
        opt   = bet_size(storage.bank, p["kf"])
        msg   = (
            f"{rec} {emoji} *{p['sport'].upper()}*\n\n"
            f"*{p['match']}*\n"
            f"`{p['label']}`\n"
            f"Odds: `{p['odds']}` | EV: `{p['ev']:+.3f}`\n"
            f"Conf: {p['conf']:.0%} | Kelly: {p['kf']*100:.1f}%\n\n"
            f"💰 Optimal: *{opt:.0f} UAH*\n\n"
            f"/place\\_bet \"{p['match']}\" \"{p['label']}\" {p['odds']} {opt:.0f}"
        )
        await storage.tg(msg)
        await asyncio.sleep(0.15)

    summary = " | ".join(f"{k}:{v}" for k, v in sorted(mk_counter.items()))
    await storage.tg(f"📊 *Scan done*: {len(picks)} picks\n{summary}")


# ─── HERMÈS COMMAND HANDLERS ─────────────────────────────────────────────────

async def cmd_hermes_status(u, c):
    """
    /hermes_status — full parameter snapshot
    """
    snap   = hermes.snapshot()
    paused = "🔴 PAUSED (stop-loss)" if snap["paused"] else "🟢 ACTIVE"
    kf     = hermes.get_kelly_fraction()

    conf_lines = "\n".join(
        f"  `{k:<14}` {v:.0%}"
        for k, v in sorted(snap["confidence"].items()))
    boost_lines = "\n".join(
        f"  `{k:<12}` ×{v:.2f}"
        for k, v in sorted(snap["sport_boost"].items()))

    msg = (
        f"🧠 *HERMÈS STATUS* v7.0\n\n"
        f"State: {paused}\n"
        f"Cycles completed: {snap['cycles']}\n\n"
        f"*Kelly*\n"
        f"  Multiplier: ×{snap['kelly_mul']:.2f}\n"
        f"  Effective fraction: {kf*100:.1f}% of bank\n\n"
        f"*EV thresholds*\n"
        f"  ✅ ACCEPT ≥ {snap['ev_accept']:.0%}\n"
        f"  🔎 Min filter ≥ {snap['min_ev']:.2%}\n\n"
        f"*Odds range*: {snap['odds_range'][0]} – {snap['odds_range'][1]}\n\n"
        f"*Confidence per market*\n{conf_lines}\n\n"
        f"*Sport boost*\n{boost_lines}\n\n"
        f"_Updated: {snap['last_updated']}_"
    )
    await u.message.reply_text(msg, parse_mode="Markdown")


async def cmd_hermes_stats(u, c):
    """
    /hermes_stats — W/L/ROI table per market and sport
    """
    gs     = tracker.global_stats()
    mk     = tracker.market_stats()
    sp     = tracker.sport_stats()
    recent = tracker.recent_roi(10)

    def row(label, s):
        if s["n"] == 0:
            return f"  `{label:<14}` — no data"
        icon = "📈" if s["roi"] > 0 else "📉"
        return (f"  `{label:<14}` "
                f"{s['n']}b  "
                f"WR={s['wr']:.0%}  "
                f"EMA={s['ema_wr']:.0%}  "
                f"ROI={s['roi']:+.1%} {icon}")

    mk_lines = "\n".join(row(k, v) for k, v in sorted(mk.items())) or "  (no data yet)"
    sp_lines = "\n".join(row(k, v) for k, v in sorted(sp.items())) or "  (no data yet)"
    r_icon   = "📈" if recent > 0 else "📉"

    msg = (
        f"📊 *HERMÈS STATS*\n\n"
        f"*Overall* ({gs['n']} settled bets)\n"
        f"  WR: {gs['wr']:.0%} | EMA WR: {gs['ema_wr']:.0%}\n"
        f"  ROI: {gs['roi']:+.1%} | Profit: {gs['total_profit']:+.2f} UAH\n"
        f"  Recent ROI (last 10): {recent:+.1%} {r_icon}\n\n"
        f"*Per market*\n{mk_lines}\n\n"
        f"*Per sport*\n{sp_lines}\n\n"
        f"_Kelly ×{hermes.state['kelly_multiplier']:.2f} | "
        f"Cycles: {hermes.state['cycles_completed']}_"
    )
    await u.message.reply_text(msg, parse_mode="Markdown")


async def cmd_hermes_update(u, c):
    """
    /hermes_update — force a learning cycle now
    """
    await u.message.reply_text("⏳ Running learning cycle…")
    changes = learner.run_cycle(storage.bank)
    if changes:
        msg = "🧠 *HERMÈS updated*\n\n" + "\n".join(f"• {ch}" for ch in changes)
    else:
        msg = "🧠 Hermès: no adjustments needed (system stable)"
    await u.message.reply_text(msg, parse_mode="Markdown")


async def cmd_hermes_updates(u, c):
    """
    /hermes_updates [N]   — last N parameter changes (default 20, max 100)
    """
    try:
        n = int(c.args[0]) if c.args else 20
        n = max(1, min(n, 100))
    except (IndexError, ValueError):
        n = 20

    log = hermes.state.get("changelog", [])
    if not log:
        await u.message.reply_text("🧠 No parameter changes recorded yet.\n"
                                   "Changes appear after /settle or /hermes\\_update.")
        return

    entries = log[-n:][::-1]          # newest first
    lines   = [f"🧠 *HERMÈS CHANGELOG* (last {len(entries)} of {len(log)})\n"]
    for e in entries:
        ts  = e.get("ts", "")[:16].replace("T", " ")
        key = e.get("key", "?")
        msg = e.get("msg", "")
        lines.append(f"`{ts}` *{key}*\n  _{msg}_")

    await u.message.reply_text("\n".join(lines), parse_mode="Markdown")


# Pending reset confirmations (chat_id → True)
_reset_pending: dict = {}


async def cmd_hermes_reset(u, c):
    """
    /hermes_reset               — show state + ask for confirmation
    /hermes_reset confirm       — actually reset
    /hermes_reset cancel        — cancel
    """
    chat_id = u.effective_chat.id

    # ── CONFIRM ──────────────────────────────────────────────────────────────
    if c.args and c.args[0].lower() == "confirm":
        if not _reset_pending.get(chat_id):
            await u.message.reply_text(
                "⚠️ No pending reset. Send /hermes\\_reset first.")
            return
        _reset_pending.pop(chat_id, None)

        old_km   = hermes.state.get("kelly_multiplier", 1.0)
        old_conf = dict(hermes.state.get("confidence", {}))

        # Reset to defaults, preserve bank + changelog
        new_state = copy.deepcopy(DEFAULT_STATE)
        new_state["initial_bank"] = hermes.state.get("initial_bank", 1019.0)
        new_state["peak_bank"]    = hermes.state.get("peak_bank",    1019.0)
        new_state["changelog"]    = hermes.state.get("changelog", [])
        new_state["changelog"].append({
            "ts":  datetime.now(UTC).isoformat(),
            "key": "RESET",
            "msg": f"Manual reset. Was: kelly×{old_km:.2f} conf={old_conf}",
        })
        hermes.state = new_state
        hermes.save()

        await u.message.reply_text(
            "✅ *Hermès RESET* — all parameters restored to defaults.\n\n"
            + hermes.format_status(),
            parse_mode="Markdown")
        return

    # ── CANCEL ───────────────────────────────────────────────────────────────
    if c.args and c.args[0].lower() == "cancel":
        _reset_pending.pop(chat_id, None)
        await u.message.reply_text("❌ Reset cancelled.")
        return

    # ── STEP 1 — show state + prompt ─────────────────────────────────────────
    snap = hermes.snapshot()
    conf_lines  = ", ".join(f"{k}={v:.0%}"   for k, v in sorted(snap["confidence"].items()))
    boost_lines = ", ".join(f"{k}=×{v:.2f}"  for k, v in sorted(snap["sport_boost"].items()))

    msg = (
        "⚠️ *HERMÈS RESET CONFIRMATION*\n\n"
        "*Current state that will be overwritten:*\n"
        f"  Cycles: {snap['cycles']} | Kelly ×{snap['kelly_mul']:.2f}\n"
        f"  Confidence: {conf_lines}\n"
        f"  Sport boost: {boost_lines}\n\n"
        "ℹ️ Bank balance and changelog are preserved.\n"
        "All learned parameters return to factory defaults.\n\n"
        "▶️ Confirm: /hermes\\_reset confirm\n"
        "✖️ Cancel:  /hermes\\_reset cancel"
    )
    _reset_pending[chat_id] = True
    await u.message.reply_text(msg, parse_mode="Markdown")


# ─── OTHER COMMAND HANDLERS ──────────────────────────────────────────────────

async def cmd_start(u, c):
    await u.message.reply_text(
        "🤖 *BETTING BOT v7.0 + HERMÈS*\n\n"
        "Self-learning parameter management active.\n\n"
        "/help — full command list",
        parse_mode="Markdown")


async def cmd_scan(u, c):
    await u.message.reply_text("⏳ Scanning markets…")
    await scan()


async def cmd_refresh(u, c):
    global ALL_MATCHES
    await u.message.reply_text("⏳ Reloading market data…")
    ALL_MATCHES = await fetch_all_matches()
    total = sum(len(v) for v in ALL_MATCHES.values())
    await u.message.reply_text(f"✅ {total} events across {len(ALL_MATCHES)} sports")


async def cmd_settle(u, c):
    """
    /settle <bet_id> WON|LOST
    Record bet result → triggers Hermès learning cycle.
    """
    try:
        if len(c.args) < 2:
            await u.message.reply_text("Usage: /settle <bet\\_id> WON|LOST",
                                       parse_mode="Markdown")
            return
        bet_id = int(c.args[0])
        result = c.args[1].upper()
        if result not in ("WON", "LOST"):
            await u.message.reply_text("Result must be WON or LOST")
            return
        rec = tracker.settle(bet_id, result)
        if not rec:
            await u.message.reply_text(f"Bet #{bet_id} not found or already settled")
            return
        storage.bank += (rec.profit or 0)
        hermes.update_bank(storage.bank)
        changes = learner.run_cycle(storage.bank)
        icon = "✅" if result == "WON" else "❌"
        msg  = (
            f"{icon} Bet #{bet_id} settled as *{result}*\n"
            f"{rec.match} | {rec.market}\n"
            f"Profit: *{rec.profit:+.2f} UAH* | Bank: *{storage.bank:.2f} UAH*\n"
        )
        if changes:
            msg += "\n🧠 *Hermès updated:*\n" + "\n".join(f"• {ch}" for ch in changes)
        await u.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await u.message.reply_text(f"Error: {e}")


async def cmd_place_bet(u, c):
    try:
        if len(c.args) < 4:
            await u.message.reply_text(
                '/place\\_bet "Match" "Market" odds stake',
                parse_mode="Markdown")
            return
        match  = c.args[0]
        market = c.args[1]
        odds   = float(c.args[2])
        stake  = float(c.args[3])
        mk_g   = ("spreads"     if "hcap"  in market else
                  "totals"      if "total" in market else
                  "btts"        if "btts"  in market else
                  "draw_no_bet" if "dnb"   in market else "h2h")
        conf = hermes.get_confidence(mk_g, "default")
        ev_  = calc_ev(odds, conf)
        kf   = kelly_fraction(conf, odds)
        bet  = storage.add_bet(match, market, mk_g, "default",
                               odds, stake, ev_, conf, kf)
        opt  = bet_size(storage.bank, kf)
        await u.message.reply_text(
            f"✅ Bet *#{bet['id']}* placed\n"
            f"{match} | {market}\n"
            f"Odds: {odds} | Stake: {stake:.0f} UAH\n"
            f"Kelly: {kf*100:.1f}% | Optimal: {opt:.0f} UAH\n\n"
            f"Settle: /settle {bet['id']} WON",
            parse_mode="Markdown")
    except Exception as e:
        await u.message.reply_text(f"Error: {e}")


async def cmd_bets(u, c):
    opens = [b for b in storage.bets if b.get("status") == "OPEN"]
    if not opens:
        await u.message.reply_text("No open bets.")
        return
    lines = ["📋 *OPEN BETS*\n"]
    for b in opens[-20:]:            # show last 20
        lines.append(
            f"*#{b['id']}* {b['match']}\n"
            f"  {b['market']} @ {b['odds']} | {b['stake']:.0f} UAH\n"
            f"  /settle {b['id']} WON  |  /settle {b['id']} LOST\n")
    await u.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_bank(u, c):
    gs = tracker.global_stats()
    await u.message.reply_text(
        f"💰 *Bank:* {storage.bank:.2f} UAH\n"
        f"Peak: {hermes.state.get('peak_bank', storage.bank):.2f} UAH\n\n"
        f"Settled bets: {gs['n']}\n"
        f"Total profit: {gs['total_profit']:+.2f} UAH\n"
        f"WR: {gs['wr']:.0%} | ROI: {gs['roi']:+.1%}",
        parse_mode="Markdown")


async def cmd_help(u, c):
    await u.message.reply_text(
        "📋 *COMMANDS*\n\n"
        "*Betting*\n"
        "/scan — scan for value picks\n"
        "/refresh — reload market data\n"
        "/place\\_bet \"Match\" \"Market\" odds stake\n"
        "/settle \\<id\\> WON|LOST — record result\n"
        "/bets — open bets\n"
        "/bank — balance + stats\n\n"
        "*Hermès (self-learning)*\n"
        "/hermes\\_status — current parameters\n"
        "/hermes\\_stats — W/L/ROI per market & sport\n"
        "/hermes\\_update — force learning cycle\n"
        "/hermes\\_updates \\[N\\] — changelog (last N changes)\n"
        "/hermes\\_reset — reset to defaults (with confirm)\n",
        parse_mode="Markdown")


# ─── SCHEDULER ───────────────────────────────────────────────────────────────

async def scheduled_full():
    global ALL_MATCHES
    ALL_MATCHES = await fetch_all_matches()
    await scan()


# ─── LIFECYCLE ───────────────────────────────────────────────────────────────

async def post_init(app):
    global ALL_MATCHES
    storage.set_app(app)
    logger.info("=== BOT v7.0+HERMÈS STARTING ===")
    ALL_MATCHES = await fetch_all_matches()
    if not scheduler.running:
        scheduler.start()
        scheduler.add_job(scan,           "cron", hour="*/1", minute=5,  id="scan")
        scheduler.add_job(scheduled_full, "cron", hour=7,     minute=0,  id="refresh")
    await scan()
    await storage.tg("🤖 *Bot v7.0 + Hermès ready*\n\n" + hermes.format_status())


async def post_stop(app):
    if scheduler.running:
        scheduler.shutdown()


def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN env var not set")
    app = Application.builder().token(TOKEN).build()
    app.post_init = post_init
    app.post_stop = post_stop

    for name, fn in [
        ("start",          cmd_start),
        ("scan",           cmd_scan),
        ("refresh",        cmd_refresh),
        ("hermes_status",  cmd_hermes_status),
        ("hermes_stats",   cmd_hermes_stats),
        ("hermes_update",  cmd_hermes_update),
        ("hermes_updates", cmd_hermes_updates),
        ("hermes_reset",   cmd_hermes_reset),
        ("settle",         cmd_settle),
        ("place_bet",      cmd_place_bet),
        ("bets",           cmd_bets),
        ("bank",           cmd_bank),
        ("help",           cmd_help),
    ]:
        app.add_handler(CommandHandler(name, fn))

    app.run_polling()


if __name__ == "__main__":
    main()
