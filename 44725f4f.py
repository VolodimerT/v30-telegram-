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
REPORTS_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

LAST_RUN_PATH = BASE_DIR / "last_run.txt"
AUDIT_PATH = BASE_DIR / "audit.json"
RUNS_PATH = BASE_DIR / "runs.txt"

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
    "rayo": ["rayo"],
    "alaves": ["alaves", "alavés"],
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
    parts = []
    current = ""
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

def append_run_line(run_line):
    try:
        with RUNS_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(run_line, ensure_ascii=False) + "\n")
    except Exception:
        pass

def ensure_runs_seed_from_latest():
    if RUNS_PATH.exists() and RUNS_PATH.stat().st_size > 0:
        return
    if not AUDIT_PATH.exists():
        return
    try:
        data = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
        append_run_line({"run_id": data.get("run_id", "unknown"), "generated_at": data.get("generated_at", ""), "request": data.get("request", {}).get("raw_text", ""), "accepted_count": data.get("accepted_count", 0), "rejected_count": data.get("rejected_count", 0), "report_path": str(REPORTS_DIR), "audit_path": str(AUDIT_PATH)})
    except Exception:
        pass

def detect_mode(bank):
    if bank < 500:
        return "FROZEN"
    if bank < 1000:
        return "EMERGENCY"
    if bank < 3000:
        return "NORMAL"
    return "GROWTH"

def parse_request(text):
    raw = text.strip().lower()
    sports = [key for key in SPORT_MAP if key in raw]
    if not sports:
        sports = ["football"]
    strict = "strict" in raw
    bank = 1000.0
    max_candidates = 20
    team_filters = []
    parts = raw.replace("=", " ").replace(",", " ").split()
    for i, token in enumerate(parts):
        if token == "bank" and i + 1 < len(parts):
            try:
                bank = float(parts[i + 1])
            except Exception:
                bank = 1000.0
        if token in ["max", "max_candidates"] and i + 1 < len(parts):
            try:
                max_candidates = int(parts[i + 1])
            except Exception:
                max_candidates = 20
    mode = detect_mode(bank)
    for i, token in enumerate(parts):
        if token == "mode" and i + 1 < len(parts):
            custom = parts[i + 1].upper()
            if custom in MODE_RULES:
                mode = custom
    for token in parts:
        if token in TEAM_ALIASES:
            team_filters.extend(TEAM_ALIASES[token])
        elif token not in ["auto", "today", "strict", "bank", "mode", "max", "max_candidates"] and token not in SPORT_MAP and len(token) >= 4:
            team_filters.append(token)
    team_filters = list(dict.fromkeys(team_filters))
    return {"raw_text": text, "sports": list(dict.fromkeys(sports)), "strict": strict, "bank": bank, "mode": mode, "markets": ["h2h", "spreads", "totals"], "max_candidates": max_candidates, "team_filters": team_filters}

def match_team_filter(match_name, team_filters):
    return True if not team_filters else any(team in match_name.lower() for team in team_filters)

def implied_probability(odds):
    return round(100.0 / odds, 2)

def estimate_model_prob(best_odds, avg_odds, book_count, market):
    implied_best = 100.0 / best_odds
    edge_bonus = max(0.0, min((best_odds - avg_odds) * 10.0, 4.0))
    consensus_bonus = 1.5 if book_count >= 6 else 1.0 if book_count >= 4 else 0.5 if book_count >= 3 else 0.0
    market_bonus = 0.5 if market == "h2h" else 0.0
    return round(max(35.0, min(implied_best + edge_bonus + consensus_bonus + market_bonus, 75.0)), 2)

def infer_variance(market, odds):
    if odds >= 3.2:
        return "EXTREME"
    if odds >= 2.4:
        return "HIGH"
    if market == "totals":
        return "MEDIUM"
    return "LOW"

def calc_data_quality(book_count, odds_age_minutes, lineup_confirmed, source_tier):
    score = 0
    score += 3 if book_count >= 6 else 2 if book_count >= 4 else 1 if book_count >= 3 else 0
    score += 3 if odds_age_minutes <= 20 else 2 if odds_age_minutes <= 60 else 1 if odds_age_minutes <= 120 else 0
    if lineup_confirmed:
        score += 1
    score += 2 if source_tier == 1 else 1 if source_tier == 2 else 0
    return "HIGH" if score >= 7 else "MEDIUM" if score >= 4 else "LOW"

