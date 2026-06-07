"""
Betting Control Bot v15.0 GLOBAL PATCH
Role: scanner + v29.1 pre-analysis cockpit, NOT an auto-capper.

Key design:
- No fake fixed-confidence EV decisions.
- No automatic cash stake from scan.
- Aggregates best odds across bookmakers.
- Produces NEED_ANALYSIS / WATCH / EXPRESS_TEST / BLOCK flags.
- Selection-level buttons with short IDs.
- Persistent test log in JSON.
- /settle command and buttons.
- v29.1 copy prompt for external analysis.
"""

from __future__ import annotations

print("BOOT: v15 global main.py imported", flush=True)

import os
import json
import uuid
import logging
import asyncio
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ContextTypes, CommandHandler, CallbackQueryHandler
from telegram.error import RetryAfter, TimedOut, BadRequest, Conflict

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", force=True)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logger = logging.getLogger("betting-control-v15")
UTC = timezone.utc
VERSION = "v15.0-global-scout"

# ─────────────────────────────────────────────────────────────────────────────
# ENV HELPERS
# ─────────────────────────────────────────────────────────────────────────────

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
SYSTEM_MODE = os.getenv("SYSTEM_MODE", "TEST").strip().upper()  # TEST / EMERGENCY / CAUTION
DATA_FILE = Path(os.getenv("DATA_FILE", "/app/data/state.json").strip())

DAILY_SCAN_ENABLED = env_bool("DAILY_SCAN_ENABLED", "false")
STARTUP_MESSAGE_ENABLED = env_bool("STARTUP_MESSAGE_ENABLED", "false")
DAILY_SCAN_HOUR_UTC = env_int("DAILY_SCAN_HOUR_UTC", 8)

MAX_MATCHES_TO_SEND = env_int("MAX_MATCHES_TO_SEND", 12)
MAX_OPTIONS_PER_MATCH = env_int("MAX_OPTIONS_PER_MATCH", 3)
MAX_COPY_LINES = env_int("MAX_COPY_LINES", 35)
TEST_STAKE = env_float("TEST_STAKE", 10.0)

MIN_ODDS_SCAN = env_float("MIN_ODDS_SCAN", 1.25)
MAX_ODDS_SCAN = env_float("MAX_ODDS_SCAN", 5.00)
MAX_ODDS_EMERGENCY = env_float("MAX_ODDS_EMERGENCY", 4.50)
EXPRESS_MIN_ODDS = env_float("EXPRESS_MIN_ODDS", 1.25)
EXPRESS_MAX_ODDS = env_float("EXPRESS_MAX_ODDS", 1.45)

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
ALLOWED_MARKETS = tuple(
    s.strip()
    for s in os.getenv("ALLOWED_MARKETS", "h2h,spreads,totals").split(",")
    if s.strip()
)

# ─────────────────────────────────────────────────────────────────────────────
# STATE
# ─────────────────────────────────────────────────────────────────────────────

STATE: dict[str, Any] = {
    "version": VERSION,
    "bank": INITIAL_BANK,
    "mode": SYSTEM_MODE,
    "open_bets": {},
    "settled": [],
    "watchlist": {},
    "next_bet_id": 1,
    "last_scan": None,
}

PENDING: dict[str, dict[str, Any]] = {}


def load_state() -> None:
    global STATE
    try:
        if DATA_FILE.exists():
            with DATA_FILE.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                STATE.update(loaded)
                STATE["version"] = VERSION
                logger.info("State loaded from %s", DATA_FILE)
    except Exception as e:
        logger.exception("State load failed: %s", e)


def save_state() -> None:
    try:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = DATA_FILE.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(STATE, f, ensure_ascii=False, indent=2)
        tmp.replace(DATA_FILE)
    except Exception as e:
        logger.exception("State save failed: %s", e)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def implied_prob(odds: float) -> float:
    return 1.0 / odds if odds > 0 else 0.0

# ─────────────────────────────────────────────────────────────────────────────
# V29.1 SCOUT CLASSIFICATION — NO FAKE MODEL PROB
# ─────────────────────────────────────────────────────────────────────────────

