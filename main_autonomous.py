"""
main_autonomous.py - BETTING BOT WITH REAL ODDS API
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
from markets_config_simple import EXPANDED_MATCHES, get_primary_markets, MARKETS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "bafb678b8ac2d2ee7cd88fdc9318d308")
UTC = timezone.utc

scheduler = AsyncIOScheduler()
INITIAL_BANK = 1019
ALL_MATCHES = None

class Storage:
    def __init__(self):
        self.recs = self.load("recommendations.json")
        self.bets = self.load("user_bets.json")
        self.results = self.load("bet_results.json")
        self.app = None
        self.bank = INITIAL_BANK
    
    def load(self, f):
        return json.load(open(f)) if os.path.exists(f) else []
    
    def save(self):
        for data, name in [(self.recs, "recommendations.json"), (self.bets, "user_bets.json"), (self.results, "bet_results.json")]:
            json.dump(data, open(name, "w"), indent=2)
    
    def set_app(self, app):
        self.app = app
    
    def add_rec(self, match, market, odds, conf, rec):
        self.recs.append({"id": len(self.recs) + 1, "match": match, "market": market, "odds": odds, "confidence": conf, "recommendation": rec, "timestamp": datetime.now(UTC).isoformat()})
        self.save()
    
    def add_bet(self, match, market, odds, stake):
        bet = {"id": len(self.bets) + 1, "match": match, "market": market, "odds": odds, "stake": stake, "timestamp": datetime.now(UTC).isoformat(), "status": "OPEN"}
        self.bets.append(bet)
        self.save()
        return bet
    
    def get_stats(self):
        if not self.results:
            return {"total": 0, "wins": 0, "losses": 0, "profit": 0, "bank": self.bank, "wr": 0}
        w = sum(1 for r in self.results if r.get("result") == "WON")
        l = sum(1 for r in self.results if r.get("result") == "LOST")
        p = sum(r.get("profit", 0) for r in self.results)
        return {"total": len(self.results), "wins": w, "losses": l, "profit": p, "bank": self.bank, "wr": w/len(self.results)*100 if self.results else 0}
    
    async def tg(self, msg):
        if self.app and CHAT_ID:
            try:
                await self.app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"TG error: {e}")

storage = Storage()

async def fetch_real_matches():
    """Fetch real matches from Odds API"""
    try:
        logger.info("Fetching from Odds API...")
        async with httpx.AsyncClient(timeout=10.0) as client:
            sports_url = "https://api.the-odds.com/v4/sports"
            params = {"apiKey": ODDS_API_KEY}
            
            response = await client.get(sports_url, params=params)
            
            if response.status_code != 200:
                logger.warning(f"Odds API error: {response.status_code}, using mock data")
                return EXPANDED_MATCHES
            
            sports = response.json()
            matches = {}
            
            for sport in sports[:5]:
                if not sport.get("active"):
                    continue
                
                sport_key = sport.get("key")
                if not sport_key:
                    continue
                
                logger.info(f"Fetching {sport_key}...")
                
                odds_url = "https://api.the-odds.com/v4/events"
                sport_params = {
                    "apiKey": ODDS_API_KEY,
                    "sport": sport_key,
                    "regions": "us",
                    "markets": "h2h,spreads,totals",
                }
                
                odds_response = await client.get(odds_url, params=sport_params, timeout=10.0)
                
                if odds_response.status_code == 200:
                    events = odds_response.json()
                    if events and len(events) > 0:
                        matches[sport_key] = events
                        logger.info(f"Got {len(events)} matches for {sport_key}")
            
            if matches:
                logger.info("Using REAL data from Odds API")
                return matches
            else:
                logger.warning("No real data from API, using mock data")
                return EXPANDED_MATCHES
    
    except Exception as e:
        logger.error(f"API error: {e}, using mock data")
        return EXPANDED_MATCHES

async def scan():
    global ALL_MATCHES
    
    logger.info("SCANNING...")
    
    if not ALL_MATCHES:
        logger.warning("No matches data, skipping scan")
        return
    
    picks = []
    for league, matches in ALL_MATCHES.items():
        for match in matches:
            sport = match.get("sport", "")
            markets = get_primary_markets(sport)
            for mkt in markets:
                key = mkt.lower().replace(" ", "_").replace("/", "")
                odds = match.get("markets", {}).get(key, 1.90)
                picks.append({"match": match["match"], "sport": sport, "league": league, "selection": mkt, "odds": odds, "confidence": 0.65})
    
    logger.info(f"Analyzing {len(picks)} picks...")
    try:
        enriched = await enrich_picks_with_hermes(picks, mode="NORMAL")
        sent = 0
        for pick in enriched.get("enriched_picks", []):
            if pick.get("hermes_recommendation") in ["ACCEPT", "RECONSIDER"]:
                conf = pick.get("hermes_confidence", 0.65)
                emoji = "+" if conf > 0.7 else "-"
                storage.add_rec(pick["match"], pick["selection"], pick["odds"], conf, pick.get("hermes_recommendation"))
                msg = f"{emoji} {pick['sport'].upper()}\n\n{pick['match']}\n{pick['selection']}\n{pick['odds']}\n{pick.get('hermes_recommendation')} ({conf:.0%})\n\n/place_bet \"{pick['match']}\" \"{pick['selection']}\" {pick['odds']} 50"
                await storage.tg(msg)
                sent += 1
        logger.info(f"Sent {sent} recommendations")
    except Exception as e:
        logger.error(f"Scan error: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("BETTING BOT\n\nAuto scan every hour\n10 markets\nHybrid mode\n\n/place_bet /bets /bank /stats /help")

async def place_bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if len(context.args) < 4:
            await update.message.reply_text("ERROR: Need 4 params\n\nUsage: /place_bet \"Match\" \"Market\" odds stake\n\nExample:\n/place_bet \"Chelsea vs Liverpool\" \"Match Winner\" 2.10 50")
            logger.error(f"place_bet: Not enough args. Got {len(context.args)}, need 4")
            return
        
        match = context.args[0]
        market = context.args[1]
        
        try:
            odds = float(context.args[2])
        except ValueError:
            await update.message.reply_text(f"ERROR: Odds '{context.args[2]}' is not a number\n\nExample: /place_bet \"Match\" \"Market\" 2.10 50")
            logger.error(f"place_bet: Invalid odds '{context.args[2]}'")
            return
        
        try:
            stake = float(context.args[3])
        except ValueError:
            await update.message.reply_text(f"ERROR: Stake '{context.args[3]}' is not a number\n\nExample: /place_bet \"Match\" \"Market\" 2.10 50")
            logger.error(f"place_bet: Invalid stake '{context.args[3]}'")
            return
        
        logger.info(f"place_bet: {match} | {market} | {odds} | {stake}")
        
        if odds < 1.0:
            await update.message.reply_text(f"ERROR: Odds must be >= 1.0\n\nYou provided: {odds}")
            logger.error(f"place_bet: Invalid odds {odds} (< 1.0)")
            return
        
        if stake <= 0:
            await update.message.reply_text(f"ERROR: Stake must be > 0\n\nYou provided: {stake}")
            logger.error(f"place_bet: Invalid stake {stake} (<= 0)")
            return
        
        if stake > storage.bank:
            await update.message.reply_text(f"ERROR: Insufficient bank\n\nYour bank: {storage.bank:.2f} UAH\nStake needed: {stake:.2f} UAH\nShortfall: {stake - storage.bank:.2f} UAH")
            logger.error(f"place_bet: Bank {storage.bank} < stake {stake}")
            return
        
        bet = storage.add_bet(match, market, odds, stake)
        potential = stake * (odds - 1)
        msg = f"BET PLACED\n\nID: {bet['id']}\nMatch: {match}\nMarket: {market}\nOdds: {odds}\nStake: {stake:.2f} UAH\nPotential: +{potential:.2f} UAH\nBank after: {storage.bank - stake:.2f} UAH"
        await update.message.reply_text(msg)
        logger.info(f"place_bet: SUCCESS - Bet ID {bet['id']}")
        
    except Exception as e:
        await update.message.reply_text(f"ERROR: {str(e)}\n\nUsage: /place_bet \"Match\" \"Market\" odds stake")
        logger.error(f"place_bet: Exception - {e}")

async def bets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    open_bets = [b for b in storage.bets if b.get("status") == "OPEN"]
    if not open_bets:
        await update.message.reply_text("No open bets")
        return
    msg = "OPEN BETS\n\n"
    for b in open_bets:
        msg += f"ID: {b['id']}\n{b['match']} - {b['market']}\n{b['odds']} @ {b['stake']}\nPotential: +{b['stake']*(b['odds']-1):.2f}\n\n"
    await update.message.reply_text(msg)

async def bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = storage.get_stats()
    await update.message.reply_text(f"BANK\n\nCurrent: {stats['bank']:.2f} UAH\nProfit: {stats['profit']:+.2f} UAH\nBets placed: {stats['total']}\nWins: {stats['wins']}\nLosses: {stats['losses']}\nWin rate: {stats['wr']:.1f}%")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = storage.get_stats()
    await update.message.reply_text(f"STATS\n\nTotal bets: {s['total']}\nWins: {s['wins']}\nLosses: {s['losses']}\nWin rate: {s['wr']:.1f}%\nProfit: {s['profit']:+.2f} UAH\nBank: {s['bank']:.2f} UAH")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("/start - Info\n/place_bet - Place bet\n/bets - Open bets\n/bank - Bank\n/stats - Stats\n/help - Help")

async def post_init(app):
    global ALL_MATCHES
    
    storage.set_app(app)
    
    # Load from REAL API
    logger.info("Loading matches from Odds API...")
    ALL_MATCHES = await fetch_real_matches()
    logger.info(f"Loaded {sum(len(m) for m in ALL_MATCHES.values())} matches")
    
    try:
        await init_hermes()
        logger.info("Hermes OK")
    except Exception as e:
        logger.error(f"Hermes: {e}")
    
    if not scheduler.running:
        scheduler.start()
        scheduler.add_job(scan, 'cron', hour='*/1', minute=0)
        logger.info("Scheduler OK")
        await scan()
    
    try:
        await storage.tg("Bot started")
    except:
        pass

async def post_stop(app):
    if scheduler.running:
        scheduler.shutdown()

def main():
    logger.info("BOT STARTING")
    app = Application.builder().token(TOKEN).build()
    app.post_init = post_init
    app.post_stop = post_stop
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("place_bet", place_bet, filters.TEXT))
    app.add_handler(CommandHandler("bets", bets))
    app.add_handler(CommandHandler("bank", bank))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    
    logger.info("Starting polling...")
    app.run_polling()

if __name__ == "__main__":
    main()