def calibration_factor(book_count, odds_age_minutes, lineup_confirmed, market, data_quality):
    factor = 1.0
    if book_count < 4:
        factor -= 0.20
    if odds_age_minutes > 120:
        factor -= 0.20
    if odds_age_minutes > 180:
        factor -= 0.15
    if (not lineup_confirmed) and market in ["spreads", "totals"]:
        factor -= 0.20
    if data_quality == "LOW":
        factor -= 0.15
    return round(max(factor, 0.20), 2)

def calc_ci_low(ev_calibrated, book_count, odds_age_minutes, variance, data_quality):
    penalty = 0.0
    if book_count < 4:
        penalty += 4.0
    if odds_age_minutes > 120:
        penalty += 4.0
    if odds_age_minutes > 180:
        penalty += 3.0
    if variance == "HIGH":
        penalty += 4.0
    if variance == "EXTREME":
        penalty += 6.0
    if data_quality == "LOW":
        penalty += 3.0
    return round(ev_calibrated - penalty, 2)

def class_from_ev(ev_calibrated, ci_low, data_quality):
    if ci_low < 0:
        return "PASS"
    if ev_calibrated >= 10 and ci_low >= 3 and data_quality == "HIGH":
        return "CORE"
    if ev_calibrated >= 7 and ci_low >= 1:
        return "SUPPORT"
    if ev_calibrated >= 4 and ci_low >= 0:
        return "MICRO"
    return "PASS"

def cap_class(base_class, max_class):
    return max_class if CLASS_ORDER[base_class] > CLASS_ORDER[max_class] else base_class

def risk_cap(mode, decision):
    rule = MODE_RULES.get(mode, MODE_RULES["NORMAL"])
    return float(rule["micro"] if decision == "MICRO" else rule["support"] if decision == "SUPPORT" else rule["core"] if decision == "CORE" else 0.0)

def calc_stake(bank, decision, ev_calibrated, odds, cap_value):
    if decision == "PASS":
        return 0.0
    b = odds - 1.0
    if b <= 0:
        return 0.0
    p = max(0.01, min((implied_probability(odds) + ev_calibrated) / 100.0, 0.95))
    q = 1.0 - p
    kelly = ((b * p) - q) / b
    if kelly < 0:
        kelly = 0.0
    stake = min(bank * kelly * 0.20, cap_value)
    rounded = math.floor(stake / 10.0) * 10.0
    return 10.0 if rounded == 0 and stake > 0 else float(max(rounded, 0.0))

def fetch_candidates(request_data):
    api_key = os.getenv("ODDS_API_KEY")
    if not api_key:
        raise RuntimeError("ODDS_API_KEY is not set")
    now = datetime.now(UTC)
    all_candidates = []
    seen_group = set()
    for sport_name in request_data["sports"]:
        sport_alias, sport_key, source_tier, max_class = SPORT_MAP[sport_name]
        response = requests.get("https://api.the-odds-api.com/v4/sports/" + sport_key + "/odds", params={"apiKey": api_key, "regions": "eu,uk", "markets": "h2h,spreads,totals", "oddsFormat": "decimal", "dateFormat": "iso"}, timeout=25)
        response.raise_for_status()
        events = response.json()
        for event in events:
            home_team = event.get("home_team")
            away_team = event.get("away_team")
            commence_raw = event.get("commence_time")
            event_id = str(event.get("id", ""))
            if not home_team or not away_team or not commence_raw:
                continue
            try:
                commence_time = datetime.fromisoformat(commence_raw.replace("Z", "+00:00"))
            except Exception:
                continue
            match_name = str(away_team) + " vs " + str(home_team)
            if not match_team_filter(match_name, request_data["team_filters"]):
                continue
            grouped = {}
            for bookmaker in event.get("bookmakers", []):
                bookmaker_title = bookmaker.get("title", "Book")
                last_update_raw = bookmaker.get("last_update")
                odds_age_minutes = 999.0
                if last_update_raw:
                    try:
                        last_update = datetime.fromisoformat(last_update_raw.replace("Z", "+00:00"))
                        odds_age_minutes = max((now - last_update).total_seconds() / 60.0, 0.0)
                    except Exception:
                        odds_age_minutes = 999.0
                for market in bookmaker.get("markets", []):
                    market_key = market.get("key", "")
                    if market_key not in ["h2h", "spreads", "totals"]:
                        continue
                    for outcome in market.get("outcomes", []):
                        name = outcome.get("name")
                        price = outcome.get("price")
                        point = outcome.get("point")
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
                        group_key = event_id + "|" + market_key + "|" + str(name) + "|" + str(point)
                        if group_key not in grouped:
                            grouped[group_key] = {"match": match_name, "sport": sport_alias, "market": market_key, "selection": str(name) if point is None else str(name) + " " + str(point), "point": point, "commence_time": commence_time, "prices": [], "best_odds": float(price), "best_bookmaker": bookmaker_title, "best_odds_age_minutes": odds_age_minutes, "source_tier": source_tier, "max_class": max_class}
                        grouped[group_key]["prices"].append(float(price))
                        if float(price) > grouped[group_key]["best_odds"]:
                            grouped[group_key]["best_odds"] = float(price)
                            grouped[group_key]["best_bookmaker"] = bookmaker_title
                            grouped[group_key]["best_odds_age_minutes"] = odds_age_minutes
            for item in grouped.values():
                if not item["prices"]:
                    continue
                uniq_key = item["match"] + "|" + item["market"] + "|" + item["selection"]
                if uniq_key in seen_group:
                    continue
                seen_group.add(uniq_key)
                best_odds = round(max(item["prices"]), 2)
                avg_odds = round(sum(item["prices"]) / len(item["prices"]), 2)
                book_count = len(item["prices"])
                market = item["market"]
                variance = infer_variance(market, best_odds)
                hours_to_start = (item["commence_time"] - now).total_seconds() / 3600.0
                lineup_confirmed = not (market in ["spreads", "totals"] and hours_to_start <= 1.5)
                model_prob = estimate_model_prob(best_odds, avg_odds, book_count, market)
                all_candidates.append({"match": item["match"], "sport": item["sport"], "market": item["market"], "selection": item["selection"], "point": item["point"], "commence_time": item["commence_time"], "odds_best": best_odds, "odds_avg": avg_odds, "book_count": book_count, "model_prob": model_prob, "lineup_confirmed": lineup_confirmed, "injury_fresh_hours": 2.0, "odds_age_minutes": round(item["best_odds_age_minutes"], 2), "source_tier": item["source_tier"], "variance": variance, "bookmaker": item["best_bookmaker"], "max_class": item["max_class"]})
    return all_candidates[:request_data["max_candidates"]]

