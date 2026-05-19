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

CLASS_ORDER = {
    "PASS": 1,
    "MICRO": 2,
    "SUPPORT": 3,
    "CORE": 4,
}

SPORT_MAP = {
    "basketball": ("basketball", "basketball_nba", 2, "CORE"),
    "football": ("football", "soccer_spain_la_liga", 2, "CORE"),
    "wnba": ("wnba", "basketball_wnba", 3, "MICRO"),
    "euroleague": ("euroleague", "basketball_euroleague", 2, "CORE"),
    "acb": ("acb", "basketball_spain_acb", 2, "SUPPORT"),
    "laliga": ("laliga", "soccer_spain_la_liga", 2, "CORE"),
}


def parse_request(text):
    raw = text.strip().lower()
    sports = []
    for key in SPORT_MAP.keys():
        if key in raw:
            sports.append(key)
    if not sports:
        sports = ["basketball"]

    strict = "strict" in raw
    bank = 1000.0
    parts = raw.replace("=", " ").split()
    for i, token in enumerate(parts):
        if token == "bank" and i + 1 < len(parts):
            try:
                bank = float(parts[i + 1])
            except Exception:
                bank = 1000.0

    if bank < 500:
        mode = "FROZEN"
    elif bank < 1000:
        mode = "EMERGENCY"
    elif bank < 3000:
        mode = "NORMAL"
    else:
        mode = "GROWTH"

    return {
        "raw_text": text,
        "sports": sports,
        "strict": strict,
        "bank": bank,
        "mode": mode,
        "markets": ["h2h", "spreads", "totals"],
        "max_candidates": 12,
    }


def implied_probability(odds):
    return round(100.0 / odds, 2)


def estimate_model_prob(best_odds, avg_odds, book_count):
    implied_best = 100.0 / best_odds
    edge_bonus = (best_odds - avg_odds) * 12.0
    if edge_bonus < 0:
        edge_bonus = 0.0
    if edge_bonus > 4.5:
        edge_bonus = 4.5

    if book_count >= 5:
        consensus_bonus = 1.5
    elif book_count >= 3:
        consensus_bonus = 0.75
    else:
        consensus_bonus = 0.0

    value = implied_best + edge_bonus + consensus_bonus
    if value < 35.0:
        value = 35.0
    if value > 75.0:
        value = 75.0
    return round(value, 2)


def calc_data_quality(book_count, odds_age_minutes, lineup_confirmed, source_tier):
    score = 0
    if book_count >= 5:
        score += 2
    elif book_count >= 3:
        score += 1

    if odds_age_minutes <= 30:
        score += 2
    elif odds_age_minutes <= 120:
        score += 1

    if lineup_confirmed:
        score += 1

    if source_tier == 1:
        score += 2
    elif source_tier == 2:
        score += 1

    if score >= 6:
        return "HIGH"
    if score >= 3:
        return "MEDIUM"
    return "LOW"


def calibration_factor(book_count, odds_age_minutes, lineup_confirmed, market, data_quality):
    factor = 1.0
    if book_count < 3:
        factor -= 0.25
    if odds_age_minutes > 120:
        factor -= 0.20
    if (not lineup_confirmed) and market in ["spreads", "totals"]:
        factor -= 0.15
    if data_quality == "LOW":
        factor -= 0.10
    if factor < 0.25:
        factor = 0.25
    return round(factor, 2)


def calc_ci_low(ev_calibrated, book_count, odds_age_minutes, variance, data_quality):
    penalty = 0.0
    if book_count < 3:
        penalty += 5.0
    if odds_age_minutes > 120:
        penalty += 4.0
    if variance in ["HIGH", "EXTREME"]:
        penalty += 5.0
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
    if CLASS_ORDER[base_class] > CLASS_ORDER[max_class]:
        return max_class
    return base_class


def infer_variance(market, odds):
    if odds >= 3.2:
        return "EXTREME"
    if odds >= 2.4:
        return "HIGH"
    if market == "totals":
        return "MEDIUM"
    return "LOW"


def risk_cap(mode, decision):
    rules = {
        "FROZEN": {"MICRO": 10, "SUPPORT": 20, "CORE": 30},
        "EMERGENCY": {"MICRO": 10, "SUPPORT": 15, "CORE": 20},
        "NORMAL": {"MICRO": 20, "SUPPORT": 35, "CORE": 50},
        "GROWTH": {"MICRO": 25, "SUPPORT": 40, "CORE": 60},
    }
    return float(rules.get(mode, {}).get(decision, 0))


