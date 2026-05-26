"""edge_engine.py — Candidate fetching, finalization, sorting (Phase 3+4+EXPANDED)."""
from __future__ import annotations
import os
import uuid
import requests
from datetime import datetime, timedelta, timezone

from gates import (
    CLASS_ORDER, MODE_RULES, SPORT_LIMITS,
    implied_probability, market_profile, infer_variance,
    calc_data_quality, calibration_factor, calc_ci_low, class_from_ev,
    cap_class, risk_cap, calc_stake, build_scorecard,
)
from sport_models import blend_model_prob

UTC = timezone.utc

SPORT_MAP = {
    # ═══════════════════════════════════════════════════════════════════════════
    # FOOTBALL - Расширенный список (все кроме России/Украины)
    # ═══════════════════════════════════════════════════════════════════════════
    
    # Англия
    "epl": ("football", "soccer_epl", 2, "CORE"),
    "premierleague": ("football", "soccer_epl", 2, "CORE"),
    "premier_league": ("football", "soccer_epl", 2, "CORE"),
    "championship": ("football", "soccer_england_championship", 3, "SUPPORT"),
    "league1": ("football", "soccer_england_league_one", 3, "SUPPORT"),
    "league2": ("football", "soccer_england_league_two", 4, "MICRO"),
    
    # Испания
    "laliga": ("football", "soccer_spain_la_liga", 2, "CORE"),
    "la_liga": ("football", "soccer_spain_la_liga", 2, "CORE"),
    "segunda": ("football", "soccer_spain_segunda_division", 3, "SUPPORT"),
    
    # Италия
    "seriea": ("football", "soccer_italy_serie_a", 2, "CORE"),
    "serie_a": ("football", "soccer_italy_serie_a", 2, "CORE"),
    "serieb": ("football", "soccer_italy_serie_b", 3, "SUPPORT"),
    
    # Германия
    "bundesliga": ("football", "soccer_germany_bundesliga", 2, "CORE"),
    "bundesliga2": ("football", "soccer_germany_bundesliga_2", 3, "SUPPORT"),
    
    # Франция
    "ligue1": ("football", "soccer_france_ligue_one", 2, "CORE"),
    "ligue_1": ("football", "soccer_france_ligue_one", 2, "CORE"),
    "ligue2": ("football", "soccer_france_ligue_two", 3, "SUPPORT"),
    
    # Нидерланды
    "eredivisie": ("football", "soccer_netherlands_eredivisie", 2, "CORE"),
    "eerste_divisie": ("football", "soccer_netherlands_eerste_divisie", 3, "SUPPORT"),
    
    # Бельгия
    "jupiler": ("football", "soccer_belgium_first_div", 3, "SUPPORT"),
    "first_division": ("football", "soccer_belgium_first_div", 3, "SUPPORT"),
    
    # Португалия
    "primeira": ("football", "soccer_portugal_primeira_liga", 3, "SUPPORT"),
    "primeiraleague": ("football", "soccer_portugal_primeira_liga", 3, "SUPPORT"),
    
    # Греция
    "super_league": ("football", "soccer_greece_super_league", 3, "SUPPORT"),
    "superleague": ("football", "soccer_greece_super_league", 3, "SUPPORT"),
    
    # Турция
    "super_lig": ("football", "soccer_turkey_super_lig", 3, "SUPPORT"),
    "superlig": ("football", "soccer_turkey_super_lig", 3, "SUPPORT"),
    
    # Швеция
    "allsvenskan": ("football", "soccer_sweden_allsvenskan", 3, "SUPPORT"),
    
    # Норвегия
    "eliteserien": ("football", "soccer_norway_eliteserien", 3, "SUPPORT"),
    
    # Дания
    "superligaen": ("football", "soccer_denmark_superligaen", 3, "SUPPORT"),
    
    # Австрия
    "bundesliga_at": ("football", "soccer_austria_bundesliga", 3, "SUPPORT"),
    
    # Швейцария
    "super_league_ch": ("football", "soccer_switzerland_super_league", 3, "SUPPORT"),
    
    # Чехия
    "fortuna_liga": ("football", "soccer_czechia_first_league", 3, "SUPPORT"),
    
    # Польша
    "ekstraklasa": ("football", "soccer_poland_ekstraklasa", 3, "SUPPORT"),
    
    # Венгрия
    "nb1": ("football", "soccer_hungary_nb1", 3, "SUPPORT"),
    
    # Румыния
    "liga_1": ("football", "soccer_romania_liga_1", 3, "SUPPORT"),
    
    # США
    "mls": ("football", "soccer_usa_mls", 3, "SUPPORT"),
    
    # Мексика
    "liga_mx": ("football", "soccer_mexico_liga_mx", 3, "SUPPORT"),
    "ligamx": ("football", "soccer_mexico_liga_mx", 3, "SUPPORT"),
    
    # Канада
    "cpl": ("football", "soccer_canada_cpl", 3, "SUPPORT"),
    
    # Аргентина
    "argentina": ("football", "soccer_argentina_primera", 3, "SUPPORT"),
    "primera_argentina": ("football", "soccer_argentina_primera", 3, "SUPPORT"),
    
    # Бразилия
    "brasileirao": ("football", "soccer_brazil_campeonato", 3, "SUPPORT"),
    "campeonato": ("football", "soccer_brazil_campeonato", 3, "SUPPORT"),
    
    # Чили
    "primera_chile": ("football", "soccer_chile_primera", 3, "SUPPORT"),
    
    # Уругвай
    "primera_uruguay": ("football", "soccer_uruguay_primera", 3, "SUPPORT"),
    
    # Япония
    "jleague": ("football", "soccer_japan_j_league", 3, "SUPPORT"),
    "j_league": ("football", "soccer_japan_j_league", 3, "SUPPORT"),
    
    # Австралия
    "afl": ("football", "soccer_australia_a_league", 3, "SUPPORT"),
    "aleague": ("football", "soccer_australia_a_league", 3, "SUPPORT"),
    
    # Новая Зеландия
    "nzfc": ("football", "soccer_newzealand_nzfc", 3, "SUPPORT"),
    
    # Южная Корея
    "kleague": ("football", "soccer_korea_kleague", 3, "SUPPORT"),
    "k_league": ("football", "soccer_korea_kleague", 3, "SUPPORT"),
    
    # Таиланд
    "thai_league": ("football", "soccer_thailand_thai_league", 3, "SUPPORT"),
    
    # Вьетнам
    "vietnamese": ("football", "soccer_vietnam_v_league", 3, "SUPPORT"),
    
    # Индия
    "isl": ("football", "soccer_india_super_league", 3, "SUPPORT"),
    "super_league_india": ("football", "soccer_india_super_league", 3, "SUPPORT"),
    
    # ═══════════════════════════════════════════════════════════════════════════
    # BASKETBALL - Расширенный список
    # ═══════════════════════════════════════════════════════════════════════════
    
    "nba": ("basketball", "basketball_nba", 2, "CORE"),
    "wnba": ("wnba", "basketball_wnba", 3, "MICRO"),
    "euroleague": ("euroleague", "basketball_euroleague", 2, "CORE"),
    "acb": ("acb", "basketball_spain_acb", 2, "SUPPORT"),
    
    # Другие европейские лиги
    "nbl_france": ("basketball", "basketball_france_lnb", 3, "SUPPORT"),
    "frenchleague": ("basketball", "basketball_france_lnb", 3, "SUPPORT"),
    "lnb": ("basketball", "basketball_france_lnb", 3, "SUPPORT"),
    
    "bbl": ("basketball", "basketball_germany_bbl", 3, "SUPPORT"),
    "german_bbl": ("basketball", "basketball_germany_bbl", 3, "SUPPORT"),
    
    "serie_a_it": ("basketball", "basketball_italy_lba", 3, "SUPPORT"),
    "lba": ("basketball", "basketball_italy_lba", 3, "SUPPORT"),
    
    "eredivisie_bk": ("basketball", "basketball_netherlands_eredivisie", 3, "SUPPORT"),
    
    "superligaen_bk": ("basketball", "basketball_denmark_superligaen", 3, "SUPPORT"),
    
    "bbl_poland": ("basketball", "basketball_poland_pko_bp", 3, "SUPPORT"),
    
    # Азиатские лиги
    "cba": ("basketball", "basketball_china_cba", 4, "MICRO"),
    "chinese": ("basketball", "basketball_china_cba", 4, "MICRO"),
    
    "bj": ("basketball", "basketball_japan_bj_league", 4, "MICRO"),
    "japanese": ("basketball", "basketball_japan_bj_league", 4, "MICRO"),
    
    "kbl": ("basketball", "basketball_korea_kbl", 4, "MICRO"),
    "korean": ("basketball", "basketball_korea_kbl", 4, "MICRO"),
    
    # ═══════════════════════════════════════════════════════════════════════════
    # TENNIS - Расширенный список (Grand Slams, Masters, Challenger)
    # ═══════════════════════════════════════════════════════════════════════════
    
    # Grand Slams
    "atp": ("tennis", "tennis_atp_french_open", 2, "SUPPORT"),
    "wta": ("tennis", "tennis_wta_french_open", 3, "MICRO"),
    
    # Masters 1000 ATP
    "atp_masters": ("tennis", "tennis_atp_masters", 2, "SUPPORT"),
    "masters": ("tennis", "tennis_atp_masters", 2, "SUPPORT"),
    
    # ATP Challenger
    "atp_challenger": ("tennis", "tennis_atp_challenger", 3, "MICRO"),
    "challenger": ("tennis", "tennis_atp_challenger", 3, "MICRO"),
    
    # WTA 1000
    "wta_1000": ("tennis", "tennis_wta_1000", 3, "MICRO"),
    
    # WTA 500
    "wta_500": ("tennis", "tennis_wta_500", 4, "MICRO"),
    
    # ITF
    "itf": ("tennis", "tennis_itf_women", 4, "MICRO"),
    "itf_women": ("tennis", "tennis_itf_women", 4, "MICRO"),
    "itf_men": ("tennis", "tennis_itf_men", 4, "MICRO"),
    
    # ═══════════════════════════════════════════════════════════════════════════
    # HOCKEY - Расширенный список
    # ═══════════════════════════════════════════════════════════════════════════
    
    "nhl": ("hockey", "icehockey_nhl", 2, "CORE"),
    
    # Европейские лиги
    "shl": ("hockey", "icehockey_sweden_shl", 3, "SUPPORT"),
    "swedish": ("hockey", "icehockey_sweden_shl", 3, "SUPPORT"),
    
    "liiga": ("hockey", "icehockey_finland_liiga", 3, "SUPPORT"),
    "finnish": ("hockey", "icehockey_finland_liiga", 3, "SUPPORT"),
    
    "ekhl": ("hockey", "icehockey_czech_ekhl", 3, "SUPPORT"),
    "czech": ("hockey", "icehockey_czech_ekhl", 3, "SUPPORT"),
    
    "extraliga": ("hockey", "icehockey_sk_extraliga", 3, "SUPPORT"),
    "slovak": ("hockey", "icehockey_sk_extraliga", 3, "SUPPORT"),
    
    "nbl": ("hockey", "icehockey_poland_phl", 3, "SUPPORT"),
    "polish": ("hockey", "icehockey_poland_phl", 3, "SUPPORT"),
    
    "erste": ("hockey", "icehockey_austria_ebel", 3, "SUPPORT"),
    "austrian": ("hockey", "icehockey_austria_ebel", 3, "SUPPORT"),
    
    "get_liga": ("hockey", "icehockey_germany_del", 3, "SUPPORT"),
    "german": ("hockey", "icehockey_germany_del", 3, "SUPPORT"),
    
    "swiss": ("hockey", "icehockey_switzerland_nla", 3, "SUPPORT"),
    "sla": ("hockey", "icehockey_switzerland_nla", 3, "SUPPORT"),
    
    "lnh": ("hockey", "icehockey_france_lnh", 3, "SUPPORT"),
    "french": ("hockey", "icehockey_france_lnh", 3, "SUPPORT"),
    
    # Северная Америка
    "ahl": ("hockey", "icehockey_ahl", 3, "SUPPORT"),
    "american": ("hockey", "icehockey_ahl", 3, "SUPPORT"),
    
    "whl": ("hockey", "icehockey_whl", 4, "MICRO"),
    "western": ("hockey", "icehockey_whl", 4, "MICRO"),
    
    "qmjhl": ("hockey", "icehockey_qmjhl", 4, "MICRO"),
    "quebec": ("hockey", "icehockey_qmjhl", 4, "MICRO"),
    
    "ohl": ("hockey", "icehockey_ohl", 4, "MICRO"),
    "ontario": ("hockey", "icehockey_ohl", 4, "MICRO"),
}


