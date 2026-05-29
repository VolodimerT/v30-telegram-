"""
main_autonomous.py - BETTING BOT v4.0
KEY FIX: Value-based sorting, min odds 1.9, Kelly > 0 filter
"""
import os
import json
import logging
import asyncio
import httpx
from datetime import datetime, timezone
from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from hermes_integration_etap2 import init_hermes, enrich_picks_with_hermes
from markets_config_simple import EXPANDED_MATCHES
from kelly_criterion import calculate_kelly_fraction, calculate_bet_size, calculate_expected_value
from market_filters import get_allowed_markets, is_market_allowed
from analytics import analytics

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID_STR = os.getenv("CHAT_ID", "")
CHAT_ID = int(CHAT_ID_STR) if CHAT_ID_STR else None
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
UTC = timezone.utc

scheduler = AsyncIOScheduler()
INITIAL_BANK = 1019
ALL_MATCHES = None

# ── VALUE BETTING PARAMETERS ─────────────────────────────────────────────
MIN_ODDS = 1.90       # Kelly< 0 для всего ниже ~1.8
MAX_ODDS = 5.00
MIN_KELLY = 0.005     # минимум 0.5%
MIN_CONF  = 0.50      # минимум 50% уверенности
TOP_N     = 40        # сколько picks брать для Hermes
# ─────────────────────────────────────────────────────────────────────────

logger.info(f"BOT v4.0 - CHAT_ID: {CHAT_ID}")
logger.info(f"TOKEN: {TOKEN[:20]}..." if TOKEN else "TOKEN: NOT SET")


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def calc_dynamic_confidence(odds: float, bookmaker_count: int = 1) -> float:
    """
    Confidence based on VALUE ZONE, not favourite zone.
    Sweet spot: odds 2.0-3.5 → higher confidence.
    Extremes (very low or very high) → lower confidence.
    """
    # Optimal value zone 2.0–3.5
    if 2.0 <= odds <= 3.5:
        base = 0.62
    elif 1.9 <= odds < 2.0:
        base = 0.55
    elif 3.5 < odds <= 4.5:
        base = 0.55
    elif 4.5 < odds <= 5.0:
        base = 0.48
    else:
        base = 0.40  # outside range (filtered anyway)

    bk_bonus = min(0.05, (bookmaker_count - 1) * 0.01)
    return round(min(0.90, base + bk_bonus), 3)


def calc_value_score(odds: float, confidence: float) -> float:
    """
    EV-based value score for sorting.
    EV = confidence * (odds - 1) - (1 - confidence)
    Positive EV = worth betting.
    """
    return confidence * (odds - 1) - (1 - confidence)


def smart_override(pick: dict) -> dict:
    """
    Correct Hermes REJECT for VALUE ZONE picks (not just favourites).
    v4: focused on odds 2.0–4.0 with positive EV.
    """
    rec = pick.get("hermes_recommendation", "REJECT")
    conf = pick.get("hermes_confidence", 0.3)
    odds = pick.get("odds", 2.0)
    ev = pick.get("ev_score", 0)
    dyn_conf = pick.get("dynamic_confidence", 0.5)

    if rec != "REJECT":
        return pick

    # Positive EV + decent confidence → RECONSIDER
    if ev > 0.05 and dyn_conf >= 0.55:
        pick["hermes_recommendation"] = "RECONSIDER"
        pick["hermes_confidence"] = max(conf, dyn_conf)
        pick["override_reason"] = f"positive_ev({ev:.2f})"

    # Strong EV + good confidence → ACCEPT
    elif ev > 0.15 and dyn_conf >= 0.62:
        pick["hermes_recommendation"] = "ACCEPT"
        pick["hermes_confidence"] = max(conf, dyn_conf)
        pick["override_reason"] = f"strong_ev({ev:.2f})"

    return pick


# ═══════════════════════════════════════════════════════════════════════════
# STORAGE
# ═══════════════════════════════════════════════════════════════════════════

