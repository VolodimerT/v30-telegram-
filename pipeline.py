"""pipeline.py — Auto + Live pipelines, request parsing, watchlist scan."""
from __future__ import annotations
import uuid
import json
from datetime import datetime, timezone
from pathlib import Path

from gates import MODE_RULES, CLASS_ORDER
from edge_engine import fetch_candidates, finalize_candidate, sort_results, SPORT_MAP
from brm import (
    BASE_DIR, LAST_RUN_PATH, AUDIT_PATH,
    load_watchlist, write_report, write_audit, record_accepted_picks,
    append_run_line, format_summary, format_top_picks, format_match_report,
)

UTC = timezone.utc

TEAM_ALIASES = {
    "chelsea":         ["chelsea"],
    "mancity":         ["man city", "manchester city", "mancity"],
    "manchestercity":  ["man city", "manchester city", "manchestercity"],
    "city":            ["man city", "manchester city"],
    "manutd":          ["man utd", "manchester united", "manutd", "man united"],
    "manchesterunited":["man utd", "manchester united", "man united"],
    "united":          ["man utd", "manchester united", "man united"],
    "tottenham":       ["tottenham", "spurs"],
    "arsenal":         ["arsenal"],
    "liverpool":       ["liverpool"],
    "bournemouth":     ["bournemouth"],
    "astonvilla":      ["aston villa", "astonvilla"],
    "villa":           ["aston villa", "villa"],
    "sunderland":      ["sunderland"],
    "juventus":        ["juventus", "juve"],
    "inter":           ["inter", "internazionale"],
    "milan":           ["milan", "ac milan"],
    "barcelona":       ["barcelona", "barca"],
    "realmadrid":      ["real madrid"],
    "atletico":        ["atletico", "atleti"],
}

DEFAULT_TEAM_SPORT = {
    "chelsea": "epl", "mancity": "epl", "manutd": "epl",
    "arsenal": "epl", "liverpool": "epl", "tottenham": "epl",
    "bournemouth": "epl", "astonvilla": "epl", "sunderland": "epl",
    "juventus": "seriea", "inter": "seriea", "milan": "seriea",
    "barcelona": "laliga", "realmadrid": "laliga", "atletico": "laliga",
}


def normalize_team_token(token: str) -> str:
    low = token.strip().lower()
    return low if low in TEAM_ALIASES else low.replace(" ", "")


def detect_mode(bank: float) -> str:
    if bank < 500:   return "FROZEN"
    if bank < 1000:  return "EMERGENCY"
    if bank < 3000:  return "NORMAL"
    return "GROWTH"


def parse_request(text: str) -> dict:
    raw = text.strip().lower()
    sports = [k for k in SPORT_MAP if k in raw] or ["football"]
    strict = "strict" in raw
    bank, max_candidates = 1000.0, 30
    team_filters = []
    parts = raw.replace("=", " ").replace(",", " ").split()
    for i, tok in enumerate(parts):
        if tok == "bank" and i + 1 < len(parts):
            try: bank = float(parts[i+1])
            except Exception: pass
        if tok in ("max", "max_candidates") and i + 1 < len(parts):
            try: max_candidates = int(parts[i+1])
            except Exception: pass
    mode = detect_mode(bank)
    for i, tok in enumerate(parts):
        if tok == "mode" and i + 1 < len(parts):
            custom = parts[i+1].upper()
            if custom in MODE_RULES:
                mode = custom
    skip = {"auto","today","strict","bank","mode","max","max_candidates"}
    for tok in parts:
        if tok in TEAM_ALIASES:
            team_filters.extend(TEAM_ALIASES[tok])
        elif tok not in skip and tok not in SPORT_MAP and len(tok) >= 4:
            team_filters.append(tok)
    return {
        "raw_text": text, "sports": list(dict.fromkeys(sports)),
        "strict": strict, "bank": bank, "mode": mode,
        "markets": ["h2h","spreads","totals","btts","team_totals"],
        "max_candidates": max_candidates,
        "team_filters": list(dict.fromkeys(team_filters)),
    }


def build_scanwatch_request() -> str | None:
    watchlist = load_watchlist()
    if not watchlist:
        return None
    sports, parts = [], ["AUTO", "today"]
    for item in watchlist:
        sp = item.get("sport", "").strip().lower()
        if sp in SPORT_MAP and sp not in sports:
            sports.append(sp)
    parts.extend(sports or ["epl"])
    for item in watchlist:
        team = item.get("team", "").strip().lower()
        if team:
            parts.append(team)
    parts.append("strict")
    return " ".join(parts)


def _persist_summary(summary: dict, request_text: str) -> None:
    report_path = write_report(summary)
    audit_path  = write_audit(summary)
    record_accepted_picks(summary)
    LAST_RUN_PATH.write_text(
        format_summary(summary) + "\n\n" + format_top_picks(summary) + "\n\n" + format_match_report(summary),
        encoding="utf-8",
    )
    AUDIT_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    append_run_line({
        "run_id": summary["run_id"], "generated_at": summary["generated_at"],
        "request": request_text, "accepted_count": summary["accepted_count"],
        "rejected_count": summary["rejected_count"],
        "report_path": report_path, "audit_path": audit_path,
    })


def run_auto_pipeline(request_text: str, dry_run: bool = False) -> dict:
    request_data = parse_request(request_text)
    request_data["dry_run"] = dry_run
    run_id = uuid.uuid4().hex[:10]
    candidates = fetch_candidates(request_data, live=False)
    results = sort_results([finalize_candidate(c, request_data, run_id) for c in candidates])
    accepted = [x for x in results if x["decision"] != "PASS"]
    summary = {
        "run_id": run_id, "request": request_data,
        "generated_at": datetime.now(UTC).isoformat(),
        "candidates_count": len(results), "accepted_count": len(accepted),
        "rejected_count": len(results) - len(accepted),
        "status": "OK" if results else "NO_CANDIDATES",
        "message": ("NO BETS / ALL PASS" if results and not accepted
                    else ("NO MATCHES FOUND" if not results else "OK")),
        "results": results, "live": False,
    }
    if not dry_run:
        _persist_summary(summary, request_text)
    return summary


def run_live_pipeline(request_text: str) -> dict:
    """Phase 5 — live in-play scan with tighter gates."""
    request_data = parse_request(request_text)
    request_data["dry_run"] = False
    run_id = uuid.uuid4().hex[:10]
    candidates = fetch_candidates(request_data, live=True)
    results = sort_results([finalize_candidate(c, request_data, run_id) for c in candidates])
    accepted = [x for x in results if x["decision"] != "PASS"]
    summary = {
        "run_id": run_id, "request": request_data,
        "generated_at": datetime.now(UTC).isoformat(),
        "candidates_count": len(results), "accepted_count": len(accepted),
        "rejected_count": len(results) - len(accepted),
        "status": "OK" if results else "NO_CANDIDATES",
        "message": ("NO LIVE BETS / ALL PASS" if results and not accepted
                    else ("NO LIVE MATCHES FOUND" if not results else "OK")),
        "results": results, "live": True,
    }
    _persist_summary(summary, "[LIVE] " + request_text)
    return summary
