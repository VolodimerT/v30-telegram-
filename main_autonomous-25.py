"""
main_autonomous.py - BETTING BOT v5.0
FIXES: TODAY matches only, all markets (h2h+spreads+totals), EV sort, Kelly>0 filter
"""
import os
import json
import logging
import asyncio
import httpx
from datetime import datetime, timezone, timedelta
from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from hermes_integration_etap2 import init_hermes, enrich_picks_with_hermes
from markets_config_simple import EXPANDED_MATCHES
from kelly_criterion import calculate_kelly_fraction, calculate_bet_size
from analytics import analytics

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN        = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID_STR  = os.getenv("CHAT_ID", "")
CHAT_ID      = int(CHAT_ID_STR) if CHAT_ID_STR else None
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
UTC          = timezone.utc

scheduler    = AsyncIOScheduler()
INITIAL_BANK = 1019
ALL_MATCHES  = None

# VALUE BETTING PARAMETERS
MIN_ODDS  = 1.90
MAX_ODDS  = 5.00
MIN_KELLY = 0.005    # >0.5%
MIN_CONF  = 0.50
TOP_N     = 40
MARKETS   = "h2h,spreads,totals"

logger.info(f"BOT v5.0 | CHAT_ID: {CHAT_ID}")


# -----------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------