class Storage:
    def __init__(self):
        self.recs = self.load("recommendations.json")
        self.bets = self.load("user_bets.json")
        self.results = self.load("bet_results.json")
        self.app = None
        self.bank = INITIAL_BANK

    def load(self, f):
        try:
            return json.load(open(f))
        except:
            return []

    def save(self):
        try:
            json.dump(self.recs, open("recommendations.json", "w"), indent=2)
            json.dump(self.bets, open("user_bets.json", "w"), indent=2)
            json.dump(self.results, open("bet_results.json", "w"), indent=2)
        except Exception as e:
            logger.error(f"Save error: {e}")

    def set_app(self, app):
        self.app = app

    def add_rec(self, match, market, odds, conf, rec):
        self.recs.append({
            "id": len(self.recs) + 1,
            "match": match,
            "market": market,
            "odds": odds,
            "confidence": conf,
            "recommendation": rec,
            "timestamp": datetime.now(UTC).isoformat()
        })
        self.save()
        try:
            analytics.record_pick(match.split()[0], market, rec, conf)
        except:
            pass

    def add_bet(self, match, market, odds, stake):
        win_prob = 0.55
        kelly_fraction = calculate_kelly_fraction(win_prob, odds)
        optimal_stake = calculate_bet_size(
            self.bank, kelly_fraction, min_bet=10, max_bet=int(self.bank * 0.1)
        )
        bet = {
            "id": len(self.bets) + 1,
            "match": match,
            "market": market,
            "odds": odds,
            "stake": stake,
            "optimal_stake": optimal_stake,
            "kelly_fraction": kelly_fraction,
            "timestamp": datetime.now(UTC).isoformat(),
            "status": "OPEN"
        }
        self.bets.append(bet)
        self.save()
        return bet

    def get_stats(self):
        if not self.results:
            return {
                "total": 0, "wins": 0, "losses": 0,
                "profit": 0, "bank": self.bank, "wr": 0, "roi": 0
            }
        w = sum(1 for r in self.results if r.get("result") == "WON")
        l = sum(1 for r in self.results if r.get("result") == "LOST")
        p = sum(r.get("profit", 0) for r in self.results)
        total_stake = sum(r.get("stake", 0) for r in self.results)
        roi = (p / total_stake * 100) if total_stake > 0 else 0
        return {
            "total": len(self.results),
            "wins": w, "losses": l, "profit": p,
            "bank": self.bank,
            "wr": w / len(self.results) * 100 if self.results else 0,
            "roi": roi
        }

    async def tg(self, msg):
        if not self.app or not CHAT_ID:
            logger.warning(f"TG not configured - CHAT_ID: {CHAT_ID}")
            return
        try:
            await self.app.bot.send_message(
                chat_id=CHAT_ID, text=msg, parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"TG error: {e}")


storage = Storage()


# ═══════════════════════════════════════════════════════════════════════════
# API FETCHING
# ═══════════════════════════════════════════════════════════════════════════

async def fetch_real_matches():
    try:
        logger.info("Fetching from Odds API...")

        if not ODDS_API_KEY:
            logger.warning("No ODDS_API_KEY, using mock data")
            return EXPANDED_MATCHES

        async with httpx.AsyncClient(timeout=10.0) as client:
            sports_url = "https://api.the-odds-api.com/v4/sports"
            params = {"apiKey": ODDS_API_KEY}
            response = await client.get(sports_url, params=params)

            if response.status_code != 200:
                logger.warning(f"Sports API error: {response.status_code}")
                return EXPANDED_MATCHES

            sports = response.json()
            matches = {}

            for sport in sports:
                if not sport.get("active"):
                    continue
                sport_key = sport.get("key")
                if not sport_key:
                    continue

                logger.info(f"Loading {sport_key}...")
                odds_url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"
                sport_params = {
                    "apiKey": ODDS_API_KEY,
                    "regions": "eu,uk",
                    "markets": "h2h,spreads,totals"
                }

                try:
                    resp = await client.get(odds_url, params=sport_params, timeout=10.0)
                    if resp.status_code == 200:
                        events = resp.json()
                        if events:
                            processed = []
                            for event in events:
                                home = event.get("home_team", "Team1")
                                away = event.get("away_team", "Team2")
                                processed.append({
                                    "match": f"{home} vs {away}",
                                    "sport": sport_key,
                                    "home": home,
                                    "away": away,
                                    "bookmakers": event.get("bookmakers", [])
                                })
                            matches[sport_key] = processed
                            logger.info(f"✅ {sport_key}: {len(processed)} matches")
                    elif resp.status_code == 422:
                        logger.info(f"⚠️ {sport_key}: Not available")
                except Exception as e:
                    logger.error(f"Error loading {sport_key}: {e}")

            if matches:
                total = sum(len(m) for m in matches.values())
                logger.info(f"✅ Loaded {total} matches from {len(matches)} sports")
                return matches
            else:
                logger.warning("No matches loaded, using mock data")
                return EXPANDED_MATCHES

    except Exception as e:
        logger.error(f"Fetch error: {e}")
        return EXPANDED_MATCHES


# ═══════════════════════════════════════════════════════════════════════════
# SCANNING — VALUE-BASED
# ═══════════════════════════════════════════════════════════════════════════

