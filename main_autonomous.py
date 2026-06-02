"""
main_autonomous_hermes.py — BETTING BOT v7.0.2 DEBUG+FIX

KEY FIXES vs v7.0:
  1. storage.tg() now reliably passes reply_markup to ALL sends
  2. callback_data uses a short pick_id (message_id) — no 64-byte truncation
  3. Pick cards use send_message directly (not edit_reply_markup trick)
  4. CallbackQueryHandler registered BEFORE MessageHandler
  5. Full debug logging on every keyboard send
  6. Explicit try/except with fallback text per button group

INLINE BUTTON FLOWS:
  Pick card  → [✅ ACCEPT <stake>]  [❌ SKIP]  [💰 CUSTOM]
  Accept     → bet placed → (DEMO) [✅ WIN]  [❌ LOSS]  [⏳ PENDING]
  Any card   → [📊 STATS]  [🤖 HERMÈS]  [⏮️ PREVIOUS]
"""

import os, json, logging, asyncio, copy
from datetime import datetime, timezone, timedelta

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    Application, ContextTypes, CommandHandler,
    CallbackQueryHandler, MessageHandler, filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from hermes_manager     import HermesManager, DEFAULT_STATE
from feedback_tracker   import FeedbackTracker
from learning_algorithm import LearningAlgorithm

# ─── LOGGING ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger  = logging.getLogger("bot")
log_kb  = logging.getLogger("bot.keyboard")   # dedicated keyboard debug logger
UTC     = timezone.utc

# ─── ENV ─────────────────────────────────────────────────────────────────────
TOKEN        = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID_STR  = os.getenv("CHAT_ID", "")
CHAT_ID      = int(CHAT_ID_STR) if CHAT_ID_STR else None
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
DEMO_MODE    = os.getenv("DEMO_MODE", "0") == "1"

# ─── HERMÈS ──────────────────────────────────────────────────────────────────
hermes  = HermesManager()
tracker = FeedbackTracker()
learner = LearningAlgorithm(hermes, tracker)

TOP_N        = 50
INITIAL_BANK = hermes.state.get("initial_bank", 1019.0)
scheduler    = AsyncIOScheduler()
ALL_MATCHES: dict = {}

# ─── SESSION STATE ────────────────────────────────────────────────────────────
# _pick_cache: maps message_id → full pick dict (survives until bot restart)
_pick_cache: dict[int, dict]  = {}
_prev_stack: list[int]        = []   # last 10 message_ids for ⏮️
_pending_custom: dict[int, int] = {} # chat_id → pick_id (waiting custom stake)
_PREV_MAX = 10

# ─── MARKET CONFIG ────────────────────────────────────────────────────────────
SPORT_MARKETS = {
    "soccer":     {"featured": "h2h,spreads,totals", "additional": "btts,draw_no_bet",
                   "accept": {"h2h","spreads","totals","btts","draw_no_bet"}},
    "basketball": {"featured": "h2h,spreads,totals,h2h_h1,totals_h1",
                   "accept": {"h2h","spreads","totals","h2h_h1","totals_h1"}},
    "icehockey":  {"featured": "h2h,spreads,totals,h2h_p1,h2h_p2,h2h_p3",
                   "accept": {"h2h","spreads","totals","h2h_p1","h2h_p2","h2h_p3"}},
    "tennis":     {"featured": "h2h", "accept": {"h2h"}},
    "mma":        {"featured": "h2h", "accept": {"h2h"}},
    "boxing":     {"featured": "h2h", "accept": {"h2h"}},
    "baseball":   {"featured": "h2h,spreads,totals",
                   "accept": {"h2h","spreads","totals"}},
    "default":    {"featured": "h2h,spreads,totals",
                   "accept": {"h2h","spreads","totals"}},
}
MK_BONUS = {"btts":0.07,"spreads":0.05,"totals":0.05,"draw_no_bet":0.04,
            "h2h_p1":0.04,"h2h_p2":0.04,"h2h_p3":0.04,"h2h_h1":0.03}
MK_EMOJI = {"h2h":"🏆","spreads":"➕","totals":"📊","btts":"⚽",
            "draw_no_bet":"🛡","h2h_h1":"½🏆","totals_h1":"½📊",
            "h2h_p1":"P1🏒","h2h_p2":"P2🏒","h2h_p3":"P3🏒"}


def sport_cat(sk: str) -> str:
    for p in ("soccer","basketball","icehockey","tennis","mma","boxing","baseball"):
        if p in sk: return p
    return "default"


def market_label(mk: str, name: str, point) -> str:
    try:    pt = f"{float(point):+.1f}" if point is not None else ""
    except: pt = ""
    return {"h2h":f"h2h: {name}", "spreads":f"hcap{pt}: {name}",
            "totals":f"total{pt}: {name}", "btts":f"btts: {name}",
            "draw_no_bet":f"dnb: {name}", "h2h_h1":f"1H h2h: {name}",
            "totals_h1":f"1H total{pt}: {name}", "h2h_p1":f"P1 h2h: {name}",
            "h2h_p2":f"P2 h2h: {name}", "h2h_p3":f"P3 h2h: {name}",
            }.get(mk, f"{mk}: {name}")


