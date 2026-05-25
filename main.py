import os
import json
import math
import uuid
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

UTC = timezone.utc
BASE_DIR = Path(__file__).resolve().parent
REPORTS_DIR = BASE_DIR / "reports"
LOGS_DIR = BASE_DIR / "logs"
DATA_DIR = BASE_DIR / "data"
REPORTS_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

LAST_RUN_PATH = BASE_DIR / "last_run.txt"
AUDIT_PATH = BASE_DIR / "audit.json"
RUNS_PATH = BASE_DIR / "runs.txt"
WATCHLIST_PATH = BASE_DIR / "watchlist.json"
PICK_HISTORY_PATH = DATA_DIR / "pick_history.json"
BANK_PATH = BASE_DIR / "bank.json"
DAILY_EXPOSURE_PATH = BASE_DIR / "daily_exposure.json"
CLOSING_LINE_PATH = DATA_DIR / "closing_lines.json"


CLASS_ORDER = {"PASS": 1, "MICRO": 2, "SUPPORT": 3, "CORE": 4}

LEAGUE_TUNING = {
    "soccer_epl":              {"h2h": 0.88, "spreads": 0.92, "totals": 0.95, "btts": 0.90, "team_totals": 0.93},
    "soccer_italy_serie_a":    {"h2h": 0.85, "spreads": 0.90, "totals": 0.94, "btts": 0.88, "team_totals": 0.91},
    "tennis_atp_french_open":  {"h2h": 0.82, "spreads": 0.88, "totals": 0.90, "btts": 1.0,  "team_totals": 1.0},
    "tennis_wta_french_open":  {"h2h": 0.80, "spreads": 0.86, "totals": 0.88, "btts": 1.0,  "team_totals": 1.0},
    "icehockey_nhl":           {"h2h": 0.90, "spreads": 0.93, "totals": 0.95, "btts": 1.0,  "team_totals": 0.92},
    "default":                 {"h2h": 1.0,  "spreads": 1.0,  "totals": 1.0,  "btts": 1.0,  "team_totals": 1.0},
}

WEAK_SLATE_MSG = "NO PLAYS TODAY — all candidates failed filters."

SPORT_MAP = {
    "basketball": ("basketball", "basketball_nba", 2, "CORE"),
    "nba": ("basketball", "basketball_nba", 2, "CORE"),
    "football": ("football", "soccer_spain_la_liga", 2, "CORE"),
    "laliga": ("football", "soccer_spain_la_liga", 2, "CORE"),
    "la_liga": ("football", "soccer_spain_la_liga", 2, "CORE"),
    "epl": ("football", "soccer_epl", 2, "CORE"),
    "premierleague": ("football", "soccer_epl", 2, "CORE"),
    "premier_league": ("football", "soccer_epl", 2, "CORE"),
    "seriea": ("football", "soccer_italy_serie_a", 2, "CORE"),
    "serie_a": ("football", "soccer_italy_serie_a", 2, "CORE"),
    "wnba": ("wnba", "basketball_wnba", 3, "MICRO"),
    "euroleague": ("euroleague", "basketball_euroleague", 2, "CORE"),
    "acb": ("acb", "basketball_spain_acb", 2, "SUPPORT"),
"tennis": ("tennis", "tennis_atp_french_open", 3, "MICRO"),
"atp": ("tennis", "tennis_atp_french_open", 3, "MICRO"),
"wta": ("tennis", "tennis_wta_french_open", 3, "MICRO"),
"hockey": ("hockey", "icehockey_nhl", 2, "SUPPORT"),
"nhl": ("hockey", "icehockey_nhl", 2, "SUPPORT"),
}
TEAM_ALIASES = {
    "chelsea": ["chelsea"],
    "mancity": ["man city", "manchester city", "mancity"],
    "manchestercity": ["man city", "manchester city", "manchestercity"],
    "city": ["man city", "manchester city"],
    "manutd": ["man utd", "manchester united", "manutd", "man united"],
    "manchesterunited": ["man utd", "manchester united", "man united"],
    "united": ["man utd", "manchester united", "man united"],
    "tottenham": ["tottenham", "spurs"],
    "arsenal": ["arsenal"],
    "liverpool": ["liverpool"],
    "bournemouth": ["bournemouth"],
    "astonvilla": ["aston villa", "astonvilla"],
    "villa": ["aston villa", "villa"],
    "sunderland": ["sunderland"],
    "juventus": ["juventus", "juve"],
    "inter": ["inter", "internazionale"],
    "milan": ["milan", "ac milan"],
}
DEFAULT_TEAM_SPORT = {
    "chelsea": "epl",
    "mancity": "epl",
    "manutd": "epl",
    "arsenal": "epl",
    "liverpool": "epl",
    "tottenham": "epl",
    "bournemouth": "epl",
    "astonvilla": "epl",
    "sunderland": "epl",
    "juventus": "seriea",
    "inter": "seriea",
    "milan": "seriea",
}
MODE_RULES = {
    "FROZEN": {"min_ev": 6.0, "micro": 10, "support": 20, "core": 30},
    "EMERGENCY": {"min_ev": 8.0, "micro": 10, "support": 15, "core": 20},
    "NORMAL": {"min_ev": 4.0, "micro": 20, "support": 35, "core": 50},
    "GROWTH": {"min_ev": 3.0, "micro": 25, "support": 40, "core": 60},
}
MAX_BETS_PER_DAY = {
    "FROZEN": 1, "EMERGENCY": 2, "NORMAL": 5, "GROWTH": 8,
}

SPORT_LIMITS = {
    "basketball": {"min_book_count": 3, "max_odds_age": 180},
    "football": {"min_book_count": 3, "max_odds_age": 180},
    "wnba": {"min_book_count": 4, "max_odds_age": 120},
    "euroleague": {"min_book_count": 4, "max_odds_age": 120},
    "acb": {"min_book_count": 4, "max_odds_age": 120},
"tennis": {"min_book_count": 3, "max_odds_age": 90},
"hockey": {"min_book_count": 3, "max_odds_age": 150},
}

def get_league_factor(sport_key: str, market: str) -> float:
    tuning = LEAGUE_TUNING.get(sport_key, LEAGUE_TUNING["default"])
    return tuning.get(market, 1.0)

def split_text(text, limit=3500):
    text = str(text)
    if len(text) <= limit:
        return [text]
    parts, current = [], ""
    for line in text.splitlines(True):
        if len(current) + len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            while len(line) > limit:
                parts.append(line[:limit])
                line = line[limit:]
        current += line
    if current:
        parts.append(current)
    return parts

async def reply_long(message, text):
    for chunk in split_text(text):
        await message.reply_text(chunk)

def load_json_list(path):
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, list) else []
    except Exception:
        return []

def save_json_list(path, items):
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding='utf-8')

def append_run_line(run_line):
    try:
        with RUNS_PATH.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(run_line, ensure_ascii=False) + '\n')
    except Exception:
        pass

def ensure_runs_seed_from_latest():
    if RUNS_PATH.exists() and RUNS_PATH.stat().st_size > 0:
        return
    if not AUDIT_PATH.exists():
        return
    try:
        data = json.loads(AUDIT_PATH.read_text(encoding='utf-8'))
        append_run_line({"run_id": data.get("run_id", "unknown"), "generated_at": data.get("generated_at", ""), "request": data.get("request", {}).get("raw_text", ""), "accepted_count": data.get("accepted_count", 0), "rejected_count": data.get("rejected_count", 0), "report_path": str(REPORTS_DIR), "audit_path": str(AUDIT_PATH)})
    except Exception:
        pass

def load_watchlist():
    return load_json_list(WATCHLIST_PATH)

def save_watchlist(items):
    save_json_list(WATCHLIST_PATH, items)

def load_pick_history():
    return load_json_list(PICK_HISTORY_PATH)

def save_pick_history(items):
    save_json_list(PICK_HISTORY_PATH, items)

def normalize_team_token(token):
    low = token.strip().lower()
    return low if low in TEAM_ALIASES else low.replace(' ', '')

def detect_mode(bank):
    if bank < 500:
        return 'FROZEN'
    if bank < 1000:
        return 'EMERGENCY'
    if bank < 3000:
        return 'NORMAL'
    return 'GROWTH'

def parse_request(text):
    raw = text.strip().lower()
    sports = [key for key in SPORT_MAP if key in raw] or ['football']
    strict = 'strict' in raw
    bank = 1000.0
    max_candidates = 30
    team_filters = []
    parts = raw.replace('=', ' ').replace(',', ' ').split()
    for i, token in enumerate(parts):
        if token == 'bank' and i + 1 < len(parts):
            try:
                bank = float(parts[i + 1])
            except Exception:
                pass
        if token in ['max', 'max_candidates'] and i + 1 < len(parts):
            try:
                max_candidates = int(parts[i + 1])
            except Exception:
                pass
    mode = detect_mode(bank)
    for i, token in enumerate(parts):
        if token == 'mode' and i + 1 < len(parts):
            custom = parts[i + 1].upper()
            if custom in MODE_RULES:
                mode = custom
    for token in parts:
        if token in TEAM_ALIASES:
            team_filters.extend(TEAM_ALIASES[token])
        elif token not in ['auto', 'today', 'strict', 'bank', 'mode', 'max', 'max_candidates'] and token not in SPORT_MAP and len(token) >= 4:
            team_filters.append(token)
    return {"raw_text": text, "sports": list(dict.fromkeys(sports)), "strict": strict, "bank": bank, "mode": mode, "markets": ['h2h', 'spreads', 'totals', 'btts', 'team_totals'], "max_candidates": max_candidates, "team_filters": list(dict.fromkeys(team_filters))}

def build_scanwatch_request():
    watchlist = load_watchlist()
    if not watchlist:
        return None
    sports, parts = [], ['AUTO', 'today']
    for item in watchlist:
        sport = item.get('sport', '').strip().lower()
        if sport in SPORT_MAP and sport not in sports:
            sports.append(sport)
    if not sports:
        sports = ['epl']
    parts.extend(sports)
    for item in watchlist:
        team = item.get('team', '').strip().lower()
        if team:
            parts.append(team)
    parts.append('strict')
    return ' '.join(parts)

def match_team_filter(match_name, team_filters):
    return True if not team_filters else any(team in match_name.lower() for team in team_filters)

def implied_probability(odds):
    return round(100.0 / odds, 2)

def market_profile(market, selection, odds):
    low = selection.lower()
    if market == 'h2h':
        if 'draw' in low:
            return 'draw'
        if odds >= 4.8:
            return 'underdog_long'
        if odds >= 3.2:
            return 'underdog'
        return 'favorite_or_balanced'
    if market == 'spreads':
        return 'spread'
    if market == 'totals':
        return 'total'
    if market == 'btts':
        return 'btts'
    if market == 'team_totals':
        return 'team_total'
    return 'other'

def estimate_model_prob(best_odds, avg_odds, book_count, market, selection):
    implied_best = 100.0 / best_odds
    gap = max(0.0, best_odds - avg_odds)
    profile = market_profile(market, selection, best_odds)
    if market == 'h2h':
        bonus = min(gap * 7.5, 2.2)
        consensus = 1.2 if book_count >= 6 else 0.8 if book_count >= 4 else 0.3 if book_count >= 3 else 0.0
        prob = implied_best + bonus + consensus + 0.3
        if profile == 'draw':
            prob -= 3.2
        elif profile == 'underdog':
            prob -= 2.4
        elif profile == 'underdog_long':
            prob -= 5.6
    elif market == 'spreads':
        bonus = min(gap * 10.0, 3.0)
        consensus = 1.2 if book_count >= 6 else 0.8 if book_count >= 4 else 0.3
        prob = implied_best + bonus + consensus
    elif market == 'btts':
        bonus = min(gap * 8.5, 2.4)
        consensus = 1.0 if book_count >= 6 else 0.7 if book_count >= 4 else 0.3
        prob = implied_best + bonus + consensus - 0.2
    elif market == 'team_totals':
        bonus = min(gap * 8.8, 2.5)
        consensus = 1.0 if book_count >= 6 else 0.7 if book_count >= 4 else 0.3
        prob = implied_best + bonus + consensus - 0.1
    else:
        bonus = min(gap * 9.0, 2.8)
        consensus = 1.0 if book_count >= 6 else 0.7 if book_count >= 4 else 0.3
        prob = implied_best + bonus + consensus
    return round(max(31.0, min(prob, 73.0)), 2)

