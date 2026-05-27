"""
enhanced_hermes.py - ENHANCED HERMÈS ANALYSIS
Улучшенный анализ с травмами, новостями, психологией, формой, итд
"""

from typing import Dict, List, Optional
from datetime import datetime, timedelta
from markets_config import HERMES_MARKET_PARAMS

# ═══════════════════════════════════════════════════════════════════════════
# INJURY & NEWS DATA (MOCK - в реальной системе подключать API)
# ═══════════════════════════════════════════════════════════════════════════

INJURY_DATABASE = {
    "football": {
        "Chelsea": {"players": [], "severity": "NONE", "last_update": "2026-05-27"},
        "Liverpool": {"players": [], "severity": "NONE", "last_update": "2026-05-27"},
        "Real Madrid": {"players": ["Benzema"], "severity": "MODERATE", "last_update": "2026-05-25"},
        "Barcelona": {"players": [], "severity": "NONE", "last_update": "2026-05-27"},
    },
    "basketball": {
        "Lakers": {"players": [], "severity": "NONE", "last_update": "2026-05-27"},
        "Celtics": {"players": ["Player X"], "severity": "LOW", "last_update": "2026-05-26"},
    },
    "tennis": {
        # Tennis injury data is per player
    },
}

NEWS_ALERTS = {
    "football": [
        # {
        #     "team": "Chelsea",
        #     "news": "Coach change",
        #     "impact": "NEGATIVE",
        #     "date": "2026-05-27",
        # },
    ],
    "basketball": [],
    "tennis": [],
}

# ═══════════════════════════════════════════════════════════════════════════
# PSYCHOLOGICAL FACTORS
# ═══════════════════════════════════════════════════════════════════════════

PSYCHOLOGICAL_FACTORS = {
    "revenge_match": 0.08,  # +8% if playing against former rival
    "pressure_situation": -0.05,  # -5% if high pressure
    "conference_championship": 0.10,  # +10% motivation
    "elimination_game": 0.07,  # +7% urgency
    "derby_match": 0.12,  # +12% intensity
    "underdog_motivation": 0.06,  # +6% if heavy underdog
    "favorite_complacency": -0.04,  # -4% if heavy favorite
}

# ═══════════════════════════════════════════════════════════════════════════
# FORM ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════

class FormAnalyzer:
    """Analyze team/player form from last N matches"""
    
    @staticmethod
    def calculate_form_score(matches: List[Dict], lookback_days: int = 30) -> float:
        """
        Calculate form score from recent matches
        Returns: -1.0 (terrible) to +1.0 (excellent)
        """
        if not matches:
            return 0.0
        
        recent_matches = [m for m in matches if 
                         (datetime.now() - datetime.fromisoformat(m.get("date", "2026-05-27"))).days <= lookback_days]
        
        if not recent_matches:
            return 0.0
        
        wins = sum(1 for m in recent_matches if m.get("result") == "WIN")
        draws = sum(1 for m in recent_matches if m.get("result") == "DRAW")
        losses = sum(1 for m in recent_matches if m.get("result") == "LOSS")
        
        total = wins + draws + losses
        
        if total == 0:
            return 0.0
        
        # Calculate points (W=3, D=1, L=0)
        points = wins * 3 + draws
        max_points = total * 3
        
        form_score = (points / max_points) * 2 - 1  # Normalize to -1 to +1
        
        return round(form_score, 2)
    
    @staticmethod
    def get_momentum(matches: List[Dict], last_n: int = 5) -> float:
        """Analyze momentum from last N matches"""
        if not matches or len(matches) < last_n:
            return 0.0
        
        recent = matches[-last_n:]
        
        wins = sum(1 for m in recent if m.get("result") == "WIN")
        momentum = (wins / last_n) * 2 - 1  # -1 to +1
        
        return round(momentum, 2)

# ═══════════════════════════════════════════════════════════════════════════
# END-OF-SEASON FATIGUE ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════

