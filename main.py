import os
import re
import json
import math
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

UTC = timezone.utc
BASE_DIR = Path(__file__).resolve().parent
REPORTS_DIR = BASE_DIR / "reports"
LOGS_DIR = BASE_DIR / "logs"
REPORTS_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

CLASS_ORDER = {"CORE": 4, "SUPPORT": 3, "MICRO": 2, "PASS": 1}

DEFAULT_CONFIG = {
    "unit": 10,
    "mode_rules": {
        "FROZEN": {"min_ev_calibrated": 6.0, "max_micro_stake": 10, "max_support_stake": 20, "max_core_stake": 30},
        "EMERGENCY": {"min_ev_calibrated": 8.0, "max_micro_stake": 10, "max_support_stake": 15, "max_core_stake": 20},
        "NORMAL": {"min_ev_calibrated": 4.0, "max_micro_stake": 20, "max_support_stake": 35, "max_core_stake": 50},
        "GROWTH": {"min_ev_calibrated": 3.0, "max_micro_stake": 25, "max_support_stake": 40, "max_core_stake": 60},
    },
    "sport_tiers": {
        "basketball": {"tier": 2, "max_class": "CORE"},
        "football": {"tier": 2, "max_class": "CORE"},
        "wnba": {"tier": 3, "max_class": "MICRO"},
        "euroleague": {"tier": 2, "max_class": "CORE"},
        "acb": {"tier": 2, "max_class": "SUPPORT"},
        "laliga": {"tier": 2, "max_class": "CORE"},
    },
}

LAST_RUN_PATH = BASE_DIR / "last_run.txt"
RUNS_PATH = BASE_DIR / "runs.txt"
AUDIT_PATH = BASE_DIR / "audit.json"


@dataclass
class AutoRequest:
    raw_text: str
    date: str = "today"
    sports: List[str] = field(default_factory=list)
    markets: List[str] = field(default_factory=lambda: ["h2h", "spreads", "totals"])
    strict: bool = False
    bank: float = 1000.0
    mode: str = "NORMAL"
    max_candidates: int = 12
    dry_run: bool = False


@dataclass
class Candidate:
    match: str
    sport: str
    market: str
    selection: str
    point: Optional[float]
    commence_time: datetime
    odds_best: float
    odds_avg: float
    book_count: int
    model_prob: float
    lineup_confirmed: bool
    injury_fresh_hours: float
    odds_age_minutes: float
    source_tier: int
    variance: str
    bookmaker: str = "BestBook"


@dataclass
class FinalDecision:
    run_id: str
    generated_at: str
    match: str
    sport: str
    market: str
    selection: str
    point: Optional[float]
    best_odds: float
    avg_odds: float
    bookmaker: str
    book_count: int
    model_prob: float
    implied_prob: float
    ev_raw: float
    ev_calibrated: float
    ci_low: float
    risk_cap: float
    data_quality: str
    decision: str
    stake: float
    reasons: List[str]


def parse_mode(bank: float) -> str:
    if bank < 500:
        return "FROZEN"
    if bank < 1000:
        return "EMERGENCY"
    if bank < 3000:
        return "NORMAL"
    return "GROWTH"


def parse_auto_request(text: str, dry_run: bool = False) -> AutoRequest:
    clean = text.strip().lower()
    strict = "strict" in clean

    sports = []
    for token in ["basketball", "football", "wnba", "euroleague", "acb", "laliga"]:
        if token in clean:
            sports.append(token)
    if not sports:
        sports = ["basketball"]

    max_candidates = 12
    m = re.search(r"max(?:_candidates)?[=s](d+)", clean)
    if m:
        max_candidates = int(m.group(1))

    bank = 1000.0
    b = re.search(r"bank[=s](d+(?:.d+)?)", clean)
    if b:
        bank = float(b.group(1))

    mode = parse_mode(bank)
    mm = re.search(r"mode[=s](frozen|emergency|normal|growth)", clean)
    if mm:
        mode = mm.group(1).upper()

    date = "today"
    if "tomorrow" in clean:
        date = "tomorrow"

    return AutoRequest(
        raw_text=text,
        date=date,
        sports=sports,
        markets=["h2h", "spreads", "totals"],
        strict=strict,
        bank=bank,
        mode=mode,
        max_candidates=max_candidates,
        dry_run=dry_run,
    )


