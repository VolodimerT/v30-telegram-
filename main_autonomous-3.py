"""
main_autonomous.py - CLEAN WORKING BOT
Простой, рабочий, без ошибок
"""
import os
import json
import logging
import asyncio
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
ODDS_KEY = os.getenv("ODDS_API_KEY")
UTC = timezone.utc

scheduler = AsyncIOScheduler()
INITIAL_BANK = 1019

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

# ═══════════════════════════════════════════════════════════════════════════
# SCAN
# ═══════════════════════════════════════════════════════════════════════════

async def scan():
    logger.info("🔍 SCANNING...")
    picks = []
    for league, matches in EXPANDED_MATCHES.items():
        for match in matches:
            sport = match.get("sport", "")
            markets = get_primary_markets(sport)
            for mkt in markets:
                key = mkt.lower().replace(" ", "_").replace("/", "")
                odds = match.get("markets", {}).get(key, 1.90)
                picks.append({"match": match["match"], "sport": sport, "league": league, "selection": mkt, "odds": odds, "confidence": 0.65})
    
    logger.info(f"📊 Analyzing {len(picks)} picks...")
    try:
        enriched = await enrich_picks_with_hermes(picks, mode="NORMAL")
        sent = 0
        for pick in enriched.get("enriched_picks", []):
            if pick.get("hermes_recommendation") in ["ACCEPT", "RECONSIDER"]:
                conf = pick.get("hermes_confidence", 0.65)
                emoji = "🟢" if conf > 0.7 else "🟡"
                storage.add_rec(pick["match"], pick["selection"], pick["odds"], conf, pick.get("hermes_recommendation"))
                msg = f"{emoji} *{pick['sport'].upper()}*\n\n📊 {pick['match']}\n🎯 {pick['selection']}\n💰 {pick['odds']}\n📈 {pick.get('hermes_recommendation')} ({conf:.0%})\n\n`/place_bet \"{pick['match']}\" \"{pick['selection']}\" {pick['odds']} 50`"
                await storage.tg(msg)
                sent += 1
        logger.info(f"✅ Sent {sent} recommendations")
    except Exception as e:
        logger.error(f"Error: {e}")

# ═══════════════════════════════════════════════════════════════════════════
# COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 *BETTING BOT*\n\n✅ Auto scan every hour\n✅ 10 markets\n✅ Hybrid mode\n\n/place_bet /bets /bank /stats /help", parse_mode="Markdown")

async def place_bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 4:
        await update.message.reply_text("Usage: /place_bet \"Match\" \"Market\" odds stake")
        return
    try:
        match, market, odds, stake = context.args[0], context.args[1], float(context.args[2]), float(context.args[3])
        if stake > storage.bank:
            await update.message.reply_text(f"Bank: {storage.bank:.2f}")
            return
        storage.add_bet(match, market, odds, stake)
        await update.message.reply_text(f"✅ Bet placed\n{match}\n{odds} @ {stake}\nPotential: +{stake*(odds-1):.2f}")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def bets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    open_bets = [b for b in storage.bets if b.get("status") == "OPEN"]
    if not open_bets:
        await update.message.reply_text("No open bets")
        return
    msg = "*OPEN BETS*\n\n"
    for b in open_bets:
        msg += f"ID: {b['id']}\n{b['match']} - {b['market']}\n{b['odds']} @ {b['stake']}\n\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = storage.get_stats()
    await update.message.reply_text(f"💰 *BANK*\n\nCurrent: {stats['bank']:.2f}\nProfit: {stats['profit']:+.2f}\nBets: {stats['total']}\nWins: {stats['wins']}\nWR: {stats['wr']:.1f}%", parse_mode="Markdown")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = storage.get_stats()
    await update.message.reply_text(f"📈 *STATS*\n\nTotal: {s['total']}\nWins: {s['wins']}\nLosses: {s['losses']}\nWR: {s['wr']:.1f}%\nProfit: {s['profit']:+.2f}\nBank: {s['bank']:.2f}", parse_mode="Markdown")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("/start /place_bet /bets /bank /stats /help")

# ═══════════════════════════════════════════════════════════════════════════
# STARTUP
# ═══════════════════════════════════════════════════════════════════════════

async def post_init(app):
    storage.set_app(app)
    try:
        await init_hermes()
        logger.info("✅ Hermes OK")
    except Exception as e:
        logger.error(f"Hermes: {e}")
    
    if not scheduler.running:
        scheduler.start()
        scheduler.add_job(scan, 'cron', hour='*/1', minute=0)
        logger.info("✅ Scheduler OK")
        await scan()
    
    try:
        await storage.tg("🤖 Bot started")
    except:
        pass

async def post_stop(app):
    if scheduler.running:
        scheduler.shutdown()

# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    logger.info("🚀 BETTING BOT STARTING")
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
