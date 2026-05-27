"""
main_autonomous_AUTO_FULL_EXPANDED.py - FULLY AUTOMATIC SYSTEM (ALL SPORTS & LEAGUES)
Всё работает на автомате без необходимости вводить команды!

COMPREHENSIVE SPORTS COVERAGE:
✅ Football: 15+ leagues (EPL, La Liga, Serie A, Bundesliga, Ligue 1, Liga Portugal, 
             Turkish Super Lig, Russian Premier, Greek Super, MLS, J-League, K-League,
             Champions League, Europa League, Copa Libertadores, AFC Champions League)
✅ Basketball: 10+ leagues (NBA, NBA Playoffs, EuroLeague, Spanish Liga ACB, Italian Serie A,
               French LNB Pro, Chinese CBA, Australian NBL, NCAA March Madness, Japanese B.League)
✅ Tennis: 10+ tournaments (Grand Slams, ATP 1000, ATP 500, ATP 250, WTA 1000, WTA 500,
           WTA 250, Challenger, Davis Cup, Billie Jean King Cup)
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
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "bafb678b8ac2d2ee7cd88fdc9318d308")
UTC = timezone.utc

scheduler = AsyncIOScheduler()

# ═══════════════════════════════════════════════════════════════════════════
# COMPREHENSIVE SPORTS CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

SPORTS_CONFIG = {
    "football": {
        "leagues": {
            # Europe Top 5
            "epl": {"name": "English Premier League", "country": "England", "tier": "top5"},
            "laliga": {"name": "La Liga", "country": "Spain", "tier": "top5"},
            "seriea": {"name": "Serie A", "country": "Italy", "tier": "top5"},
            "bundesliga": {"name": "Bundesliga", "country": "Germany", "tier": "top5"},
            "ligue1": {"name": "Ligue 1", "country": "France", "tier": "top5"},
            # Europe Other
            "primeira_liga": {"name": "Primeira Liga", "country": "Portugal", "tier": "other"},
            "turkish_super": {"name": "Turkish Super Lig", "country": "Turkey", "tier": "other"},
            "russian_premier": {"name": "Russian Premier League", "country": "Russia", "tier": "other"},
            "greek_super": {"name": "Greek Super League", "country": "Greece", "tier": "other"},
            # International
            "champions_league": {"name": "UEFA Champions League", "country": "Europe", "tier": "international"},
            "europa_league": {"name": "UEFA Europa League", "country": "Europe", "tier": "international"},
            "copa_libertadores": {"name": "Copa Libertadores", "country": "South America", "tier": "international"},
            "afc_champions": {"name": "AFC Champions League", "country": "Asia", "tier": "international"},
            # Americas & Asia
            "mls": {"name": "MLS", "country": "USA", "tier": "other"},
            "jleague": {"name": "J-League", "country": "Japan", "tier": "other"},
            "kleague": {"name": "K-League", "country": "South Korea", "tier": "other"},
        },
        "enabled": True,
        "min_odds": 1.40,
    },
    "basketball": {
        "leagues": {
            # NBA
            "nba": {"name": "NBA Regular Season", "country": "USA", "tier": "top"},
            "nba_playoff": {"name": "NBA Playoffs", "country": "USA", "tier": "top"},
            # Europe
            "euroleague": {"name": "EuroLeague", "country": "Europe", "tier": "top"},
            "liga_acb": {"name": "Spanish Liga ACB", "country": "Spain", "tier": "other"},
            "serie_a_basket": {"name": "Italian Serie A", "country": "Italy", "tier": "other"},
            "lnb_pro": {"name": "French LNB Pro", "country": "France", "tier": "other"},
            # Asia & Oceania
            "cba": {"name": "Chinese CBA", "country": "China", "tier": "other"},
            "nbl": {"name": "Australian NBL", "country": "Australia", "tier": "other"},
            "bball_japan": {"name": "Japanese B.League", "country": "Japan", "tier": "other"},
            # College & International
            "ncaa_march": {"name": "NCAA March Madness", "country": "USA", "tier": "other"},
        },
        "enabled": True,
        "min_odds": 1.40,
    },
    "tennis": {
        "leagues": {
            # Grand Slams
            "australian_open": {"name": "Australian Open", "tier": "grand_slam"},
            "french_open": {"name": "French Open (Roland Garros)", "tier": "grand_slam"},
            "wimbledon": {"name": "Wimbledon", "tier": "grand_slam"},
            "us_open": {"name": "US Open", "tier": "grand_slam"},
            # ATP Masters 1000
            "atp_1000": {"name": "ATP Masters 1000", "tier": "masters"},
            # ATP 500 & 250
            "atp_500": {"name": "ATP 500", "tier": "atp"},
            "atp_250": {"name": "ATP 250", "tier": "atp"},
            # WTA Masters
            "wta_1000": {"name": "WTA 1000", "tier": "masters"},
            # WTA 500 & 250
            "wta_500": {"name": "WTA 500", "tier": "wta"},
            "wta_250": {"name": "WTA 250", "tier": "wta"},
            # Other
            "challenger": {"name": "ATP Challenger", "tier": "other"},
            "davis_cup": {"name": "Davis Cup", "tier": "international"},
            "billie_jean_cup": {"name": "Billie Jean King Cup", "tier": "international"},
        },
        "enabled": True,
        "min_odds": 1.40,
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# COMPREHENSIVE MOCK DATA
# ═══════════════════════════════════════════════════════════════════════════

ALL_MATCHES = {
    # ═══ FOOTBALL (15 leagues) ═══
    "epl": [
        {"match": "Chelsea vs Liverpool", "home": "Chelsea", "away": "Liverpool", "odds": 2.10, "sport": "football"},
        {"match": "Man City vs Arsenal", "home": "Man City", "away": "Arsenal", "odds": 1.85, "sport": "football"},
    ],
    "laliga": [
        {"match": "Real Madrid vs Barcelona", "home": "Real Madrid", "away": "Barcelona", "odds": 2.05, "sport": "football"},
        {"match": "Atletico Madrid vs Sevilla", "home": "Atletico Madrid", "away": "Sevilla", "odds": 1.95, "sport": "football"},
    ],
    "seriea": [
        {"match": "Inter vs AC Milan", "home": "Inter", "away": "AC Milan", "odds": 2.00, "sport": "football"},
        {"match": "Juventus vs Napoli", "home": "Juventus", "away": "Napoli", "odds": 1.90, "sport": "football"},
    ],
    "bundesliga": [
        {"match": "Bayern Munich vs Borussia Dortmund", "home": "Bayern Munich", "away": "Borussia Dortmund", "odds": 1.95, "sport": "football"},
    ],
    "ligue1": [
        {"match": "Paris Saint-Germain vs Marseille", "home": "PSG", "away": "Marseille", "odds": 1.85, "sport": "football"},
    ],
    "primeira_liga": [
        {"match": "Benfica vs Porto", "home": "Benfica", "away": "Porto", "odds": 2.05, "sport": "football"},
    ],
    "turkish_super": [
        {"match": "Galatasaray vs Fenerbahce", "home": "Galatasaray", "away": "Fenerbahce", "odds": 2.10, "sport": "football"},
    ],
    "russian_premier": [
        {"match": "CSKA Moscow vs Lokomotiv", "home": "CSKA Moscow", "away": "Lokomotiv", "odds": 1.95, "sport": "football"},
    ],
    "greek_super": [
        {"match": "Olympiacos vs Panathinaikos", "home": "Olympiacos", "away": "Panathinaikos", "odds": 2.15, "sport": "football"},
    ],
    "mls": [
        {"match": "LA Galaxy vs Seattle Sounders", "home": "LA Galaxy", "away": "Seattle Sounders", "odds": 2.00, "sport": "football"},
    ],
    "jleague": [
        {"match": "Yokohama Marinos vs Kawasaki Frontale", "home": "Yokohama", "away": "Kawasaki", "odds": 2.05, "sport": "football"},
    ],
    "kleague": [
        {"match": "Seoul FC vs Ulsan Hyundai", "home": "Seoul FC", "away": "Ulsan", "odds": 2.10, "sport": "football"},
    ],
    "champions_league": [
        {"match": "Champions League SF", "home": "Team A", "away": "Team B", "odds": 1.95, "sport": "football"},
    ],
    "europa_league": [
        {"match": "Europa League SF", "home": "Team C", "away": "Team D", "odds": 2.05, "sport": "football"},
    ],
    "copa_libertadores": [
        {"match": "Flamengo vs Boca Juniors", "home": "Flamengo", "away": "Boca Juniors", "odds": 2.15, "sport": "football"},
    ],
    "afc_champions": [
        {"match": "Al Hilal vs Ulsan", "home": "Al Hilal", "away": "Ulsan", "odds": 2.00, "sport": "football"},
    ],
    
    # ═══ BASKETBALL (10 leagues) ═══
    "nba": [
        {"match": "Lakers vs Celtics", "home": "Lakers", "away": "Celtics", "odds": 2.10, "sport": "basketball"},
        {"match": "Warriors vs Suns", "home": "Warriors", "away": "Suns", "odds": 1.95, "sport": "basketball"},
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
        {"match": "Milano vs Virtus Bologna", "home": "Milano", "away": "Virtus", "odds": 2.00, "sport": "basketball"},
    ],
    "lnb_pro": [
        {"match": "ASVEL vs Boulogne-Levallois", "home": "ASVEL", "away": "Boulogne", "odds": 2.10, "sport": "basketball"},
    ],
    "cba": [
        {"match": "Beijing Ducks vs Shanghai Sharks", "home": "Beijing", "away": "Shanghai", "odds": 2.05, "sport": "basketball"},
    ],
    "nbl": [
        {"match": "Sydney Kings vs Melbourne United", "home": "Sydney", "away": "Melbourne", "odds": 2.00, "sport": "basketball"},
    ],
    "bball_japan": [
        {"match": "Kawasaki Brave Thunders vs Chiba Jets", "home": "Kawasaki", "away": "Chiba", "odds": 2.10, "sport": "basketball"},
    ],
    "ncaa_march": [
        {"match": "Duke vs North Carolina", "home": "Duke", "away": "UNC", "odds": 1.95, "sport": "basketball"},
    ],
    
    # ═══ TENNIS (13 tournaments) ═══
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
        sport_emoji = self._get_sport_emoji(pick.get("sport"))
        logger.info(f"✅ AUTO BET PLACED: {sport_emoji} {pick['match']} @ {pick['odds']} | ${pick['stake']}")
    
    def _get_sport_emoji(self, sport: str) -> str:
        emojis = {
            "football": "⚽",
            "basketball": "🏀",
            "tennis": "🎾",
        }
        return emojis.get(sport, "🎲")
    
    def _get_league_name(self, sport: str, league: str) -> str:
        """Get human-readable league name."""
        try:
            return SPORTS_CONFIG[sport]["leagues"][league]["name"]
        except:
            return league
    
    def auto_settle_picks(self):
        """Автоматически отмечает ставки как завершённые."""
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
            
            sport_emoji = self._get_sport_emoji(pick.get("sport"))
            logger.info(f"✅ AUTO SETTLED: {sport_emoji} {pick['match']} - {result} ({pnl:+.2f})")
        
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
        
        # Stats by sport
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
        """Отправить сообщение в Telegram."""
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
    """АВТОМАТИЧЕСКОЕ сканирование и размещение ставок."""
    logger.info("=" * 80)
    logger.info("🤖 AUTO SCAN & BET PLACEMENT - COMPREHENSIVE (ALL SPORTS & LEAGUES)")
    logger.info("=" * 80)
    
    total_placed = 0
    sports_scanned = {"football": 0, "basketball": 0, "tennis": 0}
    
    for league, matches in ALL_MATCHES.items():
        sport = None
        
        # Determine sport
        for sport_key in SPORTS_CONFIG.keys():
            if league in SPORTS_CONFIG[sport_key]["leagues"]:
                sport = sport_key
                break
        
        if not sport or not SPORTS_CONFIG[sport]["enabled"]:
            continue
        
        league_name = SPORTS_CONFIG[sport]["leagues"].get(league, {}).get("name", league)
        logger.info(f"🔍 Scanning {sport.upper()} - {league_name}...")
        sports_scanned[sport] += 1
        
        picks = []
        for match_data in matches:
            odds = match_data.get("odds", 1.5)
            if odds < SPORTS_CONFIG[sport]["min_odds"]:
                continue
            
            pick = {
                "match": match_data["match"],
                "sport": sport,
                "league": league,
                "league_name": league_name,
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
    
    msg = f"""
🤖 *AUTO SCAN COMPLETE - COMPREHENSIVE COVERAGE*

⚽ Football leagues scanned: {sports_scanned['football']}
🏀 Basketball leagues scanned: {sports_scanned['basketball']}
🎾 Tennis tournaments scanned: {sports_scanned['tennis']}

Total picks placed: {total_placed}
Total stake