class FatigueAnalyzer:
    """Analyze end-of-season fatigue and rest days"""
    
    # Season end dates
    SEASON_ENDS = {
        "epl": datetime(2026, 5, 24),
        "laliga": datetime(2026, 5, 24),
        "seriea": datetime(2026, 5, 31),
        "bundesliga": datetime(2026, 5, 18),
        "ligue1": datetime(2026, 5, 31),
        "nba": datetime(2026, 6, 30),
        "atp_1000": datetime(2026, 12, 31),
    }
    
    CHAMPION_FATIGUE = -0.10  # -10% if team already won title
    RELEGATED_STRESS = -0.08  # -8% if relegation confirmed
    PLAYOFF_INTENSITY = 0.15  # +15% if in playoff race
    
    @staticmethod
    def get_fatigue_factor(league: str, days_into_season: int = None) -> float:
        """
        Get fatigue factor for league
        Returns: -1.0 (very fatigued) to +0.0 (normal)
        """
        if league not in FatigueAnalyzer.SEASON_ENDS:
            return 0.0
        
        season_end = FatigueAnalyzer.SEASON_ENDS[league]
        days_since_end = (datetime.now() - season_end).days
        
        # Post-season: heavy fatigue
        if days_since_end < 0:  # Season still active
            return 0.0
        elif days_since_end < 14:  # Very fresh
            return 0.02
        elif days_since_end < 30:  # Fresh
            return 0.01
        else:  # Decaying form
            return -0.02
    
    @staticmethod
    def get_rest_quality(days_rest: int) -> float:
        """
        Evaluate quality of rest
        Returns: impact on performance
        """
        if days_rest < 3:
            return -0.10  # Poor rest
        elif days_rest < 5:
            return -0.05  # Below optimal
        elif days_rest < 7:
            return 0.0  # Normal
        elif days_rest < 10:
            return 0.05  # Good rest
        else:
            return 0.08  # Excellent rest

# ═══════════════════════════════════════════════════════════════════════════
# INJURY IMPACT ANALYZER
# ═══════════════════════════════════════════════════════════════════════════

class InjuryAnalyzer:
    """Analyze impact of injuries on team performance"""
    
    IMPACT_BY_POSITION = {
        "football": {
            "goalkeeper": -0.15,
            "defender": -0.10,
            "midfielder": -0.08,
            "forward": -0.12,
        },
        "basketball": {
            "guard": -0.15,
            "forward": -0.12,
            "center": -0.10,
        },
        "tennis": {
            "player": -0.20,  # Any injury to main player
        },
    }
    
    @staticmethod
    def get_injury_impact(sport: str, team: str) -> float:
        """
        Calculate total injury impact on team
        Returns: negative impact on performance
        """
        if sport not in INJURY_DATABASE:
            return 0.0
        
        if team not in INJURY_DATABASE[sport]:
            return 0.0
        
        injury_info = INJURY_DATABASE[sport][team]
        
        if not injury_info.get("players"):
            return 0.0
        
        num_injuries = len(injury_info["players"])
        severity = injury_info.get("severity", "LOW")
        
        # Calculate based on severity
        if severity == "CRITICAL":
            base_impact = -0.20 * num_injuries
        elif severity == "MODERATE":
            base_impact = -0.10 * num_injuries
        else:
            base_impact = -0.05 * num_injuries
        
        return round(base_impact, 3)

# ═══════════════════════════════════════════════════════════════════════════
# H2H (HEAD-TO-HEAD) ANALYZER
# ═══════════════════════════════════════════════════════════════════════════

class H2HAnalyzer:
    """Analyze historical head-to-head records"""
    
    @staticmethod
    def get_h2h_advantage(team_a: str, team_b: str, matches: List[Dict] = None) -> float:
        """
        Calculate H2H advantage for Team A
        Returns: -1.0 (Team B dominant) to +1.0 (Team A dominant)
        """
        if not matches:
            return 0.0
        
        h2h_matches = [m for m in matches if 
                      (m.get("home") == team_a and m.get("away") == team_b) or
                      (m.get("home") == team_b and m.get("away") == team_a)]
        
        if not h2h_matches:
            return 0.0
        
        team_a_wins = sum(1 for m in h2h_matches if 
                         (m.get("home") == team_a and m.get("result") == "HOME_WIN") or
                         (m.get("away") == team_a and m.get("result") == "AWAY_WIN"))
        
        team_b_wins = len(h2h_matches) - team_a_wins
        
        if team_b_wins == 0:
            return 1.0
        if team_a_wins == 0:
            return -1.0
        
        h2h_ratio = team_a_wins / team_b_wins
        advantage = (h2h_ratio - 1) / (h2h_ratio + 1)
        
        return round(advantage, 2)

# ═══════════════════════════════════════════════════════════════════════════
# HOME/AWAY ANALYZER
# ═══════════════════════════════════════════════════════════════════════════

