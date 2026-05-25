import aiohttp
import asyncio
import logging
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import json
from pathlib import Path

logger = logging.getLogger('scanner')

class OddsAPI:
    def __init__(self, api_key: str, base_url: str = "https://api.the-odds-api.com/v4"):
        self.api_key = api_key
        self.base_url = base_url
        self.cache_file = Path("data/odds_cache.json")
        self.cache_ttl = 300
        
    async def get_available_sports(self) -> List[str]:
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.base_url}/sports"
                params = {"apiKey": self.api_key}
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        sports = [s['key'] for s in data if not s['key'].startswith('archived_')]
                        logger.info(f"📊 Found {len(sports)} available sports")
                        return sports
                    else:
                        logger.error(f"❌ API Error: {resp.status}")
                        return []
        except Exception as e:
            logger.error(f"❌ Error fetching sports: {e}")
            return []
    
    async def get_events(self, sport_key: str, markets: List[str] = None, region: str = "us") -> List[Dict]:
        if markets is None:
            markets = ["h2h", "spreads", "totals"]
        
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.base_url}/sports/{sport_key}/events"
                params = {
                    "apiKey": self.api_key,
                    "regions": region,
                    "markets": ",".join(markets),
                    "oddsFormat": "decimal"
                }
                
                async with session.get(url, params=params, timeout=30) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        events = data.get('data', [])
                        logger.info(f"📊 {sport_key}: {len(events)} events fetched")
                        return events
                    else:
                        logger.error(f"❌ API Error for {sport_key}: {resp.status}")
                        return []
        except Exception as e:
            logger.error(f"❌ Error fetching events for {sport_key}: {e}")
            return []
    
    async def get_odds_for_event(self, event_id: str, sport_key: str, markets: List[str] = None) -> Dict:
        if markets is None:
            markets = ["h2h"]
        
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.base_url}/sports/{sport_key}/events/{event_id}"
                params = {
                    "apiKey": self.api_key,
                    "markets": ",".join(markets),
                    "oddsFormat": "decimal",
                    "bookmakers": "all"
                }
                
                async with session.get(url, params=params, timeout=15) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get('data', {})
                    else:
                        logger.warning(f"⚠️ Could not fetch odds for {event_id}")
                        return {}
        except Exception as e:
            logger.error(f"❌ Error fetching odds for {event_id}: {e}")
            return {}
    
    def filter_alive_events(self, events: List[Dict], min_hours_until: float = 0.5, max_hours_until: float = 336) -> List[Dict]:
        now = datetime.now(datetime.now().astimezone().tzinfo)
        alive = []
        
        for event in events:
            try:
                commence_time = datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00'))
                if commence_time.tzinfo is None:
                    continue
                
                seconds_until = (commence_time - now).total_seconds()
                hours_until = seconds_until / 3600
                
                if min_hours_until <= hours_until <= max_hours_until:
                    alive.append({**event, '_hours_until': hours_until})
            except Exception as e:
                logger.debug(f"⚠️ Error parsing event time: {e}")
                continue
        
        alive.sort(key=lambda x: x['_hours_until'])
        return alive


class EventAnalyzer:
    @staticmethod
    def get_best_odds(event: Dict, market: str = "h2h") -> Dict:
        if 'bookmakers' not in event or not event['bookmakers']:
            return {}
        
        best_odds_map = {}
        
        for bookmaker in event['bookmakers']:
            bookie_name = bookmaker['title']
            
            for market_data in bookmaker.get('markets', []):
                if market_data['key'] != market:
                    continue
                
                for outcome in market_data.get('outcomes', []):
                    selection = outcome['name']
                    odds = float(outcome.get('price', 0))
                    
                    if selection not in best_odds_map:
                        best_odds_map[selection] = {
                            'odds': odds,
                            'bookmaker': bookie_name,
                            'books': [bookie_name]
                        }
                    else:
                        if odds > best_odds_map[selection]['odds']:
                            best_odds_map[selection]['odds'] = odds
                            best_odds_map[selection]['bookmaker'] = bookie_name
                        
                        if bookie_name not in best_odds_map[selection]['books']:
                            best_odds_map[selection]['books'].append(bookie_name)
        
        return best_odds_map
    
    @staticmethod
    def calculate_ev(odds: float, win_probability: float, commission: float = 0.03) -> float:
        expected_return = (win_probability * odds) - 1
        ev = expected_return - commission
        return round(ev * 100, 2)
    
    @staticmethod
    def get_implied_probability(odds: float) -> float:
        if odds <= 0:
            return 0
        return round((1 / odds) * 100, 2)
