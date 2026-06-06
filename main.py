"""
Betting Telegram Bot v14 HOTFIX — anti-spam + persistent log

What this fixes:
- NO scan on startup by default.
- NO scanning all 48 sports: strict whitelist only.
- NO long callback_data: short UUID ids only.
- NO message flood: Top-N candidates only + safe_send with rate-limit handling.
- ACCEPT now creates a real OPEN bet in data/state.json.
- /settle supports win/loss/push and updates bank.

ENV required:
- TELEGRAM_BOT_TOKEN
- ODDS_API_KEY
Optional:
- CHAT_ID
- DATA_FILE=/app/data/state.json
- INITIAL_BANK=1019.98
- MAX_MATCHES_TO_SEND=10
- MAX_OPTIONS_PER_MATCH=2
- DAILY_SCAN_ENABLED=false
- DAILY_SCAN_HOUR_UTC=8
- ALLOWED_SPORTS=basketball_nba,basketball_wnba,icehockey_nhl,tennis_atp_french_open,tennis_wta_french_open
"""

from __future__ import annotations

print("BOOT: main.py imported", flush=True)

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", force=True)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logger = logging.getLogger("betting-bot-v14")
print("BOOT: logging configured", flush=True)

# ---------- ENV ----------
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ODDS_KEY = os.getenv("ODDS_API_KEY", "").strip()
CHAT_ID_RAW = os.getenv("CHAT_ID", "").strip()
CHAT_ID: Optional[int] = int(CHAT_ID_RAW) if CHAT_ID_RAW.lstrip("-").isdigit() else None

UTC = timezone.utc
DATA_FILE = Path(os.getenv("DATA_FILE", "/app/data/state.json"))
INITIAL_BANK = float(os.getenv("INITIAL_BANK", "1019.98"))

MAX_MATCHES_TO_SEND = int(os.getenv("MAX_MATCHES_TO_SEND", "10"))
MAX_OPTIONS_PER_MATCH = int(os.getenv("MAX_OPTIONS_PER_MATCH", "2"))
MAX_COPY_LINES = int(os.getenv("MAX_COPY_LINES", "30"))
DAILY_SCAN_ENABLED = os.getenv("DAILY_SCAN_ENABLED", "false").lower() == "true"
DAILY_SCAN_HOUR_UTC = int(os.getenv("DAILY_SCAN_HOUR_UTC", "8"))
STARTUP_MESSAGE_ENABLED = os.getenv("STARTUP_MESSAGE_ENABLED", "false").lower() == "true"

DEFAULT_ALLOWED_SPORTS = (
    "basketball_nba",
    "basketball_wnba",
    "icehockey_nhl",
    "tennis_atp_french_open",
    "tennis_wta_french_open",
)
ALLOWED_SPORTS = {
    s.strip() for s in os.getenv("ALLOWED_SPORTS", ",".join(DEFAULT_ALLOWED_SPORTS)).split(",") if s.strip()
}

BLOCKED_SPORT_KEY_PARTS = (
    "winner",
    "championship",
    "super_bowl",
    "politics",
    "golf",
)

ALLOWED_MARKETS = ("h2h", "spreads", "totals")

# ---------- Conservative scan model: rough ranking only, not final EV ----------
KELLY_FRACTION = 0.15
SPORT_CONF = {
    "basketball": {"h2h": 0.53, "spreads": 0.52, "totals": 0.52},
    "tennis": {"h2h": 0.52, "spreads": 0.51, "totals": 0.51},
    "icehockey": {"h2h": 0.51, "spreads": 0.50, "totals": 0.51},
    "default": {"h2h": 0.51, "spreads": 0.50, "totals": 0.50},
}

_pending: Dict[str, Dict[str, Any]] = {}
scheduler = AsyncIOScheduler(timezone="UTC")


# ---------- State ----------
def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _empty_state() -> Dict[str, Any]:
    return {
        "bank": INITIAL_BANK,
        "mode": "TEST",
        "next_bet_id": 1,
        "open_bets": [],
        "results": [],
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }


def load_state() -> Dict[str, Any]:
    try:
        if DATA_FILE.exists():
            with DATA_FILE.open("r", encoding="utf-8") as f:
                state = json.load(f)
            state.setdefault("bank", INITIAL_BANK)
            state.setdefault("mode", "TEST")
            state.setdefault("next_bet_id", 1)
            state.setdefault("open_bets", [])
            state.setdefault("results", [])
            return state
    except Exception as e:
        logger.exception("Failed to load state: %s", e)
    return _empty_state()


def save_state(state: Dict[str, Any]) -> None:
    state["updated_at"] = _now_iso()
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    tmp.replace(DATA_FILE)


def get_bank() -> float:
    return float(load_state().get("bank", INITIAL_BANK))