# ─── KELLY / EV ───────────────────────────────────────────────────────────────

def kelly_fraction(conf: float, odds: float) -> float:
    b = odds - 1.0
    if b <= 0: return 0.0
    raw = (b * conf - (1.0 - conf)) / b
    return max(0.0, round(raw * hermes.state["kelly_multiplier"] * 0.25, 5))


def calc_ev(odds: float, conf: float) -> float:
    return round(conf * (odds - 1.0) - (1.0 - conf), 4)


def bet_size(bank: float, kf: float, min_bet=10.0, max_frac=0.08) -> float:
    if kf <= 0: return min_bet
    return max(min_bet, min(bank * kf, bank * max_frac))


def rec_label(ev: float) -> str:
    if ev >= hermes.get_ev_accept():   return "✅ ACCEPT"
    if ev >= hermes.get_ev_consider(): return "⚠️ CONSIDER"
    return "♻️ RECONSIDER"


# ─── STORAGE ─────────────────────────────────────────────────────────────────

class Storage:
    def __init__(self):
        self.bank = INITIAL_BANK
        self.bets = self._load("user_bets.json")
        self.app: Application | None = None

    @staticmethod
    def _load(p):
        try:    return json.load(open(p))
        except: return []

    def save(self):
        json.dump(self.bets, open("user_bets.json","w"), indent=2)

    def set_app(self, a): self.app = a

    def add_bet(self, match, market, mk, scat, odds, stake, ev, conf, kf) -> dict:
        bet = {"id": len(self.bets)+1, "match": match, "market": market,
               "mk": mk, "scat": scat, "odds": odds, "stake": stake,
               "ev": ev, "conf": conf, "kf": kf, "status": "OPEN",
               "timestamp": datetime.now(UTC).isoformat()}
        self.bets.append(bet)
        self.save()
        tracker.record_pick(bet["id"], match, market, mk, scat, odds, stake, ev, conf, kf)
        logger.info("Bet #%d placed: %s | %s @ %.2f stake=%.0f", bet["id"], match, market, odds, stake)
        return bet

    async def send(self, chat_id: int, text: str,
                   parse_mode: str = "Markdown",
                   reply_markup=None) -> "Message | None":
        """
        FIX: Direct bot.send_message call with explicit reply_markup.
        Always logs keyboard presence so we can verify in Railway logs.
        """
        if not (self.app and self.app.bot):
            logger.warning("send() called but app/bot not ready")
            return None
        has_kb = reply_markup is not None
        log_kb.debug("send_message chat=%s has_markup=%s markup=%s",
                     chat_id, has_kb,
                     reply_markup.to_dict() if has_kb else "None")
        try:
            msg = await self.app.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            log_kb.debug("send OK → message_id=%s", msg.message_id)
            return msg
        except Exception as e:
            logger.error("send_message FAILED: %s | text=%s…", e, text[:60])
            # Graceful degradation: retry without markup
            if has_kb:
                try:
                    logger.warning("Retrying without reply_markup…")
                    return await self.app.bot.send_message(
                        chat_id=chat_id, text=text, parse_mode=parse_mode)
                except Exception as e2:
                    logger.error("Retry also failed: %s", e2)
            return None

    async def tg(self, text: str, parse_mode="Markdown", reply_markup=None):
        """Broadcast to default CHAT_ID."""
        if not CHAT_ID:
            logger.warning("tg() called but CHAT_ID not set")
            return None
        return await self.send(CHAT_ID, text, parse_mode, reply_markup)


storage = Storage()


# ─── DATE WINDOW ─────────────────────────────────────────────────────────────

def today_window():
    now   = datetime.now(UTC)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end   = start + timedelta(hours=47)
    fmt   = "%Y-%m-%dT%H:%M:%SZ"
    return start.strftime(fmt), end.strftime(fmt)


# ─── FETCH ───────────────────────────────────────────────────────────────────

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
                    result[(ev.get("home_team",""), ev.get("away_team",""))] = \
                        ro.json().get("bookmakers", [])
                await asyncio.sleep(0.08)
            except Exception as e:
                logger.debug("btts ev %s: %s", ev_id, e)
    except Exception as e:
        logger.error("fetch_btts %s: %s", sk, e)
    return result


async def fetch_all_matches() -> dict:
    if not ODDS_API_KEY:
        logger.warning("ODDS_API_KEY not set — no data")
        return {}
    date_from, date_to = today_window()
    matches = {}
    async with httpx.AsyncClient(timeout=25.0) as client:
        sr = await client.get("https://api.the-odds-api.com/v4/sports",
                               params={"apiKey": ODDS_API_KEY})
        if sr.status_code != 200: return {}
        for sport in [s for s in sr.json() if s.get("active")]:
            sk   = sport.get("key","")
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
                    home = ev.get("home_team",""); away = ev.get("away_team","")
                    if not home or not away: continue
                    processed.append({"match": f"{home} vs {away}", "sport": sk,
                                      "scat": scat, "home": home, "away": away,
                                      "commence": ev.get("commence_time",""),
                                      "bookmakers": ev.get("bookmakers",[])})
                if scat == "soccer" and processed:
                    btts_map = await fetch_btts(client, sk, date_from, date_to)
                    for ev in processed:
                        key = (ev["home"], ev["away"])
                        if key in btts_map: ev["bookmakers"] += btts_map[key]
                matches[sk] = processed
            except Exception as e:
                logger.error("%s: %s", sk, e)
    return matches