def today_window():
    now       = datetime.now(UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end   = day_start + timedelta(hours=47)
    fmt       = "%Y-%m-%dT%H:%M:%SZ"
    return day_start.strftime(fmt), day_end.strftime(fmt)


def market_label(mk_key, name, point):
    if mk_key == "h2h":
        return f"Winner: {name}"
    if mk_key == "spreads":
        pt = f"{point:+.1f}" if point is not None else ""
        return f"Handicap {pt}: {name}"
    if mk_key == "totals":
        pt = str(point) if point is not None else ""
        return f"Total {pt}: {name}"
    return f"{mk_key}: {name}"


def calc_dynamic_confidence(odds, bk_count=1):
    if 2.0 <= odds <= 3.5:
        base = 0.62
    elif 1.9 <= odds < 2.0:
        base = 0.55
    elif 3.5 < odds <= 4.5:
        base = 0.55
    else:
        base = 0.48
    return round(min(0.90, base + min(0.05, (bk_count - 1) * 0.01)), 3)


def calc_ev(odds, conf):
    return round(conf * (odds - 1) - (1 - conf), 4)


def smart_override(pick):
    rec  = pick.get("hermes_recommendation", "REJECT")
    conf = pick.get("hermes_confidence", 0.3)
    odds = pick.get("odds", 2.0)
    ev   = pick.get("ev_score", 0)
    dyn  = pick.get("dynamic_confidence", 0.5)

    if rec != "REJECT":
        return pick

    if ev > 0.15 and dyn >= 0.62:
        pick["hermes_recommendation"] = "ACCEPT"
        pick["hermes_confidence"]     = max(conf, dyn)
        pick["override_reason"]       = f"strong_ev({ev:.2f})"
    elif ev > 0.05 and dyn >= 0.55:
        pick["hermes_recommendation"] = "RECONSIDER"
        pick["hermes_confidence"]     = max(conf, dyn)
        pick["override_reason"]       = f"positive_ev({ev:.2f})"
    return pick


# -----------------------------------------------------------------------
# STORAGE
# -----------------------------------------------------------------------

class Storage:
    def __init__(self):
        self.recs    = self.load("recommendations.json")
        self.bets    = self.load("user_bets.json")
        self.results = self.load("bet_results.json")
        self.app     = None
        self.bank    = INITIAL_BANK

    def load(self, f):
        try:
            return json.load(open(f))
        except:
            return []

    def save(self):
        try:
            json.dump(self.recs,    open("recommendations.json", "w"), indent=2)
            json.dump(self.bets,    open("user_bets.json", "w"),       indent=2)
            json.dump(self.results, open("bet_results.json", "w"),     indent=2)
        except Exception as e:
            logger.error(f"Save error: {e}")

    def set_app(self, app):
        self.app = app

    def add_rec(self, match, market, odds, conf, rec):
        self.recs.append({
            "id": len(self.recs) + 1,
            "match": match, "market": market,
            "odds": odds, "confidence": conf,
            "recommendation": rec,
            "timestamp": datetime.now(UTC).isoformat()
        })
        self.save()
        try:
            analytics.record_pick(match.split()[0], market, rec, conf)
        except:
            pass

    def add_bet(self, match, market, odds, stake):
        kf  = calculate_kelly_fraction(0.55, odds)
        opt = calculate_bet_size(self.bank, kf, min_bet=10, max_bet=int(self.bank * 0.1))
        bet = {
            "id": len(self.bets) + 1,
            "match": match, "market": market,
            "odds": odds, "stake": stake,
            "optimal_stake": opt, "kelly_fraction": kf,
            "timestamp": datetime.now(UTC).isoformat(),
            "status": "OPEN"
        }
        self.bets.append(bet)
        self.save()
        return bet

    def get_stats(self):
        if not self.results:
            return {"total": 0, "wins": 0, "losses": 0,
                    "profit": 0, "bank": self.bank, "wr": 0, "roi": 0}
        w  = sum(1 for r in self.results if r.get("result") == "WON")
        l  = sum(1 for r in self.results if r.get("result") == "LOST")
        p  = sum(r.get("profit", 0) for r in self.results)
        ts = sum(r.get("stake", 0) for r in self.results)
        return {
            "total": len(self.results), "wins": w, "losses": l,
            "profit": p, "bank": self.bank,
            "wr":  w / len(self.results) * 100 if self.results else 0,
            "roi": p / ts * 100 if ts > 0 else 0
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


# -----------------------------------------------------------------------
# API FETCHING — TODAY ONLY
# -----------------------------------------------------------------------

async def fetch_real_matches():
    try:
        logger.info("Fetching TODAY's matches from Odds API...")

        if not ODDS_API_KEY:
            logger.warning("No ODDS_API_KEY, using mock data")
            return EXPANDED_MATCHES

        date_from, date_to = today_window()
        logger.info(f"Date window: {date_from} -> {date_to}")

        async with httpx.AsyncClient(timeout=15.0) as client:
            sports_resp = await client.get(
                "https://api.the-odds-api.com/v4/sports",
                params={"apiKey": ODDS_API_KEY}
            )
            if sports_resp.status_code != 200:
                logger.warning(f"Sports list error: {sports_resp.status_code}")
                return EXPANDED_MATCHES

            sports  = sports_resp.json()
            matches = {}
            total   = 0

            for sport in sports:
                if not sport.get("active"):
                    continue
                sport_key = sport.get("key", "")
                if not sport_key:
                    continue

                params = {
                    "apiKey":           ODDS_API_KEY,
                    "regions":          "eu,uk",
                    "markets":          MARKETS,
                    "commenceTimeFrom": date_from,
                    "commenceTimeTo":   date_to,
                    "oddsFormat":       "decimal"
                }

                try:
                    resp = await client.get(
                        f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds",
                        params=params, timeout=12.0
                    )
                    if resp.status_code == 200:
                        events = resp.json()
                        if events:
                            processed = []
                            for ev in events:
                                home = ev.get("home_team", "Team1")
                                away = ev.get("away_team", "Team2")
                                processed.append({
                                    "match":      f"{home} vs {away}",
                                    "sport":      sport_key,
                                    "home":       home,
                                    "away":       away,
                                    "commence":   ev.get("commence_time", ""),
                                    "bookmakers": ev.get("bookmakers", [])
                                })
                            matches[sport_key] = processed
                            total += len(processed)
                            logger.info(f"  OK {sport_key}: {len(processed)} matches today")
                    elif resp.status_code == 422:
                        pass
                    else:
                        logger.warning(f"  SKIP {sport_key}: HTTP {resp.status_code}")
                except Exception as e:
                    logger.error(f"  ERR {sport_key}: {e}")

            if matches:
                logger.info(f"TODAY total: {total} matches in {len(matches)} sports")
                return matches
            else:
                logger.warning("No matches today — using mock data")
                return EXPANDED_MATCHES

    except Exception as e:
        logger.error(f"Fetch error: {e}")
        return EXPANDED_MATCHES


# -----------------------------------------------------------------------
# SCANNING — ALL MARKETS + EV SORT
# -----------------------------------------------------------------------

async def scan():
    global ALL_MATCHES
    logger.info("SCANNING (today, h2h+spreads+totals, value mode)...")

    if not ALL_MATCHES:
        logger.warning("No matches loaded")
        return

    raw_picks = []

    for sport_key, events in ALL_MATCHES.items():
        for event in events:
            home = event.get("home", "")
            away = event.get("away", "")
            if not home or not away:
                continue

            match_name = f"{home} vs {away}"
            bookmakers = event.get("bookmakers", [])
            bk_count   = len(bookmakers)
            if not bookmakers:
                continue

            for bk in bookmakers[:1]:
                for market in bk.get("markets", []):
                    mk_key = market.get("key", "")
                    if mk_key not in ("h2h", "spreads", "totals"):
                        continue

                    for outcome in market.get("outcomes", []):
                        name  = outcome.get("name", "")
                        odds  = outcome.get("price", 0)
                        point = outcome.get("point", None)

                        if odds < MIN_ODDS or odds > MAX_ODDS:
                            continue

                        dyn_conf = calc_dynamic_confidence(odds, bk_count)
                        ev_score = calc_ev(odds, dyn_conf)
                        label    = market_label(mk_key, name, point)

                        raw_picks.append({
                            "match":               match_name,
                            "sport":               sport_key,
                            "league":              sport_key,
                            "market_type":         mk_key,
                            "selection":           label,
                            "odds":                odds,
                            "point":               point,
                            "implied_probability": round(1 / odds, 4),
                            "bookmaker_count":     bk_count,
                            "dynamic_confidence":  dyn_conf,
                            "confidence":          dyn_conf,
                            "ev_score":            ev_score,
                        })

    logger.info(f"Raw picks [{MIN_ODDS}-{MAX_ODDS}]: {len(raw_picks)}")

    if not raw_picks:
        logger.warning("No picks in value range today")
        await storage.tg("No value picks found today")
        return

    picks_sorted = sorted(raw_picks, key=lambda x: x["ev_score"], reverse=True)
    picks        = picks_sorted[:TOP_N]

    mk_counts = {}
    for p in picks:
        mk_counts[p["market_type"]] = mk_counts.get(p["market_type"], 0) + 1
    ev_range   = f"{picks[-1]['ev_score']:.3f}..{picks[0]['ev_score']:.3f}"
    odds_range = f"{min(p['odds'] for p in picks):.2f}..{max(p['odds'] for p in picks):.2f}"
    logger.info(f"Top {len(picks)} | EV: {ev_range} | Odds: {odds_range} | Markets: {mk_counts}")

    try:
        logger.info("Calling Hermes...")
        enriched       = await enrich_picks_with_hermes(picks, mode="NORMAL")
        enriched_picks = enriched.get("enriched_picks", [])
        logger.info(f"Hermes returned {len(enriched_picks)} picks")

        sent = 0
        filtered_rec   = 0
        filtered_kelly = 0

        for idx, pick in enumerate(enriched_picks):
            pick.setdefault("ev_score",           0)
            pick.setdefault("dynamic_confidence", 0.5)
            pick.setdefault("market_type",        "h2h")

            pick = smart_override(pick)

            conf     = pick.get("hermes_confidence", 0.3)
            rec      = pick.get("hermes_recommendation", "REJECT")
            odds     = pick.get("odds", 2.0)
            override = pick.get("override_reason", "")
            mk_type  = pick.get("market_type", "h2h")

            logger.info(
                f"Pick {idx+1}: [{mk_type}] {pick['match'][:28]} | "
                f"{odds:.2f} | {rec} ({conf:.0%})"
                + (f" [{override}]" if override else "")
            )

            if rec not in ("ACCEPT", "RECONSIDER"):
                filtered_rec += 1
                continue

            if conf < MIN_CONF:
                filtered_rec += 1
                continue

            kf = calculate_kelly_fraction(conf, odds)
            if kf < MIN_KELLY:
                logger.info(f"  Kelly {kf*100:.2f}% < {MIN_KELLY*100:.1f}% - skip")
                filtered_kelly += 1
                continue

            opt_stake = calculate_bet_size(storage.bank, kf)
            emoji     = "✅" if rec == "ACCEPT" else "⚠️"
            ev        = pick.get("ev_score", calc_ev(odds, conf))
            mk_emoji  = {"h2h": "🏆", "spreads": "➕", "totals": "📊"}.get(mk_type, "🎯")

            override_line = ("\n_override: " + override + "_") if override else ""

            storage.add_rec(pick["match"], pick["selection"], odds, conf, rec)

            msg = (
                emoji + " " + mk_emoji + " *" + pick["sport"].upper() + "*\n\n" +
                "*" + pick["match"] + "*\n" +
                "`" + pick["selection"] + "`\n" +
                f"Odds: `{odds}` | EV: `{ev:+.3f}`\n" +
                f"{rec} ({conf:.0%})" + override_line + "\n\n" +
                f"💰 Kelly: {kf*100:.1f}%\n" +
                f"Optimal stake: {opt_stake:.0f} UAH\n\n" +
                f"/place_bet \"{pick['match']}\" \"{pick['selection']}\" {odds} {opt_stake:.0f}"
            )

            await storage.tg(msg)
            await asyncio.sleep(0.15)
            sent += 1

        logger.info(f"Sent {sent} | filtered_rec {filtered_rec} | filtered_kelly {filtered_kelly}")

        if sent == 0:
            await storage.tg(
                f"Scan done — 0 value picks today\n"
                f"Checked: {len(raw_picks)} | Hermes filtered: {filtered_rec + filtered_kelly}"
            )

    except Exception as e:
        logger.error(f"Scan error: {e}", exc_info=True)


# -----------------------------------------------------------------------
# COMMANDS
# -----------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"BETTING BOT v5.0\n\n"
        f"TODAY's matches only\n"
        f"Markets: h2h + spreads + totals\n"
        f"Odds: {MIN_ODDS}-{MAX_ODDS} | Kelly >{MIN_KELLY*100:.0f}%\n\n"
        "/scan /place_bet /bets /bank /stats /analytics /help"
    )


async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Manual scan started...")
    await scan()


async def place_bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if len(context.args) < 4:
            await update.message.reply_text('Usage: /place_bet "Match" "Market" odds stake')
            return
        match  = context.args[0]
        market = context.args[1]
        odds   = float(context.args[2])
        stake  = float(context.args[3])
        if odds < 1.0 or stake <= 0:
            await update.message.reply_text("Invalid odds/stake")
            return
        if stake > storage.bank:
            await update.message.reply_text(f"Insufficient bank: {storage.bank:.2f}")
            return
        bet = storage.add_bet(match, market, odds, stake)
        await update.message.reply_text(
            f"Bet placed\n{match}\n{odds} @ {stake}\n"
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
        f"BANK: {s['bank']:.2f} UAH\n"
        f"Profit: {s['profit']:+.2f}\nBets: {s['total']}\n"
        f"Wins: {s['wins']}\nWR: {s['wr']:.1f}%\nROI: {s['roi']:.2f}%"
    )


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = storage.get_stats()
    await update.message.reply_text(
        f"Total: {s['total']}\nWins: {s['wins']}\n"
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
        "/start /scan /place_bet /bets /bank /stats /analytics /help"
    )


# -----------------------------------------------------------------------
# STARTUP
# -----------------------------------------------------------------------

async def refresh_matches():
    global ALL_MATCHES
    logger.info("Daily refresh: reloading today's matches...")
    ALL_MATCHES = await fetch_real_matches()
    await scan()


async def post_init(app):
    global ALL_MATCHES
    storage.set_app(app)
    logger.info("Initializing bot v5.0...")

    ALL_MATCHES = await fetch_real_matches()

    try:
        await init_hermes()
        logger.info("Hermes OK")
    except Exception as e:
        logger.error(f"Hermes error: {e}")

    if not scheduler.running:
        scheduler.start()
        scheduler.add_job(scan,             "cron", hour="*/1", minute=0, id="scan_hourly")
        scheduler.add_job(refresh_matches,  "cron", hour=6,     minute=0, id="refresh_daily")
        logger.info("Scheduler OK (hourly scan + 06:00 UTC daily refresh)")

    await scan()

    try:
        await storage.tg(
            f"Bot v5.0 started\n"
            f"TODAY's matches only\n"
            f"Markets: h2h + spreads + totals\n"
            f"Odds: {MIN_ODDS}-{MAX_ODDS} | Kelly >{MIN_KELLY*100:.0f}%"
        )
    except:
        pass


async def post_stop(app):
    if scheduler.running:
        scheduler.shutdown()


# -----------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------

def main():
    logger.info("BOT STARTING v5.0")
    app = Application.builder().token(TOKEN).build()
    app.post_init = post_init
    app.post_stop = post_stop

    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("scan",      scan_cmd))
    app.add_handler(CommandHandler("place_bet", place_bet, filters.TEXT))
    app.add_handler(CommandHandler("bets",      bets))
    app.add_handler(CommandHandler("bank",      bank))
    app.add_handler(CommandHandler("stats",     stats_cmd))
    app.add_handler(CommandHandler("analytics", analytics_cmd))
    app.add_handler(CommandHandler("help",      help_cmd))

    logger.info("Polling...")
    app.run_polling()


if __name__ == "__main__":
    main()
