"""
main_autonomous_HYBRID_ULTIMATE.py - ULTIMATE BETTING BOT
Полный функционал: расширенные маркеты + глубокий анализ + гибридный режим + ручной ввод
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
from hermes_integration_etap2 import init_hermes, shutdown_hermes, enrich_picks_with_hermes
from markets_config import (
    MARKETS_CONFIG, get_primary_markets, get_preferred_markets, 
    get_hermes_params, MARKETS_SUMMARY
)
from enhanced_hermes import EnhancedHermesAnalyzer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
UTC = timezone.utc

scheduler = AsyncIOScheduler()
INITIAL_BANK = 1019

# ═══════════════════════════════════════════════════════════════════════════
# MOCK DATA WITH EXPANDED MARKETS
# ═══════════════════════════════════════════════════════════════════════════

ALL_MATCHES = {
    "epl": [
        {
            "match": "Chelsea vs Liverpool",
            "home": "Chelsea",
            "away": "Liverpool",
            "sport": "football",
            "league": "epl",
            "markets": {
                "match_winner": {"home": 2.10, "draw": 3.50, "away": 3.40},
                "over_under_2.5": {"over": 1.85, "under": 1.95},
                "btts": {"yes": 1.75, "no": 2.10},
            },
        },
    ],
    "nba": [
        {
            "match": "Lakers vs Celtics",
            "home": "Lakers",
            "away": "Celtics",
            "sport": "basketball",
            "league": "nba",
            "markets": {
                "match_winner": {"home": 2.05, "away": 1.80},
                "point_spread": {"home": -4.5, "away": +4.5},
                "total_points": {"over_220": 1.90, "under_220": 1.90},
            },
        },
    ],
    "atp_1000": [
        {
            "match": "Djokovic vs Alcaraz",
            "home": "Djokovic",
            "away": "Alcaraz",
            "sport": "tennis",
            "league": "atp_1000",
            "markets": {
                "match_winner": {"home": 2.15, "away": 1.70},
                "set_betting": {"2-0": 1.85, "2-1": 2.20, "0-2": 2.05},
            },
        },
    ],
}

# ═══════════════════════════════════════════════════════════════════════════
# ULTIMATE BET STORAGE
# ═══════════════════════════════════════════════════════════════════════════

class UltimateBetStorage:
    """Ultimate storage with all features"""
    
    def __init__(self):
        self.recommendations_file = "recommendations_ultimate.json"
        self.bets_file = "user_bets_ultimate.json"
        self.results_file = "bet_results_ultimate.json"
        self.market_analysis_file = "market_analysis_ultimate.json"
        
        self.recommendations = self.load_json(self.recommendations_file)
        self.bets = self.load_json(self.bets_file)
        self.results = self.load_json(self.results_file)
        self.market_analysis = self.load_json(self.market_analysis_file)
        
        self.app = None
        self.bank = INITIAL_BANK
        self.enhanced_analyzer = EnhancedHermesAnalyzer()
    
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
        with open(self.market_analysis_file, "w") as f:
            json.dump(self.market_analysis, f, indent=2)
    
    def add_recommendation(self, rec: Dict):
        """Add enhanced recommendation"""
        rec["id"] = len(self.recommendations) + 1
        rec["timestamp"] = datetime.now(UTC).isoformat()
        rec["status"] = "SENT"
        self.recommendations.append(rec)
        self.save_all()
    
    def add_user_bet(self, match: str, market: str, selection: str, odds: float, 
                     stake: float, bookmaker: str = "Your Bookmaker"):
        """Add user bet (hybrid mode)"""
        bet = {
            "id": len(self.bets) + 1,
            "match": match,
            "market": market,
            "selection": selection,
            "odds": odds,
            "stake": stake,
            "bookmaker": bookmaker,
            "timestamp": datetime.now(UTC).isoformat(),
            "status": "OPEN",
        }
        self.bets.append(bet)
        self.save_all()
        return bet
    
    def settle_bet(self, bet_id: int, result: str):
        """Settle a bet (WON/LOST/PUSH)"""
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
                return bet
        return None
    
    def analyze_match_comprehensive(self, match: Dict, sport: str, league: str) -> List[Dict]:
        """Comprehensive analysis for all preferred markets"""
        analyses = []
        preferred = get_preferred_markets(sport)
        
        for market in preferred:
            analysis = self.enhanced_analyzer.analyze_match(
                match, sport, league, market
            )
            analyses.append(analysis)
        
        return analyses
    
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

storage = UltimateBetStorage()

# ═══════════════════════════════════════════════════════════════════════════
# ENHANCED AUTO RECOMMENDATIONS
# ═══════════════════════════════════════════════════════════════════════════

async def auto_scan_with_market_expansion():
    """Scan all matches with expanded market analysis"""
    logger.info("=" * 80)
    logger.info("🔍 ULTIMATE AUTO SCAN - ALL MARKETS ANALYSIS")
    logger.info("=" * 80)
    
    total_recommendations = 0
    market_breakdown = {}
    
    for league, matches in ALL_MATCHES.items():
        sport = None
        for sport_key in MARKETS_CONFIG.keys():
            if league in MARKETS_CONFIG[sport_key]["leagues"]:
                sport = sport_key
                break
        
        if not sport:
            continue
        
        logger.info(f"📊 Analyzing {sport.upper()} - {league}...")
        
        for match in matches:
            # Get comprehensive analysis for all markets
            analyses = storage.analyze_match_comprehensive(match, sport, league)
            
            for analysis in analyses:
                if analysis["recommendation"] in ["ACCEPT", "RECONSIDER"]:
                    confidence = analysis["confidence"]
                    confidence_emoji = "🟢" if confidence > 0.7 else "🟡"
                    
                    rec = {
                        "match": analysis["match"],
                        "sport": sport,
                        "league": league,
                        "market": analysis["market"],
                        "recommendation": analysis["recommendation"],
                        "confidence": confidence,
                        "ev": analysis["ev"],
                        "factors": analysis["factors"],
                    }
                    
                    storage.add_recommendation(rec)
                    total_recommendations += 1
                    
                    market_breakdown[analysis["market"]] = market_breakdown.get(analysis["market"], 0) + 1
                    
                    # Send to Telegram
                    msg = f"""{confidence_emoji} *RECOMMENDATION - {analysis['market'].upper()}*