def implied_probability(odds: float) -> float:
    return round(100.0 / odds, 2)


def calc_ev_raw(model_prob: float, odds: float) -> float:
    return round(model_prob - implied_probability(odds), 2)


def calc_data_quality(candidate: Candidate) -> str:
    score = 0

    if candidate.book_count >= 5:
        score += 2
    elif candidate.book_count >= 3:
        score += 1

    if candidate.odds_age_minutes <= 30:
        score += 2
    elif candidate.odds_age_minutes <= 120:
        score += 1

    if candidate.injury_fresh_hours <= 2:
        score += 2
    elif candidate.injury_fresh_hours <= 4:
        score += 1

    if candidate.lineup_confirmed:
        score += 1

    if candidate.source_tier == 1:
        score += 2
    elif candidate.source_tier == 2:
        score += 1

    if score >= 7:
        return "HIGH"
    if score >= 4:
        return "MEDIUM"
    return "LOW"


def calibration_factor(candidate: Candidate, data_quality: str) -> float:
    factor = 1.0
    if candidate.book_count < 3:
        factor -= 0.25
    if candidate.odds_age_minutes > 120:
        factor -= 0.20
    if candidate.injury_fresh_hours > 4:
        factor -= 0.20
    if not candidate.lineup_confirmed and candidate.market in ("spreads", "totals"):
        factor -= 0.15
    if data_quality == "LOW":
        factor -= 0.10
    return max(0.25, round(factor, 2))


def calc_ci_low(ev_calibrated: float, candidate: Candidate, data_quality: str) -> float:
    penalty = 0.0
    if candidate.book_count < 3:
        penalty += 5.0
    if candidate.odds_age_minutes > 120:
        penalty += 4.0
    if candidate.injury_fresh_hours > 4:
        penalty += 4.0
    if candidate.variance in ("HIGH", "EXTREME"):
        penalty += 5.0
    if data_quality == "LOW":
        penalty += 3.0
    return round(ev_calibrated - penalty, 2)


def class_from_ev(ev_calibrated: float, ci_low: float, data_quality: str) -> str:
    if ci_low < 0:
        return "PASS"
    if ev_calibrated >= 10 and ci_low >= 3 and data_quality == "HIGH":
        return "CORE"
    if ev_calibrated >= 7 and ci_low >= 1:
        return "SUPPORT"
    if ev_calibrated >= 4 and ci_low >= 0:
        return "MICRO"
    return "PASS"


def apply_class_caps(base_class: str, candidate: Candidate, request: AutoRequest) -> str:
    capped = base_class

    if candidate.source_tier >= 3 and CLASS_ORDER[capped] > CLASS_ORDER["MICRO"]:
        capped = "MICRO"

    if not candidate.lineup_confirmed and candidate.market in ("spreads", "totals"):
        if request.strict:
            return "PASS"
        if CLASS_ORDER[capped] > CLASS_ORDER["MICRO"]:
            capped = "MICRO"

    sport_cfg = DEFAULT_CONFIG["sport_tiers"].get(candidate.sport, {"max_class": "CORE"})
    max_class = sport_cfg["max_class"]
    if CLASS_ORDER[capped] > CLASS_ORDER[max_class]:
        capped = max_class

    return capped


def calculate_risk_cap(request: AutoRequest, decision_class: str) -> float:
    rules = DEFAULT_CONFIG["mode_rules"][request.mode]
    if decision_class == "MICRO":
        return rules["max_micro_stake"]
    if decision_class == "SUPPORT":
        return rules["max_support_stake"]
    if decision_class == "CORE":
        return rules["max_core_stake"]
    return 0.0