def infer_variance(market, odds):
    if odds >= 3.5:
        return 'EXTREME'
    if odds >= 2.5:
        return 'HIGH'
    if market == 'btts':
        return 'MEDIUM'
    if market in ['totals', 'team_totals']:
        return 'MEDIUM'
    return 'LOW'

def calc_data_quality(book_count, odds_age_minutes, lineup_confirmed, source_tier):
    score = 0
    score += 3 if book_count >= 6 else 2 if book_count >= 5 else 1 if book_count >= 3 else 0
    score += 3 if odds_age_minutes <= 20 else 2 if odds_age_minutes <= 60 else 1 if odds_age_minutes <= 120 else 0
    if lineup_confirmed:
        score += 1
    score += 2 if source_tier == 1 else 1 if source_tier == 2 else 0
    return 'HIGH' if score >= 7 else 'MEDIUM' if score >= 4 else 'LOW'

def calibration_factor(book_count, odds_age_minutes, lineup_confirmed, market, data_quality, odds, selection, sport_key="default"):
    profile = market_profile(market, selection, odds)
    factor = 1.0
    if book_count < 4:
        factor -= 0.14
    if odds_age_minutes > 120:
        factor -= 0.16
    if odds_age_minutes > 180:
        factor -= 0.10
    if (not lineup_confirmed) and market in ['spreads', 'totals']:
        factor -= 0.18
    if data_quality == 'LOW':
        factor -= 0.12
    if profile == 'draw':
        factor -= 0.18
    elif profile == 'underdog':
        factor -= 0.14
    elif profile == 'underdog_long':
        factor -= 0.34
    elif market == 'spreads':
        factor -= 0.03
    elif market == 'btts':
        factor -= 0.08
    elif market == 'team_totals':
        factor -= 0.06
    factor *= get_league_factor(sport_key, market)
    return round(max(factor, 0.18), 2)

def calc_ci_low(ev_calibrated, book_count, odds_age_minutes, variance, data_quality, odds, market, selection):
    profile = market_profile(market, selection, odds)
    penalty = 0.0
    if book_count < 4:
        penalty += 3.0
    if odds_age_minutes > 120:
        penalty += 3.5
    if odds_age_minutes > 180:
        penalty += 2.0
    if variance == 'HIGH':
        penalty += 2.6
    if variance == 'EXTREME':
        penalty += 4.6
    if data_quality == 'LOW':
        penalty += 2.4
    if profile == 'draw':
        penalty += 3.4
    elif profile == 'underdog':
        penalty += 2.6
    elif profile == 'underdog_long':
        penalty += 6.2
    elif market == 'spreads':
        penalty += 0.4
    elif market == 'totals':
        penalty += 0.6
    elif market == 'btts':
        penalty += 1.1
    elif market == 'team_totals':
        penalty += 0.9
    return round(ev_calibrated - penalty, 2)

def class_from_ev(ev_calibrated, ci_low, data_quality, odds, market, selection):
    profile = market_profile(market, selection, odds)
    if ci_low < 0:
        return 'PASS'
    if profile == 'underdog_long':
        return 'MICRO' if ev_calibrated >= 12 and ci_low >= 2.0 and data_quality == 'HIGH' else 'PASS'
    if profile == 'draw':
        return 'MICRO' if ev_calibrated >= 10 and ci_low >= 2.0 and data_quality == 'HIGH' else 'PASS'
    if market == 'spreads':
        if ev_calibrated >= 9 and ci_low >= 2:
            return 'SUPPORT'
        if ev_calibrated >= 5 and ci_low >= 0.5:
            return 'MICRO'
        return 'PASS'
    if market == 'totals':
        if ev_calibrated >= 8 and ci_low >= 1.5:
            return 'SUPPORT'
        if ev_calibrated >= 5 and ci_low >= 0.5:
            return 'MICRO'
        return 'PASS'
    if market == 'btts':
        if ev_calibrated >= 8 and ci_low >= 1.5 and data_quality in ['HIGH', 'MEDIUM']:
            return 'SUPPORT'
        if ev_calibrated >= 5 and ci_low >= 0.5:
            return 'MICRO'
        return 'PASS'
    if market == 'team_totals':
        if ev_calibrated >= 8 and ci_low >= 1.5 and data_quality in ['HIGH', 'MEDIUM']:
            return 'SUPPORT'
        if ev_calibrated >= 5 and ci_low >= 0.5:
            return 'MICRO'
        return 'PASS'
    if ev_calibrated >= 10 and ci_low >= 3 and data_quality == 'HIGH':
        return 'CORE'
    if ev_calibrated >= 7 and ci_low >= 1:
        return 'SUPPORT'
    if ev_calibrated >= 4 and ci_low >= 0:
        return 'MICRO'
    return 'PASS'

def cap_class(base_class, max_class):
    return max_class if CLASS_ORDER[base_class] > CLASS_ORDER[max_class] else base_class

def risk_cap(mode, decision):
    rule = MODE_RULES.get(mode, MODE_RULES['NORMAL'])
    return float(rule['micro'] if decision == 'MICRO' else rule['support'] if decision == 'SUPPORT' else rule['core'] if decision == 'CORE' else 0.0)

def calc_stake(bank, decision, ev_calibrated, odds, cap_value, market, selection):
    if decision == 'PASS':
        return 0.0
    b = odds - 1.0
    if b <= 0:
        return 0.0
    p = max(0.01, min((implied_probability(odds) + ev_calibrated) / 100.0, 0.95))
    q = 1.0 - p
    kelly = ((b * p) - q) / b
    if kelly < 0:
        kelly = 0.0
    profile = market_profile(market, selection, odds)
    if profile == 'underdog_long':
        kelly *= 0.25
    elif profile == 'underdog':
        kelly *= 0.45
    elif profile == 'draw':
        kelly *= 0.35
    elif market == 'spreads':
        kelly *= 0.85
    elif market == 'totals':
        kelly *= 0.80
    elif market == 'btts':
        kelly *= 0.72
    elif market == 'team_totals':
        kelly *= 0.76
    stake = min(bank * kelly * 0.20, cap_value)
    rounded = math.floor(stake / 10.0) * 10.0
    return 10.0 if rounded == 0 and stake > 0 else float(max(rounded, 0.0))

def fetch_candidates(request_data):
    api_key = os.getenv('ODDS_API_KEY')
    if not api_key:
        raise RuntimeError('ODDS_API_KEY is not set')
    now = datetime.now(UTC)
    all_candidates, seen_group = [], set()
    for sport_name in request_data['sports']:
        sport_alias, sport_key, source_tier, max_class = SPORT_MAP[sport_name]
        response = requests.get('https://api.the-odds-api.com/v4/sports/' + sport_key + '/odds', params={'apiKey': api_key, 'regions': 'eu,uk', 'markets': 'h2h,spreads,totals', 'oddsFormat': 'decimal', 'dateFormat': 'iso'}, timeout=25)
        response.raise_for_status()
        events = response.json()
        for event in events:
            home_team, away_team, commence_raw = event.get('home_team'), event.get('away_team'), event.get('commence_time')
            event_id = str(event.get('id', ''))
            if not home_team or not away_team or not commence_raw:
                continue
            try:
                commence_time = datetime.fromisoformat(commence_raw.replace('Z', '+00:00'))
            except Exception:
                continue
            match_name = str(away_team) + ' vs ' + str(home_team)
            if not match_team_filter(match_name, request_data['team_filters']):
                continue
            grouped = {}
            for bookmaker in event.get('bookmakers', []):
                bookmaker_title = bookmaker.get('title', 'Book')
                last_update_raw = bookmaker.get('last_update')
                odds_age_minutes = 999.0
                if last_update_raw:
                    try:
                        last_update = datetime.fromisoformat(last_update_raw.replace('Z', '+00:00'))
                        odds_age_minutes = max((now - last_update).total_seconds() / 60.0, 0.0)
                    except Exception:
                        pass
                for market in bookmaker.get('markets', []):
                    market_key = market.get('key', '')
                    if market_key not in ['h2h', 'spreads', 'totals', 'btts', 'team_totals']:
                        continue
                    for outcome in market.get('outcomes', []):
                        name, price, point = outcome.get('name'), outcome.get('price'), outcome.get('point')
                        try:
                            price = float(price)
                        except Exception:
                            continue
                        if point is not None:
                            try:
                                point = float(point)
                            except Exception:
                                point = None
                        if not name:
                            continue
                        group_key = event_id + '|' + market_key + '|' + str(name) + '|' + str(point)
                        if group_key not in grouped:
                            selection_text = str(name) if point is None else str(name) + ' ' + str(point)
                            if market_key == 'btts':
                                selection_text = 'BTTS ' + str(name)
                            elif market_key == 'team_totals':
                                selection_text = str(name) if point is None else str(name) + ' ' + str(point)
                            grouped[group_key] = {"event_id": event_id, "match": match_name, "sport": sport_alias, "market": market_key, "selection": selection_text, "point": point, "commence_time": commence_time, "prices": [], "best_odds": float(price), "best_bookmaker": bookmaker_title, "best_odds_age_minutes": odds_age_minutes, "source_tier": source_tier, "max_class": max_class}
                        grouped[group_key]['prices'].append(float(price))
                        if float(price) > grouped[group_key]['best_odds']:
                            grouped[group_key]['best_odds'] = float(price)
                            grouped[group_key]['best_bookmaker'] = bookmaker_title
                            grouped[group_key]['best_odds_age_minutes'] = odds_age_minutes
            for item in grouped.values():
                if not item['prices']:
                    continue
                uniq_key = item['match'] + '|' + item['market'] + '|' + item['selection']
                if uniq_key in seen_group:
                    continue
                seen_group.add(uniq_key)
                best_odds = round(max(item['prices']), 2)
                avg_odds = round(sum(item['prices']) / len(item['prices']), 2)
                book_count = len(item['prices'])
                market = item['market']
                variance = infer_variance(market, best_odds)
                hours_to_start = (item['commence_time'] - now).total_seconds() / 3600.0
                lineup_confirmed = not (market in ['spreads', 'totals'] and hours_to_start <= 1.5)
                # Phase 4: sport-specific model → blending с consensus
                _candidate_ctx = {"sport": sport, "market": market, "selection": item['selection'],
                                  "best_odds": best_odds, "sport_meta": item.get('sport_meta', {})}
                _sport_prob     = get_sport_model_prob(_candidate_ctx)
                _consensus_prob = estimate_model_prob(best_odds, avg_odds, book_count, market, item['selection'])
                _has_meta       = bool(item.get('sport_meta'))
                model_prob = round(_sport_prob * (0.70 if _has_meta else 0.30) +
                                   _consensus_prob * (0.30 if _has_meta else 0.70), 4)
                all_candidates.append({"event_id": item['event_id'], "match": item['match'], "sport": item['sport'], "market": item['market'], "selection": item['selection'], "point": item['point'], "commence_time": item['commence_time'], "odds_best": best_odds, "odds_avg": avg_odds, "book_count": book_count, "model_prob": model_prob, "lineup_confirmed": lineup_confirmed, "injury_fresh_hours": 2.0, "odds_age_minutes": round(item['best_odds_age_minutes'], 2), "source_tier": item['source_tier'], "variance": variance, "bookmaker": item['best_bookmaker'], "max_class": item['max_class']})
    return all_candidates[:request_data['max_candidates']]

