 """
main_autonomous.py - HYBRID BETTING BOT WITH EXPANDED MARKETS
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
from markets_config_simple import (
    MARKETS, EXPANDED_MATCHES, get_primary_markets, get_all_markets
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
UTC = timezone.utc

scheduler = AsyncIOScheduler()
INITIAL_BANK = 1019

# ═══════════════════════════════════════════════════════════════════════════
# USE EXPANDED MATCHES WITH MULTIPLE MARKETS
# ═══════════════════════════════════════════════════════════════════════════

ALL_MATCHES = EXPANDED_MATCHES

SPORTS_CONFIG = {
    "football": {
        "leagues": ["epl", "laliga", "seriea", "bundesliga", "ligue1"],
        "enabled": True,
        "min_odds": 1.40,
    },
    "basketball": {
        "leagues": ["nba"],
        "enabled": True,
        "min_odds": 1.40,
    },
    "tennis": {
        "leagues": ["atp_1000"],
        "enabled": True,
        "min_odds": 1.40,
    },
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
    
    def save_all(self):
        with open(self.recommendations_file, "w") as f:
            json.dump(self.recommendations, f, indent=2)
        with open(self.bets_file, "w") as f:
            json.dump(self.bets, f, indent=2)
        with open(self.results_file, "w") as f:
            json.dump(self.results, f, indent=2)
    
    def add_recommendation(self, rec: Dict):
        """Add automatic recommendation from Hermès"""
        rec["id"] = len(self.recommendations) + 1
        rec["timestamp"] = datetime.now(UTC).isoformat()
        rec["status"] = "SENT"
        self.recommendations.append(rec)
        self.save_all()
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
        self.save_all()
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
                
                self.save_all()
                
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
    """БОТ анализирует матчи с расширенными маркетами и отправляет рекомендации"""
    logger.info("=" * 80)
    logger.info("🔍 AUTO SCAN & EXPANDED MARKETS RECOMMENDATIONS")
    logger.info("=" * 80)
    
    recommendations = []
    markets_analyzed = 0
    
    for league, matches in ALL_MATCHES.items():
        for match_data in matches:
            sport = match_data.get("sport", "")
            
            # Get available markets for this sport
            primary_markets = get_primary_markets(sport)
            
            # For each market, create recommendation
            for market_name in primary_markets:
                market_key = market_name.lower().replace(" ", "_").replace("/", "")
                
                # Get odds for this market
                odds = match_data.get("markets", {}).get(market_key, 1.90)
                
                pick = {
                    "match": match_data["match"],
                    "sport": sport,
                    "league": league,
                    "selection": f"{market_name}",
                    "odds": odds,
                    "confidence": 0.65,
                }
                recommendations.append(pick)
                markets_analyzed += 1
    
    if not recommendations:
        logger.warning("⚠️ No recommendations generated")
        return
    
    logger.info(f"📊 Analyzing {markets_analyzed} markets...")
    
    # Analyze with Hermès
    enriched = await enrich_picks_with_hermes(recommendations, mode="NORMAL")
    
    sent_count = 0
    
    # Send recommendations that passed Hermès
    for rec in enriched["enriched_picks"]:
        if rec.get("hermes_recommendation") in ["ACCEPT", "RECONSIDER"]:
            confidence = rec.get("hermes_confidence", 0.65)
            confidence_emoji = "🟢" if confidence > 0.7 else "🟡"
            
            storage.add_recommendation({
                "match": rec["match"],
                "sport": rec["sport"],
                "market": rec["selection"],
                "odds": rec["odds"],
                "recommendation": rec.get("hermes_recommendation"),
                "confidence": confidence,
            })
            
            msg = f"""{confidence_emoji} *RECOMMENDATION - {rec['sport'].upper()}*

📊 *{rec['match'].upper()}*
🎯 Market: {rec['selection']}
💰 Odds: {rec['odds']}
📈 {rec.get('hermes_recommendation')} ({confidence:.0%})