def calc_stake(bank, decision, ev_calibrated, odds, cap_value):
    if decision == "PASS":
        return 0.0
    b = odds - 1.0
    if b <= 0:
        return 0.0

    p = (implied_probability(odds) + ev_calibrated) / 100.0
    if p < 0.01:
        p = 0.01
    if p > 0.95:
        p = 0.95

    q = 1.0 - p
    kelly = ((b * p) - q) / b
    if kelly < 0:
        kelly = 0.0

    stake = bank * kelly * 0.25
    if stake > cap_value:
        stake = cap_value

    rounded = math.floor(stake / 10.0) * 10.0
    if rounded == 0 and stake > 0:
        return 10.0
    return float(max(rounded, 0.0))


def fetch_candidates(request_data):
    api_key = os.getenv("ODDS_API_KEY")
    if not api_key:
        raise RuntimeError("ODDS_API_KEY is not set")

    now = datetime.now(UTC)
    all_candidates = []

    for sport_name in request_data["sports"]:
        sport_alias, sport_key, source_tier, max_class = SPORT_MAP[sport_name]
        url = "https://api.the-odds-api.com/v4/sports/" + sport_key + "/odds"
        params = {
            "apiKey": api_key,
            "regions": "eu,uk",
            "markets": "h2h,spreads,totals",
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
            event_id = str(event.get("id", ""))

            if not home_team or not away_team or not commence_raw:
                continue

            try:
                commence_time = datetime.fromisoformat(commence_raw.replace("Z", "+00:00"))
            except Exception:
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
                            if point is None:
                                selection_text = str(name)
                            else:
                                selection_text = str(name) + " " + str(point)

                            grouped[group_key] = {
                                "match": str(away_team) + " vs " + str(home_team),
                                "sport": sport_alias,
                                "market": market_key,
                                "selection": selection_text,
                                "point": point,
                                "commence_time": commence_time,
                                "prices": [],
                                "best_odds": price,
                                "best_bookmaker": bookmaker_title,
                                "best_odds_age_minutes": odds_age_minutes,
                                "source_tier": source_tier,
                                "max_class": max_class,
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
                market = item["market"]
                variance = infer_variance(market, best_odds)

                hours_to_start = (item["commence_time"] - now).total_seconds() / 3600.0
                lineup_confirmed = True
                if market in ["spreads", "totals"] and hours_to_start <= 1.5:
                    lineup_confirmed = False

                model_prob = estimate_model_prob(best_odds, avg_odds, book_count)

                all_candidates.append({
                    "match": item["match"],
                    "sport": item["sport"],
                    "market": item["market"],
                    "selection": item["selection"],
                    "point": item["point"],
                    "commence_time": item["commence_time"],
                    "odds_best": best_odds,
                    "odds_avg": avg_odds,
                    "book_count": book_count,
                    "model_prob": model_prob,
                    "lineup_confirmed": lineup_confirmed,
                    "injury_fresh_hours": 2.0,
                    "odds_age_minutes": round(item["best_odds_age_minutes"], 2),
                    "source_tier": item["source_tier"],
                    "variance": variance,
                    "bookmaker": item["best_bookmaker"],
                    "max_class": item["max_class"],
                })

    return all_candidates[:request_data["max_candidates"]]


def finalize_candidate(candidate, request_data, run_id):
    now = datetime.now(UTC)
    reasons = []

    implied = implied_probability(candidate["odds_best"])
    ev_raw = round(candidate["model_prob"] - implied, 2)

    data_quality = calc_data_quality(
        candidate["book_count"],
        candidate["odds_age_minutes"],
        candidate["lineup_confirmed"],
        candidate["source_tier"],
    )

    factor = calibration_factor(
        candidate["book_count"],
        candidate["odds_age_minutes"],
        candidate["lineup_confirmed"],
        candidate["market"],
        data_quality,
    )

    ev_calibrated = round(ev_raw * factor, 2)
    ci_low = calc_ci_low(
        ev_calibrated,
        candidate["book_count"],
        candidate["odds_age_minutes"],
        candidate["variance"],
        data_quality,
    )

    decision = "PASS"
    stake = 0.0
    cap_value = 0.0

    if candidate["commence_time"] < now:
        reasons.append("EXPIRED_EVENT")
    elif (candidate["commence_time"] - now) < timedelta(minutes=10):
        reasons.append("TOO_CLOSE")
    elif candidate["odds_age_minutes"] > 240:
        reasons.append("STALE_ODDS")
    elif candidate["book_count"] < 2:
        reasons.append("LOW_BOOK_COUNT")
    elif ci_low < 0:
        reasons.append("CI_LOW_NEGATIVE")
    elif request_data["mode"] == "EMERGENCY" and ev_calibrated < 8.0:
        reasons.append("EMERGENCY_CAP")
    elif request_data["mode"] == "EMERGENCY" and candidate["variance"] in ["HIGH", "EXTREME"]:
        reasons.append("HIGH_VARIANCE")
    else:
        base_class = class_from_ev(ev_calibrated, ci_low, data_quality)
        decision = base_class

        if candidate["source_tier"] >= 3 and CLASS_ORDER[decision] > CLASS_ORDER["MICRO"]:
            decision = "MICRO"

        if (not candidate["lineup_confirmed"]) and candidate["market"] in ["spreads", "totals"]:
            if request_data["strict"]:
                decision = "PASS"
            elif CLASS_ORDER[decision] > CLASS_ORDER["MICRO"]:
                decision = "MICRO"
            reasons.append("LINEUP_PENDING")

        decision = cap_class(decision, candidate["max_class"])

        if candidate["source_tier"] >= 3:
            reasons.append("SPORT_TIER_CAP")
        if candidate["book_count"] < 3:
            reasons.append("NO_CONSENSUS")

        if decision == "PASS":
            if not reasons:
                reasons.append("EV_TOO_LOW")
        else:
            reasons.append("EDGE_OK")
            reasons.append("DATA_QUALITY_" + data_quality)
            reasons.append("CLASS_" + decision)
            cap_value = risk_cap(request_data["mode"], decision)
            stake = calc_stake(request_data["bank"], decision, ev_calibrated, candidate["odds_best"], cap_value)

    if decision == "PASS" and not reasons:
        reasons.append("UNKNOWN_PASS")

    return {
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "match": candidate["match"],
        "sport": candidate["sport"],
        "market": candidate["market"],
        "selection": candidate["selection"],
        "point": candidate["point"],
        "best_odds": candidate["odds_best"],
        "avg_odds": candidate["odds_avg"],
        "bookmaker": candidate["bookmaker"],
        "book_count": candidate["book_count"],
        "model_prob": candidate["model_prob"],
        "implied_prob": implied,
        "ev_raw": ev_raw,
        "ev_calibrated": ev_calibrated,
        "ci_low": ci_low,
        "risk_cap": cap_value,
        "data_quality": data_quality,
        "decision": decision,
        "stake": stake,
        "reasons": reasons,
    }


def sort_results(results):
    dq_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    return sorted(
        results,
        key=lambda x: (
            CLASS_ORDER.get(x["decision"], 0),
            x["ev_calibrated"],
            x["ci_low"],
            x["book_count"],
            dq_rank.get(x["data_quality"], 0),
        ),
        reverse=True,
    )


def format_summary(summary):
    lines = []
    lines.append("Run: " + str(summary["run_id"]))
    lines.append("Status: " + str(summary["message"]))
    lines.append("Candidates: " + str(summary["candidates_count"]))
    lines.append("Accepted: " + str(summary["accepted_count"]))
    lines.append("Rejected: " + str(summary["rejected_count"]))
    lines.append("")

    for item in summary["results"][:5]:
        if item["decision"] == "PASS":
            lines.append("PASS | " + item["match"] + " | " + ", ".join(item["reasons"]))
        else:
            row = item["decision"] + " | " + item["match"] + " | " + item["sport"]
            row = row + " | stake " + str(item["stake"])
            row = row + " | EV " + str(item["ev_calibrated"])
            row = row + " | CI " + str(item["ci_low"])
            lines.append(row)

    return "\\n".join(lines)


def write_report(summary):
    file_path = REPORTS_DIR / (str(datetime.now().date()) + "_" + summary["run_id"] + "_report.txt")
    text = []
    text.append("DAILY REPORT | " + str(datetime.now().date()))
    text.append("Request: " + str(summary["request"]["raw_text"]))
    text.append("Mode: " + str(summary["request"]["mode"]))
    text.append("Candidates: " + str(summary["candidates_count"]))
    text.append("Accepted: " + str(summary["accepted_count"]))
    text.append("Rejected: " + str(summary["rejected_count"]))
    text.append("Status: " + str(summary["message"]))
    text.append("")

    for index, item in enumerate(summary["results"], start=1):
        text.append(str(index) + ") " + item["match"])
        text.append("Sport: " + item["sport"])
        text.append("Market: " + item["selection"])
        text.append("Best odds: " + str(item["best_odds"]) + " at " + item["bookmaker"])
        text.append("Avg odds: " + str(item["avg_odds"]) + " | Book count: " + str(item["book_count"]))
        text.append("ModelProb: " + str(item["model_prob"]) + " | Implied: " + str(item["implied_prob"]))
        text.append("EV raw: " + str(item["ev_raw"]) + " | EV calibrated: " + str(item["ev_calibrated"]) + " | CI low: " + str(item["ci_low"]))
        text.append("Decision: " + item["decision"] + " | Stake: " + str(item["stake"]) + " | Risk cap: " + str(item["risk_cap"]))
        text.append("Data quality: " + item["data_quality"])
        text.append("Reasons: " + ", ".join(item["reasons"]))
        text.append("")

    file_path.write_text("\n".join(text), encoding="utf-8")
    return str(file_path)


def write_audit(summary):
    file_path = LOGS_DIR / (str(datetime.now().date()) + "_" + summary["run_id"] + "_audit.json")
    file_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(file_path)


def run_auto_pipeline(request_text, dry_run=False):
    request_data = parse_request(request_text)
    request_data["dry_run"] = dry_run
    run_id = uuid.uuid4().hex[:10]

    candidates = fetch_candidates(request_data)
    results = []
    for candidate in candidates:
        results.append(finalize_candidate(candidate, request_data, run_id))

    results = sort_results(results)
    accepted = [x for x in results if x["decision"] != "PASS"]

    summary = {
        "run_id": run_id,
        "request": request_data,
        "generated_at": datetime.now(UTC).isoformat(),
        "candidates_count": len(results),
        "accepted_count": len(accepted),
        "rejected_count": len(results) - len(accepted),
        "status": "OK" if results else "NO_CANDIDATES",
        "message": "NO BETS / ALL PASS" if results and not accepted else "OK",
        "results": results,
    }

    if not dry_run:
        write_report(summary)
        write_audit(summary)
        LAST_RUN_PATH.write_text(format_summary(summary), encoding="utf-8")
        AUDIT_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    return summary


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "Бот запущен. Команды: /auto today wnba strict | /dryrun today wnba strict | /report | /audit"
    await update.message.reply_text(text)


async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = " ".join(context.args).strip()
    if args:
        request_text = "AUTO " + args
    else:
        request_text = "AUTO today basketball strict"

    try:
        summary = run_auto_pipeline(request_text, dry_run=False)
        await update.message.reply_text(format_summary(summary))
    except Exception as e:
        await update.message.reply_text("ERROR_REPORT: " + type(e).__name__ + ": " + str(e))


async def dryrun_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = " ".join(context.args).strip()
    if args:
        request_text = "AUTO " + args
    else:
        request_text = "AUTO today basketball strict"

    try:
        summary = run_auto_pipeline(request_text, dry_run=True)
        await update.message.reply_text("[DRYRUN]\n" + format_summary(summary))
    except Exception as e:
        await update.message.reply_text("ERROR_REPORT: " + type(e).__name__ + ": " + str(e))


async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if LAST_RUN_PATH.exists():
        await update.message.reply_text(LAST_RUN_PATH.read_text(encoding="utf-8"))
    else:
        await update.message.reply_text("Пока нет last_run.txt")


async def audit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if AUDIT_PATH.exists():
        raw = AUDIT_PATH.read_text(encoding="utf-8")
        if len(raw) > 3500:
            raw = raw[:3500] + "...truncated..."
        await update.message.reply_text(raw)
    else:
        await update.message.reply_text("Пока нет audit.json")


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

    print("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