def calculate_stake(request: AutoRequest, decision_class: str, ev_calibrated: float, odds: float, risk_cap: float) -> float:
    if decision_class == "PASS":
        return 0.0

    b = max(odds - 1.0, 0.01)
    p = max(min((implied_probability(odds) + ev_calibrated) / 100.0, 0.95), 0.01)
    q = 1 - p
    kelly = max(((b * p) - q) / b, 0.0)
    stake = request.bank * kelly * 0.25
    stake = min(stake, risk_cap)

    unit = DEFAULT_CONFIG["unit"]
    rounded = math.floor(stake / unit) * unit
    if rounded == 0 and stake > 0:
        return float(unit)
    return float(max(rounded, 0))


def map_sport_keys(sports: List[str]) -> List[Tuple[str, str]]:
    mapping = {
        "basketball": [("basketball", "basketball_nba")],
        "football": [("football", "soccer_spain_la_liga")],
        "wnba": [("wnba", "basketball_wnba")],
        "euroleague": [("euroleague", "basketball_euroleague")],
        "acb": [("acb", "basketball_spain_acb")],
        "laliga": [("laliga", "soccer_spain_la_liga")],
    }

    out = []
    for sport in sports:
        out.extend(mapping.get(sport, []))
    return out


def safe_float(value, default=None):
    try:
        return float(value)
    except Exception:
        return default


def estimate_model_prob(best_odds: float, avg_odds: float, book_count: int) -> float:
    implied_best = 100.0 / best_odds
    edge_bonus = min(max((best_odds - avg_odds) * 12.0, 0.0), 4.5)
    consensus_bonus = 1.5 if book_count >= 5 else 0.75 if book_count >= 3 else 0.0
    model_prob = implied_best + edge_bonus + consensus_bonus
    return round(min(max(model_prob, 35.0), 75.0), 2)


def infer_variance(market: str, odds: float) -> str:
    if odds >= 3.2:
        return "EXTREME"
    if odds >= 2.4:
        return "HIGH"
    if market == "totals":
        return "MEDIUM"
    return "LOW"


def normalize_market_key(market_key: str) -> str:
    if market_key == "h2h":
        return "h2h"
    if market_key == "spreads":
        return "spreads"
    if market_key == "totals":
        return "totals"
    return market_key