def match_team_filter(match_name: str, team_filters: list) -> bool:
    return True if not team_filters else any(t in match_name.lower() for t in team_filters)


def _parse_grouped_outcomes(event, now, request_data):
    """Extract best/avg odds per (match×market×selection) from an API event."""
    home_team = event.get("home_team", "")
    away_team = event.get("away_team", "")
    commence_raw = event.get("commence_time", "")
    event_id = str(event.get("id", ""))
    if not (home_team and away_team and commence_raw):
        return []
    try:
        commence_time = datetime.fromisoformat(commence_raw.replace("Z", "+00:00"))
    except Exception:
        return []
    match_name = f"{away_team} vs {home_team}"
    if not match_team_filter(match_name, request_data.get("team_filters", [])):
        return []

    grouped = {}
    for bookmaker in event.get("bookmakers", []):
        bk_title = bookmaker.get("title", "Book")
        last_update_raw = bookmaker.get("last_update")
        odds_age = 999.0
        if last_update_raw:
            try:
                lu = datetime.fromisoformat(last_update_raw.replace("Z", "+00:00"))
                odds_age = max((now - lu).total_seconds() / 60.0, 0.0)
            except Exception:
                pass
        for market in bookmaker.get("markets", []):
            mk = market.get("key", "")
            if mk not in ("h2h", "spreads", "totals", "btts", "team_totals"):
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
                gk = f"{event_id}|{mk}|{name}|{point}"
                if gk not in grouped:
                    sel = str(name) if point is None else f"{name} {point}"
                    if mk == "btts":
                        sel = f"BTTS {name}"
                    grouped[gk] = {
                        "event_id": event_id, "match": match_name,
                        "commence_time": commence_time,
                        "market": mk, "selection": sel, "point": point,
                        "prices": [], "best_odds": price,
                        "best_bookmaker": bk_title,
                        "best_odds_age_minutes": odds_age,
                    }
                grouped[gk]["prices"].append(price)
                if price > grouped[gk]["best_odds"]:
                    grouped[gk]["best_odds"] = price
                    grouped[gk]["best_bookmaker"] = bk_title
                    grouped[gk]["best_odds_age_minutes"] = odds_age
    return list(grouped.values())