class HomeAwayAnalyzer:
    """Analyze home/away performance patterns"""
    
    @staticmethod
    def get_home_advantage(team: str, sport: str, matches: List[Dict] = None) -> float:
        """
        Calculate home advantage for team
        Returns: typical home advantage in this league
        """
        league_averages = {
            "football": 0.06,  # 6% typical home advantage in football
            "basketball": 0.08,  # 8% in basketball
            "tennis": 0.00,  # No home court in tennis generally
        }
        
        if not matches:
            return league_averages.get(sport, 0.0)
        
        home_matches = [m for m in matches if m.get("home") == team]
        
        if not home_matches:
            return league_averages.get(sport, 0.0)
        
        home_wins = sum(1 for m in home_matches if m.get("result") == "HOME_WIN")
        home_win_rate = home_wins / len(home_matches)
        
        away_matches = [m for m in matches if m.get("away") == team]
        away_wins = sum(1 for m in away_matches if m.get("result") == "AWAY_WIN")
        away_win_rate = away_wins / len(away_matches) if away_matches else 0
        
        home_advantage = home_win_rate - away_win_rate
        
        return round(home_advantage, 3)

# ═══════════════════════════════════════════════════════════════════════════
# COMPOSITE ENHANCED ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════

class EnhancedHermesAnalyzer:
    """Главный анализатор с всеми факторами"""
    
    def __init__(self):
        self.form_analyzer = FormAnalyzer()
        self.fatigue_analyzer = FatigueAnalyzer()
        self.injury_analyzer = InjuryAnalyzer()
        self.h2h_analyzer = H2HAnalyzer()
        self.home_away_analyzer = HomeAwayAnalyzer()
    
    def analyze_match(self, 
                     match: Dict,
                     sport: str,
                     league: str,
                     market: str = "match_winner") -> Dict:
        """
        Полный анализ матча со всеми факторами
        
        Returns:
        {
            "match": "Team A vs Team B",
            "sport": "football",
            "market": "match_winner",
            "recommendation": "ACCEPT/RECONSIDER/REJECT",
            "confidence": 0.75,
            "ev": 0.12,
            "factors": {
                "form": 0.08,
                "injury": -0.05,
                "h2h": 0.06,
                "home_advantage": 0.06,
                "fatigue": -0.02,
                "psychology": 0.03,
            },
            "total_adjustment": 0.16,
        }
        """
        
        home_team = match.get("home", "")
        away_team = match.get("away", "")
        
        factors = {}
        
        # 1️⃣ FORM ANALYSIS
        home_form = self.form_analyzer.calculate_form_score([])
        away_form = self.form_analyzer.calculate_form_score([])
        form_diff = home_form - away_form
        factors["form"] = round(form_diff * 0.10, 3)
        
        # 2️⃣ INJURY ANALYSIS
        home_injury = self.injury_analyzer.get_injury_impact(sport, home_team)
        away_injury = self.injury_analyzer.get_injury_impact(sport, away_team)
        factors["injury"] = round(home_injury - away_injury, 3)
        
        # 3️⃣ H2H ANALYSIS
        h2h = self.h2h_analyzer.get_h2h_advantage(home_team, away_team, [])
        factors["h2h"] = round(h2h * 0.05, 3)
        
        # 4️⃣ HOME ADVANTAGE
        factors["home_advantage"] = self.home_away_analyzer.get_home_advantage(home_team, sport, [])
        
        # 5️⃣ FATIGUE ANALYSIS
        factors["fatigue"] = self.fatigue_analyzer.get_fatigue_factor(league)
        
        # 6️⃣ PSYCHOLOGICAL FACTORS
        factors["psychology"] = 0.0  # Can be customized per match
        
        # Calculate total adjustment
        total_adjustment = sum(factors.values())
        
        # Get base EV from market odds
        base_ev = 0.08  # Default EV
        adjusted_ev = base_ev + total_adjustment
        
        # Determine recommendation
        min_ev = HERMES_MARKET_PARAMS.get(sport, {}).get(market, {}).get("min_ev", 0.10)
        
        if adjusted_ev >= min_ev * 1.5:
            recommendation = "ACCEPT"
            confidence = min(0.90, 0.60 + adjusted_ev)
        elif adjusted_ev >= min_ev:
            recommendation = "RECONSIDER"
            confidence = min(0.80, 0.50 + adjusted_ev)
        else:
            recommendation = "REJECT"
            confidence = 0.30
        
        return {
            "match": f"{home_team} vs {away_team}",
            "sport": sport,
            "league": league,
            "market": market,
            "recommendation": recommendation,
            "confidence": round(confidence, 2),
            "ev": round(adjusted_ev, 3),
            "factors": factors,
            "total_adjustment": round(total_adjustment, 3),
        }

# Quick test
if __name__ == "__main__":
    analyzer = EnhancedHermesAnalyzer()
    
    test_match = {
        "home": "Chelsea",
        "away": "Liverpool",
        "odds": 2.10,
    }
    
    result = analyzer.analyze_match(test_match, "football", "epl", "match_winner")
    print(result)