def finalize_decision(candidate: Candidate, request: AutoRequest, run_id: str) -> FinalDecision:
    now = datetime.now(UTC)
    reasons: List[str] = []

    implied = implied_probability(candidate.odds_best)
    ev_raw = calc_ev_raw(candidate.model_prob, candidate.odds_best)
    data_quality = calc_data_quality(candidate)
    ev_cal = round(ev_raw * calibration_factor(candidate, data_quality), 2)
    ci_low = calc_ci_low(ev_cal, candidate, data_quality)

    decision = "PASS"
    stake = 0.0
    risk_cap = 0.0

    if candidate.commence_time < now:
        reasons.append("EXPIRED_EVENT")
    elif (candidate.commence_time - now) < timedelta(minutes=10):
        reasons.append("TOO_CLOSE")
    elif candidate.odds_age_minutes > 240:
        reasons.append("STALE_ODDS")
    elif candidate.injury_fresh_hours > 4:
        reasons.append("STALE_INJURY")
    elif candidate.book_count < 2:
        reasons.append("LOW_BOOK_COUNT")
    elif ci_low < 0:
        reasons.append("CI_LOW_NEGATIVE")
    elif request.mode == "EMERGENCY" and ev_cal < DEFAULT_CONFIG["mode_rules"]["EMERGENCY"]["min_ev_calibrated"]:
        reasons.append("EMERGENCY_CAP")
    elif request.mode == "EMERGENCY" and candidate.variance in ("HIGH", "EXTREME"):
        reasons.append("HIGH_VARIANCE")
    else:
        base_class = class_from_ev(ev_cal, ci_low, data_quality)
        decision = apply_class_caps(base_class, candidate, request)

        if not candidate.lineup_confirmed and candidate.market in ("spreads", "totals"):
            reasons.append("LINEUP_PENDING")
        if candidate.source_tier >= 3:
            reasons.append("SPORT_TIER_CAP")
        if candidate.book_count < 3:
            reasons.append("NO_CONSENSUS")

        if decision == "PASS":
            if not reasons:
                reasons.append("EV_TOO_LOW")
        else:
            reasons.append("EDGE_OK")
            reasons.append("DATA_QUALITY_" + data_quality)
            reasons.append("CLASS_" + decision)
            risk_cap = calculate_risk_cap(request, decision)
            stake = calculate_stake(request, decision, ev_cal, candidate.odds_best, risk_cap)

    if decision == "PASS" and not reasons:
        reasons.append("UNKNOWN_PASS")

    return FinalDecision(
        run_id=run_id,
        generated_at=now.isoformat(),
        match=candidate.match,
        sport=candidate.sport,
        market=candidate.market,
        selection=candidate.selection,
        point=candidate.point,
        best_odds=candidate.odds_best,
        avg_odds=candidate.odds_avg,
        bookmaker=candidate.bookmaker,
        book_count=candidate.book_count,
        model_prob=candidate.model_prob,
        implied_prob=implied,
        ev_raw=ev_raw,
        ev_calibrated=ev_cal,
        ci_low=ci_low,
        risk_cap=risk_cap,
        data_quality=data_quality,
        decision=decision,
        stake=stake,
        reasons=reasons,
    )


def sort_results(results: List[FinalDecision]) -> List[FinalDecision]:
    dq_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    return sorted(
        results,
        key=lambda x: (
            CLASS_ORDER.get(x.decision, 0),
            x.ev_calibrated,
            x.ci_low,
            x.book_count,
            dq_rank.get(x.data_quality, 0),
        ),
        reverse=True,
    )