📊 *{analysis['match']}*
🎯 Market: {analysis['market']}
💰 EV: {analysis['ev']:.1%}
📈 Confidence: {analysis['recommendation']} ({confidence:.0%})

Factors:
"""
                    for factor, value in analysis["factors"].items():
                        sign = "+" if value > 0 else ""
                        msg += f"  • {factor}: {sign}{value:.3f}\n"
                    
                    msg += f"\n*Place your bet and use:*\n"
                    msg += f"`/place_bet \"{analysis['match']}\" \"{analysis['market']}\" odds stake`"
                    
                    await storage.send_telegram(msg)
    
    # Summary
    msg = f"""✅ *SCAN COMPLETE - ULTIMATE ANALYSIS*

Total recommendations: {total_recommendations}

Market breakdown:
"""
    for market, count in market_breakdown.items():
        msg += f"  • {market}: {count}\n"
    
    msg += f"\nAnalyzed sports: {len(MARKETS_CONFIG)} (Football, Basketball, Tennis)"
    
    await storage.send_telegram(msg)
    logger.info(f"✅ SENT {total_recommendations} ULTIMATE RECOMMENDATIONS")

# ═══════════════════════════════════════════════════════════════════════════
# TELEGRAM COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = """🤖 *ULTIMATE HYBRID BETTING BOT*

*Features:*
✅ Expanded markets (Match Winner, O/U, Handicap, BTTS, итд)
✅ Enhanced analysis (Form, Injuries, H2H, Fatigue, Psychology)
✅ Hybrid mode (Auto recommendations + manual bets)
✅ Automatic result tracking
✅ Comprehensive statistics

*How it works:*
1️⃣ БОТ анализирует все маркеты
2️⃣ БОТ присылает рекомендации в ТГ
3️⃣ Ты видишь детальный анализ с 7+ факторами
4️⃣ Ты ставишь в букмекере вручную
5️⃣ Ты вводишь: /place_bet
6️⃣ БОТ отслеживает результат