# ─────────────────────────────────────────────────────────────────────────────
#  KEYBOARD BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

def kb_pick(pick_id: int, opt: int) -> InlineKeyboardMarkup:
    """
    TYPE 1  — shown on every pick card.
    callback_data uses short numeric pick_id (message_id) — well under 64 bytes.
    """
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"✅ ACCEPT {opt}", callback_data=f"accept:{pick_id}:{opt}"),
            InlineKeyboardButton("❌ SKIP",           callback_data=f"skip:{pick_id}"),
            InlineKeyboardButton("💰 CUSTOM",         callback_data=f"custom:{pick_id}"),
        ],
        [
            InlineKeyboardButton("📊 STATS",     callback_data="q:stats"),
            InlineKeyboardButton("🤖 HERMÈS",    callback_data="q:hermes"),
            InlineKeyboardButton("⏮️ PREVIOUS",  callback_data="q:prev"),
        ],
    ])
    log_kb.debug("kb_pick built: pick_id=%s opt=%s rows=%s", pick_id, opt,
                 [[b.callback_data for b in row] for row in kb.inline_keyboard])
    return kb


def kb_demo(bet_id: int) -> InlineKeyboardMarkup:
    """TYPE 3 — demo settle, shown when DEMO_MODE=1."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ WIN",     callback_data=f"demo_win:{bet_id}"),
        InlineKeyboardButton("❌ LOSS",    callback_data=f"demo_loss:{bet_id}"),
        InlineKeyboardButton("⏳ PENDING", callback_data=f"demo_pend:{bet_id}"),
    ]])


def kb_quick() -> InlineKeyboardMarkup:
    """TYPE 4 — quick-action footer."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📊 STATS",    callback_data="q:stats"),
        InlineKeyboardButton("🤖 HERMÈS",   callback_data="q:hermes"),
        InlineKeyboardButton("⏮️ PREVIOUS", callback_data="q:prev"),
    ]])


# ─────────────────────────────────────────────────────────────────────────────
#  PICK TEXT
# ─────────────────────────────────────────────────────────────────────────────

def pick_text(p: dict, opt: float) -> str:
    emoji = MK_EMOJI.get(p["mk"], "🎯")
    rec   = rec_label(p["ev"])
    return (f"{rec} {emoji} *{p['sport'].upper()}*\n\n"
            f"*{p['match']}*\n"
            f"`{p['label']}`\n"
            f"Odds: `{p['odds']}` | EV: `{p['ev']:+.3f}`\n"
            f"Conf: {p['conf']:.0%} | Kelly: {p['kf']*100:.1f}%\n\n"
            f"💰 Optimal: *{opt:.0f} UAH*")


# ─────────────────────────────────────────────────────────────────────────────
#  SCAN
# ─────────────────────────────────────────────────────────────────────────────

async def scan():
    """Force scan (used by /scan command). Delegates to scan_daily()."""
    await scan_daily(force=True)


