"""gates.py — Hard gates, calibration, EV, CI, class logic."""
from __future__ import annotations
import math

CLASS_ORDER = {"PASS": 1, "MICRO": 2, "SUPPORT": 3, "CORE": 4}

MODE_RULES = {
    "FROZEN":    {"min_ev": 6.0, "micro": 10, "support": 20, "core": 30},
    "EMERGENCY": {"min_ev": 8.0, "micro": 10, "support": 15, "core": 20},
    "NORMAL":    {"min_ev": 4.0, "micro": 20, "support": 35, "core": 50},
    "GROWTH":    {"min_ev": 3.0, "micro": 25, "support": 40, "core": 60},
}

SPORT_LIMITS = {
    "basketball": {"min_book_count": 3, "max_odds_age": 180},
    "football":   {"min_book_count": 3, "max_odds_age": 180},
    "wnba":       {"min_book_count": 4, "max_odds_age": 120},
    "euroleague": {"min_book_count": 4, "max_odds_age": 120},
    "acb":        {"min_book_count": 4, "max_odds_age": 120},
    # live mode — tighter
    "live":       {"min_book_count": 4, "max_odds_age": 60},
}

# Live mode gate overrides
LIVE_MODE_RULES = {
    "FROZEN":    {"min_ev": 8.0,  "micro": 5,  "support": 10, "core": 20},
    "EMERGENCY": {"min_ev": 10.0, "micro": 5,  "support": 10, "core": 15},
    "NORMAL":    {"min_ev": 5.0,  "micro": 10, "support": 20, "core": 35},
    "GROWTH":    {"min_ev": 4.0,  "micro": 15, "support": 25, "core": 45},
}


def implied_probability(odds: float) -> float:
    return round(100.0 / odds, 2)


def market_profile(market: str, selection: str, odds: float) -> str:
    low = selection.lower()
    if market == "h2h":
        if "draw" in low:
            return "draw"
        if odds >= 4.8:
            return "underdog_long"
        if odds >= 3.2:
            return "underdog"
        return "favorite_or_balanced"
    if market == "spreads":
        return "spread"
    if market == "totals":
        return "total"
    if market == "btts":
        return "btts"
    if market == "team_totals":
        return "team_total"
    return "other"


def infer_variance(market: str, odds: float) -> str:
    if odds >= 3.5:
        return "EXTREME"
    if odds >= 2.5:
        return "HIGH"
    if market == "btts":
        return "MEDIUM"
    if market in ("totals", "team_totals"):
        return "MEDIUM"
    return "LOW"


def calc_data_quality(book_count, odds_age_minutes, lineup_confirmed, source_tier) -> str:
    score = 0
    score += 3 if book_count >= 6 else 2 if book_count >= 5 else 1 if book_count >= 3 else 0
    score += 3 if odds_age_minutes <= 20 else 2 if odds_age_minutes <= 60 else 1 if odds_age_minutes <= 120 else 0
    if lineup_confirmed:
        score += 1
    score += 2 if source_tier == 1 else 1 if source_tier == 2 else 0
    return "HIGH" if score >= 7 else "MEDIUM" if score >= 4 else "LOW"


def calibration_factor(book_count, odds_age_minutes, lineup_confirmed,
                        market, data_quality, odds, selection) -> float:
    profile = market_profile(market, selection, odds)
    factor = 1.0
    if book_count < 4:
        factor -= 0.14
    if odds_age_minutes > 120:
        factor -= 0.16
    if odds_age_minutes > 180:
        factor -= 0.10
    if (not lineup_confirmed) and market in ("spreads", "totals"):
        factor -= 0.18
    if data_quality == "LOW":
        factor -= 0.12
    if profile == "draw":
        factor -= 0.18
    elif profile == "underdog":
        factor -= 0.14
    elif profile == "underdog_long":
        factor -= 0.34
    elif market == "spreads":
        factor -= 0.03
    elif market == "btts":
        factor -= 0.08
    elif market == "team_totals":
        factor -= 0.06
    return round(max(factor, 0.18), 2)


def calc_ci_low(ev_calibrated, book_count, odds_age_minutes, variance,
                data_quality, odds, market, selection) -> float:
    profile = market_profile(market, selection, odds)
    penalty = 0.0
    if book_count < 4:
        penalty += 3.0
    if odds_age_minutes > 120:
        penalty += 3.5
    if odds_age_minutes > 180:
        penalty += 2.0
    if variance == "HIGH":
        penalty += 2.6
    if variance == "EXTREME":
        penalty += 4.6
    if data_quality == "LOW":
        penalty += 2.4
    if profile == "draw":
        penalty += 3.4
    elif profile == "underdog":
        penalty += 2.6
    elif profile == "underdog_long":
        penalty += 6.2
    elif market == "spreads":
        penalty += 0.4
    elif market == "totals":
        penalty += 0.6
    elif market == "btts":
        penalty += 1.1
    elif market == "team_totals":
        penalty += 0.9
    return round(ev_calibrated - penalty, 2)