def finalize_candidate(candidate, request_data, run_id):
    now = datetime.now(UTC)
    reasons = []
    limits = SPORT_LIMITS.get(candidate["sport"], {"min_book_count": 3, "max_odds_age": 180})
    implied = implied_probability(candidate["odds_best"])
    ev_raw = round(candidate["model_prob"] - implied, 2)
    data_quality = calc_data_quality(candidate["book_count"], candidate["odds_age_minutes"], candidate["lineup_confirmed"], candidate["source_tier"])
    factor = calibration_factor(candidate["book_count"], candidate["odds_age_minutes"], candidate["lineup_confirmed"], candidate["market"], data_quality)
    ev_calibrated = round(ev_raw * factor, 2)
    ci_low = calc_ci_low(ev_calibrated, candidate["book_count"], candidate["odds_age_minutes"], candidate["variance"], data_quality)
    decision = "PASS"
    stake = 0.0
    cap_value = 0.0
    if candidate["commence_time"] < now:
        reasons.append("EXPIRED_EVENT")
    elif (candidate["commence_time"] - now) < timedelta(minutes=15):
        reasons.append("TOO_CLOSE")
    elif candidate["odds_age_minutes"] > limits["max_odds_age"]:
        reasons.append("STALE_ODDS")
    elif candidate["book_count"] < limits["min_book_count"]:
        reasons.append("LOW_BOOK_COUNT")
    elif request_data["mode"] == "EMERGENCY" and candidate["variance"] in ["HIGH", "EXTREME"]:
        reasons.append("HIGH_VARIANCE")
    elif ev_calibrated < MODE_RULES[request_data["mode"]]["min_ev"]:
        reasons.append("EV_TOO_LOW")
    elif ci_low < 0:
        reasons.append("CI_LOW_NEGATIVE")
    else:
        decision = class_from_ev(ev_calibrated, ci_low, data_quality)
        if candidate["source_tier"] >= 3 and CLASS_ORDER[decision] > CLASS_ORDER["MICRO"]:
            decision = "MICRO"
            reasons.append("SPORT_TIER_CAP")
        if (not candidate["lineup_confirmed"]) and candidate["market"] in ["spreads", "totals"]:
            if request_data["strict"]:
                decision = "PASS"
            elif CLASS_ORDER[decision] > CLASS_ORDER["MICRO"]:
                decision = "MICRO"
            reasons.append("LINEUP_PENDING")
        decision = cap_class(decision, candidate["max_class"])
        if candidate["book_count"] < 5:
            reasons.append("NO_STRONG_CONSENSUS")
        if decision == "PASS":
            if not reasons:
                reasons.append("RULES_BLOCK")
        else:
            reasons.extend(["EDGE_OK", "DATA_QUALITY_" + data_quality, "CLASS_" + decision])
            cap_value = risk_cap(request_data["mode"], decision)
            stake = calc_stake(request_data["bank"], decision, ev_calibrated, candidate["odds_best"], cap_value)
    if decision == "PASS" and not reasons:
        reasons.append("UNKNOWN_PASS")
    return {"run_id": run_id, "generated_at": datetime.now(UTC).isoformat(), "match": candidate["match"], "sport": candidate["sport"], "market": candidate["market"], "selection": candidate["selection"], "point": candidate["point"], "best_odds": candidate["odds_best"], "avg_odds": candidate["odds_avg"], "bookmaker": candidate["bookmaker"], "book_count": candidate["book_count"], "model_prob": candidate["model_prob"], "implied_prob": implied, "ev_raw": ev_raw, "ev_calibrated": ev_calibrated, "ci_low": ci_low, "risk_cap": cap_value, "data_quality": data_quality, "decision": decision, "stake": stake, "reasons": reasons}