CLASS_ORDER = {
    "EXPRESS_TEST": 0,
    "NEED_ANALYSIS": 1,
    "WATCH": 2,
    "UPSET_WATCH": 3,
    "MICRO_ONLY": 4,
    "BLOCK": 9,
}


def classify_candidate(c: dict[str, Any]) -> tuple[str, str]:
    sport = c.get("sport", "")
    market = c.get("market", "")
    odds = float(c.get("odds", 0.0) or 0.0)
    book_count = int(c.get("book_count", 0) or 0)

    if odds <= 1.01:
        return "BLOCK", "bad odds"
    if odds > MAX_ODDS_EMERGENCY:
        return "BLOCK", f"odds>{MAX_ODDS_EMERGENCY}; high-variance, no auto-value"
    if sport == "basketball_wnba":
        if odds >= 3.00:
            return "BLOCK", "WNBA underdog 3.00+ blocked; needs manual upset thesis"
        if market == "h2h" and odds > 2.20:
            return "MICRO_ONLY", "WNBA dog ML; micro/watch only"
        return "WATCH", "WNBA early/volatile; no auto cash"
    if sport.startswith("tennis_wta") or sport == "tennis_wta_french_open":
        return "WATCH", "WTA volatility; needs scenario + momentum gate"
    if market == "h2h" and EXPRESS_MIN_ODDS <= odds <= EXPRESS_MAX_ODDS:
        return "EXPRESS_TEST", "low-odds candidate for TEST express/system only"
    if market == "h2h" and odds >= 3.00:
        return "UPSET_WATCH", "underdog ML; no fake EV, scenario required"
    if market == "h2h" and odds > 2.20:
        return "WATCH", "dog/fair ML; needs strong scenario confirmation"
    if book_count < 2:
        return "WATCH", "low bookmaker coverage; stale/soft-line risk"
    return "NEED_ANALYSIS", "candidate needs v29.1 scenario check; model_prob unknown"


def candidate_score(c: dict[str, Any]) -> tuple[int, float, int]:
    cls = c.get("status", "WATCH")
    odds = float(c.get("odds", 0.0) or 0.0)
    book_count = int(c.get("book_count", 0) or 0)
    # lower class order is better; then prefer higher book_count; then odds closer to 1.7-2.2 for singles
    distance = abs(odds - 1.85)
    return (CLASS_ORDER.get(cls, 5), distance, -book_count)

# ─────────────────────────────────────────────────────────────────────────────
# KEYBOARDS
# ─────────────────────────────────────────────────────────────────────────────


def kb_candidate(cid: str, status: str) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton("🧠 ANALYZE", callback_data=f"an:{cid}"),
        InlineKeyboardButton("🧪 ACCEPT TEST", callback_data=f"ta:{cid}"),
        InlineKeyboardButton("❌ SKIP", callback_data=f"sk:{cid}"),
    ]
    if status == "BLOCK":
        buttons = [
            InlineKeyboardButton("🧠 SAVE WATCH", callback_data=f"an:{cid}"),
            InlineKeyboardButton("❌ SKIP", callback_data=f"sk:{cid}"),
        ]
    return InlineKeyboardMarkup([buttons])


def kb_settle(bet_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("WIN", callback_data=f"win:{bet_id}"),
        InlineKeyboardButton("LOSS", callback_data=f"loss:{bet_id}"),
        InlineKeyboardButton("PUSH", callback_data=f"push:{bet_id}"),
    ]])


def kb_done(label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="noop")]])

# ─────────────────────────────────────────────────────────────────────────────
# ODDS API — AGGREGATE BEST ODDS ACROSS BOOKMAKERS
# ─────────────────────────────────────────────────────────────────────────────


def outcome_label(market_key: str, outcome: dict[str, Any]) -> str:
    name = str(outcome.get("name", "")).strip()
    point = outcome.get("point", None)
    if point is None:
        return name
    if market_key == "totals":
        # The Odds API often uses name Over/Under and point total.
        return f"{name} {point}"
    if market_key == "spreads":
        return f"{name} {point:+g}"
    return f"{name} {point}"