async def scan():
    global ALL_MATCHES

    logger.info("SCANNING (value mode)...")

    if not ALL_MATCHES:
        logger.warning("No matches")
        return

    raw_picks = []

    for sport_key, events in ALL_MATCHES.items():
        allowed_markets = get_allowed_markets(sport_key)

        for event in events:
            home = event.get("home", "")
            away = event.get("away", "")
            if not home or not away:
                continue

            match_name = f"{home} vs {away}"
            bookmakers = event.get("bookmakers", [])
            bk_count = len(bookmakers)

            if not bookmakers:
                continue

            for bk in bookmakers[:1]:
                for market in bk.get("markets", []):
                    mk_key = market.get("key", "")

                    if not is_market_allowed(sport_key, mk_key):
                        continue

                    for outcome in market.get("outcomes", []):
                        name = outcome.get("name", "")
                        odds = outcome.get("price", 0)

                        # ── HARD FILTER: odds range ──────────────────────
                        if odds < MIN_ODDS or odds > MAX_ODDS:
                            continue

                        implied_prob = round(1 / odds, 4)
                        dyn_conf = calc_dynamic_confidence(odds, bk_count)
                        ev_score = round(calc_value_score(odds, dyn_conf), 4)

                        raw_picks.append({
                            "match": match_name,
                            "sport": sport_key,
                            "league": sport_key,
                            "selection": f"{mk_key}: {name}",
                            "odds": odds,
                            "implied_probability": implied_prob,
                            "bookmaker_count": bk_count,
                            "dynamic_confidence": dyn_conf,
                            "confidence": dyn_conf,
                            "ev_score": ev_score,
                        })

    logger.info(f"Raw picks after odds filter [{MIN_ODDS}-{MAX_ODDS}]: {len(raw_picks)}")

    if not raw_picks:
        logger.warning("No picks in value range — check MIN_ODDS or market filters")
        return

    # ── SORT BY EV DESC, take TOP_N ─────────────────────────────────────
    picks_sorted = sorted(raw_picks, key=lambda x: x["ev_score"], reverse=True)
    picks = picks_sorted[:TOP_N]

    ev_range = f"{picks[-1]['ev_score']:.3f}..{picks[0]['ev_score']:.3f}"
    odds_range = f"{min(p['odds'] for p in picks):.2f}..{max(p['odds'] for p in picks):.2f}"
    logger.info(f"Top {len(picks)} picks | EV: {ev_range} | Odds: {odds_range}")

    try:
        logger.info("Calling Hermes...")
        enriched = await enrich_picks_with_hermes(picks, mode="NORMAL")
        enriched_picks = enriched.get("enriched_picks", [])
        logger.info(f"Hermes returned {len(enriched_picks)} picks")

        sent = 0
        filtered_rec = 0
        filtered_kelly = 0

        for idx, pick in enumerate(enriched_picks):
            # Re-attach fields Hermes may drop
            pick.setdefault("ev_score", 0)
            pick.setdefault("dynamic_confidence", 0.5)

            # Smart override (EV-based)
            pick = smart_override(pick)

            conf = pick.get("hermes_confidence", 0.3)
            rec = pick.get("hermes_recommendation", "REJECT")
            odds = pick.get("odds", 2.0)
            override = pick.get("override_reason", "")

            logger.info(
                f"Pick {idx+1}: {pick['match'][:30]} | {odds:.2f} | "
                f"{rec} ({conf:.0%})" + (f" [{override}]" if override else "")
            )

            # ── FILTER 1: only ACCEPT / RECONSIDER ──────────────────────
            if rec not in ("ACCEPT", "RECONSIDER"):
                filtered_rec += 1
                continue

            # ── FILTER 2: confidence threshold ──────────────────────────
            if conf < MIN_CONF:
                logger.info(f"  ❌ conf {conf:.0%} < {MIN_CONF:.0%}")
                filtered_rec += 1
                continue

            # ── FILTER 3: Kelly must be positive ────────────────────────
            kelly_fraction = calculate_kelly_fraction(conf, odds)
            if kelly_fraction < MIN_KELLY:
                logger.info(f"  ❌ Kelly {kelly_fraction*100:.2f}% < {MIN_KELLY*100:.1f}%")
                filtered_kelly += 1
                continue

            optimal_stake = calculate_bet_size(storage.bank, kelly_fraction)

            emoji = "✅" if rec == "ACCEPT" else "⚠️"
            ev = pick.get("ev_score", calc_value_score(odds, conf))
            override_note = f"\n_override: {override}_" if override else ""

            storage.add_rec(pick["match"], pick["selection"], odds, conf, rec)

            msg = (
                f"{emoji} *{pick['sport'].upper()}*\n\n"
                f"*{pick['match']}*\n"
                f"{pick['selection']}\n"
                f"Odds: `{odds}` | EV: `{ev:+.3f}`\n"
                f"{rec} ({conf:.0%}){override_note}\n\n"
                f"💰 Kelly: {kelly_fraction*100:.1f}%\n"
                f"Optimal stake: {optimal_stake:.0f} UAH\n\n"
                f"/place\_bet \"{pick['match']}\" \"{pick['selection']}\" {odds} {optimal_stake:.0f}"
            )

            await storage.tg(msg)
            await asyncio.sleep(0.15)
            sent += 1

        logger.info(
            f"✅ Sent {sent} | "
            f"❌ filtered_rec {filtered_rec} | "
            f"❌ filtered_kelly {filtered_kelly}"
        )

    except Exception as e:
        logger.error(f"Scan error: {e}", exc_info=True)