*Commands:*
/place_bet - Place a bet
/bets - Show open bets
/bank - Bank status
/stats - Statistics
/markets - Markets info
/help - Help"""
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_place_bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 4:
        await update.message.reply_text(
            "Usage: /place_bet \"Match\" \"Market\" odds stake\n"
            "Example: /place_bet \"Chelsea vs Liverpool\" \"Match Winner\" 2.10 50"
        )
        return
    
    try:
        match = context.args[0]
        market = context.args[1]
        odds = float(context.args[2])
        stake = float(context.args[3])
        
        if stake > storage.bank:
            await update.message.reply_text(f"Insufficient bank! Available: {storage.bank:.2f} UAH")
            return
        
        bet = storage.add_user_bet(match, market, market, odds, stake)
        
        msg = f"""✅ *BET PLACED - ULTIMATE*

📊 {match}
🎯 {market}
💰 {odds} @ {stake} UAH
🏦 Potential: +{stake * (odds - 1):.2f} UAH

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
        msg += f"Market: {bet['market']}\n"
        msg += f"Odds: {bet['odds']} x {bet['stake']} UAH\n"
        msg += f"Potential: +{bet['stake'] * (bet['odds'] - 1):.2f} UAH\n\n"
    
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = storage.get_stats()
    
    msg = f"""💰 *BANK STATUS - ULTIMATE*

Initial: {INITIAL_BANK} UAH
Current: {stats['bank']:.2f} UAH
Profit: {stats['profit']:+.2f} UAH
ROI: {(stats['profit']/INITIAL_BANK)*100:+.1f}%

Bets placed: {stats['total_bets']}
✅ Wins: {stats['wins']}
❌ Losses: {stats['losses']}
📊 Win rate: {stats['win_rate']:.1f}%"""
    
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = storage.get_stats()
    
    msg = f"""📈 *ULTIMATE STATISTICS*

Total bets: {stats['total_bets']}
✅ Wins: {stats['wins']}
❌ Losses: {stats['losses']}
⏸️ Pushes: {stats['pushes']}

Win rate: {stats['win_rate']:.1f}%
Profit: {stats['profit']:+.2f} UAH
Bank: {stats['bank']:.2f} UAH
ROI: {(stats['profit']/INITIAL_BANK)*100:+.1f}%"""
    
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_markets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "*📊 MARKETS COVERAGE - ULTIMATE*\n\n"
    
    for sport, summary in MARKETS_SUMMARY.items():
        msg += f"*{sport.upper()}*\n"
        msg += f"  Total markets: {summary['total_markets']}\n"
        msg += f"  Primary: {summary['primary']}\n"
        msg += f"  Secondary: {summary['secondary']}\n"
        msg += f"  Recommend: {summary['recommend_for']}\n\n"
    
    msg += "✅ 8 Football markets\n"
    msg += "✅ 6 Basketball markets\n"
    msg += "✅ 6 Tennis markets\n"
    msg += "\nTotal: 20 markets across all sports"
    
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = """📋 *ULTIMATE BOT COMMANDS*

/start - Start info
/place_bet - Place bet manually
/bets - Show open bets
/bank - Bank status
/stats - Statistics
/markets - Markets coverage
/help - This message

*Example:*
/place_bet "Chelsea vs Liverpool" "Match Winner" 2.10 50

*Hybrid Mode:*
БОТ рекомендует → Ты ставишь → БОТ отслеживает"""
    
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
        scheduler.add_job(auto_scan_with_market_expansion, 'cron', hour='*/1', minute=0)
        logger.info("✅ Scheduler started")
        await auto_scan_with_market_expansion()
    
    msg = "🤖 *ULTIMATE HYBRID BOT STARTED*\n\n✅ All features active\n✅ Enhanced market analysis\n✅ Deep Hermès analysis"
    try:
        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except:
        pass

async def post_stop(app):
    if scheduler.running:
        scheduler.shutdown()

# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    logger.info("=" * 80)
    logger.info("🚀 ULTIMATE HYBRID BETTING BOT")
    logger.info("✅ Expanded Markets (20 total)")
    logger.info("✅ Enhanced Analysis (7+ factors)")
    logger.info("✅ Hybrid Mode (Auto + Manual)")
    logger.info("=" * 80)
    
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    app.post_init = post_init
    app.post_stop = post_stop
    
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
