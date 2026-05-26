"""form_tracking.py — Team form monitoring & settlement automation (Phase 7)."""
from __future__ import annotations
import json
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

UTC = timezone.utc
BASE_DIR = Path(__file__).resolve().parent
FORM_CACHE_DIR = BASE_DIR / "form_cache"
FORM_CACHE_DIR.mkdir(exist_ok=True)


class TeamFormTracker:
    """Track team form (last 5-10 results) for autonomous decision-making."""
    
    # Free APIs for sports data
    FOOTBALL_API = "https://api.football-data.org/v4"  # Requires API key
    BASKETBALL_API = "https://api.balldontlie.io/v2"   # Free up to 120 req/min
    HOCKEY_API = "https://statsapi.web.nhl.com/api/v1" # Free, no auth
    
    def __init__(self, api_keys: dict = None):
        """
        Args:
            api_keys: dict with optional keys for various APIs
                     {"football_data": "your-key", "balldontlie": "your-key", ...}
        """
        self.api_keys = api_keys or {}
        self.cache_ttl_minutes = 60
    
    def get_team_form(self, team: str, sport: str = "football", 
                      matches_count: int = 5) -> Optional[dict]:
        """
        Get team's recent form (last N matches).
        
        Returns:
            {
                "team": "Chelsea",
                "sport": "football",
                "matches": [
                    {"date": "2024-01-15", "opponent": "Man City", 
                     "result": "W", "score": "2-1", "home": true},
                    ...
                ],
                "stats": {
                    "wins": 3, "draws": 1, "losses": 1,
                    "goals_for": 12, "goals_against": 5,
                    "win_rate": 0.60,
                    "avg_goals": 2.40,
                    "strength": "HOT"  # HOT, WARM, COLD, INJURED
                }
            }
        """
        cache_key = f"{team.lower()}_{sport}_{matches_count}"
        cached = self._load_cache(cache_key)
        if cached and cached.get("cached_at"):
            age_min = (datetime.now(UTC) - 
                      datetime.fromisoformat(cached["cached_at"])).total_seconds() / 60
            if age_min < self.cache_ttl_minutes:
                return cached["data"]
        
        # Fetch fresh data based on sport
        if sport in ("football", "soccer"):
            data = self._fetch_football_form(team, matches_count)
        elif sport in ("basketball", "nba"):
            data = self._fetch_nba_form(team, matches_count)
        elif sport == "hockey":
            data = self._fetch_hockey_form(team, matches_count)
        else:
            return None
        
        # Cache it
        if data:
            self._save_cache(cache_key, data)
        return data
    
    def _fetch_football_form(self, team: str, count: int) -> Optional[dict]:
        """Fetch football team form from free/paid APIs."""
        # Note: football-data.org requires API key
        # Alternative: use ESPN, Flashscore, or football-reference
        try:
            # This is a placeholder - you'd need to implement actual API calls
            # Using football-data.org as example
            api_key = self.api_keys.get("football_data")
            if not api_key:
                # Could fallback to scraping or cached data
                return self._get_football_form_mock(team, count)
            
            # Example endpoint (varies by API)
            # Would need team ID lookup first
            return None
        except Exception as e:
            print(f"Football form fetch error: {e}")
            return self._get_football_form_mock(team, count)
    
    def _fetch_nba_form(self, team: str, count: int) -> Optional[dict]:
        """Fetch NBA team form from balldontlie.io (free)."""
        try:
            # balldontlie.io free API
            headers = {"Authorization": self.api_keys.get("balldontlie", "")}
            
            # Get team ID
            resp = requests.get(
                "https://api.balldontlie.io/v2/teams",
                params={"search": team},
                headers=headers,
                timeout=10
            )
            if resp.status_code != 200:
                return None
            
            teams = resp.json().get("data", [])
            if not teams:
                return None
            
            team_id = teams[0]["id"]
            team_name = teams[0]["full_name"]
            
            # Get recent games
            resp = requests.get(
                "https://api.balldontlie.io/v2/games",
                params={
                    "team_ids[]": team_id,
                    "order_by": "date",
                    "per_page": count,
                    "sort": "desc"
                },
                headers=headers,
                timeout=10
            )
            
            if resp.status_code != 200:
                return None
            
            games = resp.json().get("data", [])
            matches = []
            wins, losses = 0, 0
            points_for, points_against = 0, 0
            
            for game in games:
                home_team = game.get("home_team", {}).get("name", "")
                away_team = game.get("away_team", {}).get("name", "")
                home_score = game.get("home_team_score", 0)
                away_score = game.get("away_team_score", 0)
                
                is_home = home_team.lower() == team_name.lower()
                own_score = home_score if is_home else away_score
                opp_score = away_score if is_home else home_score
                
                result = "W" if own_score > opp_score else ("L" if own_score < opp_score else "D")
                if result == "W":
                    wins += 1
                elif result == "L":
                    losses += 1
                
                points_for += own_score
                points_against += opp_score
                
                opponent = away_team if is_home else home_team
                matches.append({
                    "date": game.get("date", ""),
                    "opponent": opponent,
                    "result": result,
                    "score": f"{own_score}-{opp_score}",
                    "home": is_home
                })
            
            win_rate = wins / (wins + losses) if (wins + losses) > 0 else 0
            strength = self._determine_strength(win_rate, wins, losses)
            
            return {
                "team": team_name,
                "sport": "basketball",
                "matches": matches,
                "stats": {
                    "wins": wins,
                    "losses": losses,
                    "win_rate": round(win_rate, 3),
                    "points_for": points_for,
                    "points_against": points_against,
                    "avg_points_for": round(points_for / len(matches), 1) if matches else 0,
                    "avg_points_against": round(points_against / len(matches), 1) if matches else 0,
                    "point_diff": round(points_for - points_against, 1),
                    "strength": strength
                },
                "cached_at": datetime.now(UTC).isoformat()
            }
        except Exception as e:
            print(f"NBA form fetch error: {e}")
            return None
    
    def _fetch_hockey_form(self, team: str, count: int) -> Optional[dict]:
        """Fetch NHL team form from statsapi.web.nhl.com (free)."""
        try:
            # Get team ID
            resp = requests.get("https://statsapi.web.nhl.com/api/v1/teams", timeout=10)
            teams = resp.json().get("teams", [])
            team_data = next((t for t in teams 
                            if t["name"].lower() == team.lower()), None)
            if not team_data:
                return None
            
            team_id = team_data["id"]
            team_name = team_data["name"]
            
            # Get recent games
            resp = requests.get(
                f"https://statsapi.web.nhl.com/api/v1/teams/{team_id}/schedule",
                timeout=10
            )
            games = resp.json().get("dates", [])
            
            matches = []
            wins, losses, ot_losses = 0, 0, 0
            goals_for, goals_against = 0, 0
            
            for date_obj in games[-count:]:
                for game in date_obj.get("games", []):
                    if game["status"]["detailedState"] not in ("Final", "Final/OT", "Final/SO"):
                        continue
                    
                    away_team = game["away"]["team"]["name"]
                    home_team = game["home"]["team"]["name"]
                    away_score = game["away"]["score"]
                    home_score = game["home"]["score"]
                    
                    is_home = home_team.lower() == team_name.lower()
                    own_score = home_score if is_home else away_score
                    opp_score = away_score if is_home else home_score
                    
                    result = "W" if own_score > opp_score else ("L" if own_score < opp_score else "D")
                    if result == "W":
                        wins += 1
                    elif result == "L":
                        if "OT" in game["status"]["detailedState"]:
                            ot_losses += 1
                        else:
                            losses += 1
                    
                    goals_for += own_score
                    goals_against += opp_score
                    
                    opponent = home_team if is_home else away_team
                    matches.append({
                        "date": game["gameDate"][:10],
                        "opponent": opponent,
                        "result": result,
                        "score": f"{own_score}-{opp_score}",
                        "home": is_home
                    })
            
            total_games = wins + losses + ot_losses
            win_rate = wins / total_games if total_games > 0 else 0
            strength = self._determine_strength(win_rate, wins, losses + ot_losses)
            
            return {
                "team": team_name,
                "sport": "hockey",
                "matches": matches[-count:],
                "stats": {
                    "wins": wins,
                    "losses": losses,
                    "ot_losses": ot_losses,
                    "win_rate": round(win_rate, 3),
                    "goals_for": goals_for,
                    "goals_against": goals_against,
                    "goals_diff": goals_for - goals_against,
                    "strength": strength
                },
                "cached_at": datetime.now(UTC).isoformat()
            }
        except Exception as e:
            print(f"Hockey form fetch error: {e}")
            return None
    
    def _get_football_form_mock(self, team: str, count: int) -> Optional[dict]:
        """Fallback mock data for football when API unavailable."""
        # In production, you'd fetch this from paid API or web scraping
        return {
            "team": team.title(),
            "sport": "football",
            "matches": [
                {"date": "2024-01-20", "opponent": f"Opponent {i}", 
                 "result": ["W", "L", "D"][i % 3], "score": f"{2-i}-{i}", "home": i % 2 == 0}
                for i in range(count)
            ],
            "stats": {
                "wins": count // 2,
                "draws": (count - count // 2) // 2,
                "losses": count - count // 2 - (count - count // 2) // 2,
                "goals_for": count * 2,
                "goals_against": count,
                "win_rate": 0.50,
                "strength": "WARM"
            },
            "cached_at": datetime.now(UTC).isoformat()
        }
    
    def _determine_strength(self, win_rate: float, wins: int, losses: int) -> str:
        """Categorize team strength."""
        if win_rate >= 0.65 and wins >= 3:
            return "HOT"
        if win_rate >= 0.50:
            return "WARM"
        if win_rate >= 0.30:
            return "COLD"
        return "STRUGGLING"
    
    def _load_cache(self, key: str) -> Optional[dict]:
        """Load cached form data."""
        cache_file = FORM_CACHE_DIR / f"{key}.json"
        if cache_file.exists():
            try:
                return json.loads(cache_file.read_text())
            except Exception:
                pass
        return None
    
    def _save_cache(self, key: str, data: dict) -> None:
        """Save form data to cache."""
        cache_file = FORM_CACHE_DIR / f"{key}.json"
        try:
            cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception:
            pass
    
    def adjust_confidence(self, odds: float, form_data: Optional[dict],
                         selection: str = "home") -> float:
        """Adjust odds confidence based on team form."""
        if not form_data or not form_data.get("stats"):
            return 0.0
        
        strength = form_data["stats"]["strength"]
        win_rate = form_data["stats"].get("win_rate", 0.5)
        
        # Higher confidence if team is in form
        adjustments = {
            "HOT": 0.05,      # +5% if hot
            "WARM": 0.02,     # +2% if warm
            "COLD": -0.03,    # -3% if cold
            "STRUGGLING": -0.08  # -8% if struggling
        }
        
        adjustment = adjustments.get(strength, 0.0)
        
        # Additional boost for home teams in form
        if "home" in selection.lower() and strength in ("HOT", "WARM"):
            adjustment += 0.02
        
        return round(adjustment * 100, 2)


class SettlementAutomation:
    """Automatically settle picks based on API results."""
    
    def __init__(self, tracker: TeamFormTracker):
        self.tracker = tracker
    
    def auto_settle_pending_picks(self) -> dict:
        """
        Auto-settle open picks by fetching match results from APIs.
        
        Returns:
            {
                "settled_count": 5,
                "settled_picks": [...],
                "still_pending": 10,
                "errors": []
            }
        """
        from brm import load_pick_history, settle_pick
        
        settled_count = 0
        settled_picks = []
        errors = []
        
        history = load_pick_history()
        open_picks = [p for p in history if p.get("status") == "OPEN"]
        
        for pick in open_picks:
            try:
                # Check if match has finished
                match_date = pick.get("commence_time", "")
                if not match_date:
                    continue
                
                commence = datetime.fromisoformat(match_date.replace("Z", "+00:00"))
                if datetime.now(UTC) < commence + timedelta(hours=3):
                    # Not enough time passed, skip
                    continue
                
                # Fetch result (depends on sport)
                sport = pick.get("sport", "")
                match = pick.get("match", "")
                result = self._fetch_match_result(match, sport)
                
                if result:
                    ok, item = settle_pick(pick["pick_id"], result)
                    if ok:
                        settled_count += 1
                        settled_picks.append({
                            "pick_id": pick["pick_id"],
                            "match": match,
                            "result": result,
                            "pnl": item.get("pnl", 0)
                        })
            except Exception as e:
                errors.append(f"Error settling {pick.get('pick_id')}: {e}")
        
        return {
            "settled_count": settled_count,
            "settled_picks": settled_picks,
            "still_pending": len(open_picks) - settled_count,
            "errors": errors
        }
    
    def _fetch_match_result(self, match: str, sport: str) -> Optional[str]:
        """Fetch match result from API. Returns 'WIN', 'LOSS', 'PUSH', or None."""
        # Implementation depends on specific sport API
        # This is a placeholder
        return None
