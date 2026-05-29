"""
main_autonomous.py - BETTING BOT (ALL BUGS FIXED)
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

logger.info(f"CHAT_ID: {CHAT_ID}")

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
    
    def add_bet(self, match, market, odds, stake):
        bet = {
            "id": len(self.bets) + 1,
            "match": match,
            "market": market,
            "odds": odds,
            "stake": stake,
            "timestamp": datetime.now(UTC).isoformat(),
            "status": "OPEN"
        }
        self.bets.append(bet)
        self.save()
        return bet
    
    def get_stats(self):
        if not self.results:
            return {
                "total": 0,
                "wins": 0,
                "losses": 0,
                "profit": 0,
                "bank": self.bank,
                "wr": 0
            }
        w = sum(1 for r in self.results if r.get("result") == "WON")
        l = sum(1 for r in self.results if r.get("result") == "LOST")
        p = sum(r.get("profit", 0) for r in self.results)
        return {
            "total": len(self.results),
            "wins": w,
            "losses": l,
            "profit": p,
            "bank": self.bank,
            "wr": w/len(self.results)*100 if self.results else 0
        }
    
    async def tg(self, msg):
        if not self.app or not CHAT_ID:
            logger.warning(f"TG not configured - CHAT_ID: {CHAT_ID}")
            return
        
        try:
            await self.app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"TG error: {e}")

storage = Storage()

# ═══════════════════════════════════════════════════════════════════════════
# API FETCHING
# ═══════════════════════════════════════════════════════════════════════════

async def fetch_real_matches():
    """Fetch ALL sports from Odds API"""
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
                    odds_response = await client.get(odds_url, params=sport_params, timeout=10.0)
                    
                    if odds_response.status_code == 200:
                        events = odds_response.json()
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
                    elif odds_response.status_code == 422:
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
# SCANNING
# ═══════════════════════════════════════════════════════════════════════════

async def scan():
    global ALL_MATCHES
    
    logger.info("🔍 SCANNING...")
    
    if not ALL_MATCHES:
        logger.warning("No matches")
        return
    
    picks = []
    for sport_key, events in ALL_MATCHES.items():
        for event in events:
            home = event.get("home", "")
            away = event.get("away", "")
            if not home or not away:
                continue
            
            match_name = f"{home} vs {away}"
            bookmakers = event.get("bookmakers", [])
            
            if not bookmakers:
                continue
            
            for bk in bookmakers[:1]:
                for market in bk.get("markets", []):
                    mk_key = market.get("key", "")
                    
                    # Take ALL markets - no filtering
                    for outcome in market.get("outcomes", []):
                        name = outcome.get("name", "")
                        price = outcome.get("price", 0)
                        
                        if price < 1.3 or price > 5.0:
                            continue
                        
                        # Calculate implied probability for sorting
                        implied_prob = 1 / price if price > 0 else 0
                        
                        picks.append({
                            "match": match_name,
                            "sport": sport_key,
                            "league": sport_key,
                            "selection": f"{mk_key}: {name}",
                            "odds": price,
                            "probability": implied_prob,
                            "confidence": 0.65
                        })
    
    # Sort by probability distance and take top 30-40 best picks
    picks_sorted = sorted(picks, key=lambda x: abs(x["probability"] - 0.5), reverse=True)
    picks = picks_sorted[:40]
    logger.info(f"Analyzing {len(picks)} picks...")
    
    if len(picks) == 0:
        logger.warning("No picks generated")
        return
    
    try:
        logger.info("Calling Hermès...")
        enriched = await enrich_picks_with_hermes(picks, mode="NORMAL")
        logger.info(f"Hermès returned {len(enriched.get('enriched_picks', []))} picks")
        
        sent = 0
        
        for pick in enriched.get("enriched_picks", []):
            conf = pick.get("hermes_confidence", 0.65)
            recommendation = pick.get("hermes_recommendation", "UNKNOWN")
            
            # Emoji based on recommendation
            if recommendation == "ACCEPT":
                emoji = "✅"
            elif recommendation == "RECONSIDER":
                emoji = "⚠️"
            else:
                emoji = "❌"
            
            storage.add_rec(
                pick["match"],
                pick["selection"],
                pick["odds"],
                conf,
                recommendation
            )
            
            msg = f"{emoji} {pick['sport'].upper()}\n\n{pick['match']}\n{pick['selection']}\n{pick['odds']}\n{recommendation} ({conf:.0%})\n\n/place_bet \"{pick['match']}\" \"{pick['selection']}\" {pick['odds']} 50"
            
            await storage.tg(msg)
            await asyncio.sleep(0.1)
            sent += 1
        
        logger.info(f"✅ Sent {sent} recommendations")
    
    except Exception as e:
        logger.error(f"Scan error: {e}", exc_info=True)

# ═══════════════════════════════════════════════════════════════════════════
# COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("BETTING BOT\n\nAuto scan every hour\nMultiple sports\n\n/place_bet /bets /bank /stats /help")

async def place_bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if len(context.args) < 4:
            await update.message.reply_text("Usage: /place_bet \"Match\" \"Market\" odds stake")
            return
        
        match = context.args[0]
        market = context.args[1]
        odds = float(context.args[2])
        stake = float(context.args[3])
        
        if odds < 1.0 or stake <= 0:
            await update.message.reply_text("Invalid odds/stake")
            return
        
        if stake > storage.bank:
            await update.message.reply_text(f"Bank: {storage.bank:.2f}")
            return
        
        storage.add_bet(match, market, odds, stake)
        await update.message.reply_text(f"✅ Bet placed")
    
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def bets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    open_bets = [b for b in storage.bets if b.get("status") == "OPEN"]
    if not open_bets:
        await update.message.reply_text("No bets")
        return
    msg = "BETS\n\n"
    for b in open_bets:
        msg += f"ID: {b['id']}\n{b['match']}\n{b['odds']} @ {b['stake']}\n\n"
    await update.message.reply_text(msg)

async def bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = storage.get_stats()
    await update.message.reply_text(f"BANK: {s['bank']:.2f}\nProfit: {s['profit']:+.2f}\nBets: {s['total']}\nWins: {s['wins']}")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = storage.get_stats()
    await update.message.reply_text(f"Total: {s['total']}\nWins: {s['wins']}\nWR: {s['wr']:.1f}%\nProfit: {s['profit']:+.2f}")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("/start /place_bet /bets /bank /stats /help")

# ═══════════════════════════════════════════════════════════════════════════
# STARTUP
# ═══════════════════════════════════════════════════════════════════════════

async def post_init(app):
    global ALL_MATCHES
    
    storage.set_app(app)
    
    logger.info("Initializing bot...")
    ALL_MATCHES = await fetch_real_matches()
    
    try:
        await init_hermes()
        logger.info("✅ Hermes OK")
    except Exception as e:
        logger.error(f"Hermes error: {e}")
    
    if not scheduler.running:
        scheduler.start()
        scheduler.add_job(scan, 'cron', hour='*/1', minute=0)
        logger.info("✅ Scheduler OK")
        await scan()
    
    try:
        await storage.tg("✅ Bot started")
    except:
        pass

async def post_stop(app):
    if scheduler.running:
        scheduler.shutdown()

# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    logger.info("🚀 BOT STARTING")
    app = Application.builder().token(TOKEN).build()
    app.post_init = post_init
    app.post_stop = post_stop
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("place_bet", place_bet, filters.TEXT))
    app.add_handler(CommandHandler("bets", bets))
    app.add_handler(CommandHandler("bank", bank))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    
    logger.info("Polling...")
    app.run_polling()

if __name__ == "__main__":
    main()
