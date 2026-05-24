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

CLASS_ORDER = {"PASS": 1, "MICRO": 2, "SUPPORT": 3, "CORE": 4}
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
SPORT_LIMITS = {
    "basketball": {"min_book_count": 3, "max_odds_age": 180},
    "football": {"min_book_count": 3, "max_odds_age": 180},
    "wnba": {"min_book_count": 4, "max_odds_age": 120},
    "euroleague": {"min_book_count": 4, "max_odds_age": 120},
    "acb": {"min_book_count": 4, "max_odds_age": 120},
}

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
    return {"raw_text": text, "sports": list(dict.fromkeys(sports)), "strict": strict, "bank": bank, "mode": mode, "markets": ['h2h', 'spreads', 'totals'], "max_candidates": max_candidates, "team_filters": list(dict.fromkeys(team_filters))}

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
    if market == 'totals':
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

def calibration_factor(book_count, odds_age_minutes, lineup_confirmed, market, data_quality, odds, selection):
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
                    if market_key not in ['h2h', 'spreads', 'totals']:
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
                            grouped[group_key] = {"event_id": event_id, "match": match_name, "sport": sport_alias, "market": market_key, "selection": str(name) if point is None else str(name) + ' ' + str(point), "point": point, "commence_time": commence_time, "prices": [], "best_odds": float(price), "best_bookmaker": bookmaker_title, "best_odds_age_minutes": odds_age_minutes, "source_tier": source_tier, "max_class": max_class}
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
                model_prob = estimate_model_prob(best_odds, avg_odds, book_count, market, item['selection'])
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

def finalize_candidate(candidate, request_data, run_id):
    now = datetime.now(UTC)
    reasons = []
    limits = SPORT_LIMITS.get(candidate['sport'], {"min_book_count": 3, "max_odds_age": 180})
    implied = implied_probability(candidate['odds_best'])
    ev_raw = round(candidate['model_prob'] - implied, 2)
    data_quality = calc_data_quality(candidate['book_count'], candidate['odds_age_minutes'], candidate['lineup_confirmed'], candidate['source_tier'])
    factor = calibration_factor(candidate['book_count'], candidate['odds_age_minutes'], candidate['lineup_confirmed'], candidate['market'], data_quality, candidate['odds_best'], candidate['selection'])
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
    app.run_polling()

if __name__ == '__main__':
    main()
