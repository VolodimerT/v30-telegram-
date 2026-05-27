"""
main_autonomous_AUTO.py - FULLY AUTOMATIC SYSTEM (ALL SPORTS & LEAGUES)
Fixed version - no syntax errors
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
# SPORTS CONFIGURATION - ALL SPORTS & LEAGUES
# ═══════════════════════════════════════════════════════════════════════════

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

# ═══════════════════════════════════════════════════════════════════════════
# MOCK DATA
# ═══════════════════════════════════════════════════════════════════════════

ALL_MATCHES = {
    # FOOTBALL
    "epl": [
        {"match": "Chelsea vs Liverpool", "home": "Chelsea", "away": "Liverpool", "odds": 2.10, "sport": "football"},
        {"match": "Man City vs Arsenal", "home": "Man City", "away": "Arsenal", "odds": 1.85, "sport": "football"},
    ],
    "laliga": [
        {"match": "Real Madrid vs Barcelona", "home": "Real Madrid", "away": "Barcelona", "odds": 2.05, "sport": "football"},
    ],
    "seriea": [
        {"match": "Inter vs AC Milan", "home": "Inter", "away": "AC Milan", "odds": 2.00, "sport": "football"},
    ],
    "bundesliga": [
        {"match": "Bayern Munich vs Dortmund", "home": "Bayern", "away": "Dortmund", "odds": 1.95, "sport": "football"},
    ],
    "ligue1": [
        {"match": "PSG vs Marseille", "home": "PSG", "away": "Marseille", "odds": 1.85, "sport": "football"},
    ],
    "primeira_liga": [
        {"match": "Benfica vs Porto", "home": "Benfica", "away": "Porto", "odds": 2.05, "sport": "football"},
    ],
    "turkish_super": [
        {"match": "Galatasaray vs Fenerbahce", "home": "Galatasaray", "away": "Fenerbahce", "odds": 2.10, "sport": "football"},
    ],
    "russian_premier": [
        {"match": "CSKA vs Lokomotiv", "home": "CSKA", "away": "Lokomotiv", "odds": 1.95, "sport": "football"},
    ],
    "greek_super": [
        {"match": "Olympiacos vs Panathinaikos", "home": "Olympiacos", "away": "Panathinaikos", "odds": 2.15, "sport": "football"},
    ],
    "mls": [
        {"match": "LA Galaxy vs Seattle", "home": "LA Galaxy", "away": "Seattle", "odds": 2.00, "sport": "football"},
    ],
    "jleague": [
        {"match": "Yokohama vs Kawasaki", "home": "Yokohama", "away": "Kawasaki", "odds": 2.05, "sport": "football"},
    ],
    "kleague": [
        {"match": "Seoul FC vs Ulsan", "home": "Seoul FC", "away": "Ulsan", "odds": 2.10, "sport": "football"},
    ],
    "champions_league": [
        {"match": "Champions League SF", "home": "Team A", "away": "Team B", "odds": 1.95, "sport": "football"},
    ],
    "europa_league": [
        {"match": "Europa League SF", "home": "Team C", "away": "Team D", "odds": 2.05, "sport": "football"},
    ],
    "copa_libertadores": [
        {"match": "Flamengo vs Boca", "home": "Flamengo", "away": "Boca", "odds": 2.15, "sport": "football"},
    ],
    "afc_champions": [
        {"match": "Al Hilal vs Ulsan", "home": "Al Hilal", "away": "Ulsan", "odds": 2.00, "sport": "football"},
    ],
    # BASKETBALL
    "nba": [
        {"match": "Lakers vs Celtics", "home": "Lakers", "away": "Celtics", "odds": 2.10, "sport": "basketball"},
    ],
    "nba_playoff": [
        {"match": "NBA Finals Game 1", "home": "Team A", "away": "Team B", "odds": 1.85, "sport": "basketball"},
    ],
    "euroleague": [
        {"match": "Real Madrid vs Barcelona", "home": "Real Madrid", "away": "Barcelona", "odds": 2.05, "sport": "basketball"},
    ],
    "liga_acb": [
        {"match": "Real Madrid vs Valencia", "home": "Real Madrid", "away": "Valencia", "odds": 1.95, "sport": "basketball"},
    ],
    "serie_a_basket": [
        {"match": "Milano vs Virtus", "home": "Milano", "away": "Virtus", "odds": 2.00, "sport": "basketball"},
    ],
    "lnb_pro": [
        {"match": "ASVEL vs Boulogne", "home": "ASVEL", "away": "Boulogne", "odds": 2.10, "sport": "basketball"},
    ],
    "cba": [
        {"match": "Beijing vs Shanghai", "home": "Beijing", "away": "Shanghai", "odds": 2.05, "sport": "basketball"},
    ],
    "nbl": [
        {"match": "Sydney vs Melbourne", "home": "Sydney", "away": "Melbourne", "odds": 2.00, "sport": "basketball"},
    ],
    "bball_japan": [
        {"match": "Kawasaki vs Chiba", "home": "Kawasaki", "away": "Chiba", "odds": 2.10, "sport": "basketball"},
    ],
    "ncaa_march": [
        {"match": "Duke vs UNC", "home": "Duke", "away": "UNC", "odds": 1.95, "sport": "basketball"},
    ],
    # TENNIS
    "australian_open": [
        {"match": "Djokovic vs Sinner", "home": "Djokovic", "away": "Sinner", "odds": 2.15, "sport": "tennis"},
    ],
    "french_open": [
        {"match": "Alcaraz vs Sinner", "home": "Alcaraz", "away": "Sinner", "odds": 2.05, "sport": "tennis"},
    ],
    "wimbledon": [
        {"match": "Alcaraz vs Medvedev", "home": "Alcaraz", "away": "Medvedev", "odds": 1.95, "sport": "tennis"},
    ],
    "us_open": [
        {"match": "Alcaraz vs Djokovic", "home": "Alcaraz", "away": "Djokovic", "odds": 2.00, "sport": "tennis"},
    ],
    "atp_1000": [
        {"match": "ATP Masters 1000", "home": "Player A", "away": "Player B", "odds": 2.10, "sport": "tennis"},
    ],
    "atp_500": [
        {"match": "ATP 500 Tournament", "home": "Player C", "away": "Player D", "odds": 1.95, "sport": "tennis"},
    ],
    "atp_250": [
        {"match": "ATP 250 Tournament", "home": "Player E", "away": "Player F", "odds": 2.05, "sport": "tennis"},
    ],
    "wta_1000": [
        {"match": "WTA 1000 Masters", "home": "Player G", "away": "Player H", "odds": 2.00, "sport": "tennis"},
    ],
    "wta_500": [
        {"match": "WTA 500 Tournament", "home": "Player I", "away": "Player J", "odds": 2.10, "sport": "tennis"},
    ],
    "wta_250": [
        {"match": "WTA 250 Tournament", "home": "Player K", "away": "Player L", "odds": 1.95, "sport": "tennis"},
    ],
    "challenger": [
        {"match": "ATP Challenger", "home": "Player M", "away": "Player N", "odds": 2.05, "sport": "tennis"},
    ],
    "davis_cup": [
        {"match": "Davis Cup Tie", "home": "Country A", "away": "Country B", "odds": 2.00, "sport": "tennis"},
    ],
    "billie_jean_cup": [
        {"match": "Billie Jean King Cup", "home": "Country C", "away": "Country D", "odds": 2.10, "sport": "tennis"},
    ],
}

# ═══════════════════════════════════════════════════════════════════════════
# STORAGE
# ═══════════════════════════════════════════════════════════════════════════

class AutoBetStorage:
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
        open_picks = [p for p in self.picks if p.get("status") == "OPEN"]
        
        for pick in open_picks:
            result = random.choice(["WIN", "LOSS", "PUSH"])
            odds = pick["odds"]
            stake = pick["stake"]
            
            if result == "WIN":
                pnl = stake * (odds - 1)
            elif result == "LOSS":
                pnl = -stake
            else:
                pnl = 0
            
            pick["status"] = "SETTLED"
            pick["result"] = result
            pick["pnl"] = round(pnl, 2)
            pick["settled_at"] = datetime.now(UTC).isoformat()
            
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
                "by_sport": {},
            }
        
        wins = sum(1 for r in self.results if r.get("result") == "WIN")
        losses = sum(1 for r in self.results if r.get("result") == "LOSS")
        pushes = sum(1 for r in self.results if r.get("result") == "PUSH")
        profit = sum(r.get("pnl", 0) for r in self.results)
        total = len(self.results)
        win_rate = (wins / total * 100) if total > 0 else 0
        roi = (profit / (total * 50)) * 100 if total > 0 else 0
        
        by_sport = {}
        for sport in ["football", "basketball", "tennis"]:
            sport_results = [r for r in self.results if r.get("sport") == sport]
            sport_wins = sum(1 for r in sport_results if r.get("result") == "WIN")
            sport_total = len(sport_results)
            by_sport[sport] = {
                "total": sport_total,
                "wins": sport_wins,
                "win_rate": (sport_wins / sport_total * 100) if sport_total > 0 else 0,
            }
        
        return {
            "total_picks": total,
            "wins": wins,
            "losses": losses,
            "pushes": pushes,
            "profit": profit,
            "roi": roi,
            "win_rate": win_rate,
            "by_sport": by_sport,
        }
    
    async def send_telegram(self, msg: str):
        if self.app and CHAT_ID:
            try:
                await self.app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Telegram error: {e}")

storage = AutoBetStorage()

# ═══════════════════════════════════════════════════════════════════════════
# AUTOMATIC TASKS
# ═══════════════════════════════════════════════════════════════════════════

async def auto_scan_and_place_bets():
    logger.info("=" * 80)
    logger.info("AUTO SCAN - ALL SPORTS & LEAGUES")
    logger.info("=" * 80)
    
    total_placed = 0
    
    for league, matches in ALL_MATCHES.items():
        sport = None
        for sport_key in SPORTS_CONFIG.keys():
            if league in SPORTS_CONFIG[sport_key]["leagues"]:
                sport = sport_key
                break
        
        if not sport or not SPORTS_CONFIG[sport]["enabled"]:
            continue
        
        logger.info(f"Scanning {sport.upper()} - {league}...")
        
        picks = []
        for match_data in matches:
            odds = match_data.get("odds", 1.5)
            if odds < SPORTS_CONFIG[sport]["min_odds"]:
                continue
            
            pick = {
                "match": match_data["match"],
                "sport": sport,
                "league": league,
                "home": match_data.get("home", ""),
                "away": match_data.get("away", ""),
                "odds": odds,
                "stake": 50,
                "selection": f"{match_data.get('home', 'Home')} Win",
                "confidence": 0.65,
            }
            picks.append(pick)
        
        if not picks:
            continue
        
        enriched = await enrich_picks_with_hermes(picks, mode="NORMAL")
        
        for pick in enriched["enriched_picks"]:
            if pick.get("hermes_recommendation") == "ACCEPT":
                storage.add_pick(pick)
                total_placed += 1
            elif pick.get("hermes_recommendation") == "RECONSIDER":
                pick["stake"] = int(pick["stake"] * 0.5)
                storage.add_pick(pick)
                total_placed += 1
    
    msg = f"AUTO SCAN COMPLETE - Total picks: {total_placed}\nNext settle in 60 minutes..."
    await storage.send_telegram(msg)
    logger.info(f"AUTO PLACED {total_placed} BETS")

async def auto_settle_and_report():
    logger.info("=" * 80)
    logger.info("AUTO SETTLE & REPORT")
    logger.info("=" * 80)
    
    open_count = len([p for p in storage.picks if p.get("status") == "OPEN"])
    
    if open_count == 0:
        logger.info("No open picks to settle")
        return
    
    storage.auto_settle_picks()
    stats = storage.get_stats()
    
    msg = f"REPORT - Wins: {stats['wins']}, Losses: {stats['losses']}, Profit: ${stats['profit']:+.2f}"
    await storage.send_telegram(msg)
    logger.info(f"REPORT SENT")

async def auto_hourly_status():
    stats = storage.get_stats()
    open_picks = len([p for p in storage.picks if p.get("status") == "OPEN"])
    
    msg = f"STATUS - Open: {open_picks}, Profit: ${stats['profit']:+.2f}"
    await storage.send_telegram(msg)

# ═══════════════════════════════════════════════════════════════════════════
# COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "BOT RUNNING - AUTO MODE ACTIVE"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = storage.get_stats()
    msg = f"Stats: {stats['total_picks']} bets, {stats['wins']} wins, Profit: ${stats['profit']:+.2f}"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "/start - Info\n/stats - Statistics\n/help - Help"
    await update.message.reply_text(msg, parse_mode="Markdown")

# ═══════════════════════════════════════════════════════════════════════════
# STARTUP & SHUTDOWN
# ═══════════════════════════════════════════════════════════════════════════

async def post_init(app):
    storage.set_app(app)
    
    try:
        await init_hermes()
        logger.info("Hermes initialized")
    except Exception as e:
        logger.error(f"Hermes error: {e}")
    
    if not scheduler.running:
        scheduler.start()
        
        scheduler.add_job(auto_scan_and_place_bets, 'cron', hour='*/1', minute=0)
        scheduler.add_job(auto_settle_and_report, 'cron', hour='*/1', minute=30)
        scheduler.add_job(auto_hourly_status, 'cron', hour='*', minute=45)
        
        logger.info("Scheduler started")
        await auto_scan_and_place_bets()
    
    msg = "BOT STARTED - COMPREHENSIVE SPORTS COVERAGE"
    try:
        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except:
        pass

async def post_stop(app):
    try:
        await shutdown_hermes()
        logger.info("Hermes shutdown")
    except Exception as e:
        logger.error(f"Hermes stop error: {e}")
    
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler stopped")

# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    logger.info("=" * 80)
    logger.info("BETTING BOT - FULLY AUTOMATIC VERSION")
    logger.info("COVERAGE: Football (16 leagues) + Basketball (10 leagues) + Tennis (13 tournaments)")
    logger.info("=" * 80)
    
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    app.post_init = post_init
    app.post_stop = post_stop
    
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("help", cmd_help))
    
    logger.info("Starting bot...")
    app.run_polling()

if __name__ == "__main__":
    main()