# ═══════════════════════════════════════════════════════════════════════════
# COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "BETTING BOT v4.0\n\n"
        "✅ Value betting (EV-sort)\n"
        f"✅ Odds range: {MIN_ODDS}–{MAX_ODDS}\n"
        f"✅ Kelly filter: >{MIN_KELLY*100:.0f}%\n"
        "✅ Smart Hermes override\n\n"
        "/place_bet /bets /bank /stats /analytics /help"
    )


async def place_bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if len(context.args) < 4:
            await update.message.reply_text(
                'Usage: /place_bet "Match" "Market" odds stake'
            )
            return

        match = context.args[0]
        market = context.args[1]
        odds = float(context.args[2])
        stake = float(context.args[3])

        if odds < 1.0 or stake <= 0:
            await update.message.reply_text("Invalid odds/stake")
            return
        if stake > storage.bank:
            await update.message.reply_text(f"Insufficient bank: {storage.bank:.2f}")
            return

        bet = storage.add_bet(match, market, odds, stake)
        await update.message.reply_text(
            f"✅ Bet placed\n{match}\n{odds} @ {stake}\n"
            f"Kelly: {bet['kelly_fraction']*100:.1f}%\n"
            f"Optimal: {bet['optimal_stake']:.0f} UAH"
        )
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def bets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    open_bets = [b for b in storage.bets if b.get("status") == "OPEN"]
    if not open_bets:
        await update.message.reply_text("No open bets")
        return
    msg = "OPEN BETS\n\n"
    for b in open_bets:
        msg += (
            f"ID: {b['id']}\n{b['match']}\n"
            f"{b['odds']} @ {b['stake']}\n"
            f"Kelly: {b['kelly_fraction']*100:.1f}%\n\n"
        )
    await update.message.reply_text(msg)


async def bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = storage.get_stats()
    await update.message.reply_text(
        f"💰 BANK: {s['bank']:.2f} UAH\n"
        f"Profit: {s['profit']:+.2f}\nBets: {s['total']}\n"
        f"Wins: {s['wins']}\nWR: {s['wr']:.1f}%\nROI: {s['roi']:.2f}%"
    )


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = storage.get_stats()
    await update.message.reply_text(
        f"📊 Total: {s['total']}\nWins: {s['wins']}\n"
        f"Losses: {s['losses']}\nWR: {s['wr']:.1f}%\n"
        f"Profit: {s['profit']:+.2f}\nROI: {s['roi']:.2f}%"
    )


async def analytics_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        report = analytics.get_report()
    except:
        report = "Analytics not available"
    await update.message.reply_text(report)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start /place_bet /bets /bank /stats /analytics /help"
    )


# ═══════════════════════════════════════════════════════════════════════════
# STARTUP
# ═══════════════════════════════════════════════════════════════════════════

async def post_init(app):
    global ALL_MATCHES

    storage.set_app(app)

    logger.info("Initializing bot v4.0...")
    ALL_MATCHES = await fetch_real_matches()

    try:
        await init_hermes()
        logger.info("✅ Hermes OK")
    except Exception as e:
        logger.error(f"Hermes error: {e}")

    if not scheduler.running:
        scheduler.start()
        scheduler.add_job(scan, "cron", hour="*/1", minute=0)
        logger.info("✅ Scheduler OK")

    await scan()

    try:
        await storage.tg(
            f"✅ Bot v4.0 started\n"
            f"Value mode: odds {MIN_ODDS}–{MAX_ODDS}\n"
            f"Kelly filter: >{MIN_KELLY*100:.0f}%"
        )
    except:
        pass


async def post_stop(app):
    if scheduler.running:
        scheduler.shutdown()


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    logger.info("BOT STARTING v4.0")
    app = Application.builder().token(TOKEN).build()
    app.post_init = post_init
    app.post_stop = post_stop

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("place_bet", place_bet, filters.TEXT))
    app.add_handler(CommandHandler("bets", bets))
    app.add_handler(CommandHandler("bank", bank))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("analytics", analytics_cmd))
    app.add_handler(CommandHandler("help", help_cmd))

    logger.info("Polling...")
    app.run_polling()


if __name__ == "__main__":
    main()
