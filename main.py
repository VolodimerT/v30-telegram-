import os
import re
import json
import math
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any

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
        "FROZEN": {
            "min_ev_calibrated": 6.0,
            "max_micro_stake": 10,
            "max_support_stake": 20,
            "max_core_stake": 30,
        },
        "EMERGENCY": {
            "min_ev_calibrated": 8.0,
            "max_micro_stake": 10,
            "max_support_stake": 15,
            "max_core_stake": 20,
        },
        "NORMAL": {
            "min_ev_calibrated": 4.0,
            "max_micro_stake": 20,
            "max_support_stake": 35,
            "max_core_stake": 50,
        },
        "GROWTH": {
            "min_ev_calibrated": 3.0,
            "max_micro_stake": 25,
            "max_support_stake": 40,
            "max_core_stake": 60,
        },
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

    markets = ["h2h", "spreads", "totals"]

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
        markets=markets,
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


def fetch_candidates_stub(request: AutoRequest) -> List[Candidate]:
    now = datetime.now(UTC)
    sport = request.sports[0]

    return [
        Candidate(
            match="Liberty vs Sun",
            sport=sport,
            market="spreads",
            selection="Liberty -4.5",
            point=-4.5,
            commence_time=now + timedelta(hours=5),
            odds_best=1.91,
            odds_avg=1.84,
            book_count=6,
            model_prob=56.2,
            lineup_confirmed=True,
            injury_fresh_hours=1.0,
            odds_age_minutes=20,
            source_tier=2,
            variance="MEDIUM",
        ),
        Candidate(
            match="Aces vs Storm",
            sport=sport,
            market="totals",
            selection="Over 167.5",
            point=167.5,
            commence_time=now + timedelta(hours=2),
            odds_best=1.87,
            odds_avg=1.83,
            book_count=4,
            model_prob=54.0,
            lineup_confirmed=False,
            injury_fresh_hours=2.0,
            odds_age_minutes=35,
            source_tier=2,
            variance="MEDIUM",
        ),
        Candidate(
            match="Wings vs Fever",
            sport=sport,
            market="h2h",
            selection="Fever ML",
            point=None,
            commence_time=now + timedelta(minutes=8),
            odds_best=2.05,
            odds_avg=1.98,
            book_count=5,
            model_prob=51.5,
            lineup_confirmed=True,
            injury_fresh_hours=1.0,
            odds_age_minutes=15,
            source_tier=2,
            variance="HIGH",
        ),
    ]


def write_txt_report(summary: Dict[str, Any]) -> str:
    path = REPORTS_DIR / f"{datetime.now().date()}_{summary['run_id']}_report.txt"

    req = summary["request"]
    lines = []
    lines.append("DAILY REPORT | " + str(datetime.now().date()))
    lines.append("Request: " + str(req["raw_text"]))
    lines.append("Mode: " + str(req["mode"]))
    lines.append("Candidates: " + str(summary["candidates_count"]))
    lines.append("Accepted: " + str(summary["accepted_count"]))
    lines.append("Rejected: " + str(summary["rejected_count"]))
    lines.append("Status: " + str(summary["message"]))
    lines.append("")

    for i, r in enumerate(summary["results"], start=1):
        lines.append(str(i) + ") " + str(r["match"]))
        lines.append("Market: " + str(r["selection"]))
        lines.append("Best odds: " + str(r["best_odds"]) + " at " + str(r["bookmaker"]))
        lines.append("Avg odds: " + str(r["avg_odds"]) + " | Book count: " + str(r["book_count"]))
        lines.append("ModelProb: " + str(r["model_prob"]) + " | Implied: " + str(r["implied_prob"]))
        lines.append("EV raw: " + str(r["ev_raw"]) + " | EV calibrated: " + str(r["ev_calibrated"]) + " | CI low: " + str(r["ci_low"]))
        lines.append("Decision: " + str(r["decision"]) + " | Stake: " + str(r["stake"]) + " | Risk cap: " + str(r["risk_cap"]))
        lines.append("Data quality: " + str(r["data_quality"]))
        lines.append("Reasons: " + ", ".join(r["reasons"]))
        lines.append("")

    with path.open("w", encoding="utf-8") as f:
        for item in lines:
            print(item, file=f)

    return str(path)


