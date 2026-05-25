import logging

logger = logging.getLogger('perplexity')

class PerplexityAnalyzer:
    def __init__(self, api_key: str):
        self.api_key = api_key
    
    async def analyze_match(self, team1: str, team2: str, sport: str, date: str, custom_request=None):
        return {
            'probability': 50.0,
            'recommendation': 'MICRO',
            'analysis': 'Analysis unavailable',
            'red_flags': 'None'
        }

class AnalysisUtils:
    @staticmethod
    def has_red_flags(analysis: dict) -> bool:
        return False