async def scan_daily(force: bool = False):
    """
    Main daily scan — called once at 08:00 UTC (or via /scan).
    Collects top-20 picks and sends them in a BATCHED message with
    individual Accept/Skip/Custom buttons under each pick.
    """
    global ALL_MATCHES
    if not ALL_MATCHES or force:
        ALL_MATCHES = await fetch_all_matches()

    if not ALL_MATCHES:
        await storage.tg("⚠️ No match data from API.", reply_markup=kb_quick())
        return

    if hermes.is_paused():
        await storage.tg("🔴 *HERMÈS STOP-LOSS ACTIVE* — scanning paused.\n"
                         "Use /hermes_status for details.", reply_markup=kb_quick())
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
                    if mk not in accept:
                        continue
                    for out in market.get("outcomes", []):
                        odds  = out.get("price", 0.0)
                        name  = out.get("name", "")
                        point = out.get("point", None)
                        if not (MIN_ODDS <= odds <= MAX_ODDS):
                            continue
                        label = market_label(mk, name, point)
                        key   = (event["match"], label)
                        if key in seen:
                            continue
                        seen.add(key)
                        conf = hermes.get_confidence(mk, scat)
                        ev_  = calc_ev(odds, conf)
                        kf   = kelly_fraction(conf, odds)
                        if ev_ < MIN_EV or kf < MIN_KELLY:
                            continue
                        raw.append({"match": event["match"], "sport": sk, "scat": scat,
                                    "mk": mk, "label": label, "odds": odds,
                                    "conf": conf, "ev": ev_, "kf": kf})

    if not raw:
        await storage.tg("⚠️ 0 value picks after Hermès filters.\n"
                         "_Try /refresh then /scan_", reply_markup=kb_quick())
        return

    picks = sorted(raw, key=lambda p: p["ev"] + MK_BONUS.get(p["mk"], 0),
                   reverse=True)[:20]   # hard cap at 20

    now_str = datetime.now(UTC).strftime("%d %b %Y %H:%M UTC")
    logger.info("scan_daily: %d picks → sending individually with buttons", len(picks))

    # ── Send header ───────────────────────────────────────────────────────────
    mk_counter: dict = {}
    for p in picks:
        mk_counter[p["mk"]] = mk_counter.get(p["mk"], 0) + 1
    summary_line = " | ".join(f"{k}:{v}" for k, v in sorted(mk_counter.items()))
    await storage.tg(
        f"📅 *Daily scan — {now_str}*\n"
        f"📊 Top {len(picks)} picks: {summary_line}\n\n"
        f"_Each pick has inline buttons ↓_",
        reply_markup=None)

    # ── Send each pick as separate card (with buttons) ────────────────────────
    for idx, p in enumerate(picks, 1):
        opt = int(round(bet_size(storage.bank, p["kf"])))
        header = f"*[{idx}/{len(picks)}]*  "
        text   = header + pick_text(p, opt)

        # Placeholder pick_id=0 — replaced immediately after send
        keyboard = kb_pick(0, opt)
        sent = await storage.tg(text, reply_markup=keyboard)

        if sent:
            pick_id = sent.message_id
            p["opt"] = opt
            _pick_cache[pick_id] = p
            _prev_stack.append(pick_id)
            if len(_prev_stack) > _PREV_MAX:
                _prev_stack.pop(0)
            try:
                await sent.edit_reply_markup(reply_markup=kb_pick(pick_id, opt))
            except Exception as e:
                logger.warning("edit_reply_markup L%d: %s", idx, e)
        else:
            logger.error("Pick #%d send failed: %s", idx, p["match"])

        await asyncio.sleep(0.3)   # stay well under Telegram 30msg/s limit

    # ── Footer ────────────────────────────────────────────────────────────────
    await storage.tg(f"✅ *Done* — {len(picks)} picks sent\n"
                     f"Bank: {storage.bank:.2f} UAH | "
                     f"Next scan: tomorrow 08:00 UTC",
                     reply_markup=kb_quick())


async def summary_daily():
    """Evening report at 20:00 UTC."""
    gs  = tracker.global_stats()
    now = datetime.now(UTC).strftime("%d %b %Y")
    open_bets = [b for b in storage.bets if b.get("status") == "OPEN"]
    await storage.tg(
        f"🌙 *Evening Summary — {now}*\n\n"
        f"Bank: *{storage.bank:.2f} UAH*\n"
        f"Open bets: {len(open_bets)}\n\n"
        f"Settled total: {gs['n']} | WR: {gs['wr']:.0%}\n"
        f"ROI: {gs['roi']:+.1%} | Profit: {gs['total_profit']:+.2f} UAH\n\n"
        f"_Next picks scan: tomorrow 08:00 UTC_",
        reply_markup=kb_quick())