def create_open_bet(selection: Dict[str, Any], stake: Optional[float] = None) -> Dict[str, Any]:
    state = load_state()
    bet_id = int(state.get("next_bet_id", 1))
    bank = float(state.get("bank", INITIAL_BANK))
    default_stake = max(10.0, min(14.0, round(bank * 0.014, 2)))
    stake_value = float(stake if stake is not None else selection.get("stake", default_stake))

    bet = {
        "id": bet_id,
        "status": "OPEN",
        "created_at": _now_iso(),
        "sport": selection.get("sport", ""),
        "match": selection.get("match", ""),
        "market": selection.get("market", ""),
        "selection": selection.get("selection", ""),
        "odds": float(selection.get("odds", 0.0)),
        "stake": stake_value,
        "source": "telegram_v14_hotfix",
        "note": "TEST MODE / NEED_ANALYSIS before real cash",
    }
    state["open_bets"].append(bet)
    state["next_bet_id"] = bet_id + 1
    save_state(state)
    return bet


def settle_bet(bet_id: int, result: str) -> Tuple[bool, str]:
    result = result.upper().strip()
    if result not in {"WIN", "LOSS", "PUSH"}:
        return False, "Result must be WIN, LOSS or PUSH."

    state = load_state()
    open_bets = state.get("open_bets", [])
    bet = next((b for b in open_bets if int(b.get("id", -1)) == bet_id and b.get("status") == "OPEN"), None)
    if not bet:
        return False, f"Open bet #{bet_id} not found."

    stake = float(bet.get("stake", 0.0))
    odds = float(bet.get("odds", 0.0))
    if result == "WIN":
        profit = round((odds - 1.0) * stake, 2)
    elif result == "LOSS":
        profit = -round(stake, 2)
    else:
        profit = 0.0

    state["bank"] = round(float(state.get("bank", INITIAL_BANK)) + profit, 2)
    bet["status"] = "SETTLED"
    bet["result"] = result
    bet["profit"] = profit
    bet["settled_at"] = _now_iso()
    state["results"].append(dict(bet))
    save_state(state)

    sign = "+" if profit > 0 else ""
    return True, f"#{bet_id} {result} | P/L: {sign}{profit:.2f} | Bank: {state['bank']:.2f} UAH"


# ---------- Helpers ----------
def conf_for(sport_key: str, market_key: str) -> float:
    base = sport_key.split("_")[0]
    table = SPORT_CONF.get(base, SPORT_CONF["default"])
    return float(table.get(market_key, SPORT_CONF["default"].get(market_key, 0.50)))


def calc_ev(conf: float, odds: float) -> Tuple[float, float]:
    if odds <= 1:
        return -1.0, 0.0
    ev = conf * (odds - 1) - (1 - conf)
    raw_kelly = (conf * odds - 1) / (odds - 1)
    kelly = max(raw_kelly * KELLY_FRACTION, 0.0)
    return ev, kelly


def is_allowed_sport(sport_key: str) -> bool:
    if sport_key not in ALLOWED_SPORTS:
        return False
    return not any(part in sport_key for part in BLOCKED_SPORT_KEY_PARTS)


def format_point(point: Any) -> str:
    if point is None:
        return ""
    try:
        p = float(point)
        return f" {p:+g}"
    except Exception:
        return f" {point}"


def selection_key(event_id: str, market: str, name: str, point: Any) -> str:
    return f"{event_id}|{market}|{name}|{point}"


def kb_candidate(sid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ ACCEPT", callback_data=f"acc:{sid}"),
            InlineKeyboardButton("❌ SKIP", callback_data=f"skip:{sid}"),
        ]
    ])


def kb_settle(bet_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("WIN", callback_data=f"win:{bet_id}"),
            InlineKeyboardButton("LOSS", callback_data=f"loss:{bet_id}"),
            InlineKeyboardButton("PUSH", callback_data=f"push:{bet_id}"),
        ]
    ])


def kb_done(label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="noop")]])


async def safe_send(bot, chat_id: int, text: str, **kwargs):
    """Send without crashing the whole bot on Telegram limits/errors."""
    try:
        return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except RetryAfter as e:
        wait = int(getattr(e, "retry_after", 3)) + 1
        logger.warning("Telegram RetryAfter: sleeping %ss", wait)
        await asyncio.sleep(wait)
        try:
            return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
        except Exception as e2:
            logger.warning("Telegram send failed after retry: %s", e2)
            return None
    except (TimedOut, NetworkError) as e:
        logger.warning("Telegram network issue: %s", e)
        await asyncio.sleep(2)
        return None
    except BadRequest as e:
        logger.warning("Telegram BadRequest skipped: %s", e)
        return None
    except Exception as e:
        logger.exception("Telegram send failed: %s", e)
        return None


