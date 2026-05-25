import logging

logger = logging.getLogger('scanner')

class OddsAPI:
    def __init__(self, api_key: str, base_url: str = "https://api.the-odds-api.com/v4"):
        self.api_key = api_key
        self.base_url = base_url
    
    async def get_events(self, sport_key: str, markets=None, region="us"):
        return []
    
    def filter_alive_events(self, events, min_hours_until=0.5, max_hours_until=336):
        return []

class EventAnalyzer:
    @staticmethod
    def calculate_ev(odds: float, win_probability: float, commission: float = 0.03) -> float:
        expected_return = (win_probability * odds) - 1
        ev = expected_return - commission
        return round(ev * 100, 2)