def sort_results(results):
    dq_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    return sorted(results, key=lambda x: (CLASS_ORDER.get(x["decision"], 0), x["ev_calibrated"], x["ci_low"], x["book_count"], dq_rank.get(x["data_quality"], 0)), reverse=True)

def format_summary(summary):
    lines = ["Run: " + str(summary["run_id"]), "Mode: " + str(summary["request"]["mode"]), "Sports: " + ", ".join(summary["request"]["sports"]), "Teams: " + (", ".join(summary["request"]["team_filters"]) if summary["request"]["team_filters"] else "all"), "Status: " + str(summary["message"]), "Candidates: " + str(summary["candidates_count"]), "Accepted: " + str(summary["accepted_count"]), "Rejected: " + str(summary["rejected_count"]), ""]
    for item in summary["results"][:10]:
        if item["decision"] == "PASS":
            lines.append("PASS | " + item["match"] + " | " + item["selection"] + " | " + ", ".join(item["reasons"]))
        else:
            lines.append(item["decision"] + " | " + item["match"] + " | " + item["selection"] + " | odds " + str(item["best_odds"]) + " | stake " + str(item["stake"]) + " | EV " + str(item["ev_calibrated"]) + " | CI " + str(item["ci_low"]))
    return "\n".join(lines)

def write_report(summary):
    file_path = REPORTS_DIR / (str(datetime.now().date()) + "_" + summary["run_id"] + "_report.txt")
    rows = ["DAILY REPORT | " + str(datetime.now().date()), "Request: " + str(summary["request"]["raw_text"]), "Mode: " + str(summary["request"]["mode"]), "Sports: " + ", ".join(summary["request"]["sports"]), "Team filters: " + (", ".join(summary["request"]["team_filters"]) if summary["request"]["team_filters"] else "all"), "Candidates: " + str(summary["candidates_count"]), "Accepted: " + str(summary["accepted_count"]), "Rejected: " + str(summary["rejected_count"]), "Status: " + str(summary["message"]), ""]
    for idx, item in enumerate(summary["results"], start=1):
        rows.extend([str(idx) + ") " + item["match"], "Sport: " + item["sport"], "Selection: " + item["selection"], "Odds: " + str(item["best_odds"]) + " | Avg: " + str(item["avg_odds"]), "Bookmaker: " + item["bookmaker"] + " | Books: " + str(item["book_count"]), "ModelProb: " + str(item["model_prob"]) + " | Implied: " + str(item["implied_prob"]), "EV raw: " + str(item["ev_raw"]) + " | EV calibrated: " + str(item["ev_calibrated"]) + " | CI low: " + str(item["ci_low"]), "Decision: " + item["decision"] + " | Stake: " + str(item["stake"]) + " | Risk cap: " + str(item["risk_cap"]), "Reasons: " + ", ".join(item["reasons"]), ""])
    file_path.write_text("\n".join(rows), encoding="utf-8")
    return str(file_path)

def write_audit(summary):
    file_path = LOGS_DIR / (str(datetime.now().date()) + "_" + summary["run_id"] + "_audit.json")
    file_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(file_path)

def read_runs():
    ensure_runs_seed_from_latest()
    items = []
    if not RUNS_PATH.exists():
        return items
    for line in RUNS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except Exception:
            continue
    return items