def write_audit_json(summary: Dict[str, Any]) -> str:
    path = LOGS_DIR / f"{datetime.now().date()}_{summary['run_id']}_audit.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return str(path)


def format_summary_for_telegram(summary: Dict[str, Any]) -> str:
    lines = []
    lines.append("Run: " + str(summary["run_id"]))
    lines.append("Status: " + str(summary["message"]))
    lines.append("Candidates: " + str(summary["candidates_count"]))
    lines.append("Accepted: " + str(summary["accepted_count"]))
    lines.append("Rejected: " + str(summary["rejected_count"]))
    lines.append("")

    for r in summary["results"][:5]:
        if r["decision"] == "PASS":
            lines.append("PASS | " + str(r["match"]) + " | " + ", ".join(r["reasons"]))
        else:
            lines.append(
                str(r["decision"]) + " | " + str(r["match"]) + " | stake " + str(r["stake"]) + " | EV " + str(r["ev_calibrated"]) + " | CI " + str(r["ci_low"])
            )

    return os.linesep.join(lines)


def format_last_report_text() -> str:
    if LAST_RUN_PATH.exists():
        return LAST_RUN_PATH.read_text(encoding="utf-8")
    return "Пока нет last_run.txt"


def run_auto_pipeline(request_text: str, dry_run: bool = False) -> Dict[str, Any]:
    request = parse_auto_request(request_text, dry_run=dry_run)
    run_id = uuid.uuid4().hex[:10]

    candidates = fetch_candidates_stub(request)[:request.max_candidates]
    results = [finalize_decision(c, request, run_id) for c in candidates]
    results = sort_results(results)
    actionable = [r for r in results if r.decision != "PASS"]

    summary = {
        "run_id": run_id,
        "request": asdict(request),
        "generated_at": datetime.now(UTC).isoformat(),
        "candidates_count": len(results),
        "accepted_count": len(actionable),
        "rejected_count": len(results) - len(actionable),
        "status": "OK" if results else "NO_CANDIDATES",
        "message": "NO BETS / ALL PASS" if results and not actionable else "OK",
        "results": [asdict(r) for r in results],
    }

    if not dry_run:
        txt_path = write_txt_report(summary)
        json_path = write_audit_json(summary)
        LAST_RUN_PATH.write_text(format_summary_for_telegram(summary), encoding="utf-8")

        run_line = json.dumps({
            "run_id": run_id,
            "generated_at": summary["generated_at"],
            "request": request_text,
            "accepted_count": summary["accepted_count"],
            "rejected_count": summary["rejected_count"],
            "txt_report": txt_path,
            "audit_json": json_path,
        }, ensure_ascii=False)

        with RUNS_PATH.open("a", encoding="utf-8") as f:
            print(run_line, file=f)

        with AUDIT_PATH.open("w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

    return summary


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "Бот запущен.
Команды:
/auto today basketball strict
/dryrun today basketball strict
/report
/audit"
    await update.message.reply_text(text)


async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = " ".join(context.args).strip()
    request_text = "AUTO " + args if args else "AUTO today basketball strict"

    try:
        summary = run_auto_pipeline(request_text, dry_run=False)
        await update.message.reply_text(format_summary_for_telegram(summary))
    except Exception as e:
        await update.message.reply_text("ERROR_REPORT
" + type(e).__name__ + ": " + str(e))


async def dryrun_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = " ".join(context.args).strip()
    request_text = "AUTO " + args if args else "AUTO today basketball strict"

    try:
        summary = run_auto_pipeline(request_text, dry_run=True)
        text = "[DRYRUN]" + os.linesep + format_summary_for_telegram(summary)
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text("ERROR_REPORT
" + type(e).__name__ + ": " + str(e))


async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(format_last_report_text())


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