def class_from_ev(ev_calibrated, ci_low, data_quality, odds, market, selection) -> str:
    profile = market_profile(market, selection, odds)
    if ci_low < 0:
        return "PASS"
    if profile == "underdog_long":
        return "MICRO" if (ev_calibrated >= 12 and ci_low >= 2.0 and data_quality == "HIGH") else "PASS"
    if profile == "draw":
        return "MICRO" if (ev_calibrated >= 10 and ci_low >= 2.0 and data_quality == "HIGH") else "PASS"
    if market == "spreads":
        if ev_calibrated >= 9 and ci_low >= 2:
            return "SUPPORT"
        if ev_calibrated >= 5 and ci_low >= 0.5:
            return "MICRO"
        return "PASS"
    if market == "totals":
        if ev_calibrated >= 8 and ci_low >= 1.5:
            return "SUPPORT"
        if ev_calibrated >= 5 and ci_low >= 0.5:
            return "MICRO"
        return "PASS"
    if market in ("btts", "team_totals"):
        if ev_calibrated >= 8 and ci_low >= 1.5 and data_quality in ("HIGH", "MEDIUM"):
            return "SUPPORT"
        if ev_calibrated >= 5 and ci_low >= 0.5:
            return "MICRO"
        return "PASS"
    if ev_calibrated >= 10 and ci_low >= 3 and data_quality == "HIGH":
        return "CORE"
    if ev_calibrated >= 7 and ci_low >= 1:
        return "SUPPORT"
    if ev_calibrated >= 4 and ci_low >= 0:
        return "MICRO"
    return "PASS"


def cap_class(base_class, max_class) -> str:
    return max_class if CLASS_ORDER[base_class] > CLASS_ORDER[max_class] else base_class


def risk_cap(mode, decision, live=False) -> float:
    rules = LIVE_MODE_RULES if live else MODE_RULES
    rule = rules.get(mode, rules["NORMAL"])
    return float(
        rule["micro"] if decision == "MICRO" else
        rule["support"] if decision == "SUPPORT" else
        rule["core"] if decision == "CORE" else 0.0
    )


def calc_stake(bank, decision, ev_calibrated, odds, cap_value, market, selection) -> float:
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
    profile = market_profile(market, selection, odds)
    if profile == "underdog_long":
        kelly *= 0.25
    elif profile == "underdog":
        kelly *= 0.45
    elif profile == "draw":
        kelly *= 0.35
    elif market == "spreads":
        kelly *= 0.85
    elif market == "totals":
        kelly *= 0.80
    elif market == "btts":
        kelly *= 0.72
    elif market == "team_totals":
        kelly *= 0.76
    stake = min(bank * kelly * 0.20, cap_value)
    rounded = math.floor(stake / 10.0) * 10.0
    return 10.0 if rounded == 0 and stake > 0 else float(max(rounded, 0.0))


def build_scorecard(candidate, implied, ev_raw, ev_calibrated, ci_low, data_quality) -> dict:
    profile = market_profile(candidate["market"], candidate["selection"], candidate["odds_best"])
    score = 0
    score += 2 if candidate["book_count"] >= 6 else 1 if candidate["book_count"] >= 4 else 0
    score += 2 if candidate["odds_age_minutes"] <= 20 else 1 if candidate["odds_age_minutes"] <= 60 else 0
    score += 1 if candidate["lineup_confirmed"] else 0
    score += 2 if ev_calibrated >= 10 else 1 if ev_calibrated >= 5 else 0
    score += 1 if ci_low >= 2 else 0
    if profile == "draw":
        score -= 3
    elif profile == "underdog":
        score -= 2
    elif profile == "underdog_long":
        score -= 5
    label = "A" if score >= 6 else "B" if score >= 4 else "C" if score >= 2 else "D"
    return {
        "grade": label, "score": score, "book_count": candidate["book_count"],
        "odds_age_minutes": candidate["odds_age_minutes"], "implied_prob": implied,
        "ev_raw": ev_raw, "ev_calibrated": ev_calibrated, "ci_low": ci_low,
        "data_quality": data_quality, "profile": profile,
    }
