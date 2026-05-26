"""
main_autonomous_AUTO.py - FULLY AUTOMATIC SYSTEM
Всё работает на автомате без необходимости вводить команды!
"""
import os
import json
import logging
import asyncio
import random
from datetime import datetime, timezone
from typing import List, Dict
from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from hermes_integration_etap2 import (
    init_hermes, shutdown_hermes, get_integration_metrics,
    format_integration_status, enrich_picks_with_hermes, report_bet_result_async
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
UTC = timezone.utc

scheduler = AsyncIOScheduler()

# ═══════════════════════════════════════════════════════════════════════════
# MOCK DATA
# ═══════════════════════════════════════════════════════════════════════════

ALL_MATCHES = {
    "epl": [
        {"match": "Chelsea vs Liverpool", "home": "Chelsea", "away": "Liverpool", "odds": 2.10},
        {"match": "Man City vs Arsenal", "home": "Man City", "away": "Arsenal", "odds": 1.85},
        {"match": "Manchester United vs Tottenham", "home": "Manchester United", "away": "Tottenham", "odds": 2.20},
    ],
    "laliga": [
        {"match": "Real Madrid vs Barcelona", "home": "Real Madrid", "away": "Barcelona", "odds": 2.05},
        {"match": "Atletico Madrid vs Sevilla", "home": "Atletico Madrid", "away": "Sevilla", "odds": 1.95},
        {"match": "Valencia vs Villarreal", "home": "Valencia", "away": "Villarreal", "odds": 2.15},
    ],
    "seriea": [
        {"match": "Inter vs AC Milan", "home": "Inter", "away": "AC Milan", "odds": 2.00},
        {"match": "Juventus vs Napoli", "home": "Juventus", "away": "Napoli", "odds": 1.90},
        {"match": "Roma vs Lazio", "home": "Roma", "away": "Lazio", "odds": 2.10},
    ],
}

# ═══════════════════════════════════════════════════════════════════════════
# STORAGE
# ═══════════════════════════════════════════════════════════════════════════

class AutoBetStorage:
    """Automatic bet storage and management."""
    
    def __init__(self):
        self.picks_file = "picks_history.json"
        self.results_file = "results_history.json"
        self.picks = self.load_picks()
        self.results = self.load_results()
        self.app = None
    
    def set_app(self, app):
        self.app = app
    
    def load_picks(self) -> List[Dict]:
        if os.path.exists(self.picks_file):
            try:
                with open(self.picks_file) as f:
                    return json.load(f)
            except:
                pass
        return []
    
    def load_results(self) -> List[Dict]:
        if os.path.exists(self.results_file):
            try:
                with open(self.results_file) as f:
                    return json.load(f)
            except:
                pass
        return []
    
    def save_picks(self):
        with open(self.picks_file, "w") as f:
            json.dump(self.picks, f, indent=2)
    
    def save_results(self):
        with open(self.results_file, "w") as f:
            json.dump(self.results, f, indent=2)
    
    def add_pick(self, pick: Dict):
        pick["id"] = len(self.picks) + 1
        pick["timestamp"] = datetime.now(UTC).isoformat()
        pick["status"] = "OPEN"
        self.picks.append(pick)
        self.save_picks()
        logger.info(f"✅ AUTO BET PLACED: {pick['match']} @ {pick['odds']} | ${pick['stake']}")
    
    def auto_settle_picks(self):
        """Автоматически отмечает ставки как завершённые (симуляция результатов)."""
        open_picks = [p for p in self.picks if p.get("status") == "OPEN"]
        
        for pick in open_picks:
            # Симуляция результата
            result = random.choice(["WIN", "LOSS", "PUSH"])
            odds = pick["odds"]
            stake = pick["stake"]
            
            if result == "WIN":
                pnl = stake * (odds - 1)
            elif result == "LOSS":
                pnl = -stake
            else:  # PUSH
                pnl = 0
            
            # Отметь как завершённую
            pick["status"] = "SETTLED"
            pick["result"] = result
            pick["pnl"] = round(pnl, 2)
            pick["settled_at"] = datetime.now(UTC).isoformat()
            
            # Сохрани в результаты
            self.results.append(pick.copy())
            
            logger.info(f"✅ AUTO SETTLED: {pick['match']} - {result} ({pnl:+.2f})")
        
        self.save_picks()
        self.save_results()
    
    def get_stats(self) -> Dict:
        if not self.results:
            return {
                "total_picks": 0,
                "wins": 0,
                "losses": 0,
                "pushes": 0,
                "profit": 0.0,
                "roi": 0.0,
                "win_rate": 0.0,
            }
        
        wins = sum(1 for r in self.results if r.get("result") == "WIN")
        losses = sum(1 for r in self.results if r.get("result") == "LOSS")
        pushes = sum(1 for r in self.results if r.get("result") == "PUSH")
        profit = sum(r.get("pnl", 0) for r in self.results)
        total = len(self.results)
        win_rate = (wins / total * 100) if total > 0 else 0
        roi = (profit / (total * 50)) * 100 if total > 0 else 0
        
        return {
            "total_picks": total,
            "wins": wins,
            "losses": losses,
            "pushes": pushes,
            "profit": profit,
            "roi": roi,
            "win_rate": win_rate,
        }
    
    async def send_telegram(self, msg: str):
        """Отправить сообщение в Telegram."""
        if self.app and CHAT_ID:
            try:
                await self.app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Telegram error: {e}")

storage = AutoBetStorage()

# ═══════════════════════════════════════════════════════════════════════════
# AUTOMATIC TASKS (работают на автомате!)
# ═══════════════════════════════════════════════════════════════════════════

async def auto_scan_and_place_bets():
    """АВТОМАТИЧЕСКОЕ сканирование и размещение ставок."""
    logger.info("=" * 80)
    logger.info("🤖 AUTO SCAN & BET PLACEMENT STARTED")
    logger.info("=" * 80)
    
    total_placed = 0
    
    for league, matches in ALL_MATCHES.items():
        logger.info(f"🔍 Scanning {league}...")
        
        picks = []
        for match_data in matches:
            pick = {
                "match": match_data["match"],
                "league": league,
                "home": match_data["home"],
                "away": match_data["away"],
                "odds": match_data["odds"],
                "stake": 50,
                "selection": f"{match_data['home']} Win",
                "confidence": 0.65,
            }
            picks.append(pick)
        
        # Enrich with Hermès
        enriched = await enrich_picks_with_hermes(picks, mode="NORMAL")
        
        # Place bets automatically
        for pick in enriched["enriched_picks"]:
            if pick.get("hermes_recommendation") == "ACCEPT":
                storage.add_pick(pick)
                total_placed += 1
            elif pick.get("hermes_recommendation") == "RECONSIDER":
                pick["stake"] = int(pick["stake"] * 0.5)
                storage.add_pick(pick)
                total_placed += 1
    
    msg = f"""
🤖 *AUTO SCAN COMPLETE*

Total picks placed: {total_placed}
Total stake: ${total_placed * 50}
Status: ✅ Waiting for results

Next settle in 60 minutes...
"""
    await storage.send_telegram(msg)
    logger.info(f"✅ AUTO PLACED {total_placed} BETS")

async def auto_settle_and_report():
    """АВТОМАТИЧЕСКОЕ отслеживание результатов и отчёты."""
    logger.info("=" * 80)
    logger.info("🤖 AUTO SETTLE & REPORT")
    logger.info("=" * 80)
    
    open_count = len([p for p in storage.picks if p.get("status") == "OPEN"])
    
    if open_count == 0:
        logger.info("No open picks to settle")
        return
    
    # Settle open picks
    storage.auto_settle_picks()
    
    # Get statistics
    stats = storage.get_stats()
    
    # Send report
    msg = f"""
📊 *AUTOMATIC REPORT*

Results from last cycle:
✅ Wins: {stats['wins']}
❌ Losses: {stats['losses']}
⏸️ Pushes: {stats['pushes']}

📈 Statistics:
Total bets: {stats['total_picks']}
Win rate: {stats['win_rate']:.1f}%
Profit: ${stats['profit']:+.2f}
ROI: {stats['roi']:+.1f}%

🤖 Bot is working automatically!
Next scan in 60 minutes...
"""
    await storage.send_telegram(msg)
    logger.info(f"✅ REPORT SENT: {stats['total_picks']} total bets, ${stats['profit']:+.2f}")

async def auto_hourly_status():
    """АВТОМАТИЧЕСКИЙ почасовой статус."""
    stats = storage.get_stats()
    open_picks = len([p for p in storage.picks if p.get("status") == "OPEN"])
    
    msg = f"""
⏰ *HOURLY STATUS*

Open bets: {open_picks}
Total settled: {stats['total_picks']}
Current profit: ${stats['profit']:+.2f}

Win rate: {stats['win_rate']:.1f}%
Hermès: ✅ Analyzing automatically

🤖 Bot is running 24/7!
"""
    await storage.send_telegram(msg)

# ═══════════════════════════════════════════════════════════════════════════
# MANUAL COMMANDS (для справки)
# ═══════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = """
🤖 *BETTING BOT - FULLY AUTOMATIC MODE*

✅ Автоматическое сканирование каждый час
✅ Автоматическое размещение ставок
✅ Автоматическое отслеживание результатов
✅ Автоматические отчеты каждый час

Система работает БЕЗ КОМАНД - всё на автомате!

Смотри отчеты которые приходят каждый час в чат.

/stats - Текущая статистика
"""
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Текущая статистика."""
    stats = storage.get_stats()
    open_picks = len([p for p in storage.picks if p.get("status") == "OPEN"])
    
    msg = f"""
📊 *CURRENT STATISTICS*

Total bets placed: {stats['total_picks']}
✅ Wins: {stats['wins']}
❌ Losses: {stats['losses']}
⏸️ Pushes: {stats['pushes']}

Open bets: {open_picks}
Win rate: {stats['win_rate']:.1f}%
Profit: ${stats['profit']:+.2f}
ROI: {stats['roi']:+.1f}%

🤖 Bot status: ✅ RUNNING AUTOMATICALLY
"""
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = """
📋 *BOT COMMANDS*

/start - Info about auto mode
/stats - Current statistics
/help - This message

Note: Bot works AUTOMATICALLY!
No need to send commands for scanning/betting/settling.

Check your chat for automatic reports every hour.
"""
    await update.message.reply_text(msg, parse_mode="Markdown")

# ═══════════════════════════════════════════════════════════════════════════
# STARTUP & SHUTDOWN
# ═══════════════════════════════════════════════════════════════════════════

async def post_init(app):
    storage.set_app(app)
    
    try:
        await init_hermes()
        logger.info("✅ Hermès ETAP 2 initialized")
    except Exception as e:
        logger.error(f"Hermès init error: {e}")
    
    # Start scheduler
    if not scheduler.running:
        scheduler.start()
        
        # Schedule automatic tasks
        scheduler.add_job(auto_scan_and_place_bets, 'cron', hour='*/1', minute=0)  # Каждый час
        scheduler.add_job(auto_settle_and_report, 'cron', hour='*/1', minute=30)    # Через 30 мин после скана
        scheduler.add_job(auto_hourly_status, 'cron', hour='*', minute=45)           # Каждый час в 45 мин
        
        logger.info("✅ Scheduler started with automatic tasks")
        
        # Immediately run first scan
        await auto_scan_and_place_bets()
    
    msg = """
🤖 *BOT STARTED - AUTOMATIC MODE ACTIVE*

✅ Auto scanning enabled
✅ Auto betting enabled
✅ Auto settling enabled
✅ Auto reports enabled

Schedule:
- :00 - Scan & place bets
- :30 - Settle & send report
- :45 - Hourly status

Watch this chat for automatic updates!
"""
    try:
        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except:
        pass

async def post_stop(app):
    try:
        await shutdown_hermes()
        logger.info("✅ Hermès shutdown")
    except Exception as e:
        logger.error(f"Hermès stop error: {e}")
    
    if scheduler.running:
        scheduler.shutdown()
        logger.info("✅ Scheduler stopped")

# ═══════════════════════════════════════════════════════════════════════════
# MAIN APPLICATION
# ═══════════════════════════════════════════════════════════════════════════

def main():
    logger.info("=" * 80)
    logger.info("🚀 BETTING BOT (ETAP 2) - FULLY AUTOMATIC VERSION")
    logger.info("✅ Auto scan every hour")
    logger.info("✅ Auto place bets")
    logger.info("✅ Auto settle results")
    logger.info("✅ Auto send reports")
    logger.info("=" * 80)
    
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    app.post_init = post_init
    app.post_stop = post_stop
    
    # Manual commands (справка)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("help", cmd_help))
    
    logger.info("Starting bot...")
    app.run_polling()

if __name__ == "__main__":
    main()
    