def fetch_candidates(request_data: dict, live: bool = False) -> list:
    api_key = os.getenv("ODDS_API_KEY")
    if not api_key:
        raise RuntimeError("ODDS_API_KEY is not set")
    now = datetime.now(UTC)
    all_candidates, seen = [], set()

    for sport_name in request_data["sports"]:
        if sport_name not in SPORT_MAP:
            print(f"⚠️ Sport '{sport_name}' not found in SPORT_MAP")
            continue
            
        sport_alias, sport_key, source_tier, max_class = SPORT_MAP[sport_name]
        params = {
            "apiKey": api_key,
            "regions": "eu,uk",
            "markets": "h2h,spreads,totals",
            "oddsFormat": "decimal",
            "dateFormat": "iso",
        }
        if live:
            params["inPlay"] = "true"
        try:
            resp = requests.get(
                f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds",
                params=params, timeout=25,
            )
            resp.raise_for_status()
            events = resp.json()
        except Exception as exc:
            print(f"⚠️ API error for {sport_key}: {exc}")
            continue

        for event in events:
            for item in _parse_grouped_outcomes(event, now, request_data):
                if not item["prices"]:
                    continue
                uniq = f"{item['match']}|{item['market']}|{item['selection']}"
                if uniq in seen:
                    continue
                seen.add(uniq)
                best_odds = round(max(item["prices"]), 2)
                avg_odds  = round(sum(item["prices"]) / len(item["prices"]), 2)
                book_count = len(item["prices"])
                market = item["market"]
                variance = infer_variance(market, best_odds)
                hours_to_start = (item["commence_time"] - now).total_seconds() / 3600.0
                lineup_confirmed = not (market in ("spreads", "totals") and hours_to_start <= 1.5)
                _ctx = {
                    "sport": sport_alias, "market": market,
                    "selection": item["selection"], "best_odds": best_odds,
                    "sport_meta": item.get("sport_meta", {}),
                }
                model_prob = blend_model_prob(
                    _ctx, best_odds, avg_odds, book_count, market, item["selection"]
                )
                all_candidates.append({
                    "event_id": item["event_id"], "match": item["match"],
                    "sport": sport_alias, "market": market,
                    "selection": item["selection"], "point": item["point"],
                    "commence_time": item["commence_time"],
                    "odds_best": best_odds, "odds_avg": avg_odds,
                    "book_count": book_count, "model_prob": model_prob,
                    "lineup_confirmed": lineup_confirmed,
                    "injury_fresh_hours": 2.0,
                    "odds_age_minutes": round(item["best_odds_age_minutes"], 2),
                    "source_tier": source_tier, "variance": variance,
                    "bookmaker": item["best_bookmaker"], "max_class": max_class,
                    "live": live,
                })
    return all_candidates[: request_data["max_candidates"]]