async def scheduled_full():
    """Legacy — kept for compatibility but not used in v7.0.2 schedule."""
    await scan_daily()


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    data    = query.data or ""
    chat_id = query.message.chat_id
    log_kb.debug("callback received: data=%r chat=%s", data, chat_id)

    # ── ACKNOWLEDGE IMMEDIATELY (must happen within 30s, do it FIRST) ─────────
    # cache_time=0 prevents Telegram from caching the answer → handlers stay live
    try:
        await query.answer(cache_time=0)
    except Exception as e:
        log_kb.warning("query.answer() failed (non-fatal): %s", e)

    # ── QUICK ACTIONS ─────────────────────────────────────────────────────────
    if data.startswith("q:"):
        action = data[2:]
        try:
            if action == "stats":
                gs = tracker.global_stats()
                await query.message.reply_text(
                    f"📊 *Quick Stats*\n\n"
                    f"Settled: {gs['n']} bets\n"
                    f"WR: {gs['wr']:.0%} | EMA: {gs['ema_wr']:.0%}\n"
                    f"ROI: {gs['roi']:+.1%} | Profit: {gs['total_profit']:+.2f} UAH\n\n"
                    f"_Full: /hermes_stats_",
                    parse_mode="Markdown", reply_markup=kb_quick())

            elif action == "hermes":
                await query.message.reply_text(
                    hermes.format_status(), parse_mode="Markdown", reply_markup=kb_quick())

            elif action == "prev":
                cur  = query.message.message_id
                cand = [mid for mid in reversed(_prev_stack) if mid != cur]
                if not cand:
                    await query.answer("No previous picks in this session", show_alert=True, cache_time=0)
                    return
                prev_id = cand[0]
                p = _pick_cache.get(prev_id)
                if not p:
                    await query.answer("Previous pick expired", show_alert=True, cache_time=0)
                    return
                opt = p.get("opt", int(round(bet_size(storage.bank, p["kf"]))))
                await query.message.reply_text(
                    pick_text(p, opt) + "\n\n_⏮️ Re-shown previous pick_",
                    parse_mode="Markdown",
                    reply_markup=kb_pick(prev_id, opt))
        except Exception as e:
            logger.error("quick action %r failed: %s", action, e)
        return

    # ── SKIP ──────────────────────────────────────────────────────────────────
    if data.startswith("skip:"):
        try:
            pick_id = int(data.split(":")[1])
            _pick_cache.pop(pick_id, None)
            log_kb.debug("skip pick_id=%s", pick_id)
            # Replace ACCEPT/SKIP row with "Skipped" label, keep nav row active
            skipped_kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("⏭️ Skipped", callback_data="noop"),
            ], [
                InlineKeyboardButton("📊 STATS",    callback_data="q:stats"),
                InlineKeyboardButton("🤖 HERMÈS",   callback_data="q:hermes"),
                InlineKeyboardButton("⏮️ PREVIOUS", callback_data="q:prev"),
            ]])
            try:
                await query.message.edit_reply_markup(reply_markup=skipped_kb)
            except Exception as e:
                log_kb.warning("skip edit_markup: %s", e)
        except Exception as e:
            logger.error("skip handler: %s", e)
        return

    # ── ACCEPT ────────────────────────────────────────────────────────────────
    if data.startswith("accept:"):
        try:
            parts   = data.split(":")    # accept:<pick_id>:<stake>
            pick_id = int(parts[1])
            stake   = float(parts[2])
            log_kb.debug("accept pick_id=%s stake=%s", pick_id, stake)
            # Replace keyboard with quick-actions immediately (before async work)
            # This keeps the message alive and prevents double-tap
            try:
                await query.message.edit_reply_markup(reply_markup=kb_quick())
            except Exception: pass
            p = _pick_cache.get(pick_id)
            if not p:
                await query.message.reply_text("⚠️ Pick expired — run /scan again.",
                                               reply_markup=kb_quick())
                return
            await _do_accept(query.message, p, stake, pick_id)
        except Exception as e:
            logger.error("accept handler: %s", e)
            await query.message.reply_text(f"⚠️ Accept error: {e}")
        return

    # ── CUSTOM STAKE ──────────────────────────────────────────────────────────
    if data.startswith("custom:"):
        try:
            pick_id = int(data.split(":")[1])
            p = _pick_cache.get(pick_id)
            if not p:
                await query.message.reply_text("⚠️ Pick expired.", reply_markup=kb_quick())
                return
            _pending_custom[chat_id] = pick_id
            await query.message.reply_text(
                "💰 *Custom stake*\n\nReply with amount in UAH (e.g. `125`):",
                parse_mode="Markdown")
        except Exception as e:
            logger.error("custom handler: %s", e)
        return

    # ── DEMO WIN ──────────────────────────────────────────────────────────────
    if data.startswith("demo_win:"):
        try:
            bet_id = int(data.split(":")[1])
            await _do_settle(query.message, bet_id, "WON")
        except Exception as e:
            logger.error("demo_win handler: %s", e)
        return

    # ── DEMO LOSS ─────────────────────────────────────────────────────────────
    if data.startswith("demo_loss:"):
        try:
            bet_id = int(data.split(":")[1])
            await _do_settle(query.message, bet_id, "LOST")
        except Exception as e:
            logger.error("demo_loss handler: %s", e)
        return

    # ── DEMO PENDING ──────────────────────────────────────────────────────────
    if data.startswith("demo_pend:"):
        # query.answer() already called at top of function
        await query.message.reply_text(
            "⏳ *Pending* — settle manually with:\n`/settle <id> WON` or `/settle <id> LOST`",
            parse_mode="Markdown", reply_markup=kb_quick())
        return

    if data == "noop":
        return   # status label button — intentionally does nothing
    logger.warning("Unhandled callback_data: %r", data)


async def _do_accept(message, p: dict, stake: float, pick_id: int):
    """Register bet, update original message to 'accepted' state, reply with confirmation."""
    bet = storage.add_bet(
        p["match"], p["label"], p["mk"], p["scat"],
        p["odds"], stake, p["ev"], p["conf"], p["kf"])

    _pick_cache.pop(pick_id, None)
    log_kb.debug("_do_accept bet_id=%s DEMO=%s stake=%s", bet["id"], DEMO_MODE, stake)

    # ── Step 1: Update original pick card → show accepted status + keep nav row
    # Never remove keyboard entirely — quick-action buttons must stay clickable
    accepted_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"✅ Accepted {stake:.0f} UAH — Bet #{bet['id']}", callback_data="noop"),
    ], [
        InlineKeyboardButton("📊 STATS",    callback_data="q:stats"),
        InlineKeyboardButton("🤖 HERMÈS",   callback_data="q:hermes"),
        InlineKeyboardButton("⏮️ PREVIOUS", callback_data="q:prev"),
    ]])
    try:
        await message.edit_reply_markup(reply_markup=accepted_kb)
    except Exception as e:
        log_kb.warning("edit accepted_kb: %s", e)

    # ── Step 2: Reply with settlement card
    markup = kb_demo(bet["id"]) if DEMO_MODE else kb_quick()
    settle_hint = ("_Demo mode — use buttons below to settle:_"
                   if DEMO_MODE
                   else f"_Settle: /settle {bet['id']} WON or LOST_")
    text = (f"✅ *Registered: {stake:.0f} UAH*\n\n"
            f"Bet *#{bet['id']}*: {p['match']}\n"
            f"`{p['label']}`\n"
            f"Odds: `{p['odds']}` | EV: `{p['ev']:+.3f}`\n\n"
            + settle_hint)
    await message.reply_text(text, parse_mode="Markdown", reply_markup=markup)