def fetch_candidates(request: AutoRequest) -> List[Candidate]:
    api_key = os.getenv("ODDS_API_KEY")
    if not api_key:
        raise RuntimeError("ODDS_API_KEY is not set")

    sport_pairs = map_sport_keys(request.sports)
    if not sport_pairs:
        return []

    all_candidates: List[Candidate] = []
    now = datetime.now(UTC)

    for sport_alias, sport_key in sport_pairs:
        url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"
        params = {
            "apiKey": api_key,
            "regions": "eu,uk",
            "markets": ",".join(request.markets),
            "oddsFormat": "decimal",
            "dateFormat": "iso",
        }

        response = requests.get(url, params=params, timeout=25)
        response.raise_for_status()
        events = response.json()

        for event in events:
            home_team = event.get("home_team")
            away_team = event.get("away_team")
            commence_raw = event.get("commence_time")
            event_id = event.get("id", "")

            if not home_team or not away_team or not commence_raw:
                continue

            try:
                commence_time = datetime.fromisoformat(commence_raw.replace("Z", "+00:00"))
            except Exception:
                continue

            grouped: Dict[str, Dict[str, Any]] = {}

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
                    market_key = normalize_market_key(market.get("key", ""))
                    if market_key not in request.markets:
                        continue

                    for outcome in market.get("outcomes", []):
                        name = outcome.get("name")
                        price = safe_float(outcome.get("price"))
                        point = safe_float(outcome.get("point"))

                        if not name or not price:
                            continue

                        group_key = "|".join([str(event_id), market_key, str(name), str(point)])

                        if group_key not in grouped:
                            grouped[group_key] = {
                                "match": str(away_team) + " vs " + str(home_team),
                                "sport": sport_alias,
                                "market": market_key,
                                "selection": str(name) if point is None else str(name) + " " + str(point),
                                "point": point,
                                "commence_time": commence_time,
                                "prices": [],
                                "best_odds": price,
                                "best_bookmaker": bookmaker_title,
                                "best_odds_age_minutes": odds_age_minutes,
                            }

                        grouped[group_key]["prices"].append(price)

                        if price > grouped[group_key]["best_odds"]:
                            grouped[group_key]["best_odds"] = price
                            grouped[group_key]["best_bookmaker"] = bookmaker_title
                            grouped[group_key]["best_odds_age_minutes"] = odds_age_minutes

            for item in grouped.values():
                prices = item["prices"]
                if not prices:
                    continue

                best_odds = round(max(prices), 2)
                avg_odds = round(sum(prices) / len(prices), 2)
                book_count = len(prices)
                model_prob = estimate_model_prob(best_odds, avg_odds, book_count)

                market = item["market"]
                variance = infer_variance(market, best_odds)

                lineup_confirmed = True
                if market in ("spreads", "totals"):
                    hours_to_start = (item["commence_time"] - now).total_seconds() / 3600.0
                    lineup_confirmed = hours_to_start > 1.5

                source_tier = DEFAULT_CONFIG["sport_tiers"].get(sport_alias, {}).get("tier", 2)

                candidate = Candidate(
                    match=item["match"],
                    sport=sport_alias,
                    market=market,
                    selection=item["selection"],
                    point=item["point"],
                    commence_time=item["commence_time"],
                    odds_best=best_odds,
                    odds_avg=avg_odds,
                    book_count=book_count,
                    model_prob=model_prob,
                    lineup_confirmed=lineup_confirmed,
                    injury_fresh_hours=2.0,
                    odds_age_minutes=round(item["best_odds_age_minutes"], 2),
                    source_tier=source_tier,
                    variance=variance,
                    bookmaker=item["best_bookmaker"],
                )
                all_candidates.append(candidate)

    return all_candidates[:request.max_candidates]


def write_txt_report(summary: Dict[str, Any]) -> str:
    path = REPORTS_DIR / f"{datetime.now().date()}_{summary['run_id']}_report.txt"
    req = summary["request"]

    with path.open("w", encoding="utf-8") as f:
        print("DAILY REPORT | " + str(datetime.now().date()), file=f)
        print("Request: " + str(req["raw_text"]), file=f)
        print("Mode: " + str(req["mode"]), file=f)
        print("Candidates: " + str(summary["candidates_count"]), file=f)
        print("Accepted: " + str(summary["accepted_count"]), file=f)
        print("Rejected: " + str(summary["rejected_count"]), file=f)
        print("Status: " + str(summary["message"]), file=f)
        print("", file=f)

        for i, r in enumerate(summary["results"], start=1):
            print(str(i) + ") " + str(r["match"]), file=f)
            print("Sport: " + str(r["sport"]), file=f)
            print("Market: " + str(r["selection"]), file=f)
            print("Best odds: " + str(r["best_odds"]) + " at " + str(r["bookmaker"]), file=f)
            print("Avg odds: " + str(r["avg_odds"]) + " | Book count: " + str(r["book_count"]), file=f)
            print("ModelProb: " + str(r["model_prob"]) + " | Implied: " + str(r["implied_prob"]), file=f)
            print("EV raw: " + str(r["ev_raw"]) + " | EV calibrated: " + str(r["ev_calibrated"]) + " | CI low: " + str(r["ci_low"]), file=f)
            print("Decision: " + str(r["decision"]) + " | Stake: " + str(r["stake"]) + " | Risk cap: " + str(r["risk_cap"]), file=f)
            print("Data quality: " + str(r["data_quality"]), file=f)
            print("Reasons: " + ", ".join(r["reasons"]), file=f)
            print("", file=f)

    return str(path)


def write_audit_json(summary: Dict[str, Any]) -> str:
    path = LOGS_DIR / f"{datetime.now().date()}_{summary['run_id']}_audit.json"
    with path.open("w",