async def fetch_candidates() -> list[dict[str, Any]]:
    if not ODDS_KEY:
        logger.warning("No ODDS_API_KEY — returning empty list")
        return []

    aggregated: dict[tuple, dict[str, Any]] = {}

    async with httpx.AsyncClient(timeout=20.0) as client:
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
                    timeout=12.0,
                )
                if r2.status_code != 200:
                    logger.warning("%s odds error: %s %s", sport_key, r2.status_code, r2.text[:160])
                    continue
                events = r2.json()
            except Exception as e:
                logger.warning("%s fetch error: %s", sport_key, e)
                continue

            for event in events:
                event_id = event.get("id") or f"{sport_key}:{event.get('home_team')}:{event.get('away_team')}:{event.get('commence_time')}"
                home = event.get("home_team", "")
                away = event.get("away_team", "")
                if not home or not away:
                    continue
                match_name = f"{home} vs {away}"
                commence_time = event.get("commence_time", "")
                bookmakers = event.get("bookmakers", [])
                if not bookmakers:
                    continue

                per_match_count = 0
                for bookmaker in bookmakers:
                    book_title = bookmaker.get("title") or bookmaker.get("key") or "book"
                    for market in bookmaker.get("markets", []):
                        mk = market.get("key", "")
                        if mk not in ALLOWED_MARKETS:
                            continue
                        for outcome in market.get("outcomes", []):
                            try:
                                odds = float(outcome.get("price", 0.0) or 0.0)
                            except Exception:
                                continue
                            if not (MIN_ODDS_SCAN <= odds <= MAX_ODDS_SCAN):
                                continue
                            label = outcome_label(mk, outcome)
                            point = outcome.get("point", None)
                            key = (sport_key, event_id, mk, label, point)
                            item = aggregated.get(key)
                            if not item:
                                item = {
                                    "id": "",
                                    "sport": sport_key,
                                    "event_id": event_id,
                                    "match": match_name,
                                    "market": mk,
                                    "selection": label,
                                    "point": point,
                                    "odds": odds,
                                    "best_book": book_title,
                                    "book_count": 0,
                                    "books": {},
                                    "commence_time": commence_time,
                                    "created_at": now_iso(),
                                }
                                aggregated[key] = item
                            item["books"][book_title] = odds
                            if odds > float(item["odds"]):
                                item["odds"] = odds
                                item["best_book"] = book_title

                # actual per-match output cap is applied after classification, not during aggregation

    candidates = []
    by_match: dict[str, int] = {}
    for item in aggregated.values():
        item["book_count"] = len(item.get("books", {}))
        item["avg_odds"] = round(sum(item["books"].values()) / max(1, item["book_count"]), 3)
        item["implied"] = implied_prob(float(item["odds"]))
        status, reason = classify_candidate(item)
        item["status"] = status
        item["reason"] = reason
        item["model_prob"] = None
        item["ev"] = None
        item["stake"] = 0.0
        item["id"] = uuid.uuid4().hex[:10]
        if status == "BLOCK":
            continue
        key = f"{item['sport']}|{item['match']}"
        if by_match.get(key, 0) >= MAX_OPTIONS_PER_MATCH:
            continue
        by_match[key] = by_match.get(key, 0) + 1
        candidates.append(item)

    candidates.sort(key=candidate_score)
    logger.info("Candidates produced: %d", len(candidates))
    return candidates

# ─────────────────────────────────────────────────────────────────────────────
# PROMPT
# ─────────────────────────────────────────────────────────────────────────────


def build_prompt(c: dict[str, Any]) -> str:
    return (
        "V29.1 DATA-CHECK REQUEST\n"
        f"Sport: {c.get('sport')}\n"
        f"Match: {c.get('match')}\n"
        f"Start: {c.get('commence_time')}\n"
        f"Market: {c.get('market')} | Selection: {c.get('selection')}\n"
        f"Best odds: {c.get('odds')} | Best book: {c.get('best_book')} | Book count: {c.get('book_count')} | Avg odds: {c.get('avg_odds')}\n"
        f"Implied probability: {c.get('implied', 0)*100:.1f}%\n"
        f"Bot status: {c.get('status')} | Reason: {c.get('reason')}\n\n"
        "Return structured facts only, no prediction unless asked:\n"
        "1) Last 5 results and opponent strength\n"
        "2) Injuries / lineup / goalie / starting roster status\n"
        "3) Rest, travel, B2B, schedule spot\n"
        "4) Pace/tempo/style and market thesis\n"
        "5) Line movement and stale-line risk\n"
        "6) Devil's advocate: what breaks this bet\n"
        "7) Scenario truth: CONTROL / NEUTRAL / CHAOS\n"
        "8) Fair probability range and EV_CI_Low if enough data\n"
        "9) Final gate suggestion: CORE / SUPPORT / MICRO / WATCH / PASS\n"
    )


