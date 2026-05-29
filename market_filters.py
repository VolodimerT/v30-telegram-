"""
Market filtering by sport type
Select only best markets for each sport
"""

MARKET_FILTERS = {
    # Football/Soccer
    "soccer_epl": ["h2h", "spreads", "totals"],
    "soccer_bundesliga": ["h2h", "spreads", "totals"],
    "soccer_la_liga": ["h2h", "spreads", "totals"],
    "soccer_france_ligue_one": ["h2h", "spreads", "totals"],
    "soccer_italy_serie_a": ["h2h", "spreads", "totals"],
    "soccer_fifa_world_cup": ["h2h", "spreads", "totals"],
    
    # Basketball
    "basketball_nba": ["h2h", "spreads", "totals"],
    "basketball_euroleague": ["h2h", "spreads", "totals"],
    
    # Tennis
    "tennis_atp": ["h2h"],
    "tennis_wta": ["h2h"],
    
    # Baseball
    "baseball_mlb": ["h2h", "spreads", "totals"],
    "baseball_npb": ["h2h", "spreads"],
    
    # American Football
    "americanfootball_nfl": ["h2h", "spreads", "totals"],
    
    # Hockey
    "icehockey_nhl": ["h2h", "spreads", "totals"],
    
    # Rugby
    "rugby_union_international": ["h2h", "spreads"],
    
    # Default for unknown sports
    "default": ["h2h", "spreads"]
}

def get_allowed_markets(sport_key):
    """Get allowed markets for a specific sport"""
    return MARKET_FILTERS.get(sport_key, MARKET_FILTERS["default"])

def is_market_allowed(sport_key, market_key):
    """Check if market is allowed for sport"""
    allowed = get_allowed_markets(sport_key)
    return market_key in allowed