def format_history(limit=10):
    runs = read_runs()
    if not runs:
        return "Пока нет history. Сначала запусти хотя бы один /auto"
    return "\n".join([str(item.get("generated_at", "")) + " | accepted " + str(item.get("accepted_count", 0)) + " | rejected " + str(item.get("rejected_count", 0)) + " | " + str(item.get("request", "")) for item in runs[-limit:][::-1]])

def format_global_summary():
    runs = read_runs()
    if not runs:
        return "Пока нет summary. Сначала запусти хотя бы один /auto"
    return "\n".join(["Runs: " + str(len(runs)), "Accepted total: " + str(sum(int(item.get("accepted_count", 0)) for item in runs)), "Rejected total: " + str(sum(int(item.get("rejected_count", 0)) for item in runs))])

def format_latest():
    runs = read_runs()
    if runs:
        item = runs[-1]
        return "\n".join(["Run: " + str(item.get("run_id", "")), "Generated: " + str(item.get("generated_at", "")), "Request: " + str(item.get("request", "")), "Accepted: " + str(item.get("accepted_count", 0)), "Rejected: " + str(item.get("rejected_count", 0)), "Report: " + str(item.get("report_path", ""))])
    if AUDIT_PATH.exists():
        try:
            data = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
            return "\n".join(["Run: " + str(data.get("run_id", "")), "Generated: " + str(data.get("generated_at", "")), "Request: " + str(data.get("request", {}).get("raw_text", "")), "Accepted: " + str(data.get("accepted_count", 0)), "Rejected: " + str(data.get("rejected_count", 0))])
        except Exception:
            pass
    return "Пока нет latest. Сначала запусти хотя бы один /auto"

def run_auto_pipeline(request_text, dry_run=False):
    request_data = parse_request(request_text)
    request_data["dry_run"] = dry_run
    run_id = uuid.uuid4().hex[:10]
    candidates = fetch_candidates(request_data)
    results = sort_results([finalize_candidate(candidate, request_data, run_id) for candidate in candidates])
    accepted = [x for x in results if x["decision"] != "PASS"]
    summary = {"run_id": run_id, "request": request_data, "generated_at": datetime.now(UTC).isoformat(), "candidates_count": len(results), "accepted_count": len(accepted), "rejected_count": len(results) - len(accepted), "status": "OK" if results else "NO_CANDIDATES", "message": "NO BETS / ALL PASS" if results and not accepted else ("NO MATCHES FOUND" if not results else "OK"), "results": results}
    if not dry_run:
        report_path = write_report(summary)
        audit_path = write_audit(summary)
        LAST_RUN_PATH.write_text(format_summary(summary), encoding="utf-8")
        AUDIT_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        append_run_line({"run_id": run_id, "generated_at": summary["generated_at"], "request": request_text, "accepted_count": summary["accepted_count"], "rejected_count": summary["rejected_count"], "report_path": report_path, "audit_path": audit_path})
    return summary

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_long(update.message, "/auto today epl chelsea strict\n/auto today epl mancity strict\n/auto today laliga rayo strict\n/dryrun today epl chelsea strict\n/report | /audit | /latest | /history | /summary")
async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = " ".join(context.args).strip()
    try:
        await reply_long(update.message, format_summary(run_auto_pipeline("AUTO " + args if args else "AUTO today football strict", dry_run=False)))
    except Exception as e:
        await reply_long(update.message, "ERROR_REPORT: " + type(e).__name__ + ": " + str(e))
async def dryrun_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = " ".join(context.args).strip()
    try:
        await reply_long(update.message, "[DRYRUN]\n" + format_summary(run_auto_pipeline("AUTO " + args if args else "AUTO today football strict", dry_run=True)))
    except Exception as e:
        await reply_long(update.message, "ERROR_REPORT: " + type(e).__name__ + ": " + str(e))
async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_long(update.message, LAST_RUN_PATH.read_text(encoding="utf-8") if LAST_RUN_PATH.exists() else "Пока нет last_run.txt")
async def audit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_long(update.message, AUDIT_PATH.read_text(encoding="utf-8") if AUDIT_PATH.exists() else "Пока нет audit.json")
async def latest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_long(update.message, format_latest())
async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_long(update.message, format_history())
async def summary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_long(update.message, format_global_summary())

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN or BOT_TOKEN is not set")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("auto", auto_cmd))
    app.add_handler(CommandHandler("dryrun", dryrun_cmd))
    app.add_handler(CommandHandler("report", report_cmd))
    app.add_handler(CommandHandler("audit", audit_cmd))
    app.add_handler(CommandHandler("latest", latest_cmd))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("summary", summary_cmd))
    print("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()