async def _do_settle(message, bet_id: int, result: str):
    """Settle a bet (from demo button or /settle command) + trigger learning."""
    rec = tracker.settle(bet_id, result)
    if not rec:
        await message.reply_text(f"⚠️ Bet #{bet_id} not found or already settled")
        return
    storage.bank += rec.profit or 0
    hermes.update_bank(storage.bank)
    changes = learner.run_cycle(storage.bank)
    icon = "✅" if result == "WON" else "❌"
    text = (f"{icon} Bet *#{bet_id}* → *{result}*\n"
            f"{rec.match} | {rec.market}\n"
            f"Profit: *{rec.profit:+.2f} UAH* | Bank: *{storage.bank:.2f} UAH*")
    if changes:
        text += "\n\n🧠 *Hermès updated:*\n" + "\n".join(f"• {c}" for c in changes)
    # Keep nav row accessible after settle
    settled_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"{icon} Settled — Bet #{bet_id} {result}", callback_data="noop"),
    ], [
        InlineKeyboardButton("📊 STATS",    callback_data="q:stats"),
        InlineKeyboardButton("🤖 HERMÈS",   callback_data="q:hermes"),
        InlineKeyboardButton("⏮️ PREVIOUS", callback_data="q:prev"),
    ]])
    try:
        await message.edit_reply_markup(reply_markup=settled_kb)
    except Exception: pass
    await message.reply_text(text, parse_mode="Markdown", reply_markup=kb_quick())


# ─────────────────────────────────────────────────────────────────────────────
#  TEXT HANDLER — custom stake input
# ─────────────────────────────────────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id  = update.effective_chat.id
    pick_id  = _pending_custom.get(chat_id)
    if pick_id is None: return

    text = (update.message.text or "").strip()
    try:
        stake = float(text.replace(",","."))
        if stake <= 0: raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Invalid amount. Enter a number, e.g. `125`:",
                                        parse_mode="Markdown")
        return

    p = _pick_cache.get(pick_id)
    if not p:
        _pending_custom.pop(chat_id, None)
        await update.message.reply_text("⚠️ Pick expired. Rescan with /scan.")
        return

    _pending_custom.pop(chat_id, None)
    await _do_accept(update.message, p, stake, pick_id)


# ─────────────────────────────────────────────────────────────────────────────
#  HERMÈS COMMANDS
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_hermes_status(u, c):
    snap = hermes.snapshot()
    paused = "🔴 PAUSED" if snap["paused"] else "🟢 ACTIVE"
    kf     = hermes.get_kelly_fraction()
    conf_l = "\n".join(f"  `{k:<14}` {v:.0%}" for k,v in sorted(snap["confidence"].items()))
    boost_l= "\n".join(f"  `{k:<12}` ×{v:.2f}" for k,v in sorted(snap["sport_boost"].items()))
    msg = (f"🧠 *HERMÈS STATUS* v7.0.2\n\nState: {paused}\nCycles: {snap['cycles']}\n\n"
           f"*Kelly* ×{snap['kelly_mul']:.2f} → {kf*100:.1f}% of bank\n"
           f"*EV* accept≥{snap['ev_accept']:.0%} min≥{snap['min_ev']:.2%}\n"
           f"*Odds* {snap['odds_range'][0]}–{snap['odds_range'][1]}\n\n"
           f"*Confidence*\n{conf_l}\n\n*Sport boost*\n{boost_l}\n\n"
           f"_Updated: {snap['last_updated']}_")
    await u.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb_quick())


async def cmd_hermes_stats(u, c):
    gs = tracker.global_stats()
    mk = tracker.market_stats()
    sp = tracker.sport_stats()
    def row(label, s):
        if s["n"]==0: return f"  `{label:<14}` — no data"
        icon = "📈" if s["roi"]>0 else "📉"
        return f"  `{label:<14}` {s['n']}b WR={s['wr']:.0%} EMA={s['ema_wr']:.0%} ROI={s['roi']:+.1%} {icon}"
    recent = tracker.recent_roi(10)
    msg = (f"📊 *HERMÈS STATS*\n\n*Overall* ({gs['n']} bets)\n"
           f"  WR:{gs['wr']:.0%} EMA:{gs['ema_wr']:.0%} ROI:{gs['roi']:+.1%} Profit:{gs['total_profit']:+.2f}UAH\n"
           f"  Recent10:{recent:+.1%}\n\n"
           f"*Per market*\n" + "\n".join(row(k,v) for k,v in sorted(mk.items())) +
           f"\n\n*Per sport*\n"  + "\n".join(row(k,v) for k,v in sorted(sp.items())))
    await u.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb_quick())