Command:
`/place_bet "{rec['match']}" "{rec['selection']}" {rec['odds']} <stake>`

Example: `/place_bet "{rec['match']}" "{rec['selection']}" {rec['odds']} 50`
"""
            await storage.send_telegram(msg)
            sent_count += 1
    
    summary = f"""✅ *SCAN COMPLETE - EXPANDED MARKETS*

Markets analyzed: {markets_analyzed}
Recommendations sent: {sent_count}
Sports covered: {len(MARKETS)}

Markets per sport:
⚽ Football: {len(get_primary_markets('football'))} markets
🏀 Basketball: {len(get_primary_markets('basketball'))} markets
🎾 Tennis: {len(get_primary_markets('tennis'))} markets
"""
    
    await storage.send_telegram(summary)
    logger.info(f"✅ SENT {sent_count} EXPANDED MARKET RECOMMENDATIONS")

# ═══════════════════════════════════════════════════════════════════════════
# BOT COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = """🤖 *HYBRID BETTING BOT - EXPANDED MARKETS*

*Features:*
✅ Football: 4 markets (Match Winner, O/U, Handicap, BTTS)
✅ Basketball: 3 markets (Winner, Spread, Totals)
✅ Tennis: 3 markets (Winner, Set Betting, Games)
✅ Hybrid mode (Auto recommendations + manual bets)
✅ Auto result tracking

*How it works:*
1️⃣ БОТ сканирует матчи со ВСЕМИ маркетами
2️⃣ БОТ анализирует Hermès AI
3️⃣ БОТ присилает рекомендації в ТГ
4️⃣ Ты ставишь в букмекере вручную
5️⃣ Ты вводишь: /place_bet
6️⃣ БОТ отслідкує результат

*Commands:*
/place_bet - Place bet
/bets - Show bets
/bank - Bank info
/stats - Statistics
/markets - Markets info
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
        
        if stake > storage.bank:
            await update.message.reply_text(f"Insufficient bank! Available: {storage.bank:.2f} UAH")
            return
        
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

async def cmd_markets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = """📊 *MARKETS COVERAGE*

⚽ *FOOTBALL:*
"""
    for market in get_primary_markets("football"):
        msg += f"  • {market}\n"
    
    msg += f"""
🏀 *BASKETBALL:*
"""
    for market in get_primary_markets("basketball"):
        msg += f"  • {market}\n"
    
    msg += f"""
🎾 *TENNIS:*
"""
    for market in get_primary_markets("tennis"):
        msg += f"  • {market}\n"
    
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = """📋 *COMMANDS*

/start - Start info
/place_bet - Place bet manually
/bets - Show open bets
/bank - Bank status
/stats - Statistics
/markets - Markets coverage
/help - This message"""
    
    await update.message.reply_text(msg, parse_mode="Markdown")

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
    
    msg = "🤖 *HYBRID BOT STARTED - EXPANDED MARKETS*\n\n✅ 10 markets across 3 sports\n✅ Auto analysis enabled\n✅ Awaiting your bets 👀"
    try:
        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except:
        pass

async def post_stop(app):
    try:
        from hermes_integration_etap2 import shutdown_hermes
        await shutdown_hermes()
        logger.info("✅ Hermès shutdown")
    except Exception as e:
        logger.error(f"Hermès stop error: {e}")
    
    if scheduler.running:
        scheduler.shutdown()
        logger.info("✅ Scheduler stopped")

# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    logger.info("=" * 80)
    logger.info("🚀 HYBRID BETTING BOT - EXPANDED MARKETS")
    logger.info("✅ Football: 4 markets")
    logger.info("✅ Basketball: 3 markets")
    logger.info("✅ Tennis: 3 markets")
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
    app.add_handler(CommandHandler("markets", cmd_markets))
    app.add_handler(CommandHandler("help", cmd_help))
    
    logger.info("Starting bot...")
    app.run_polling()

if __name__ == "__main__":
    main()