def build_scorecard(candidate, implied, ev_raw, ev_calibrated, ci_low, data_quality):
    profile = market_profile(candidate['market'], candidate['selection'], candidate['odds_best'])
    score = 0
    score += 2 if candidate['book_count'] >= 6 else 1 if candidate['book_count'] >= 4 else 0
    score += 2 if candidate['odds_age_minutes'] <= 20 else 1 if candidate['odds_age_minutes'] <= 60 else 0
    score += 1 if candidate['lineup_confirmed'] else 0
    score += 2 if ev_calibrated >= 10 else 1 if ev_calibrated >= 5 else 0
    score += 1 if ci_low >= 2 else 0
    if profile == 'draw':
        score -= 3
    elif profile == 'underdog':
        score -= 2
    elif profile == 'underdog_long':
        score -= 5
    label = 'A' if score >= 6 else 'B' if score >= 4 else 'C' if score >= 2 else 'D'
    return {"grade": label, "score": score, "book_count": candidate['book_count'], "odds_age_minutes": candidate['odds_age_minutes'], "implied_prob": implied, "ev_raw": ev_raw, "ev_calibrated": ev_calibrated, "ci_low": ci_low, "data_quality": data_quality, "profile": profile}


# ══════════════════════════════════════════════════════════════════
# PHASE 4 — SPORT-SPECIFIC MODELS
# Каждая модель возвращает model_prob (0.0–1.0) и meta-dict
# Используется в finalize_candidate для замены AI-угадайки
# ══════════════════════════════════════════════════════════════════

def _clamp(v: float, lo: float = 0.01, hi: float = 0.99) -> float:
    return max(lo, min(hi, v))

# ── FOOTBALL (xG-based) ───────────────────────────────────────────
FOOTBALL_HOME_ADVANTAGE = 0.06   # +6% к home win prob

def football_model(
    home_xg: float = 1.4,
    away_xg: float = 1.1,
    home_form: float = 0.5,   # 0..1, 5 матчей
    away_form: float = 0.5,
    motivation_flag: int = 0,  # +1 / -1 / 0
    is_home: bool = True,
    selection: str = "home",   # home / away / draw
) -> dict:
    """
    Простая xG-модель для football h2h.
    Возвращает model_prob для выбранного исхода.
    """
    # Скорректированный xG
    adj_home = home_xg * (0.85 + home_form * 0.30) + FOOTBALL_HOME_ADVANTAGE
    adj_away = away_xg * (0.85 + away_form * 0.30)
    # Poisson approx для win/draw/loss
    import math
    def poisson_pmf(k, lam):
        return (lam ** k) * math.exp(-lam) / math.factorial(k)
    MAX_GOALS = 8
    home_win = draw = away_win = 0.0
    for h in range(MAX_GOALS + 1):
        for a in range(MAX_GOALS + 1):
            p = poisson_pmf(h, adj_home) * poisson_pmf(a, adj_away)
            if h > a:   home_win += p
            elif h == a: draw    += p
            else:        away_win += p
    # Motivation adjustment
    if motivation_flag == 1:
        if selection == "home": home_win = _clamp(home_win * 1.04)
        if selection == "away": away_win = _clamp(away_win * 1.04)
    elif motivation_flag == -1:
        if selection == "home": home_win = _clamp(home_win * 0.96)
        if selection == "away": away_win = _clamp(away_win * 0.96)
    probs = {"home": home_win, "draw": draw, "away": away_win}
    model_prob = probs.get(selection, 0.33)
    return {
        "model_prob":    round(_clamp(model_prob), 4),
        "home_xg":       round(adj_home, 3),
        "away_xg":       round(adj_away, 3),
        "home_win_prob": round(home_win, 4),
        "draw_prob":     round(draw, 4),
        "away_win_prob": round(away_win, 4),
        "sport":         "football",
    }

def football_total_model(
    home_xg: float = 1.4,
    away_xg: float = 1.1,
    line: float = 2.5,
    selection: str = "over",   # over / under
) -> dict:
    """xG-модель для тоталов."""
    import math
    def poisson_pmf(k, lam):
        return (lam ** k) * math.exp(-lam) / math.factorial(k)
    MAX_GOALS = 10
    over_prob = 0.0
    for h in range(MAX_GOALS + 1):
        for a in range(MAX_GOALS + 1):
            if h + a > line:
                over_prob += poisson_pmf(h, home_xg) * poisson_pmf(a, away_xg)
    under_prob = 1.0 - over_prob
    model_prob = over_prob if selection == "over" else under_prob
    return {
        "model_prob":    round(_clamp(model_prob), 4),
        "over_prob":     round(over_prob, 4),
        "under_prob":    round(under_prob, 4),
        "expected_total": round(home_xg + away_xg, 3),
        "line":           line,
        "sport":          "football",
    }


# ── BASKETBALL (ORTG/DRTG + pace) ────────────────────────────────
BASKETBALL_HOME_COURT = 3.0   # очков

def basketball_model(
    home_ortg: float = 112.0,
    home_drtg: float = 110.0,
    away_ortg: float = 111.0,
    away_drtg: float = 111.0,
    pace:      float = 100.0,  # possessions per 48 min
    line:      float = 0.0,    # spread (отриц = home favorite)
    selection: str = "home",   # home / away
    is_total:  bool = False,
    total_line: float = 220.0,
) -> dict:
    """
    Four Factors / ORTG-DRTG модель.
    """
    # Ожидаемые очки на 100 владений
    home_pts = (home_ortg + away_drtg) / 2.0 + BASKETBALL_HOME_COURT
    away_pts = (away_ortg + home_drtg) / 2.0
    expected_margin = home_pts - away_pts
    expected_total  = (home_pts + away_pts) * pace / 100.0

    # Blowout risk: если маржа > 12 → высокая, > 18 → экстремальная
    blowout_risk = "LOW"
    if abs(expected_margin) > 18: blowout_risk = "EXTREME"
    elif abs(expected_margin) > 12: blowout_risk = "HIGH"
    elif abs(expected_margin) > 7:  blowout_risk = "MEDIUM"

    # Spread prob через нормальное распределение
    import math
    def normal_cdf(x, mu=0, sigma=12):
        return 0.5 * (1 + math.erf((x - mu) / (sigma * math.sqrt(2))))

    if is_total:
        # Σ очков ~ Normal(expected_total, σ=18)
        over_prob  = 1 - normal_cdf(total_line, expected_total, 18)
        model_prob = over_prob if selection == "over" else 1 - over_prob
    else:
        # Маржа ~ Normal(expected_margin, σ=12)
        cover_home = 1 - normal_cdf(line, expected_margin, 12)
        model_prob = cover_home if selection == "home" else 1 - cover_home

    return {
        "model_prob":      round(_clamp(model_prob), 4),
        "expected_margin": round(expected_margin, 2),
        "expected_total":  round(expected_total, 1),
        "blowout_risk":    blowout_risk,
        "home_pts":        round(home_pts, 1),
        "away_pts":        round(away_pts, 1),
        "sport":           "basketball",
    }


# ── TENNIS (surface win% + hold/return) ──────────────────────────
SURFACE_WEIGHTS = {
    "clay":  {"serve_weight": 0.40, "return_weight": 0.60},
    "hard":  {"serve_weight": 0.52, "return_weight": 0.48},
    "grass": {"serve_weight": 0.60, "return_weight": 0.40},
}

def tennis_model(
    p1_surface_winrate: float = 0.55,  # 0..1 на данном покрытии
    p2_surface_winrate: float = 0.50,
    p1_hold_rate:       float = 0.70,  # % удержания подачи
    p2_hold_rate:       float = 0.68,
    p1_return_rate:     float = 0.32,  # % брейков
    p2_return_rate:     float = 0.30,
    surface:            str   = "hard",
    p1_fatigue_days:    int   = 3,     # дней с последнего матча
    p2_fatigue_days:    int   = 2,
    selection:          str   = "p1",  # p1 / p2
) -> dict:
    """
    Surface-adjusted tennis h2h model.
    """
    w = SURFACE_WEIGHTS.get(surface, SURFACE_WEIGHTS["hard"])

    # Взвешенная сила игрока
    p1_strength = (
        p1_surface_winrate * 0.50 +
        p1_hold_rate   * w["serve_weight"] +
        p1_return_rate * w["return_weight"]
    )
    p2_strength = (
        p2_surface_winrate * 0.50 +
        p2_hold_rate   * w["serve_weight"] +
        p2_return_rate * w["return_weight"]
    )

    # Fatigue penalty (>3 дней отдыха = свежий, <2 = усталость)
    def fatigue_penalty(days):
        if days <= 1: return -0.03
        if days == 2: return -0.01
        if days >= 5: return  0.02   # слишком долго без игры
        return 0.0

    p1_strength += fatigue_penalty(p1_fatigue_days)
    p2_strength += fatigue_penalty(p2_fatigue_days)

    total = p1_strength + p2_strength
    p1_prob = p1_strength / total if total > 0 else 0.5
    model_prob = p1_prob if selection == "p1" else 1 - p1_prob

    return {
        "model_prob":    round(_clamp(model_prob), 4),
        "p1_win_prob":   round(_clamp(p1_prob), 4),
        "p2_win_prob":   round(_clamp(1 - p1_prob), 4),
        "surface":       surface,
        "p1_strength":   round(p1_strength, 4),
        "p2_strength":   round(p2_strength, 4),
        "sport":         "tennis",
    }


# ── HOCKEY (Goalie + PP/PK) ───────────────────────────────────────
HOCKEY_HOME_ADVANTAGE = 0.04

def hockey_model(
    home_gf60:  float = 3.0,   # голов за 60 мин (за всё время)
    home_ga60:  float = 2.8,
    away_gf60:  float = 2.9,
    away_ga60:  float = 3.0,
    home_goalie_sv: float = 0.915,  # save%
    away_goalie_sv: float = 0.910,
    home_pp_pct: float = 0.200,     # powerplay %
    away_pp_pct: float = 0.185,
    home_pk_pct: float = 0.820,     # penalty kill %
    away_pk_pct: float = 0.815,
    home_b2b:   bool  = False,      # back-to-back
    away_b2b:   bool  = False,
    selection:  str   = "home",     # home / away / over / under
    total_line: float = 5.5,
) -> dict:
    """
    Goalie-adjusted hockey model с PP/PK поправкой.
    """
    import math

    # Скорректированные xG с учётом голкипера и PP/PK
    home_xg = home_gf60 * (1 - away_goalie_sv) * (1 + home_pp_pct - away_pk_pct)
    away_xg = away_gf60 * (1 - home_goalie_sv) * (1 + away_pp_pct - home_pk_pct)

    # Back-to-back penalty
    if home_b2b: home_xg *= 0.94
    if away_b2b: away_xg *= 0.94

    home_xg += HOCKEY_HOME_ADVANTAGE
    home_xg = max(0.5, home_xg)
    away_xg = max(0.5, away_xg)

    # Poisson win/draw/loss
    def poisson_pmf(k, lam):
        return (lam ** k) * math.exp(-lam) / math.factorial(k)
    MAX_G = 8
    home_win = draw = away_win = 0.0
    over_prob = 0.0
    for h in range(MAX_G + 1):
        for a in range(MAX_G + 1):
            p = poisson_pmf(h, home_xg) * poisson_pmf(a, away_xg)
            if h > a:    home_win  += p
            elif h == a: draw      += p
            else:        away_win  += p
            if h + a > total_line: over_prob += p

    # В хоккее ничья идёт в ОТ/буллиты → корректируем
    home_win_reg = home_win + draw * 0.50
    away_win_reg = away_win + draw * 0.50

    probs = {
        "home":  home_win_reg,
        "away":  away_win_reg,
        "over":  over_prob,
        "under": 1 - over_prob,
    }
    model_prob = probs.get(selection, 0.5)

    return {
        "model_prob":    round(_clamp(model_prob), 4),
        "home_win_prob": round(home_win_reg, 4),
        "away_win_prob": round(away_win_reg, 4),
        "over_prob":     round(over_prob, 4),
        "home_xg":       round(home_xg, 3),
        "away_xg":       round(away_xg, 3),
        "expected_total": round(home_xg + away_xg, 2),
        "sport":         "hockey",
    }


