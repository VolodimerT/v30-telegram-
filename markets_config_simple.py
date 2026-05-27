"""
markets_config_simple.py - SIMPLE MARKETS CONFIG (NO ASYNCIO)
Только данные, без кода - просто конфигурация
"""

MARKETS = {
    "football": {
        "primary": ["Match Winner", "Over/Under 2.5", "Asian Handicap", "BTTS"],
        "secondary": ["Correct Score", "First Goal Scorer"],
    },
    "basketball": {
        "primary": ["Match Winner", "Point Spread", "Total Points"],
        "secondary": ["Quarter Markets"],
    },
    "tennis": {
        "primary": ["Match Winner", "Set Betting", "Total Games"],
        "secondary": ["First Set Winner"],
    },
}

# Expanded matches with multiple markets
EXPANDED_MATCHES = {
    "epl": [
        {
            "match": "Chelsea vs Liverpool",
            "home": "Chelsea",
            "away": "Liverpool",
            "sport": "football",
            "markets": {
                "match_winner": 2.10,
                "over_under_2.5": 1.85,
                "asian_handicap": 1.90,
                "btts": 1.75,
            },
        },
        {
            "match": "Man City vs Arsenal",
            "home": "Man City",
            "away": "Arsenal",
            "sport": "football",
            "markets": {
                "match_winner": 1.85,
                "over_under_2.5": 1.90,
                "asian_handicap": 1.80,
                "btts": 1.70,
            },
        },
    ],
    "laliga": [
        {
            "match": "Real Madrid vs Barcelona",
            "home": "Real Madrid",
            "away": "Barcelona",
            "sport": "football",
            "markets": {
                "match_winner": 2.05,
                "over_under_2.5": 1.88,
                "asian_handicap": 1.95,
                "btts": 1.80,
            },
        },
    ],
    "nba": [
        {
            "match": "Lakers vs Celtics",
            "home": "Lakers",
            "away": "Celtics",
            "sport": "basketball",
            "markets": {
                "match_winner": 2.05,
                "point_spread": 1.90,
                "total_points": 1.90,
            },
        },
        {
            "match": "Warriors vs Suns",
            "home": "Warriors",
            "away": "Suns",
            "sport": "basketball",
            "markets": {
                "match_winner": 1.95,
                "point_spread": 1.85,
                "total_points": 1.85,
            },
        },
    ],
    "atp_1000": [
        {
            "match": "Djokovic vs Alcaraz",
            "home": "Djokovic",
            "away": "Alcaraz",
            "sport": "tennis",
            "markets": {
                "match_winner": 2.15,
                "set_betting": 2.05,
                "total_games": 1.95,
            },
        },
    ],
}

def get_markets_for_sport(sport: str) -> dict:
    """Get market config for sport"""
    return MARKETS.get(sport, {})

def get_primary_markets(sport: str) -> list:
    """Get primary markets for sport"""
    return MARKETS.get(sport, {}).get("primary", [])

def get_secondary_markets(sport: str) -> list:
    """Get secondary markets for sport"""
    return MARKETS.get(sport, {}).get("secondary", [])

def get_all_markets(sport: str) -> list:
    """Get all markets for sport"""
    market_config = MARKETS.get(sport, {})
    return market_config.get("primary", []) + market_config.get("secondary", [])