# ---------- Odds scan ----------
async def fetch_candidates() -> List[Dict[str, Any]]:
    if not ODDS_KEY:
        logger.warning("ODDS_API_KEY not set")
        return []

    candidates_by_key: Dict[str, Dict[str, Any]] = {}

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            r = await client.get("https://api.the-odds-api.com/v4/sports", params={"apiKey": ODDS_KEY})
            r.raise_for_status()
            active = [s.get("key") for s in r.json() if s.get("active") and s.get("key")]
        except Exception as e:
            logger.warning("Sports fetch failed: %s", e)
            return []

        sports_to_scan = [s for s in active if is_allowed_sport(s)]
        logger.info("Allowed sports scanned: %s", sports_to_scan)

        for sport_key in sports_to_scan:
            try:
                r2 = await client.get(
                    f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds",
                    params={
                        "apiKey": ODDS_KEY,
                        "regions": "us,eu,uk,au",
                        "markets": ",".join(ALLOWED_MARKETS),
                        "oddsFormat": "decimal",
                    },
                    timeout=12.0,
                )
                if r2.status_code != 200:
                    logger.info("%s skipped: HTTP %s", sport_key, r2.status_code)
                    continue
                events = r2.json()
            except Exception as e:
                logger.warning("%s odds fetch failed: %s", sport_key, e)
                continue

            for event in events:
                event_id = event.get("id", uuid.uuid4().hex[:8])
                home = event.get("home_team") or ""
                away = event.get("away_team") or ""
                if not home or not away:
                    continue
                match_name = f"{home} vs {away}"
                commence_time = event.get("commence_time", "")

                for bookmaker in event.get("bookmakers", []) or []:
                    book = bookmaker.get("title") or bookmaker.get("key") or "book"
                    for market in bookmaker.get("markets", []) or []:
                        mk = market.get("key", "")
                        if mk not in ALLOWED_MARKETS:
                            continue
                        conf = conf_for(sport_key, mk)

                        for outcome in market.get("outcomes", []) or []:
                            name = outcome.get("name") or ""
                            odds = float(outcome.get("price") or 0.0)
                            point = outcome.get("point")
                            if not name or not (1.30 <= odds <= 4.50):
                                continue

                            ev, kelly = calc_ev(conf, odds)
                            # This is only a rough candidate ranking. Do not present as final value.
                            if ev < -0.02:
                                continue

                            sel_name = f"{name}{format_point(point)}"
                            key = selection_key(str(event_id), mk, name, point)
                            existing = candidates_by_key.get(key)
                            if existing and existing["odds"] >= odds:
                                continue

                            candidates_by_key[key] = {
                                "sport": sport_key,
                                "match": match_name,
                                "commence_time": commence_time,
                                "market": mk,
                                "selection": sel_name,
                                "odds": odds,
                                "book": book,
                                "conf_est": conf,
                                "ev_est": ev,
                                "kelly_est": kelly,
                            }

    candidates = list(candidates_by_key.values())
    candidates.sort(key=lambda x: (x.get("ev_est", 0.0), x.get("odds", 0.0)), reverse=True)
    return candidates


async def scan(app: Application, chat_id: Optional[int] = None) -> None:
    target_chat = chat_id or CHAT_ID
    if not target_chat:
        logger.warning("No chat_id available for scan output")
        return

    logger.info("SCAN START")
    candidates = await fetch_candidates()
    bank = get_bank()

    if not candidates:
        await safe_send(app.bot, target_chat, f"🔍 Scan finished: no candidates. Bank: {bank:.2f} UAH")
        return

    # Limit globally, then per match.
    per_match_count: Dict[str, int] = {}
    limited: List[Dict[str, Any]] = []
    for c in candidates:
        m = c["match"]
        if per_match_count.get(m, 0) >= MAX_OPTIONS_PER_MATCH:
            continue
        limited.append(c)
        per_match_count[m] = per_match_count.get(m, 0) + 1
        if len(limited) >= MAX_MATCHES_TO_SEND:
            break

    logger.info("Candidates total=%s sent=%s", len(candidates), len(limited))

    header = (
        f"📊 Scan v14 HOTFIX | {datetime.now(UTC).strftime('%d %b %H:%M')} UTC\n"
        f"Bank: {bank:.2f} UAH | Mode: TEST\n"
        f"Sports: {', '.join(sorted(ALLOWED_SPORTS))}\n"
        f"Sent: {len(limited)} / Found: {len(candidates)}\n\n"
        "⚠️ Это НЕ финальные ставки. Это кандидаты для анализа v29.1.\n"
    )
    await safe_send(app.bot, target_chat, header)

    copy_lines = []
    for i, c in enumerate(limited[:MAX_COPY_LINES], 1):
        copy_lines.append(
            f"{i}) {c['sport']} | {c['match']} | {c['market'].upper()} {c['selection']} @ {c['odds']} "
            f"({c['book']}) | roughEV {c['ev_est']:+.3f}"
        )
    if copy_lines:
        await safe_send(app.bot, target_chat, "📋 COPY TO CHATGPT/PERPLEXITY:\n" + "\n".join(copy_lines))

    for c in limited:
        sid = uuid.uuid4().hex[:10]
        _pending[sid] = c
        text = (
            f"🏟 {c['sport']}\n"
            f"{c['match']}\n"
            f"Start: {c.get('commence_time') or 'unknown'}\n\n"
            f"Market: {c['market'].upper()}\n"
            f"Selection: {c['selection']}\n"
            f"Odds: {c['odds']} | Book: {c['book']}\n"
            f"Rough EV: {c['ev_est']:+.3f} | Conf est: {c['conf_est']:.0%}\n\n"
            "STATUS: NEED_ANALYSIS\n"
            "Gate: scenario → ENV → line movement → injuries → EV_CI_Low → stake"
        )
        await safe_send(app.bot, target_chat, text, reply_markup=kb_candidate(sid))
        await asyncio.sleep(0.4)

    logger.info("SCAN DONE")