# ── DISPATCHER: выбирает модель по sport ─────────────────────────
def get_sport_model_prob(candidate: dict) -> float:
    """
    Возвращает model_prob из спорт-модели на основе candidate.
    Если спорт неизвестен или данных недостаточно — fallback на
    consensus-based estimate (implied_prob * calibration).
    Candidate должен содержать:
      - sport: str
      - market: str (h2h / spreads / totals)
      - selection: str
      - best_odds: float
      - sport_meta: dict (опционально — расширенные данные)
    """
    sport  = candidate.get("sport", "")
    market = candidate.get("market", "h2h")
    sel    = candidate.get("selection", "").lower()
    meta   = candidate.get("sport_meta", {})
    odds   = float(candidate.get("best_odds", 2.0))

    # implied как baseline fallback
    implied = round(1.0 / odds, 4) if odds > 1.0 else 0.5

    try:
        if sport in ("football", "soccer"):
            if market == "totals":
                result = football_total_model(
                    home_xg   = meta.get("home_xg", 1.35),
                    away_xg   = meta.get("away_xg", 1.10),
                    line      = float(meta.get("line", 2.5)),
                    selection = "over" if "over" in sel else "under",
                )
            else:
                home_sel = "home" if any(w in sel for w in ["home","1","win"]) else (
                           "draw" if any(w in sel for w in ["draw","x","tie"]) else "away")
                result = football_model(
                    home_xg        = meta.get("home_xg", 1.35),
                    away_xg        = meta.get("away_xg", 1.10),
                    home_form      = meta.get("home_form", 0.50),
                    away_form      = meta.get("away_form", 0.50),
                    motivation_flag= int(meta.get("motivation_flag", 0)),
                    selection      = home_sel,
                )
            return result["model_prob"]

        elif sport in ("basketball", "wnba", "nba", "euroleague", "acb"):
            is_total = market == "totals"
            result = basketball_model(
                home_ortg  = meta.get("home_ortg", 112.0),
                home_drtg  = meta.get("home_drtg", 110.0),
                away_ortg  = meta.get("away_ortg", 111.0),
                away_drtg  = meta.get("away_drtg", 111.0),
                pace       = meta.get("pace", 100.0),
                line       = float(meta.get("line", 0.0)),
                selection  = "home" if any(w in sel for w in ["home","1"]) else (
                             "over" if "over" in sel else
                             "under" if "under" in sel else "away"),
                is_total   = is_total,
                total_line = float(meta.get("total_line", 220.0)),
            )
            return result["model_prob"]

        elif sport in ("tennis", "atp", "wta"):
            result = tennis_model(
                p1_surface_winrate = meta.get("p1_surface_winrate", 0.55),
                p2_surface_winrate = meta.get("p2_surface_winrate", 0.50),
                p1_hold_rate       = meta.get("p1_hold_rate", 0.70),
                p2_hold_rate       = meta.get("p2_hold_rate", 0.68),
                p1_return_rate     = meta.get("p1_return_rate", 0.32),
                p2_return_rate     = meta.get("p2_return_rate", 0.30),
                surface            = meta.get("surface", "hard"),
                p1_fatigue_days    = int(meta.get("p1_fatigue_days", 3)),
                p2_fatigue_days    = int(meta.get("p2_fatigue_days", 2)),
                selection          = "p2" if "away" in sel or "p2" in sel else "p1",
            )
            return result["model_prob"]

        elif sport in ("hockey", "nhl"):
            sel_mapped = "over" if "over" in sel else (
                         "under" if "under" in sel else (
                         "away" if "away" in sel else "home"))
            result = hockey_model(
                home_gf60       = meta.get("home_gf60", 3.0),
                home_ga60       = meta.get("home_ga60", 2.8),
                away_gf60       = meta.get("away_gf60", 2.9),
                away_ga60       = meta.get("away_ga60", 3.0),
                home_goalie_sv  = meta.get("home_goalie_sv", 0.915),
                away_goalie_sv  = meta.get("away_goalie_sv", 0.910),
                home_pp_pct     = meta.get("home_pp_pct", 0.200),
                away_pp_pct     = meta.get("away_pp_pct", 0.185),
                home_pk_pct     = meta.get("home_pk_pct", 0.820),
                away_pk_pct     = meta.get("away_pk_pct", 0.815),
                home_b2b        = bool(meta.get("home_b2b", False)),
                away_b2b        = bool(meta.get("away_b2b", False)),
                selection       = sel_mapped,
                total_line      = float(meta.get("total_line", 5.5)),
            )
            return result["model_prob"]

    except Exception:
        pass  # fallback ниже

    # Fallback: consensus-based estimate с небольшой поправкой
    return _clamp(implied * 1.05)  # +5% к implied как минимальная edge


# ── /MODEL команда: показывает расчёт модели ─────────────────────
def format_model_report(sport: str, meta: dict) -> str:
    sport = sport.lower()
    lines = [f"SPORT MODEL: {sport.upper()}"]
    try:
        if sport in ("football", "soccer"):
            r = football_model(**{k: v for k, v in meta.items()
                if k in ("home_xg","away_xg","home_form","away_form","motivation_flag","selection")})
            r2 = football_total_model(
                home_xg=meta.get("home_xg",1.4),
                away_xg=meta.get("away_xg",1.1),
                line=meta.get("line",2.5))
            lines += [
                f"xG:     home {r['home_xg']} | away {r['away_xg']}",
                f"1X2:    home {r['home_win_prob']} | draw {r['draw_prob']} | away {r['away_win_prob']}",
                f"Total {meta.get('line',2.5)}: over {r2['over_prob']} | under {r2['under_prob']}",
                f"Expected goals: {r2['expected_total']}",
            ]
        elif sport in ("basketball","nba","wnba","euroleague","acb"):
            r = basketball_model(**{k: v for k, v in meta.items()
                if k in ("home_ortg","home_drtg","away_ortg","away_drtg","pace","line","selection","is_total","total_line")})
            lines += [
                f"Proj:   home {r['home_pts']} | away {r['away_pts']}",
                f"Margin: {r['expected_margin']} | Total: {r['expected_total']}",
                f"Blowout risk: {r['blowout_risk']}",
                f"model_prob ({meta.get('selection','home')}): {r['model_prob']}",
            ]
        elif sport in ("tennis","atp","wta"):
            r = tennis_model(**{k: v for k, v in meta.items()
                if k in ("p1_surface_winrate","p2_surface_winrate","p1_hold_rate","p2_hold_rate",
                         "p1_return_rate","p2_return_rate","surface","p1_fatigue_days","p2_fatigue_days","selection")})
            lines += [
                f"Surface: {r['surface']}",
                f"P1 win: {r['p1_win_prob']} | P2 win: {r['p2_win_prob']}",
                f"Strength: P1 {r['p1_strength']} vs P2 {r['p2_strength']}",
                f"model_prob ({meta.get('selection','p1')}): {r['model_prob']}",
            ]
        elif sport in ("hockey","nhl"):
            r = hockey_model(**{k: v for k, v in meta.items()
                if k in ("home_gf60","home_ga60","away_gf60","away_ga60","home_goalie_sv","away_goalie_sv",
                         "home_pp_pct","away_pp_pct","home_pk_pct","away_pk_pct","home_b2b","away_b2b",
                         "selection","total_line")})
            lines += [
                f"xG:     home {r['home_xg']} | away {r['away_xg']}",
                f"Total:  {r['expected_total']} | over {r['over_prob']}",
                f"H/A win: {r['home_win_prob']} / {r['away_win_prob']}",
                f"model_prob ({meta.get('selection','home')}): {r['model_prob']}",
            ]
        else:
            lines.append(f"Unknown sport: {sport}")
    except Exception as e:
        lines.append(f"Error: {e}")
    return "\n".join(lines)

def finalize_candidate(candidate, request_data, run_id):
    now = datetime.now(UTC)
    reasons = []
    limits = SPORT_LIMITS.get(candidate['sport'], {"min_book_count": 3, "max_odds_age": 180})
    implied = implied_probability(candidate['odds_best'])
    ev_raw = round(candidate['model_prob'] - implied, 2)
    data_quality = calc_data_quality(candidate['book_count'], candidate['odds_age_minutes'], candidate['lineup_confirmed'], candidate['source_tier'])
    sport_key = SPORT_MAP.get(candidate['sport'], ('', 'default', 2, 'CORE'))[1]
    factor = calibration_factor(candidate['book_count'], candidate['odds_age_minutes'], candidate['lineup_confirmed'], candidate['market'], data_quality, candidate['odds_best'], candidate['selection'], sport_key=sport_key)
    ev_calibrated = round(ev_raw * factor, 2)
    ci_low = calc_ci_low(ev_calibrated, candidate['book_count'], candidate['odds_age_minutes'], candidate['variance'], data_quality, candidate['odds_best'], candidate['market'], candidate['selection'])
    decision, stake, cap_value = 'PASS', 0.0, 0.0
    profile = market_profile(candidate['market'], candidate['selection'], candidate['odds_best'])
    if candidate['commence_time'] < now:
        reasons.append('EXPIRED_EVENT')
    elif (candidate['commence_time'] - now) < timedelta(minutes=15):
        reasons.append('TOO_CLOSE')
    elif candidate['odds_age_minutes'] > limits['max_odds_age']:
        reasons.append('STALE_ODDS')
    elif candidate['book_count'] < limits['min_book_count']:
        reasons.append('LOW_BOOK_COUNT')
    elif request_data['mode'] == 'EMERGENCY' and candidate['variance'] in ['HIGH', 'EXTREME']:
        reasons.append('HIGH_VARIANCE')
    elif profile == 'draw' and (data_quality != 'HIGH' or candidate['book_count'] < 5):
        reasons.append('DRAW_GUARDRAIL')
    elif profile == 'underdog_long' and (data_quality != 'HIGH' or candidate['book_count'] < 6):
        reasons.append('UNDERDOG_GUARDRAIL')
    elif count_today_bets() >= MAX_BETS_PER_DAY.get(request_data['mode'], 5):
        reasons.append('MAX_BETS_PER_DAY')
    elif is_same_match_blocked(candidate['match'], candidate['market']):
        reasons.append('SAME_MATCH_BLOCK')
    elif ev_calibrated < MODE_RULES[request_data['mode']]['min_ev']:
        reasons.append('EV_TOO_LOW')
    elif ci_low < 0:
        reasons.append('CI_LOW_NEGATIVE')
    else:
        decision = class_from_ev(ev_calibrated, ci_low, data_quality, candidate['odds_best'], candidate['market'], candidate['selection'])
        if candidate['source_tier'] >= 3 and CLASS_ORDER[decision] > CLASS_ORDER['MICRO']:
            decision = 'MICRO'
            reasons.append('SPORT_TIER_CAP')
        if (not candidate['lineup_confirmed']) and candidate['market'] in ['spreads', 'totals']:
            if request_data['strict']:
                decision = 'PASS'
            elif CLASS_ORDER[decision] > CLASS_ORDER['MICRO']:
                decision = 'MICRO'
            reasons.append('LINEUP_PENDING')
        if profile == 'draw' and CLASS_ORDER[decision] > CLASS_ORDER['MICRO']:
            decision = 'MICRO'
            reasons.append('DRAW_CAP')
        if profile == 'underdog_long' and CLASS_ORDER[decision] > CLASS_ORDER['MICRO']:
            decision = 'MICRO'
            reasons.append('LONGSHOT_CAP')
        decision = cap_class(decision, candidate['max_class'])
        if candidate['book_count'] < 5:
            reasons.append('NO_STRONG_CONSENSUS')
        if decision == 'PASS':
            reasons.append('RULES_BLOCK')
        else:
            reasons.extend(['EDGE_OK', 'DATA_QUALITY_' + data_quality, 'PROFILE_' + profile.upper(), 'CLASS_' + decision])
            cap_value = risk_cap(request_data['mode'], decision)
            stake = calc_stake(request_data['bank'], decision, ev_calibrated, candidate['odds_best'], cap_value, candidate['market'], candidate['selection'])
    scorecard = build_scorecard(candidate, implied, ev_raw, ev_calibrated, ci_low, data_quality)
    return {"pick_id": uuid.uuid4().hex[:12], "run_id": run_id, "generated_at": datetime.now(UTC).isoformat(), "event_id": candidate['event_id'], "match": candidate['match'], "sport": candidate['sport'], "market": candidate['market'], "selection": candidate['selection'], "point": candidate['point'], "commence_time": candidate['commence_time'].isoformat(), "best_odds": candidate['odds_best'], "avg_odds": candidate['odds_avg'], "bookmaker": candidate['bookmaker'], "book_count": candidate['book_count'], "model_prob": candidate['model_prob'], "implied_prob": implied, "ev_raw": ev_raw, "ev_calibrated": ev_calibrated, "ci_low": ci_low, "risk_cap": cap_value, "data_quality": data_quality, "decision": decision, "stake": stake, "reasons": reasons, "scorecard": scorecard}