def finalize_candidate(candidate: dict, request_data: dict, run_id: str) -> dict:
    now = datetime.now(UTC)
    reasons = []
    live = candidate.get("live", False)
    limits = SPORT_LIMITS.get("live" if live else candidate["sport"],
                               SPORT_LIMITS.get(candidate["sport"],
                               {"min_book_count": 3, "max_odds_age": 180}))
    implied = implied_probability(candidate["odds_best"])
    ev_raw = round(candidate["model_prob"] - implied, 2)
    dq = calc_data_quality(candidate["book_count"], candidate["odds_age_minutes"],
                            candidate["lineup_confirmed"], candidate["source_tier"])
    factor = calibration_factor(candidate["book_count"], candidate["odds_age_minutes"],
                                 candidate["lineup_confirmed"], candidate["market"], dq,
                                 candidate["odds_best"], candidate["selection"])
    ev_calibrated = round(ev_raw * factor, 2)
    ci_low = calc_ci_low(ev_calibrated, candidate["book_count"], candidate["odds_age_minutes"],
                          candidate["variance"], dq, candidate["odds_best"],
                          candidate["market"], candidate["selection"])
    decision, stake, cap_value = "PASS", 0.0, 0.0
    profile = market_profile(candidate["market"], candidate["selection"], candidate["odds_best"])

    # Hard gates
    if candidate["commence_time"] < now:
        reasons.append("EXPIRED_EVENT")
    elif (candidate["commence_time"] - now) < timedelta(minutes=15) and not live:
        reasons.append("TOO_CLOSE")
    elif candidate["odds_age_minutes"] > limits["max_odds_age"]:
        reasons.append("STALE_ODDS")
    elif candidate["book_count"] < limits["min_book_count"]:
        reasons.append("LOW_BOOK_COUNT")
    elif request_data["mode"] == "EMERGENCY" and candidate["variance"] in ("HIGH", "EXTREME"):
        reasons.append("HIGH_VARIANCE")
    elif profile == "draw" and (dq != "HIGH" or candidate["book_count"] < 5):
        reasons.append("DRAW_GUARDRAIL")
    elif profile == "underdog_long" and (dq != "HIGH" or candidate["book_count"] < 6):
        reasons.append("UNDERDOG_GUARDRAIL")
    elif ev_calibrated < MODE_RULES[request_data["mode"]]["min_ev"]:
        reasons.append("EV_TOO_LOW")
    elif ci_low < 0:
        reasons.append("CI_LOW_NEGATIVE")
    else:
        decision = class_from_ev(ev_calibrated, ci_low, dq, candidate["odds_best"],
                                  candidate["market"], candidate["selection"])
        if candidate["source_tier"] >= 3 and CLASS_ORDER[decision] > CLASS_ORDER["MICRO"]:
            decision = "MICRO"
            reasons.append("SPORT_TIER_CAP")
        if (not candidate["lineup_confirmed"]) and candidate["market"] in ("spreads", "totals"):
            if request_data["strict"]:
                decision = "PASS"
            elif CLASS_ORDER[decision] > CLASS_ORDER["MICRO"]:
                decision = "MICRO"
            reasons.append("LINEUP_PENDING")
        if profile == "draw" and CLASS_ORDER[decision] > CLASS_ORDER["MICRO"]:
            decision = "MICRO"
            reasons.append("DRAW_CAP")
        if profile == "underdog_long" and CLASS_ORDER[decision] > CLASS_ORDER["MICRO"]:
            decision = "MICRO"
            reasons.append("LONGSHOT_CAP")
        if live and CLASS_ORDER[decision] > CLASS_ORDER["SUPPORT"]:
            decision = "SUPPORT"
            reasons.append("LIVE_CAP")
        decision = cap_class(decision, candidate["max_class"])
        if candidate["book_count"] < 5:
            reasons.append("NO_STRONG_CONSENSUS")
        reasons.extend([
            "EDGE_OK", f"DATA_QUALITY_{dq}",
            f"PROFILE_{profile.upper()}", f"CLASS_{decision}",
        ])

    if decision == "PASS":
        reasons.append("RULES_BLOCK")
    cap_value = risk_cap(request_data["mode"], decision, live=live)
    stake = calc_stake(request_data["bank"], decision, ev_calibrated,
                        candidate["odds_best"], cap_value,
                        candidate["market"], candidate["selection"])
    scorecard = build_scorecard(candidate, implied, ev_raw, ev_calibrated, ci_low, dq)
    return {
        "pick_id": uuid.uuid4().hex[:12], "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "event_id": candidate["event_id"], "match": candidate["match"],
        "sport": candidate["sport"], "market": candidate["market"],
        "selection": candidate["selection"], "point": candidate["point"],
        "commence_time": candidate["commence_time"].isoformat(),
        "best_odds": candidate["odds_best"], "avg_odds": candidate["odds_avg"],
        "bookmaker": candidate["bookmaker"], "book_count": candidate["book_count"],
        "model_prob": candidate["model_prob"], "implied_prob": implied,
        "ev_raw": ev_raw, "ev_calibrated": ev_calibrated, "ci_low": ci_low,
        "risk_cap": cap_value, "data_quality": dq, "decision": decision,
        "stake": stake, "reasons": reasons, "scorecard": scorecard,
        "live": live,
    }


def sort_results(results: list) -> list:
    dq_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    return sorted(
        results,
        key=lambda x: (CLASS_ORDER.get(x["decision"], 0), x["scorecard"]["score"],
                        x["ev_calibrated"], x["ci_low"],
                        dq_rank.get(x["data_quality"], 0)),
        reverse=True,
    )


def best_per_match(results: list) -> list:
    grouped: dict = {}
    for item in results:
        grouped.setdefault(item["match"], []).append(item)
    final = []
    for items in grouped.values():
        favored = [x for x in items if x["decision"] != "PASS"]
        pool = favored if favored else items
        best = sorted(pool,
                       key=lambda x: (CLASS_ORDER.get(x["decision"], 0),
                                       x["scorecard"]["score"], x["ev_calibrated"]),
                       reverse=True)[0]
        final.append(best)
    return sorted(final,
                   key=lambda x: (CLASS_ORDER.get(x["decision"], 0),
                                   x["scorecard"]["score"], x["ev_calibrated"]),
                   reverse=True)