# ---------- Commands ----------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🤖 Betting Bot v14 HOTFIX\n"
        "Anti-spam mode ON. Startup scan disabled.\n\n"
        "Commands:\n"
        "/scan - manual scan top candidates\n"
        "/bank - current test bank\n"
        "/bets - open bets\n"
        "/stats - settled stats\n"
        "/settle <id> win|loss|push\n"
        "/help"
    )


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🔍 Manual scan started. I will send only limited candidates.")
    await scan(context.application, chat_id=update.effective_chat.id)


async def cmd_bank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    settled_profit = sum(float(r.get("profit", 0.0)) for r in state.get("results", []))
    await update.message.reply_text(
        f"💰 Bank: {float(state.get('bank', INITIAL_BANK)):.2f} UAH\n"
        f"Mode: {state.get('mode', 'TEST')}\n"
        f"P/L: {settled_profit:+.2f} UAH\n"
        f"Open: {sum(1 for b in state.get('open_bets', []) if b.get('status') == 'OPEN')}"
    )


async def cmd_bets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    open_bets = [b for b in state.get("open_bets", []) if b.get("status") == "OPEN"]
    if not open_bets:
        await update.message.reply_text("No open bets.")
        return
    lines = [f"Open bets: {len(open_bets)}"]
    for b in open_bets[:20]:
        lines.append(
            f"#{b['id']} | {b.get('match','')[:36]} | {b.get('market','').upper()} "
            f"{b.get('selection','')} @{b.get('odds')} | stake {b.get('stake')}"
        )
    await update.message.reply_text("\n".join(lines))


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    results = state.get("results", [])
    if not results:
        await update.message.reply_text("No settled bets yet.")
        return
    wins = sum(1 for r in results if r.get("result") == "WIN")
    losses = sum(1 for r in results if r.get("result") == "LOSS")
    pushes = sum(1 for r in results if r.get("result") == "PUSH")
    profit = sum(float(r.get("profit", 0.0)) for r in results)
    turnover = sum(float(r.get("stake", 0.0)) for r in results if r.get("result") != "PUSH")
    roi = (profit / turnover * 100) if turnover else 0.0
    await update.message.reply_text(
        f"📊 Stats\n"
        f"Settled: {len(results)} | W-L-P: {wins}-{losses}-{pushes}\n"
        f"ROI: {roi:+.1f}% | Profit: {profit:+.2f} UAH\n"
        f"Bank: {float(state.get('bank', INITIAL_BANK)):.2f} UAH"
    )


async def cmd_settle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if len(args) != 2 or not args[0].isdigit():
        await update.message.reply_text("Usage: /settle <id> win|loss|push")
        return
    ok, msg = settle_bet(int(args[0]), args[1])
    await update.message.reply_text(("✅ " if ok else "⚠️ ") + msg)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


# ---------- Callbacks ----------
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    data = (q.data or "").strip()
    try:
        await q.answer(cache_time=0)
    except Exception:
        pass

    if data == "noop":
        return

    if data.startswith("skip:"):
        sid = data.split(":", 1)[1]
        _pending.pop(sid, None)
        try:
            await q.message.edit_reply_markup(reply_markup=kb_done("Skipped"))
        except Exception:
            pass
        return

    if data.startswith("acc:"):
        sid = data.split(":", 1)[1]
        selection = _pending.pop(sid, None)
        if not selec
