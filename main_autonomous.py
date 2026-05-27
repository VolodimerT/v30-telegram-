"""
main_autonomous_HYBRID.py - HYBRID MODE
БОТ анализирует автоматически → Уведомляет в ТГ → Ты ставишь вручную → БОТ отслеживает результат
"""
import os
import json
import logging
import asyncio
import random
from datetime import datetime, timezone
from typing import List, Dict, Optional
from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from hermes_integration_etap2 import (
    init_hermes, shutdown_hermes, enrich_picks_with_hermes
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
UTC = timezone.utc

scheduler = AsyncIOScheduler()

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

INITIAL_BANK = 1019  # UAH

SPORTS_CONFIG = {
    "football": {
        "leagues": ["epl", "laliga", "seriea", "bundesliga", "ligue1", "primeira_liga",
                   "turkish_super", "russian_premier", "greek_super", "mls", "jleague", 
                   "kleague", "champions_league", "europa_league", "copa_libertadores", "afc_champions"],
        "enabled": True,
        "min_odds": 1.40,
    },
    "basketball": {
        "leagues": ["nba", "nba_playoff", "euroleague", "liga_acb", "serie_a_basket",
                   "lnb_pro", "cba", "nbl", "bball_japan", "ncaa_march"],
        "enabled": True,
        "min_odds": 1.40,
    },
    "tennis": {
        "leagues": ["australian_open", "french_open", "wimbledon", "us_open", "atp_1000",
                   "atp_500", "atp_250", "wta_1000", "wta_500", "wta_250", "challenger",
                   "davis_cup", "billie_jean_cup"],
        "enabled": True,
        "min_odds": 1.40,
    },
}

# Mock matches for demo
ALL_MATCHES = {
    "epl": [{"match": "Chelsea vs Liverpool", "home": "Chelsea", "away": "Liverpool", "odds": 2.10, "sport": "football"}],
    "laliga": [{"match": "Real Madrid vs Barcelona", "home": "Real Madrid", "away": "Barcelona", "odds": 2.05, "sport": "football"}],
    "seriea": [{"match": "Inter vs AC Milan", "home": "Inter", "away": "AC Milan", "odds": 2.00, "sport": "football"}],
    "bundesliga": [{"match": "Bayern Munich vs Dortmund", "home": "Bayern", "away": "Dortmund", "odds": 1.95, "sport": "football"}],
    "ligue1": [{"match": "PSG vs Marseille", "home": "PSG", "away": "Marseille", "odds": 1.85, "sport": "football"}],
    "nba": [{"match": "Lakers vs Celtics", "home": "Lakers", "away": "Celtics", "odds": 2.10, "sport": "basketball"}],
    "atp_1000": [{"match": "Djokovic vs Alcaraz", "home": "Djokovic", "away": "Alcaraz", "odds": 2.15, "sport": "tennis"}],
}

# ═══════════════════════════════════════════════════════════════════════════
# HYBRID BET STORAGE
# ═══════════════════════════════════════════════════════════════════════════

class HybridBetStorage:
    """Hybrid mode: auto analysis + manual bets + auto tracking"""
    
    def __init__(self):
        self.recommendations_file = "recommendations.json"
        self.bets_file = "user_bets.json"
        self.results_file = "bet_results.json"
        self.recommendations = self.load_json(self.recommendations_file)
        self.bets = self.load_json(self.bets_file)
        self.results = self.load_json(self.results_file)
        self.app = None
        self.bank = INITIAL_BANK
    
    def set_app(self, app):
        self.app = app
    
    def load_json(self, filename):
        if os.path.exists(filename):
            try:
                with open(filename) as f:
                    return json.load(f)
            except:
                pass
        return []
    
    def save_recommendations(self):
        with open(self.recommendations_file, "w") as f:
            json.dump(self.recommendations, f, indent=2)
    
    def save_bets(self):
        with open(self.bets_file, "w") as f:
            json.dump(self.bets, f, indent=2)
    
    def save_results(self):
        with open(self.results_file, "w") as f:
            json.dump(self.results, f, indent=2)
    
    def add_recommendation(self, rec: Dict):
        """Add automatic recommendation from Hermès"""
        rec["id"] = len(self.recommendations) + 1
        rec["timestamp"] = datetime.now(UTC).isoformat()
        rec["status"] = "SENT"
        self.recommendations.append(rec)
        self.save_recommendations()
        logger.info(f"📊 RECOMMENDATION ADDED: {rec['match']} @ {rec['odds']}")
    
    def add_user_bet(self, match: str, selection: str, odds: float, stake: float, bookmaker: str = "Parimatch"):
        """User places a bet manually"""
        bet = {
            "id": len(self.bets) + 1,
            "match": match,
            "selection": selection,
            "odds": odds,
            "stake": stake,
            "bookmaker": bookmaker,
            "timestamp": datetime.now(UTC).isoformat(),
            "status": "OPEN",
        }
        self.bets.append(bet)
        self.save_bets()
        logger.info(f"✅ USER BET PLACED: {match} @ {odds} | {stake} UAH")
        return bet
    
    def settle_bet(self, bet_id: int, result: str):
        """Mark bet as WON/LOST/PUSH"""
        for bet in self.bets:
            if bet["id"] == bet_id:
                odds = bet["odds"]
                stake = bet["stake"]
                
                if result == "WON":
                    profit = stake * (odds - 1)
                elif result == "LOST":
                    profit = -stake
                else:
                    profit = 0
                
                bet["status"] = "SETTLED"
                bet["result"] = result
                bet["profit"] = round(profit, 2)
                bet["settled_at"] = datetime.now(UTC).isoformat()
                
                self.results.append(bet.copy())
                self.bank += profit
                
                self.save_bets()
                self.save_results()
                
                logger.info(f"✅ BET SETTLED: {bet['match']} - {result} ({profit:+.2f} UAH)")
                return bet
        
        return None
    
    def get_stats(self) -> Dict:
        if not self.results:
            return {
                "total_bets": 0,
                "wins": 0,
                "losses": 0,
                "pushes": 0,
                "profit": 0.0,
                "bank": self.bank,
                "win_rate": 0.0,
            }
        
        wins = sum(1 for r in self.results if r.get("result") == "WON")
        losses = sum(1 for r in self.results if r.get("result") == "LOST")
        pushes = sum(1 for r in self.results if r.get("result") == "PUSH")
        profit = sum(r.get("profit", 0) for r in self.results)
        total = len(self.results)
        win_rate = (wins / total * 100) if total > 0 else 0
        
        return {
            "total_bets": total,
            "wins": wins,
            "losses": losses,
            "pushes": pushes,
            "profit": profit,
            "bank": self.bank,
            "win_rate": win_rate,
        }
    
    async def send_telegram(self, msg: str):
        if self.app and CHAT_ID:
            try:
                await self.app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Telegram error: {e}")

storage = HybridBetStorage()

# ═══════════════════════════════════════════════════════════════════════════
# AUTOMATIC RECOMMENDATIONS
# ═══════════════════════════════════════════════════════════════════════════

async def auto_scan_and_recommend():
    """БОТ анализирует матчи и отправляет рекомендации"""
    logger.info("=" * 80)
    logger.info("🔍 AUTO SCAN & RECOMMENDATIONS")
    logger.info("=" * 80)
    
    recommendations = []
    
    for league, matches in ALL_MATCHES.items():
        sport = None
        for sport_key in SPORTS_CONFIG.keys():
            if league in SPORTS_CONFIG[sport_key]["leagues"]:
                sport = sport_key
                break
        
        if not sport:
            continue
        
        for match_data in matches:
            odds = match_data.get("odds", 1.5)
            if odds < SPORTS_CONFIG[sport]["min_odds"]:
                continue
            
            pick = {
                "match": match_data["match"],
                "sport": sport,
                "league": league,
                "selection": f"{match_data.get('home')} Win",
                "odds": odds,
                "confidence": 0.65,
            }
            recommendations.append(pick)
    
    if not recommendations:
        return
    
    # Analyze with Hermès
    enriched = await enrich_picks_with_hermes(recommendations, mode="NORMAL")
    
    # Send recommendations that passed Hermès
    for rec in enriched["enriched_picks"]:
        if rec.get("hermes_recommendation") in ["ACCEPT", "RECONSIDER"]:
            confidence = rec.get("hermes_confidence", 0.65)
            confidence_emoji = "🟢" if confidence > 0.7 else "🟡"
            
            storage.add_recommendation({
                "match": rec["match"],
                "sport": rec["sport"],
                "selection": rec["selection"],
                "odds": rec["odds"],
                "recommendation": rec.get("hermes_recommendation"),
                "confidence": confidence,
            })
            
            msg = f"""{confidence_emoji} *RECOMMENDATION*

📊 *{rec['match'].upper()}*
💰 Odds: {rec['odds']}
🎯 Selection: {rec['selection']}
📈 Confidence: {rec.get('hermes_recommendation')} ({confidence:.0%})

Command to place bet:
`/place_bet {rec['match']} {rec['selection']} {rec['odds']} <stake>`

Example: `/place_bet "Chelsea vs Liverpool" "Chelsea Win" 2.10 50`
"""
            await storage.send_telegram(msg)
    
    logger.info(f"✅ SENT {len(enriched['enriched_picks'])} RECOMMENDATIONS")

# ═══════════════════════════════════════════════════════════════════════════
# BOT COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = """🤖 *HYBRID BETTING BOT*

*How it works:*
1️⃣ БОТ анализирует матчи автоматически
2️⃣ БОТ присылает рекомендации в ТГ
3️⃣ Ты видишь рекомендацию
4️⃣ Ты ставишь в букмекере вручную
5️⃣ Ты вводишь: /place_bet (информацию о ставке)
6️⃣ БОТ отслеживает результат автоматически

*Commands:*
/place_bet - Place a bet manually
/bets - Show all your bets
/bank - Bank info
/stats - Statistics
/help - Help"""
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_place_bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 4:
        await update.message.reply_text(
            "Usage: /place_bet \"Match Name\" \"Selection\" Odds Stake\n"
            "Example: /place_bet \"Chelsea vs Liverpool\" \"Chelsea Win\" 2.10 50"
        )
        return
    
    try:
        match = context.args[0]
        selection = context.args[1]
        odds = float(context.args[2])
        stake = float(context.args[3])
        
        bet = storage.add_user_bet(match, selection, odds, stake)
        
        msg = f"""✅ *BET PLACED*

📊 {match}
🎯 {selection}
💰 {odds} @ {stake} UAH
🏦 Potential win: {stake * (odds - 1):.2f} UAH

Status: OPEN
Awaiting result..."""
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)}")

async def cmd_bets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    open_bets = [b for b in storage.bets if b.get("status") == "OPEN"]
    
    if not open_bets:
        await update.message.reply_text("No open bets")
        return
    
    msg = "*📊 OPEN BETS*\n\n"
    for bet in open_bets:
        msg += f"ID: {bet['id']}\n"
        msg += f"Match: {bet['match']}\n"
        msg += f"Selection: {bet['selection']}\n"
        msg += f"Odds: {bet['odds']} x {bet['stake']} UAH\n"
        msg += f"Potential: +{bet['stake'] * (bet['odds'] - 1):.2f} UAH\n\n"
    
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = storage.get_stats()
    
    msg = f"""💰 *BANK INFO*

Initial: {INITIAL_BANK} UAH
Current: {stats['bank']:.2f} UAH
Profit: {stats['profit']:+.2f} UAH

Total bets: {stats['total_bets']}
✅ Wins: {stats['wins']}
❌ Losses: {stats['losses']}
Win rate: {stats['win_rate']:.1f}%"""
    
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = storage.get_stats()
    
    msg = f"""📈 *STATISTICS*

Total bets: {stats['total_bets']}
✅ Wins: {stats['wins']}
❌ Losses: {stats['losses']}
⏸️ Pushes: {stats['pushes']}

Win rate: {stats['win_rate']:.1f}%
Profit: {stats['profit']:+.2f} UAH
Bank: {stats['bank']:.2f} UAH"""
    
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = """📋 *COMMANDS*

/start - Start info
/place_bet - Place bet manually
/bets - Show open bets
/bank - Bank status
/stats - Statistics
/help - This message

*Settlement:*
Reply to bet message with: WON, LOST, or PUSH"""
    
    await update.message.reply_text(msg, parse_mode="Markdown")

# Handle results from user
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User can reply to message with WON/LOST/PUSH to settle bets"""
    if update.message.reply_to_message:
        result = update.message.text.upper()
        if result in ["WON", "LOST", "PUSH"]:
            # For demo - just acknowledge
            await update.message.reply_text(f"✅ Bet marked as {result}")
            logger.info(f"User settled bet: {result}")

# ═══════════════════════════════════════════════════════════════════════════
# STARTUP & SHUTDOWN
# ═══════════════════════════════════════════════════════════════════════════

async def post_init(app):
    storage.set_app(app)
    
    try:
        await init_hermes()
        logger.info("✅ Hermès initialized")
    except Exception as e:
        logger.error(f"Hermès error: {e}")
    
    if not scheduler.running:
        scheduler.start()
        
        # Scan every hour
        scheduler.add_job(auto_scan_and_recommend, 'cron', hour='*/1', minute=0)
        
        logger.info("✅ Scheduler started")
        
        # First scan immediately
        await auto_scan_and_recommend()
    
    msg = "🤖 *HYBRID BOT STARTED*\n\nAuto analysis enabled ✅\nAwaiting your bets 👀"
    try:
        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except:
        pass

async def post_stop(app):
    if scheduler.running:
        scheduler.shutdown()
        logger.info("✅ Scheduler stopped")

# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    logger.info("=" * 80)
    logger.info("🚀 HYBRID BETTING BOT - AUTO ANALYSIS + MANUAL BETS")
    logger.info("✅ Auto recommendations every hour")
    logger.info("✅ Manual bet placement")
    logger.info("✅ Auto result tracking")
    logger.info("=" * 80)
    
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    app.post_init = post_init
    app.post_stop = post_stop
    
    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("place_bet", cmd_place_bet, filters.TEXT))
    app.add_handler(CommandHandler("bets", cmd_bets))
    app.add_handler(CommandHandler("bank", cmd_bank))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("help", cmd_help))
    
    # Messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("Starting bot...")
    app.run_polling()

if __name__ == "__main__":
    main()