async def cmd_hermes_update(u, c):
    await u.message.reply_text("⏳ Running learning cycle…")
    changes = learner.run_cycle(storage.bank)
    msg = ("🧠 *HERMÈS updated*\n\n" + "\n".join(f"• {ch}" for ch in changes)
           if changes else "🧠 No adjustments needed")
    await u.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb_quick())


async def cmd_hermes_updates(u, c):
    try:   n = int(c.args[0]) if c.args else 20
    except: n = 20
    n = max(1, min(n, 100))
    log = hermes.state.get("changelog",[])
    if not log:
        await u.message.reply_text("🧠 No parameter changes recorded yet."); return
    entries = log[-n:][::-1]
    lines = [f"🧠 *HERMÈS CHANGELOG* (last {len(entries)} of {len(log)})\n"]
    for e in entries:
        ts = e.get("ts","")[:16].replace("T"," ")
        lines.append(f"`{ts}` *{e.get('key','?')}*\n  _{e.get('msg','')}_")
    await u.message.reply_text("\n".join(lines), parse_mode="Markdown")


_reset_pending: dict = {}

async def cmd_hermes_reset(u, c):
    chat_id = u.effective_chat.id
    if c.args and c.args[0].lower() == "confirm":
        if not _reset_pending.get(chat_id):
            await u.message.reply_text("⚠️ No pending reset."); return
        _reset_pending.pop(chat_id, None)
        old_km   = hermes.state.get("kelly_multiplier",1.0)
        old_conf = dict(hermes.state.get("confidence",{}))
        new_state = copy.deepcopy(DEFAULT_STATE)
        new_state["initial_bank"] = hermes.state.get("initial_bank",1019.0)
        new_state["peak_bank"]    = hermes.state.get("peak_bank",1019.0)
        new_state["changelog"]    = hermes.state.get("changelog",[])
        new_state["changelog"].append({"ts":datetime.now(UTC).isoformat(),"key":"RESET",
                                       "msg":f"Manual reset. Was: kelly×{old_km:.2f}"})
        hermes.state = new_state; hermes.save()
        await u.message.reply_text("✅ *Hermès RESET*\n\n" + hermes.format_status(),
                                   parse_mode="Markdown"); return
    if c.args and c.args[0].lower() == "cancel":
        _reset_pending.pop(chat_id,None)
        await u.message.reply_text("❌ Cancelled."); return
    snap = hermes.snapshot()
    _reset_pending[chat_id] = True
    conf_s  = ", ".join(f"{k}={v:.0%}" for k,v in sorted(snap["confidence"].items()))
    await u.message.reply_text(
        f"⚠️ *HERMÈS RESET CONFIRMATION*\n\n"
        f"Cycles:{snap['cycles']} Kelly×{snap['kelly_mul']:.2f}\n{conf_s}\n\n"
        f"▶️ /hermes\\_reset confirm\n✖️ /hermes\\_reset cancel",
        parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────────────────────
#  STANDARD COMMANDS
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_start(u, c):
    await u.message.reply_text(
        "🤖 *BETTING BOT v7.0.2 + HERMÈS*\n\n"
        "✅ Inline buttons active\n\n"
        "📅 *Schedule*\n"
        "• 08:00 UTC — Daily scan (top-20 picks)\n"
        "• 20:00 UTC — Evening summary\n\n"
        "_Use /scan to force scan now_\n"
        "_Use /help for all commands_",
        parse_mode="Markdown", reply_markup=kb_quick())


async def cmd_scan(u, c):
    await u.message.reply_text("⏳ Scanning markets…")
    await scan()


async def cmd_refresh(u, c):
    global ALL_MATCHES
    await u.message.reply_text("⏳ Reloading…")
    ALL_MATCHES = await fetch_all_matches()
    total = sum(len(v) for v in ALL_MATCHES.values())
    await u.message.reply_text(f"✅ {total} events / {len(ALL_MATCHES)} sports",
                               reply_markup=kb_quick())


async def cmd_settle(u, c):
    try:
        if len(c.args) < 2:
            await u.message.reply_text("Usage: /settle <id> WON|LOST"); return
        await _do_settle(u.message, int(c.args[0]), c.args[1].upper())
    except Exception as e:
        await u.message.reply_text(f"Error: {e}")


async def cmd_place_bet(u, c):
    try:
        if len(c.args) < 4:
            await u.message.reply_text('/place\\_bet "Match" "Market" odds stake',
                                       parse_mode="Markdown"); return
        match = c.args[0]; market = c.args[1]
        odds  = float(c.args[2]); stake = float(c.args[3])
        mk_g  = ("spreads" if "hcap" in market else "totals" if "total" in market
                 else "btts" if "btts" in market else "draw_no_bet" if "dnb" in market else "h2h")
        conf = hermes.get_confidence(mk_g,"default")
        ev_  = calc_ev(odds,conf); kf = kelly_fraction(conf,odds)
        bet  = storage.add_bet(match,market,mk_g,"default",odds,stake,ev_,conf,kf)
        markup = kb_demo(bet["id"]) if DEMO_MODE else kb_quick()
        await u.message.reply_text(
            f"✅ Bet *#{bet['id']}* placed\n{match} | {market}\n"
            f"Odds:{odds} Stake:{stake:.0f}UAH\n_/settle {bet['id']} WON_",
            parse_mode="Markdown", reply_markup=markup)
    except Exception as e:
        await u.message.reply_text(f"Error: {e}")


async def cmd_bets(u, c):
    opens = [b for b in storage.bets if b.get("status")=="OPEN"]
    if not opens:
        await u.message.reply_text("No open bets.", reply_markup=kb_quick()); return
    lines = ["📋 *OPEN BETS*\n"]
    for b in opens[-20:]:
        lines.append(f"*#{b['id']}* {b['match']}\n  {b['market']} @{b['odds']} {b['stake']:.0f}UAH\n"
                     f"  /settle {b['id']} WON  |  /settle {b['id']} LOST\n")
    await u.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=kb_quick())