def sort_results(results):
    dq_rank = {'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}
    return sorted(results, key=lambda x: (CLASS_ORDER.get(x['decision'], 0), x['scorecard']['score'], x['ev_calibrated'], x['ci_low'], dq_rank.get(x['data_quality'], 0)), reverse=True)

def best_per_match(results):
    grouped = {}
    for item in results:
        grouped.setdefault(item['match'], []).append(item)
    final = []
    for items in grouped.values():
        favored = [x for x in items if x['decision'] != 'PASS']
        pool = favored if favored else items
        best = sorted(pool, key=lambda x: (CLASS_ORDER.get(x['decision'], 0), x['scorecard']['score'], x['ev_calibrated'], x['ci_low']), reverse=True)[0]
        final.append(best)
    return sorted(final, key=lambda x: (CLASS_ORDER.get(x['decision'], 0), x['scorecard']['score'], x['ev_calibrated']), reverse=True)

def record_accepted_picks(summary):
    history = load_pick_history()
    existing = {item.get('pick_id') for item in history}
    added = 0
    for item in summary['results']:
        if item['decision'] == 'PASS' or item['pick_id'] in existing:
            continue
        history.append({
            'pick_id': item['pick_id'],
            'run_id': item['run_id'],
            'generated_at': item['generated_at'],
            'event_id': item['event_id'],
            'match': item['match'],
            'sport': item['sport'],
            'market': item['market'],
            'selection': item['selection'],
            'best_odds': item['best_odds'],
            'stake': item['stake'],
            'decision': item['decision'],
            'profile': item['scorecard']['profile'],
            'grade': item['scorecard']['grade'],
            'ev_calibrated': item['ev_calibrated'],
            'ci_low': item['ci_low'],
            'status': 'OPEN',
            'settled_result': '',
            'pnl': 0.0,
            'settled_at': ''
        })
        added += 1
    save_pick_history(history)
    return added

def compute_pnl(odds, stake, result):
    if result == 'WIN':
        return round((odds - 1.0) * stake, 2)
    if result == 'LOSS':
        return round(-stake, 2)
    return 0.0

def format_summary(summary):
    lines = ['Run: ' + str(summary['run_id']), 'Mode: ' + str(summary['request']['mode']), 'Sports: ' + ', '.join(summary['request']['sports']), 'Teams: ' + (', '.join(summary['request']['team_filters']) if summary['request']['team_filters'] else 'all'), 'Status: ' + str(summary['message']), 'Candidates: ' + str(summary['candidates_count']), 'Accepted: ' + str(summary['accepted_count']), 'Rejected: ' + str(summary['rejected_count']), '']
    for item in summary['results'][:10]:
        if item['decision'] == 'PASS':
            lines.append('PASS | ' + item['match'] + ' | ' + item['selection'] + ' | ' + ', '.join(item['reasons'][:2]))
        else:
            lines.append(item['decision'] + ' | ' + item['match'] + ' | ' + item['selection'] + ' | odds ' + str(item['best_odds']) + ' | stake ' + str(item['stake']) + ' | EV ' + str(item['ev_calibrated']) + ' | CI ' + str(item['ci_low']) + ' | grade ' + item['scorecard']['grade'] + ' | ' + item['scorecard']['profile'])
    if summary.get('accepted_count', 0) == 0:
        lines.append('')
        lines.append(WEAK_SLATE_MSG)
    return '\n'.join(lines)

def format_top_picks(summary):
    accepted = [x for x in best_per_match(summary['results']) if x['decision'] != 'PASS']
    if not accepted:
        return 'TOP PICKS\n- none'
    lines = ['TOP PICKS']
    for item in accepted[:5]:
        lines.append('- ' + item['match'] + ' | ' + item['decision'] + ' | ' + item['selection'] + ' | odds ' + str(item['best_odds']) + ' | stake ' + str(item['stake']) + ' | grade ' + item['scorecard']['grade'] + ' | ' + item['scorecard']['profile'])
    return '\n'.join(lines)

def format_match_report(summary):
    picks = best_per_match(summary['results'])
    if not picks:
        return 'NO MATCHES FOUND'
    lines = ['MATCH REPORT']
    for best in picks[:10]:
        if best['decision'] == 'PASS':
            lines.append('- ' + best['match'] + ' -> PASS (' + ', '.join(best['reasons'][:2]) + ')')
        else:
            lines.append('- ' + best['match'] + ' -> ' + best['decision'] + ' | ' + best['selection'] + ' | odds ' + str(best['best_odds']) + ' | stake ' + str(best['stake']) + ' | grade ' + best['scorecard']['grade'] + ' | ' + best['scorecard']['profile'])
    return '\n'.join(lines)

def write_report(summary):
    file_path = REPORTS_DIR / (str(datetime.now().date()) + '_' + summary['run_id'] + '_report.txt')
    rows = ['DAILY REPORT | ' + str(datetime.now().date()), 'Request: ' + str(summary['request']['raw_text']), 'Mode: ' + str(summary['request']['mode']), 'Sports: ' + ', '.join(summary['request']['sports']), 'Team filters: ' + (', '.join(summary['request']['team_filters']) if summary['request']['team_filters'] else 'all'), 'Candidates: ' + str(summary['candidates_count']), 'Accepted: ' + str(summary['accepted_count']), 'Rejected: ' + str(summary['rejected_count']), 'Status: ' + str(summary['message']), '', format_top_picks(summary), '', format_match_report(summary), '']
    for idx, item in enumerate(summary['results'], start=1):
        rows.extend([str(idx) + ') ' + item['match'], 'Sport: ' + item['sport'], 'Market: ' + item['market'], 'Selection: ' + item['selection'], 'Odds: ' + str(item['best_odds']) + ' | Avg: ' + str(item['avg_odds']), 'Bookmaker: ' + item['bookmaker'] + ' | Books: ' + str(item['book_count']), 'ModelProb: ' + str(item['model_prob']) + ' | Implied: ' + str(item['implied_prob']), 'EV raw: ' + str(item['ev_raw']) + ' | EV calibrated: ' + str(item['ev_calibrated']) + ' | CI low: ' + str(item['ci_low']), 'Decision: ' + item['decision'] + ' | Stake: ' + str(item['stake']) + ' | Risk cap: ' + str(item['risk_cap']), 'Scorecard: grade ' + item['scorecard']['grade'] + ' | score ' + str(item['scorecard']['score']) + ' | profile ' + item['scorecard']['profile'], 'Reasons: ' + ', '.join(item['reasons']), ''])
    file_path.write_text('\n'.join(rows), encoding='utf-8')
    return str(file_path)

def write_audit(summary):
    file_path = LOGS_DIR / (str(datetime.now().date()) + '_' + summary['run_id'] + '_audit.json')
    file_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return str(file_path)

def read_runs():
    ensure_runs_seed_from_latest()
    items = []
    if not RUNS_PATH.exists():
        return items
    for line in RUNS_PATH.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except Exception:
            continue
    return items

def summarize_history(history):
    settled = [x for x in history if x.get('status') == 'SETTLED']
    open_items = [x for x in history if x.get('status') == 'OPEN']
    wins = sum(1 for x in settled if x.get('settled_result') == 'WIN')
    losses = sum(1 for x in settled if x.get('settled_result') == 'LOSS')
    pushes = sum(1 for x in settled if x.get('settled_result') == 'PUSH')
    total_stake = round(sum(float(x.get('stake', 0.0)) for x in settled), 2)
    total_pnl = round(sum(float(x.get('pnl', 0.0)) for x in settled), 2)
    roi = round((total_pnl / total_stake) * 100.0, 2) if total_stake > 0 else 0.0
    return {"total": len(history), "open": len(open_items), "settled": len(settled), "wins": wins, "losses": losses, "pushes": pushes, "total_stake": total_stake, "total_pnl": total_pnl, "roi": roi}

def group_stats(history, key):
    settled = [x for x in history if x.get('status') == 'SETTLED']
    groups = {}
    for item in settled:
        group = item.get(key, 'unknown') or 'unknown'
        groups.setdefault(group, {"count": 0, "stake": 0.0, "pnl": 0.0, "wins": 0, "losses": 0, "pushes": 0})
        g = groups[group]
        g['count'] += 1
        g['stake'] += float(item.get('stake', 0.0))
        g['pnl'] += float(item.get('pnl', 0.0))
        result = item.get('settled_result')
        if result == 'WIN':
            g['wins'] += 1
        elif result == 'LOSS':
            g['losses'] += 1
        elif result == 'PUSH':
            g['pushes'] += 1
    rows = []
    for group, data in groups.items():
        stake = round(data['stake'], 2)
        pnl = round(data['pnl'], 2)
        roi = round((pnl / stake) * 100.0, 2) if stake > 0 else 0.0
        rows.append((group, data['count'], data['wins'], data['losses'], data['pushes'], stake, pnl, roi))
    return sorted(rows, key=lambda x: (x[6], x[1]), reverse=True)

def format_history(limit=10):
    runs = read_runs()
    if not runs:
        return 'Пока нет history. Сначала запусти хотя бы один /auto'
    return '\n'.join([str(item.get('generated_at', '')) + ' | accepted ' + str(item.get('accepted_count', 0)) + ' | rejected ' + str(item.get('rejected_count', 0)) + ' | ' + str(item.get('request', '')) for item in runs[-limit:][::-1]])

def format_global_summary():
    runs = read_runs()
    if not runs:
        return 'Пока нет summary. Сначала запусти хотя бы один /auto'
    watchlist = load_watchlist()
    hist = summarize_history(load_pick_history())
    return '\n'.join(['Runs: ' + str(len(runs)), 'Accepted total: ' + str(sum(int(item.get('accepted_count', 0)) for item in runs)), 'Rejected total: ' + str(sum(int(item.get('rejected_count', 0)) for item in runs)), 'Watchlist teams: ' + str(len(watchlist)), 'Settled picks: ' + str(hist['settled']), 'PnL: ' + str(hist['total_pnl']), 'ROI%: ' + str(hist['roi'])])

def format_latest():
    runs = read_runs()
    if runs:
        item = runs[-1]
        return '\n'.join(['Run: ' + str(item.get('run_id', '')), 'Generated: ' + str(item.get('generated_at', '')), 'Request: ' + str(item.get('request', '')), 'Accepted: ' + str(item.get('accepted_count', 0)), 'Rejected: ' + str(item.get('rejected_count', 0)), 'Report: ' + str(item.get('report_path', ''))])
    return 'Пока нет latest. Сначала запусти хотя бы один /auto'

def format_watchlist():
    watchlist = load_watchlist()
    if not watchlist:
        return 'Watchlist пуст. Добавь командой /watch chelsea'
    return '\n'.join(['WATCHLIST'] + [str(i + 1) + ') ' + str(item.get('team', '')) + ' | sport ' + str(item.get('sport', '')) for i, item in enumerate(watchlist)])

def format_open_picks(limit=20):
    history = load_pick_history()
    open_items = [x for x in history if x.get('status') == 'OPEN']
    if not open_items:
        return 'OPEN PICKS\n- none'
    lines = ['OPEN PICKS']
    for item in open_items[-limit:][::-1]:
        lines.append('- ' + item['pick_id'] + ' | ' + item['match'] + ' | ' + item['selection'] + ' | ' + item['decision'] + ' | odds ' + str(item['best_odds']) + ' | stake ' + str(item['stake']))
    return '\n'.join(lines)

def format_stats_report():
    history = load_pick_history()
    meta = summarize_history(history)
    lines = ['STATS', 'Total picks: ' + str(meta['total']), 'Open: ' + str(meta['open']), 'Settled: ' + str(meta['settled']), 'W/L/P: ' + str(meta['wins']) + '/' + str(meta['losses']) + '/' + str(meta['pushes']), 'Stake: ' + str(meta['total_stake']), 'PnL: ' + str(meta['total_pnl']), 'ROI%: ' + str(meta['roi']), '']
    for title, key in [('By market', 'market'), ('By profile', 'profile'), ('By decision', 'decision'), ('By sport', 'sport')]:
        rows = group_stats(history, key)
        lines.append(title.upper())
        if not rows:
            lines.append('- none')
        else:
            for row in rows[:10]:
                lines.append('- ' + str(row[0]) + ' | n ' + str(row[1]) + ' | W/L/P ' + str(row[2]) + '/' + str(row[3]) + '/' + str(row[4]) + ' | pnl ' + str(row[6]) + ' | roi ' + str(row[7]) + '%')
        lines.append('')
    return '\n'.join(lines).strip()



def load_latest_summary():
    if not AUDIT_PATH.exists():
        return None
    try:
        return json.loads(AUDIT_PATH.read_text(encoding='utf-8'))
    except Exception:
        return None

def format_quick():
    summary = load_latest_summary()
    if not summary:
        return 'QUICK\n- no runs yet'
    picks = best_per_match(summary.get('results', []))
    accepted = [x for x in picks if x.get('decision') != 'PASS']
    req = summary.get('request', {})
    lines = [
        'QUICK',
        'Run: ' + str(summary.get('run_id', '')) + ' | mode ' + str(req.get('mode', '')),
        'Sports: ' + ', '.join(req.get('sports', [])) + ' | candidates ' + str(summary.get('candidates_count', 0)) + ' | accepted ' + str(summary.get('accepted_count', 0)),
        ''
    ]
    if accepted:
        for idx, item in enumerate(accepted[:5], start=1):
            lines.append(str(idx) + ') ' + item['match'] + ' | ' + item['decision'] + ' | ' + item['selection'] + ' | ' + str(item['best_odds']) + ' | stake ' + str(item['stake']))
    else:
        pass_items = picks[:5]
        if not pass_items:
            lines.append('- no picks')
        else:
            lines.append('- no picks (all PASS)')
            for item in pass_items:
                lines.append('  ' + item['match'] + ' | PASS (' + ', '.join(item.get('reasons', [])[:1]) + ')')
    return '\n'.join(lines)

def _today_utc_iso():
    return datetime.now(UTC).date().isoformat()

def _items_for_day(items, day_iso=None):
    day_iso = day_iso or _today_utc_iso()
    out = []
    for item in items:
        stamp = str(item.get('generated_at', ''))
        if stamp[:10] == day_iso:
            out.append(item)
    return out

def format_day_summary(day_iso=None):
    day_iso = day_iso or _today_utc_iso()
    history = load_pick_history()
    today_items = _items_for_day(history, day_iso)
    if not today_items:
        return 'DAY SUMMARY ' + day_iso + '\n- no picks today'
    settled = [x for x in today_items if x.get('status') == 'SETTLED']
    open_items = [x for x in today_items if x.get('status') == 'OPEN']
    wins = sum(1 for x in settled if x.get('settled_result') == 'WIN')
    losses = sum(1 for x in settled if x.get('settled_result') == 'LOSS')
    pushes = sum(1 for x in settled if x.get('settled_result') == 'PUSH')
    stake = round(sum(float(x.get('stake', 0.0)) for x in settled), 2)
    pnl = round(sum(float(x.get('pnl', 0.0)) for x in settled), 2)
    roi = round((pnl / stake) * 100.0, 2) if stake > 0 else 0.0
    lines = [
        'DAY SUMMARY ' + day_iso,
        'Picks: total ' + str(len(today_items)) + ' | settled ' + str(len(settled)) + ' | open ' + str(len(open_items)),
        'W/L/P: ' + str(wins) + '/' + str(losses) + '/' + str(pushes) + ' | stake ' + str(stake) + ' | PnL ' + str(pnl) + ' | ROI ' + str(roi) + '%',
        '',
        'BY SPORT'
    ]
    sport_groups = {}
    for item in settled:
        sport = item.get('sport', 'unknown') or 'unknown'
        sport_groups.setdefault(sport, {'n': 0, 'stake': 0.0, 'pnl': 0.0})
        sport_groups[sport]['n'] += 1
        sport_groups[sport]['stake'] += float(item.get('stake', 0.0))
        sport_groups[sport]['pnl'] += float(item.get('pnl', 0.0))
    if not sport_groups:
        lines.append('- no settled picks yet')
    else:
        for sport, data in sorted(sport_groups.items()):
            s = round(data['stake'], 2)
            pp = round(data['pnl'], 2)
            rr = round((pp / s) * 100.0, 2) if s > 0 else 0.0
            lines.append('- ' + sport + ' | n ' + str(data['n']) + ' | pnl ' + str(pp) + ' | roi ' + str(rr) + '%')
    return '\n'.join(lines)

def format_stats_report_compact():
    history = load_pick_history()
    meta = summarize_history(history)
    lines = ['STATS', 'Total picks: ' + str(meta['total']), 'Open: ' + str(meta['open']) + ' | Settled: ' + str(meta['settled']), 'W/L/P: ' + str(meta['wins']) + '/' + str(meta['losses']) + '/' + str(meta['pushes']), 'Stake: ' + str(meta['total_stake']) + ' | PnL: ' + str(meta['total_pnl']) + ' | ROI%: ' + str(meta['roi']), '']
    sections = [('By market', 'market'), ('By profile', 'profile'), ('By decision', 'decision'), ('By sport', 'sport')]
    for title, key in sections:
        rows = group_stats(history, key)
        lines.append(title.upper())
        if not rows:
            lines.append('- none')
        else:
            best = rows[0]
            lines.append('BEST: ' + str(best[0]) + ' | n ' + str(best[1]) + ' | pnl ' + str(best[6]) + ' | roi ' + str(best[7]) + '%')
            for row in rows[:5]:
                lines.append('- ' + str(row[0]) + ' | n ' + str(row[1]) + ' | W/L/P ' + str(row[2]) + '/' + str(row[3]) + '/' + str(row[4]) + ' | pnl ' + str(row[6]) + ' | roi ' + str(row[7]) + '%')
        lines.append('')
    return '\n'.join(lines).strip()

def settle_pick(pick_id, result):
    result = result.upper()
    if result not in ['WIN', 'LOSS', 'PUSH']:
        return False, 'Используй только WIN, LOSS или PUSH'
    history = load_pick_history()
    for item in history:
        if item.get('pick_id') == pick_id:
            item['status'] = 'SETTLED'
            item['settled_result'] = result
            item['settled_at'] = datetime.now(UTC).isoformat()
            item['pnl'] = compute_pnl(float(item.get('best_odds', 0.0)), float(item.get('stake', 0.0)), result)
            save_pick_history(history)
            return True, item
    return False, 'pick_id не найден'

def run_auto_pipeline(request_text, dry_run=False):
    request_data = parse_request(request_text)
    request_data['dry_run'] = dry_run
    run_id = uuid.uuid4().hex[:10]
    candidates = fetch_candidates(request_data)
    results = sort_results([finalize_candidate(candidate, request_data, run_id) for candidate in candidates])
    accepted = [x for x in results if x['decision'] != 'PASS']
    summary = {"run_id": run_id, "request": request_data, "generated_at": datetime.now(UTC).isoformat(), "candidates_count": len(results), "accepted_count": len(accepted), "rejected_count": len(results) - len(accepted), "status": 'OK' if results else 'NO_CANDIDATES', "message": 'NO BETS / ALL PASS' if results and not accepted else ('NO MATCHES FOUND' if not results else 'OK'), "results": results}
    if not dry_run:
        report_path = write_report(summary)
        audit_path = write_audit(summary)
        record_accepted_picks(summary)
        LAST_RUN_PATH.write_text(format_summary(summary) + '\n\n' + format_top_picks(summary) + '\n\n' + format_match_report(summary), encoding='utf-8')
        AUDIT_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        append_run_line({"run_id": run_id, "generated_at": summary['generated_at'], "request": request_text, "accepted_count": summary['accepted_count'], "rejected_count": summary['rejected_count'], "report_path": report_path, "audit_path": audit_path})
    return summary

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_long(update.message, 'Я готов.\n\nОсновные команды:\n- /auto today epl seriea strict\n- /scanwatch\n- /openpicks\n- /settle PICK_ID WIN\n- /stats\n- /quick\n- /day\n- /template_morning\n- /template_evening')

async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = ' '.join(context.args).strip()
    try:
        summary = run_auto_pipeline('AUTO ' + args if args else 'AUTO today football strict', dry_run=False)
        await reply_long(update.message, format_summary(summary) + '\n\n' + format_top_picks(summary) + '\n\n' + format_match_report(summary))
    except Exception as e:
        await reply_long(update.message, 'ERROR_REPORT: ' + type(e).__name__ + ': ' + str(e))

async def dryrun_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = ' '.join(context.args).strip()
    try:
        summary = run_auto_pipeline('AUTO ' + args if args else 'AUTO today football strict', dry_run=True)
        await reply_long(update.message, '[DRYRUN]\n' + format_summary(summary) + '\n\n' + format_top_picks(summary) + '\n\n' + format_match_report(summary))
    except Exception as e:
        await reply_long(update.message, 'ERROR_REPORT: ' + type(e).__name__ + ': ' + str(e))

async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_long(update.message, LAST_RUN_PATH.read_text(encoding='utf-8') if LAST_RUN_PATH.exists() else 'Пока нет last_run.txt')

async def audit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_long(update.message, AUDIT_PATH.read_text(encoding='utf-8') if AUDIT_PATH.exists() else 'Пока нет audit.json')

async def latest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_long(update.message, format_latest())

async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_long(update.message, format_history())

async def summary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_long(update.message, format_global_summary())

async def watch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = ' '.join(context.args).strip().lower()
    if not args:
        await reply_long(update.message, 'Используй так: /watch chelsea')
        return
    key = normalize_team_token(args)
    sport = DEFAULT_TEAM_SPORT.get(key, 'epl')
    watchlist = load_watchlist()
    if any(item.get('team') == key for item in watchlist):
        await reply_long(update.message, 'Уже в watchlist: ' + key)
        return
    watchlist.append({'team': key, 'sport': sport})
    save_watchlist(watchlist)
    await reply_long(update.message, 'Добавил в watchlist: ' + key + ' | sport ' + sport)

async def unwatch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = ' '.join(context.args).strip().lower()
    if not args:
        await reply_long(update.message, 'Используй так: /unwatch chelsea')
        return
    key = normalize_team_token(args)
    watchlist = load_watchlist()
    save_watchlist([item for item in watchlist if item.get('team') != key])
    await reply_long(update.message, 'Удалил из watchlist: ' + key)

async def watchlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_long(update.message, format_watchlist())

async def scanwatch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    request_text = build_scanwatch_request()
    if not request_text:
        await reply_long(update.message, 'Watchlist пуст. Добавь команды через /watch')
        return
    try:
        summary = run_auto_pipeline(request_text, dry_run=False)
        await reply_long(update.message, format_summary(summary) + '\n\n' + format_top_picks(summary) + '\n\n' + format_match_report(summary))
    except Exception as e:
        await reply_long(update.message, 'ERROR_REPORT: ' + type(e).__name__ + ': ' + str(e))

async def openpicks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_long(update.message, format_open_picks())

async def settle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await reply_long(update.message, 'Используй так: /settle PICK_ID WIN')
        return
    ok, result = settle_pick(context.args[0].strip(), context.args[1].strip())
    if not ok:
        await reply_long(update.message, str(result))
        return
    item = result
    await reply_long(update.message, 'SETTLED | ' + item['pick_id'] + ' | ' + item['match'] + ' | ' + item['selection'] + ' | ' + item['settled_result'] + ' | pnl ' + str(item['pnl']))

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_long(update.message, format_stats_report_compact())

async def quick_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_long(update.message, format_quick())

async def day_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_long(update.message, format_day_summary())

async def template_morning_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_long(update.message, 'TEMPLATE MORNING\nСкопируй и отправь так:\n\n/auto today epl seriea strict')

async def template_evening_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_long(update.message, 'TEMPLATE EVENING\nСкопируй и отправь так:\n\n/scanwatch')


# ══════════════════════════════════════════════════════════════════
# RISK ENGINE
# ══════════════════════════════════════════════════════════════════

def load_bank() -> dict:
    if not BANK_PATH.exists():
        return {"balance": 1000.0, "peak": 1000.0, "history": []}
    try:
        return json.loads(BANK_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"balance": 1000.0, "peak": 1000.0, "history": []}

def save_bank(bank_data: dict):
    BANK_PATH.write_text(json.dumps(bank_data, ensure_ascii=False, indent=2), encoding="utf-8")

def set_bank(new_balance: float) -> dict:
    bank = load_bank()
    old = bank.get("balance", 1000.0)
    bank["balance"] = round(new_balance, 2)
    bank["peak"] = max(bank.get("peak", new_balance), new_balance)
    bank.setdefault("history", []).append({
        "pick_id": "MANUAL_SET", "pnl": round(new_balance - old, 2),
        "balance_after": bank["balance"], "timestamp": datetime.now(UTC).isoformat()
    })
    save_bank(bank)
    return bank

def load_daily_exposure() -> dict:
    today = datetime.now(UTC).date().isoformat()
    if not DAILY_EXPOSURE_PATH.exists():
        return {"date": today, "exposed": 0.0}
    try:
        data = json.loads(DAILY_EXPOSURE_PATH.read_text(encoding="utf-8"))
        return data if data.get("date") == today else {"date": today, "exposed": 0.0}
    except Exception:
        return {"date": today, "exposed": 0.0}

def save_daily_exposure(data: dict):
    DAILY_EXPOSURE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def daily_exposure_limit(balance: float, mode: str) -> float:
    rates = {"FROZEN": 0.02, "EMERGENCY": 0.04, "NORMAL": 0.06, "GROWTH": 0.08}
    return round(balance * rates.get(mode, 0.06), 2)

def register_exposure(stake: float):
    exp = load_daily_exposure()
    exp["exposed"] = round(exp.get("exposed", 0.0) + stake, 2)
    save_daily_exposure(exp)

def update_bank_after_settle(pick_id: str, pnl: float):
    bank = load_bank()
    bank["balance"] = round(bank["balance"] + pnl, 2)
    bank["peak"] = max(bank["peak"], bank["balance"])
    bank.setdefault("history", []).append({
        "pick_id": pick_id, "pnl": pnl,
        "balance_after": bank["balance"], "timestamp": datetime.now(UTC).isoformat()
    })
    save_bank(bank)

def count_today_bets() -> int:
    today = datetime.now(UTC).date().isoformat()
    history = load_pick_history()
    return sum(1 for x in history if x.get("status") == "OPEN" and str(x.get("generated_at", ""))[:10] == today)

def is_same_match_blocked(match: str, market: str) -> bool:
    today = datetime.now(UTC).date().isoformat()
    history = load_pick_history()
    return any(
        x.get("status") == "OPEN"
        and str(x.get("generated_at", ""))[:10] == today
        and x.get("match") == match and x.get("market") == market
        for x in history
    )

def format_risk_report() -> str:
    bank = load_bank()
    balance = bank.get("balance", 1000.0)
    peak = bank.get("peak", balance)
    mode = detect_mode(balance)
    exp = load_daily_exposure()
    today_exp = round(exp.get("exposed", 0.0), 2)
    limit = daily_exposure_limit(balance, mode)
    drawdown = round((peak - balance) / peak * 100.0, 1) if peak > 0 else 0.0
    history = bank.get("history", [])
    total_pnl = round(sum(h.get("pnl", 0.0) for h in history), 2)
    today_bets = count_today_bets()
    max_bets = MAX_BETS_PER_DAY.get(mode, 5)
    lines = [
        "RISK REPORT",
        f"Balance:    {balance}",
        f"Peak:       {peak}",
        f"Drawdown:   {drawdown}%",
        f"Mode:       {mode}",
        f"Exp today:  {today_exp} / {limit} ({round(limit/balance*100,1) if balance else 0}% cap)",
        f"Bets today: {today_bets} / {max_bets}",
        f"Total PnL:  {total_pnl}",
        f"Txns:       {len(history)}",
    ]
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════
# VALIDATE
# ══════════════════════════════════════════════════════════════════

def validate_config() -> list:
    issues = []
    bank = load_bank()
    balance = bank.get("balance", 0.0)
    if balance <= 0:
        issues.append("BANK_ZERO_OR_NEGATIVE: " + str(balance))
    if balance < 100:
        issues.append("BANK_CRITICALLY_LOW: " + str(balance))
    api_key = os.getenv("ODDS_API_KEY", "")
    if not api_key:
        issues.append("ODDS_API_KEY_MISSING")
    elif len(api_key) < 10:
        issues.append("ODDS_API_KEY_SUSPICIOUS")
    mode = detect_mode(balance)
    if mode not in MODE_RULES:
        issues.append("MODE_RULES_MISSING: " + mode)
    for sport, lims in SPORT_LIMITS.items():
        if lims.get("min_book_count", 0) < 1:
            issues.append("SPORT_LIMIT_BAD: " + sport)
    exp = load_daily_exposure()
    limit = daily_exposure_limit(balance, mode)
    if exp.get("exposed", 0.0) > limit:
        issues.append("DAILY_EXPOSURE_EXCEEDED: " + str(exp.get("exposed")) + " > " + str(limit))
    today_count = count_today_bets()
    max_bets = MAX_BETS_PER_DAY.get(mode, 5)
    if today_count >= max_bets:
        issues.append("MAX_BETS_REACHED: " + str(today_count) + "/" + str(max_bets))
    return issues

def format_validate_report() -> str:
    issues = validate_config()
    bank = load_bank()
    balance = bank.get("balance", 1000.0)
    mode = detect_mode(balance)
    today_count = count_today_bets()
    max_bets = MAX_BETS_PER_DAY.get(mode, 5)
    exp = load_daily_exposure()
    limit = daily_exposure_limit(balance, mode)
    lines = [
        "CONFIG VALIDATOR",
        f"Bank: {balance} | Mode: {mode}",
        f"Bets today: {today_count}/{max_bets} | Exposure: {exp.get('exposed',0.0)}/{limit}",
        "",
    ]
    if not issues:
        lines.append("\u2705 All checks passed.")
    else:
        lines.append(f"\u26a0\ufe0f {len(issues)} issue(s):")
        for iss in issues:
            lines.append("  - " + iss)
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════
# CLOSING LINE TRACKING
# ══════════════════════════════════════════════════════════════════

def load_closing_lines() -> list:
    if not CLOSING_LINE_PATH.exists():
        return []
    try:
        return json.loads(CLOSING_LINE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []

def save_closing_lines(items: list):
    CLOSING_LINE_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

def record_closing_line(pick_id, match, market, selection, open_odds, closing_odds):
    items = load_closing_lines()
    clv = round((open_odds / closing_odds - 1) * 100, 2)
    for x in items:
        if x["pick_id"] == pick_id:
            x["closing_odds"] = closing_odds
            x["clv"] = clv
            x["recorded_at"] = datetime.now(UTC).isoformat()
            save_closing_lines(items)
            return
    items.append({
        "pick_id": pick_id, "match": match, "market": market,
        "selection": selection, "open_odds": open_odds,
        "closing_odds": closing_odds, "clv": clv,
        "recorded_at": datetime.now(UTC).isoformat()
    })
    save_closing_lines(items)

def format_clv_report(limit: int = 20) -> str:
    items = load_closing_lines()
    if not items:
        return "CLV REPORT\n- no data yet\nUse: /clv PICK_ID CLOSING_ODDS"
    items_sorted = sorted(items, key=lambda x: x.get("recorded_at", ""), reverse=True)
    positive = sum(1 for x in items if x.get("clv", 0) > 0)
    avg_clv = round(sum(x.get("clv", 0) for x in items) / len(items), 2)
    lines = [
        "CLV REPORT",
        f"Total: {len(items)} | Positive CLV: {positive} | Avg CLV: {avg_clv}%",
        ""
    ]
    for x in items_sorted[:limit]:
        clv = x.get("clv", 0)
        sign = "+" if clv > 0 else ""
        lines.append(
            f"  {x['pick_id']} | {x['match']} | {x['selection']} | "
            f"open {x['open_odds']} → close {x['closing_odds']} | CLV {sign}{clv}%"
        )
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════
# BACKTEST
# ══════════════════════════════════════════════════════════════════

def run_backtest(min_ev: float = 4.0, max_odds: float = 10.0) -> str:
    history = load_pick_history()
    settled = [
        x for x in history
        if x.get("status") == "SETTLED"
        and x.get("settled_result") in ("WIN", "LOSS", "PUSH")
        and float(x.get("best_odds", 0)) <= max_odds
        and float(x.get("ev_calibrated", 0)) >= min_ev
    ]
    if not settled:
        return f"BACKTEST\n- no settled picks (min_ev={min_ev}, max_odds={max_odds})\nSettle picks first with /settle PICK_ID WIN|LOSS"
    total_stake = sum(float(x.get("stake", 0)) for x in settled)
    total_pnl   = sum(float(x.get("pnl", 0)) for x in settled)
    wins   = sum(1 for x in settled if x.get("settled_result") == "WIN")
    losses = sum(1 for x in settled if x.get("settled_result") == "LOSS")
    pushes = sum(1 for x in settled if x.get("settled_result") == "PUSH")
    roi    = round(total_pnl / total_stake * 100, 2) if total_stake > 0 else 0.0
    win_rate = round(wins / len(settled) * 100, 1)
    by_profile: dict = {}
    for x in settled:
        prof = x.get("profile", "unknown")
        by_profile.setdefault(prof, {"n": 0, "stake": 0.0, "pnl": 0.0})
        by_profile[prof]["n"] += 1
        by_profile[prof]["stake"] += float(x.get("stake", 0))
        by_profile[prof]["pnl"]   += float(x.get("pnl", 0))
    lines = [
        "BACKTEST REPORT",
        f"Filters: min_ev={min_ev}% | max_odds={max_odds}",
        f"Picks: {len(settled)} | W/L/P: {wins}/{losses}/{pushes} | Win rate: {win_rate}%",
        f"Stake: {round(total_stake,2)} | PnL: {round(total_pnl,2)} | ROI: {roi}%",
        "",
        "BY PROFILE:"
    ]
    for prof, data in sorted(by_profile.items(), key=lambda x: x[1]["pnl"], reverse=True):
        s = round(data["stake"], 2)
        pp = round(data["pnl"], 2)
        rr = round(pp / s * 100, 2) if s > 0 else 0.0
        lines.append(f"  {prof}: n={data['n']} | pnl={pp} | roi={rr}%")
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════
# HTML DASHBOARD
# ══════════════════════════════════════════════════════════════════

def generate_html_dashboard() -> str:
    history  = load_pick_history()
    bank     = load_bank()
    clv_data = load_closing_lines()
    meta     = summarize_history(history)
    balance  = bank.get("balance", 1000.0)
    peak     = bank.get("peak", balance)
    mode     = detect_mode(balance)
    drawdown = round((peak - balance) / peak * 100, 1) if peak > 0 else 0.0
    exp      = load_daily_exposure()
    today_exp = exp.get("exposed", 0.0)
    limit_exp = daily_exposure_limit(balance, mode)
    today_bets = count_today_bets()
    max_bets   = MAX_BETS_PER_DAY.get(mode, 5)
    avg_clv    = round(sum(x.get("clv", 0) for x in clv_data) / len(clv_data), 2) if clv_data else 0.0
    open_picks = [x for x in history if x.get("status") == "OPEN"]
    recent     = sorted(history, key=lambda x: x.get("generated_at", ""), reverse=True)[:15]

    def pc(v):
        v = float(v)
        return "#4caf50" if v > 0 else ("#f44336" if v < 0 else "#aaa")

    rows = ""
    for x in recent:
        result  = x.get("settled_result", "—")
        pnl_v   = x.get("pnl", 0)
        col     = pc(pnl_v) if x.get("status") == "SETTLED" else "#aaa"
        decision = x.get("decision","")
        badge_col = {"CORE":"#4caf50","SUPPORT":"#ffd54f","MICRO":"#ce93d8","PASS":"#777"}.get(decision,"#777")
        rows += (
            f"<tr>"
            f"<td style='font-size:.72rem;color:#777'>{x.get('pick_id','')}</td>"
            f"<td>{x.get('match','')}</td>"
            f"<td>{x.get('market','')}</td>"
            f"<td>{x.get('selection','')}</td>"
            f"<td>{x.get('best_odds','')}</td>"
            f"<td>{x.get('stake','')}</td>"
            f"<td><span style='color:{badge_col};font-weight:600'>{decision}</span></td>"
            f"<td>{result}</td>"
            f"<td style='color:{col};font-weight:600'>{pnl_v}</td>"
            f"</tr>"
        )

    exp_pct = min(round(today_exp / limit_exp * 100 if limit_exp else 0), 100)
    bets_pct = min(round(today_bets / max_bets * 100 if max_bets else 0), 100)
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Betting Dashboard</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',system-ui,sans-serif;background:#0d0f18;color:#dde1f0;padding:16px 20px;min-height:100vh}}
h1{{font-size:1.3rem;color:#fff;margin-bottom:18px;padding-bottom:10px;border-bottom:1px solid #1e2130}}
h2{{font-size:.85rem;color:#7986cb;text-transform:uppercase;letter-spacing:.08em;margin:22px 0 10px}}
.kpi{{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px;margin-bottom:6px}}
.k{{background:#13162a;border:1px solid #1e2130;border-radius:10px;padding:14px 16px}}
.k .lbl{{font-size:.68rem;color:#666;text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px}}
.k .val{{font-size:1.45rem;font-weight:700;line-height:1}}
.green{{color:#4caf50}}.red{{color:#ef5350}}.blue{{color:#90caf9}}.yellow{{color:#ffd54f}}.purple{{color:#ce93d8}}.muted{{color:#666}}
.bar-wrap{{background:#1e2130;border-radius:6px;height:7px;margin-top:8px;overflow:hidden}}
.bar{{height:7px;border-radius:6px;transition:width .4s}}
.bar.green{{background:#4caf50}}.bar.red{{background:#ef5350}}.bar.yellow{{background:#ffd54f}}
table{{width:100%;border-collapse:collapse;font-size:.82rem;margin-top:4px}}
th{{background:#13162a;padding:8px 10px;text-align:left;color:#555;font-weight:500;border-bottom:1px solid #1e2130;white-space:nowrap}}
td{{padding:7px 10px;border-bottom:1px solid #13162a;white-space:nowrap}}
tr:hover td{{background:#13162a}}
.ts{{font-size:.68rem;color:#333;margin-top:20px;text-align:right}}
</style>
</head>
<body>
<h1>🎯 Betting Dashboard</h1>

<h2>Bankroll</h2>
<div class="kpi">
  <div class="k"><div class="lbl">Balance</div><div class="val blue">{balance}</div></div>
  <div class="k"><div class="lbl">Peak</div><div class="val muted">{peak}</div></div>
  <div class="k"><div class="lbl">Drawdown</div><div class="val {'red' if drawdown>10 else 'green'}">{drawdown}%</div></div>
  <div class="k"><div class="lbl">Mode</div><div class="val {'green' if mode in ('NORMAL','GROWTH') else 'red'}">{mode}</div></div>
  <div class="k"><div class="lbl">Total PnL</div><div class="val" style="color:{pc(meta['total_pnl'])}">{meta['total_pnl']}</div></div>
  <div class="k"><div class="lbl">ROI</div><div class="val {'green' if meta['roi']>0 else 'red'}">{meta['roi']}%</div></div>
  <div class="k"><div class="lbl">W / L / P</div><div class="val">{meta['wins']}/{meta['losses']}/{meta['pushes']}</div></div>
  <div class="k"><div class="lbl">Avg CLV</div><div class="val {'green' if avg_clv>0 else 'red'}">{avg_clv}%</div></div>
</div>

<h2>Daily Limits</h2>
<div class="kpi">
  <div class="k" style="grid-column:span 2">
    <div class="lbl">Exposure {today_exp} / {limit_exp}</div>
    <div class="bar-wrap"><div class="bar {'red' if exp_pct>80 else 'green'}" style="width:{exp_pct}%"></div></div>
  </div>
  <div class="k" style="grid-column:span 2">
    <div class="lbl">Bets today {today_bets} / {max_bets}</div>
    <div class="bar-wrap"><div class="bar {'red' if bets_pct>=100 else 'yellow'}" style="width:{bets_pct}%"></div></div>
  </div>
  <div class="k"><div class="lbl">Open picks</div><div class="val yellow">{len(open_picks)}</div></div>
  <div class="k"><div class="lbl">CLV records</div><div class="val purple">{len(clv_data)}</div></div>
</div>

<h2>Recent Picks (last 15)</h2>
<div style="overflow-x:auto">
<table>
  <thead><tr>
    <th>ID</th><th>Match</th><th>Market</th><th>Selection</th>
    <th>Odds</th><th>Stake</th><th>Class</th><th>Result</th><th>PnL</th>
  </tr></thead>
  <tbody>{rows if rows else '<tr><td colspan=9 style="color:#444;padding:20px">No picks yet — run /auto to get started</td></tr>'}</tbody>
</table>
</div>
<div class="ts">Generated: {now_str}</div>
</body></html>"""

    out = REPORTS_DIR / f"{datetime.now().date()}_dashboard.html"
    out.write_text(html, encoding="utf-8")
    return str(out)


async def risk_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_long(update.message, format_risk_report())

async def bankset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await reply_long(update.message, "Использование: /bankset 1200.50")
        return
    try:
        new_val = float(args[0])
        if new_val < 0:
            await reply_long(update.message, "Баланс не может быть отрицательным.")
            return
        bank = set_bank(new_val)
        mode = detect_mode(bank["balance"])
        await reply_long(update.message, f"✅ Банк обновлён\nBalance: {bank['balance']}\nPeak: {bank['peak']}\nMode: {mode}")
    except ValueError:
        await reply_long(update.message, "Ошибка: /bankset 1200.50")

async def validate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_long(update.message, format_validate_report())

async def clv_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) == 2:
        pick_id = args[0]
        try:
            closing_odds = float(args[1])
        except ValueError:
            await reply_long(update.message, "Ошибка: /clv PICK_ID 2.10")
            return
        history = load_pick_history()
        pick = next((x for x in history if x.get("pick_id") == pick_id), None)
        if not pick:
            await reply_long(update.message, f"pick_id {pick_id} не найден")
            return
        record_closing_line(
            pick_id=pick_id, match=pick.get("match",""),
            market=pick.get("market",""), selection=pick.get("selection",""),
            open_odds=float(pick.get("best_odds", 0)), closing_odds=closing_odds
        )
        clv = round((float(pick.get("best_odds", 0)) / closing_odds - 1) * 100, 2)
        sign = "+" if clv > 0 else ""
        await reply_long(update.message, f"✅ CLV записан\nPick: {pick_id} | Open: {pick.get('best_odds')} → Close: {closing_odds} | CLV {sign}{clv}%")
    else:
        await reply_long(update.message, format_clv_report())

async def backtest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    min_ev   = float(args[0]) if len(args) > 0 else 4.0
    max_odds = float(args[1]) if len(args) > 1 else 10.0
    await reply_long(update.message, run_backtest(min_ev=min_ev, max_odds=max_odds))

async def dashboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        path = generate_html_dashboard()
        await reply_long(update.message, f"✅ Dashboard сгенерирован:\n{path}")
    except Exception as e:
        await reply_long(update.message, f"Ошибка: {e}")


async def model_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /model football home_xg=1.5 away_xg=1.0 selection=home
    /model basketball home_ortg=114 away_drtg=108 selection=home
    /model tennis surface=clay p1_surface_winrate=0.62 selection=p1
    /model hockey home_goalie_sv=0.920 away_b2b=true selection=over
    """
    args = context.args
    if not args:
        await reply_long(update.message,
            "Использование:\n"
            "/model football home_xg=1.5 away_xg=1.0 selection=home\n"
            "/model basketball home_ortg=114 away_drtg=108 selection=home\n"
            "/model tennis surface=clay p1_surface_winrate=0.62 selection=p1\n"
            "/model hockey home_goalie_sv=0.920 away_b2b=true selection=over total_line=5.5"
        )
        return
    sport = args[0].lower()
    meta = {}
    for arg in args[1:]:
        if "=" in arg:
            k, v = arg.split("=", 1)
            try:
                if v.lower() in ("true", "false"):
                    meta[k] = v.lower() == "true"
                elif "." in v:
                    meta[k] = float(v)
                else:
                    meta[k] = int(v)
            except ValueError:
                meta[k] = v
    await reply_long(update.message, format_model_report(sport, meta))

def main():
    token = os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('BOT_TOKEN')
    if not token:
        raise RuntimeError('TELEGRAM_BOT_TOKEN or BOT_TOKEN is not set')
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler('start', start_cmd))
    app.add_handler(CommandHandler('auto', auto_cmd))
    app.add_handler(CommandHandler('dryrun', dryrun_cmd))
    app.add_handler(CommandHandler('report', report_cmd))
    app.add_handler(CommandHandler('audit', audit_cmd))
    app.add_handler(CommandHandler('latest', latest_cmd))
    app.add_handler(CommandHandler('history', history_cmd))
    app.add_handler(CommandHandler('summary', summary_cmd))
    app.add_handler(CommandHandler('watch', watch_cmd))
    app.add_handler(CommandHandler('unwatch', unwatch_cmd))
    app.add_handler(CommandHandler('watchlist', watchlist_cmd))
    app.add_handler(CommandHandler('scanwatch', scanwatch_cmd))
    app.add_handler(CommandHandler('openpicks', openpicks_cmd))
    app.add_handler(CommandHandler('settle', settle_cmd))
    app.add_handler(CommandHandler('stats', stats_cmd))
    app.add_handler(CommandHandler('quick', quick_cmd))
    app.add_handler(CommandHandler('day', day_cmd))
    app.add_handler(CommandHandler('template_morning', template_morning_cmd))
    app.add_handler(CommandHandler('template_evening', template_evening_cmd))
    print('Bot started')
    app.add_handler(CommandHandler('risk', risk_cmd))
    app.add_handler(CommandHandler('bankset', bankset_cmd))
    app.add_handler(CommandHandler('validate', validate_cmd))
    app.add_handler(CommandHandler('clv', clv_cmd))
    app.add_handler(CommandHandler('backtest', backtest_cmd))
    app.add_handler(CommandHandler('dashboard', dashboard_cmd))
    app.add_handler(CommandHandler('model', model_cmd))
    app.run_polling()

if __name__ == '__main__':
    main()
