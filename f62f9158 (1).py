"""sport_models.py — Sport-specific probability models (Phase 4)."""
from __future__ import annotations
import math


def _football_model(ctx: dict) -> float | None:
    meta = ctx.get("sport_meta", {})
    home_xg = meta.get("home_xg")
    away_xg = meta.get("away_xg")
    selection = ctx.get("selection", "").lower()
    if home_xg is None or away_xg is None:
        return None
    lambda_h, lambda_a = float(home_xg), float(away_xg)
    probs = {}
    for h in range(8):
        for a in range(8):
            p = (math.exp(-lambda_h) * lambda_h**h / math.factorial(h)
                 * math.exp(-lambda_a) * lambda_a**a / math.factorial(a))
            if h > a:
                probs["home"] = probs.get("home", 0.0) + p
            elif a > h:
                probs["away"] = probs.get("away", 0.0) + p
            else:
                probs["draw"] = probs.get("draw", 0.0) + p
    if "home" in selection:
        return round(probs.get("home", 0.0) * 100, 2)
    if "away" in selection:
        return round(probs.get("away", 0.0) * 100, 2)
    if "draw" in selection:
        return round(probs.get("draw", 0.0) * 100, 2)
    return None


def _basketball_model(ctx: dict) -> float | None:
    meta = ctx.get("sport_meta", {})
    home_ortg = meta.get("home_ortg")
    home_drtg = meta.get("home_drtg")
    away_ortg = meta.get("away_ortg")
    away_drtg = meta.get("away_drtg")
    selection = ctx.get("selection", "").lower()
    if None in (home_ortg, home_drtg, away_ortg, away_drtg):
        return None
    home_net = float(home_ortg) - float(home_drtg) + 3.0  # home court +3
    away_net = float(away_ortg) - float(away_drtg)
    diff = home_net - away_net
    # logistic: every 3 pts of net diff ≈ 10% prob swing
    home_prob = 1.0 / (1.0 + math.exp(-diff / 8.0))
    if "home" in selection:
        return round(home_prob * 100, 2)
    if "away" in selection:
        return round((1.0 - home_prob) * 100, 2)
    return None


def _tennis_model(ctx: dict) -> float | None:
    meta = ctx.get("sport_meta", {})
    p1_wr = meta.get("p1_surface_winrate")
    p1_hr = meta.get("p1_hold_rate")
    p2_wr = meta.get("p2_surface_winrate", 0.50)
    selection = ctx.get("selection", "").lower()
    if p1_wr is None:
        return None
    surface_edge = float(p1_wr) - float(p2_wr)
    hold_bonus = (float(p1_hr) - 0.65) * 0.30 if p1_hr else 0.0
    raw = 0.50 + surface_edge * 0.60 + hold_bonus
    prob = max(0.15, min(raw, 0.88))
    if "p1" in selection:
        return round(prob * 100, 2)
    if "p2" in selection:
        return round((1.0 - prob) * 100, 2)
    return None


def _hockey_model(ctx: dict) -> float | None:
    meta = ctx.get("sport_meta", {})
    selection = ctx.get("selection", "").lower()
    total_line = float(meta.get("total_line", 5.5))
    home_goalie_sv = float(meta.get("home_goalie_sv", 0.910))
    away_b2b = bool(meta.get("away_b2b", False))
    # Expected goals proxy
    base_total_prob = 1.0 / (1.0 + math.exp(-(total_line - 5.5)))
    goalie_adj = (home_goalie_sv - 0.910) * 2.0
    b2b_adj = 0.08 if away_b2b else 0.0
    over_prob = base_total_prob - goalie_adj + b2b_adj
    over_prob = max(0.20, min(over_prob, 0.80))
    if "over" in selection:
        return round(over_prob * 100, 2)
    if "under" in selection:
        return round((1.0 - over_prob) * 100, 2)
    if "home" in selection:
        home_prob = 0.52 + (home_goalie_sv - 0.910) * 3.0 - (0.04 if away_b2b else 0.0)
        return round(max(0.30, min(home_prob, 0.75)) * 100, 2)
    return None


_SPORT_HANDLERS = {
    "football":   _football_model,
    "soccer":     _football_model,
    "basketball": _basketball_model,
    "tennis":     _tennis_model,
    "hockey":     _hockey_model,
}


def get_sport_model_prob(ctx: dict) -> float:
    """Return sport-model probability (0–100). Falls back to 0 if no meta."""
    sport = ctx.get("sport", "").lower()
    handler = _SPORT_HANDLERS.get(sport)
    if handler:
        result = handler(ctx)
        if result is not None:
            return float(result)
    return 0.0


def estimate_model_prob(best_odds, avg_odds, book_count, market, selection) -> float:
    """Consensus-based model probability (fallback when no sport_meta)."""
    implied_best = 100.0 / best_odds
    gap = max(0.0, best_odds - avg_odds)
    from gates import market_profile
    profile = market_profile(market, selection, best_odds)
    if market == "h2h":
        bonus = min(gap * 7.5, 2.2)
        consensus = 1.2 if book_count >= 6 else 0.8 if book_count >= 4 else 0.3 if book_count >= 3 else 0.0
        prob = implied_best + bonus + consensus + 0.3
        if profile == "draw":
            prob -= 3.2
        elif profile == "underdog":
            prob -= 2.4
        elif profile == "underdog_long":
            prob -= 5.6
    elif market == "spreads":
        bonus = min(gap * 10.0, 3.0)
        consensus = 1.2 if book_count >= 6 else 0.8 if book_count >= 4 else 0.3
        prob = implied_best + bonus + consensus
    elif market == "btts":
        bonus = min(gap * 8.5, 2.4)
        consensus = 1.0 if book_count >= 6 else 0.7 if book_count >= 4 else 0.3
        prob = implied_best + bonus + consensus - 0.2
    elif market == "team_totals":
        bonus = min(gap * 8.8, 2.5)
        consensus = 1.0 if book_count >= 6 else 0.7 if book_count >= 4 else 0.3
        prob = implied_best + bonus + consensus - 0.1
    else:
        bonus = min(gap * 9.0, 2.8)
        consensus = 1.0 if book_count >= 6 else 0.7 if book_count >= 4 else 0.3
        prob = implied_best + bonus + consensus
    return round(max(31.0, min(prob, 73.0)), 2)


def blend_model_prob(ctx: dict, best_odds, avg_odds, book_count, market, selection) -> float:
    """Phase 4 blending: sport model (70%) + consensus (30%) when meta available."""
    sport_prob = get_sport_model_prob(ctx)
    consensus_prob = estimate_model_prob(best_odds, avg_odds, book_count, market, selection)
    has_meta = bool(ctx.get("sport_meta"))
    return round(
        sport_prob * (0.70 if has_meta else 0.30)
        + consensus_prob * (0.30 if has_meta else 0.70),
        4,
    )