async def cmd_bank(u, c):
    gs = tracker.global_stats()
    await u.message.reply_text(
        f"💰 *Bank:* {storage.bank:.2f} UAH\n"
        f"Peak: {hermes.state.get('peak_bank',storage.bank):.2f} UAH\n\n"
        f"Settled:{gs['n']} WR:{gs['wr']:.0%} ROI:{gs['roi']:+.1%}\n"
        f"Profit:{gs['total_profit']:+.2f}UAH",
        parse_mode="Markdown", reply_markup=kb_quick())


async def cmd_help(u, c):
    await u.message.reply_text(
        "📋 *BETTING BOT v7.0.2*\n\n"
        "*Inline buttons* appear on every pick — tap to accept/skip/custom.\n\n"
        "/scan  /refresh  /bets  /bank\n"
        "/settle \\<id\\> WON|LOST\n"
        "/place\\_bet \"M\" \"Mkt\" odds stake\n\n"
        "*Hermès*\n"
        "/hermes_status /hermes_stats\n"
        "/hermes\\_update /hermes\\_updates\n"
        "/hermes_reset\n\n"
        f"DEMO\\_MODE={'ON ✅' if DEMO_MODE else 'OFF'} — set env DEMO\\_MODE=1 for WIN/LOSS buttons",
        parse_mode="Markdown", reply_markup=kb_quick())


# ─────────────────────────────────────────────────────────────────────────────
#  SCHEDULER + LIFECYCLE
# ─────────────────────────────────────────────────────────────────────────────

async def scheduled_full():
    global ALL_MATCHES
    ALL_MATCHES = await fetch_all_matches()
    await scan()


async def post_init(app: Application):
    global ALL_MATCHES
    storage.set_app(app)
    logger.info("=== BOT v7.0.2 STARTING === DEMO_MODE=%s CHAT_ID=%s", DEMO_MODE, CHAT_ID)
    ALL_MATCHES = await fetch_all_matches()
    logger.info("Initial fetch: %d sports", len(ALL_MATCHES))
    if not scheduler.running:
        scheduler.start()
        # Single scan per day at 08:00 UTC
        scheduler.add_job(scan_daily,     "cron", hour=8,  minute=0,  id="scan_daily",
                          timezone="UTC")
        # Evening summary at 20:00 UTC
        scheduler.add_job(summary_daily,  "cron", hour=20, minute=0,  id="summary_daily",
                          timezone="UTC")
    now_utc = datetime.now(UTC).strftime("%H:%M UTC")
    await storage.tg(
        f"🤖 *Bot v7.0.2 + Hermès READY* (started {now_utc})\n\n"
        + hermes.format_status()
        + "\n\n📅 *Schedule*\n"
          "• 08:00 UTC — Daily scan (top-20 picks)\n"
          "• 20:00 UTC — Evening summary\n\n"
          "_Use /scan to run now_",
        reply_markup=kb_quick())
    # NOTE: No auto-scan on startup — prevents spam on every redeploy.
    # The scheduler will fire at 08:00 UTC. Use /scan to trigger manually.


async def post_stop(app):
    if scheduler.running: scheduler.shutdown()


def main():
    if not TOKEN: raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

    app = Application.builder().token(TOKEN).build()
    app.post_init = post_init
    app.post_stop = post_stop

    # ── COMMAND HANDLERS ──────────────────────────────────────────────────────
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

    # ── CALLBACK HANDLER (MUST be before MessageHandler) ─────────────────────
    app.add_handler(CallbackQueryHandler(handle_callback))

    # ── TEXT HANDLER (custom stake input) ─────────────────────────────────────
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("All handlers registered — starting polling")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