def candidate_text(c: dict[str, Any]) -> str:
    odds = float(c.get("odds", 0.0) or 0.0)
    implied = implied_prob(odds) * 100
    books = c.get("book_count", 0)
    return (
        f"{c['sport'].upper()}\n"
        f"{c['match']}\n"
        f"{c['market'].upper()}: {c['selection']}\n\n"
        f"Odds: {odds:.2f} | Implied: {implied:.1f}%\n"
        f"Best book: {c.get('best_book')} | Books: {books} | Avg: {c.get('avg_odds')}\n"
        f"STATUS: {c.get('status')}\n"
        f"WHY: {c.get('reason')}\n"
        f"MODEL_PROB: UNKNOWN | EV: NOT_CALCULATED | STAKE: 0\n"
        f"ID: {c.get('id')}"
    )

# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM SAFE SEND
# ─────────────────────────────────────────────────────────────────────────────


async def safe_send(bot, chat_id: int, text: str, **kwargs):
    try:
        return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except RetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        try:
            return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
        except Exception as e2:
            logger.exception("Send retry failed: %s", e2)
            return None
    except (TimedOut, BadRequest) as e:
        logger.warning("Telegram send failed: %s", e)
        return None
    except Exception as e:
        logger.exception("Telegram send unexpected failure: %s", e)
        return None

# ─────────────────────────────────────────────────────────────────────────────
# SCAN
# ─────────────────────────────────────────────────────────────────────────────


async def scan(app: Optional[Application] = None, reply_chat_id: Optional[int] = None) -> None:
    logger.info("SCAN START")
    chat_id = reply_chat_id or CHAT_ID
    if not chat_id:
        logger.warning("CHAT_ID missing; scan cannot send")
        return

    candidates = await fetch_candidates()
    STATE["last_scan"] = {"at": now_iso(), "count": len(candidates)}
    save_state()

    if not candidates:
        if app:
            await safe_send(app.bot, chat_id, "Scan complete: no candidates after v15 filters.")
        return

    header = (
        f"📡 Daily Scout {VERSION}\n"
        f"Mode: {SYSTEM_MODE} | Cash auto: OFF\n"
        f"Candidates: {len(candidates)} | Sending top {min(MAX_MATCHES_TO_SEND, len(candidates))}\n"
        f"Rule: odds are price only; MODEL_PROB unknown until analysis."
    )
    if app:
        await safe_send(app.bot, chat_id, header)

    sent_count = 0
    for c in candidates[:MAX_MATCHES_TO_SEND]:
        PENDING[c["id"]] = c
        if app:
            await safe_send(app.bot, chat_id, candidate_text(c), reply_markup=kb_candidate(c["id"], c["status"]))
        sent_count += 1
        await asyncio.sleep(0.20)

    logger.info("SCAN DONE sent=%d", sent_count)

# ─────────────────────────────────────────────────────────────────────────────
# CALLBACKS
# ─────────────────────────────────────────────────────────────────────────────


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    data = (q.data or "").strip()
    try:
        await q.answer(cache_time=0)
    except Exception:
        pass

    if data == "noop":
        return

    action, _, cid = data.partition(":")
    if not cid:
        await q.message.reply_text("Bad callback data.")
        return

    c = PENDING.get(cid) or STATE.get("watchlist", {}).get(cid)
    if not c:
        await q.message.reply_text("Candidate expired. Run /scan again.")
        return

    if action == "sk":
        PENDING.pop(cid, None)
        try:
            await q.message.edit_reply_markup(reply_markup=kb_done("Skipped"))
        except Exception:
            pass
        return

    if action == "an":
        STATE.setdefault("watchlist", {})[cid] = c
        save_state()
        try:
            await q.message.edit_reply_markup(reply_markup=kb_done("Saved to WATCH"))
        exce
