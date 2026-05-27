"""
markets_config.py - EXPANDED MARKETS CONFIGURATION
Полная конфигурация всех доступных маркетов для всех спортов
"""

# ═══════════════════════════════════════════════════════════════════════════
# MARKETS CONFIGURATION - ВСЕ МАРКЕТЫ ДЛЯ ВСЕХ СПОРТОВ
# ═══════════════════════════════════════════════════════════════════════════

MARKETS_CONFIG = {
    # ═══════════════════════════════════════════════════════════════════════
    # ⚽ FOOTBALL MARKETS
    # ═══════════════════════════════════════════════════════════════════════
    "football": {
        "primary_markets": [
            {
                "name": "match_winner",
                "display": "Match Winner (1X2)",
                "selections": ["Home", "Draw", "Away"],
                "priority": 1,
                "min_odds": 1.40,
                "hermes_weight": 0.35,
            },
            {
                "name": "over_under",
                "display": "Over/Under Goals",
                "thresholds": [1.5, 2.5, 3.5, 4.5],
                "selections": ["Over", "Under"],
                "priority": 2,
                "min_odds": 1.50,
                "hermes_weight": 0.30,
            },
            {
                "name": "asian_handicap",
                "display": "Asian Handicap",
                "lines": [-0.5, -1.0, -1.5, -2.0, +0.5, +1.0, +1.5, +2.0],
                "priority": 3,
                "min_odds": 1.70,
                "hermes_weight": 0.20,
            },
            {
                "name": "btts",
                "display": "Both Teams To Score",
                "selections": ["Yes", "No"],
                "priority": 4,
                "min_odds": 1.50,
                "hermes_weight": 0.10,
            },
            {
                "name": "correct_score",
                "display": "Correct Score",
                "priority": 5,
                "min_odds": 2.00,
                "hermes_weight": 0.05,
            },
        ],
        "secondary_markets": [
            {
                "name": "first_goal",
                "display": "First Goal Scorer",
                "priority": 6,
                "min_odds": 1.80,
            },
            {
                "name": "goal_line",
                "display": "Goal Line Markets (0.5, 1.5, 2.5)",
                "priority": 7,
                "min_odds": 1.60,
            },
            {
                "name": "ht_ft",
                "display": "HT/FT Combinations",
                "priority": 8,
                "min_odds": 2.50,
            },
        ],
        "enabled": True,
        "analyze_depth": "FULL",  # FULL, STANDARD, BASIC
    },
    
    # ═══════════════════════════════════════════════════════════════════════
    # 🏀 BASKETBALL MARKETS
    # ═══════════════════════════════════════════════════════════════════════
    "basketball": {
        "primary_markets": [
            {
                "name": "match_winner",
                "display": "Match Winner (MoneyLine)",
                "selections": ["Home", "Away"],
                "priority": 1,
                "min_odds": 1.40,
                "hermes_weight": 0.35,
            },
            {
                "name": "point_spread",
                "display": "Point Spread",
                "lines": [-2.5, -3.5, -4.5, -5.5, +2.5, +3.5, +4.5, +5.5],
                "priority": 2,
                "min_odds": 1.90,
                "hermes_weight": 0.30,
            },
            {
                "name": "total_points",
                "display": "Total Points (Over/Under)",
                "thresholds": [200, 210, 220, 230],
                "selections": ["Over", "Under"],
                "priority": 3,
                "min_odds": 1.85,
                "hermes_weight": 0.25,
            },
            {
                "name": "quarter_markets",
                "display": "Quarter Markets",
                "priority": 4,
                "min_odds": 1.80,
                "hermes_weight": 0.05,
            },
        ],
        "secondary_markets": [
            {
                "name": "player_props",
                "display": "Player Props (Points, Rebounds, Assists)",
                "priority": 5,
                "min_odds": 1.70,
            },
            {
                "name": "halftime_spread",
                "display": "Halftime Spread",
                "priority": 6,
                "min_odds": 1.85,
            },
        ],
        "enabled": True,
        "analyze_depth": "FULL",
    },
    
    # ═══════════════════════════════════════════════════════════════════════
    # 🎾 TENNIS MARKETS
    # ═══════════════════════════════════════════════════════════════════════
    "tennis": {
        "primary_markets": [
            {
                "name": "match_winner",
                "display": "Match Winner",
                "selections": ["Player 1", "Player 2"],
                "priority": 1,
                "min_odds": 1.40,
                "hermes_weight": 0.40,
            },
            {
                "name": "set_betting",
                "display": "Set Betting (2-0, 2-1, 1-2, 0-2)",
                "priority": 2,
                "min_odds": 1.60,
                "hermes_weight": 0.30,
            },
            {
                "name": "total_games",
                "display": "Total Games (Over/Under)",
                "thresholds": [20, 22, 24, 26, 28],
                "selections": ["Over", "Under"],
                "priority": 3,
                "min_odds": 1.70,
                "hermes_weight": 0.20,
            },
            {
                "name": "correct_score_set",
                "display": "Correct Score Set",
                "priority": 4,
                "min_odds": 2.00,
                "hermes_weight": 0.10,
            },
        ],
        "secondary_markets": [
            {
                "name": "first_set_winner",
                "display": "First Set Winner",
                "priority": 5,
                "min_odds": 1.60,
            },
            {
                "name": "game_betting",
                "display": "Game Betting",
                "priority": 6,
                "min_odds": 1.85,
            },
        ],
        "enabled": True,
        "analyze_depth": "FULL",
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# MARKET SELECTION RULES - ПРАВИЛА ВЫБОРА МАРКЕТОВ
# ═══════════════════════════════════════════════════════════════════════════

MARKET_SELECTION_RULES = {
    "football": {
        "preferred_markets": ["match_winner", "over_under"],  # Основные
        "secondary_markets": ["asian_handicap", "btts"],  # Если низкий EV в основных
        "avoid_markets": ["correct_score"],  # Слишком высокие маржи
        "liquidity_threshold": 10000,  # Мин ликвидность в USD
    },
    "basketball": {
        "preferred_markets": ["match_winner", "point_spread", "total_points"],
        "secondary_markets": ["quarter_markets"],
        "avoid_markets": ["player_props"],
        "liquidity_threshold": 5000,
    },
    "tennis": {
        "preferred_markets": ["match_winner", "set_betting"],
        "secondary_markets": ["total_games"],
        "avoid_markets": ["game_betting"],
        "liquidity_threshold": 2000,
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# HERMÈS ANALYSIS PARAMETERS FOR EACH MARKET
# ═══════════════════════════════════════════════════════════════════════════

HERMES_MARKET_PARAMS = {
    "football": {
        "match_winner": {
            "min_ev": 0.10,  # Minimum 10% EV
            "min_odds": 1.50,
            "form_weight": 0.25,
            "h2h_weight": 0.20,
            "home_advantage": 0.06,
        },
        "over_under": {
            "min_ev": 0.08,
            "min_odds": 1.55,
            "goal_expectancy_weight": 0.40,
            "team_stats_weight": 0.35,
            "recent_trends_weight": 0.25,
        },
        "asian_handicap": {
            "min_ev": 0.12,
            "min_odds": 1.70,
            "form_weight": 0.30,
            "strength_difference": 0.40,
        },
        "btts": {
            "min_ev": 0.10,
            "min_odds": 1.60,
            "attack_defense_weight": 0.40,
            "historical_btts_rate": 0.35,
        },
    },
    "basketball": {
        "match_winner": {
            "min_ev": 0.10,
            "min_odds": 1.45,
            "form_weight": 0.30,
            "strength_weight": 0.35,
            "home_advantage": 0.04,
        },
        "point_spread": {
            "min_ev": 0.12,
            "min_odds": 1.85,
            "team_stats_weight": 0.40,
            "matchup_weight": 0.30,
        },
        "total_points": {
            "min_ev": 0.10,
            "min_odds": 1.80,
            "pace_weight": 0.40,
            "defense_weight": 0.35,
        },
    },
    "tennis": {
        "match_winner": {
            "min_ev": 0.10,
            "min_odds": 1.50,
            "ranking_weight": 0.30,
            "h2h_weight": 0.25,
            "surface_weight": 0.25,
            "form_weight": 0.20,
        },
        "set_betting": {
            "min_ev": 0.15,
            "min_odds": 1.70,
            "serve_weight": 0.35,
            "return_weight": 0.25,
        },
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def get_primary_markets(sport: str) -> list:
    """Get primary markets for a sport"""
    if sport in MARKETS_CONFIG:
        return MARKETS_CONFIG[sport]["primary_markets"]
    return []

def get_all_markets(sport: str) -> list:
    """Get all markets (primary + secondary) for a sport"""
    if sport in MARKETS_CONFIG:
        primary = MARKETS_CONFIG[sport].get("primary_markets", [])
        secondary = MARKETS_CONFIG[sport].get("secondary_markets", [])
        return primary + secondary
    return []

def get_preferred_markets(sport: str) -> list:
    """Get preferred markets for a sport"""
    if sport in MARKET_SELECTION_RULES:
        return MARKET_SELECTION_RULES[sport]["preferred_markets"]
    return []

def get_hermes_params(sport: str, market: str) -> dict:
    """Get Hermès analysis parameters for a specific market"""
    if sport in HERMES_MARKET_PARAMS:
        if market in HERMES_MARKET_PARAMS[sport]:
            return HERMES_MARKET_PARAMS[sport][market]
    return {"min_ev": 0.10, "min_odds": 1.50}

def get_market_weight(sport: str, market_name: str) -> float:
    """Get priority weight of a market"""
    markets = get_all_markets(sport)
    for market in markets:
        if market["name"] == market_name:
            return market.get("hermes_weight", 0.1)
    return 0.1

# ═══════════════════════════════════════════════════════════════════════════
# QUICK REFERENCE
# ═══════════════════════════════════════════════════════════════════════════

MARKETS_SUMMARY = {
    "football": {
        "total_markets": 8,
        "primary": 5,
        "secondary": 3,
        "recommend_for": "Match Winner + Over/Under",
    },
    "basketball": {
        "total_markets": 6,
        "primary": 4,
        "secondary": 2,
        "recommend_for": "Match Winner + Point Spread + Totals",
    },
    "tennis": {
        "total_markets": 6,
        "primary": 4,
        "secondary": 2,
        "recommend_for": "Match Winner + Set Betting",
    },
}
