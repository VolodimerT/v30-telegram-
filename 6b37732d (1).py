"""brm.py — Bankroll management, pick history, stats, watchlist, reports."""
from __future__ import annotations
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc
BASE_DIR    = Path(__file__).resolve().parent
REPORTS_DIR = BASE_DIR / "reports"
LOGS_DIR    = BASE_DIR / "logs"
DATA_DIR    = BASE_DIR / "data"
for _d in (REPORTS_DIR, LOGS_DIR, DATA_DIR):
    _d.mkdir(exist_ok=True)

LAST_RUN_PATH    = BASE_DIR / "last_run.txt"
AUDIT_PATH       = BASE_DIR / "audit.json"
RUNS_PATH        = BASE_DIR / "runs.txt"
WATCHLIST_PATH   = BASE_DIR / "watchlist.json"
PICK_HISTORY_PATH = DATA_DIR / "pick_history.json"

from gates import CLASS_ORDER, MODE_RULES
from edge_engine import best_per_match, sort_results


# ── JSON helpers ─────────────────────────────────────────────────────────────
def load_json_list(path: Path) -> list:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_json_list(path: Path, items: list) -> None:
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def append_run_line(run_line: dict) -> None:
    try:
        with RUNS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(run_line, ensure_ascii=False) + "\n")
    except Exception:
        pass


def ensure_runs_seed_from_latest() -> None:
    if RUNS_PATH.exists() and RUNS_PATH.stat().st_size > 0:
        return
    if not AUDIT_PATH.exists():
        return
    try:
        data = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
        append_run_line({
            "run_id": data.get("run_id", "unknown"),
            "generated_at": data.get("generated_at", ""),
            "request": data.get("request", {}).get("raw_text", ""),
            "accepted_count": data.get("accepted_count", 0),
            "rejected_count": data.get("rejected_count", 0),
            "report_path": str(REPORTS_DIR),
            "audit_path": str(AUDIT_PATH),
        })
    except Exception:
        pass


# ── Watchlist ─────────────────────────────────────────────────────────────────
def load_watchlist() -> list:
    return load_json_list(WATCHLIST_PATH)

def save_watchlist(items: list) -> None:
    save_json_list(WATCHLIST_PATH, items)


# ── Pick history ──────────────────────────────────────────────────────────────
def load_pick_history() -> list:
    return load_json_list(PICK_HISTORY_PATH)

def save_pick_history(items: list) -> None:
    save_json_list(PICK_HISTORY_PATH, items)


def record_accepted_picks(summary: dict) -> int:
    history = load_pick_history()
    existing = {item.get("pick_id") for item in history}
    added = 0
    for item in summary["results"]:
        if item["decision"] == "PASS" or item["pick_id"] in existing:
            continue
        history.append({
            "pick_id": item["pick_id"], "run_id": item["run_id"],
            "generated_at": item["generated_at"], "event_id": item["event_id"],
            "match": item["match"], "sport": item["sport"],
            "market": item["market"], "selection": item["selection"],
            "best_odds": item["best_odds"], "stake": item["stake"],
            "decision": item["decision"], "profile": item["scorecard"]["profile"],
            "grade": item["scorecard"]["grade"], "ev_calibrated": item["ev_calibrated"],
            "ci_low": item["ci_low"], "status": "OPEN",
            "settled_result": "", "pnl": 0.0, "settled_at": "",
        })
        added += 1
    save_pick_history(history)
    return added


def compute_pnl(odds: float, stake: float, result: str) -> float:
    if result == "WIN":
        return round((odds - 1.0) * stake, 2)
    if result == "LOSS":
        return round(-stake, 2)
    return 0.0


def settle_pick(pick_id: str, result: str):
    result = result.upper()
    if result not in ("WIN", "LOSS", "PUSH"):
        return False, "Используй только WIN, LOSS или PUSH"
    history = load_pick_history()
    for item in history:
        if item.get("pick_id") == pick_id:
            item["status"] = "SETTLED"
            item["settled_result"] = result
            item["settled_at"] = datetime.now(UTC).isoformat()
            item["pnl"] = compute_pnl(float(item.get("best_odds", 0.0)),
                                       float(item.get("stake", 0.0)), result)
            save_pick_history(history)
            return True, item
    return False, "pick_id не найден"


# ── Stats ─────────────────────────────────────────────────────────────────────
def summarize_history(history: list) -> dict:
    settled = [x for x in history if x.get("status") == "SETTLED"]
    open_items = [x for x in history if x.get("status") == "OPEN"]
    wins   = sum(1 for x in settled if x.get("settled_result") == "WIN")
    losses = sum(1 for x in settled if x.get("settled_result") == "LOSS")
    pushes = sum(1 for x in settled if x.get("settled_result") == "PUSH")
    total_stake = round(sum(float(x.get("stake", 0.0)) for x in settled), 2)
    total_pnl   = round(sum(float(x.get("pnl", 0.0)) for x in settled), 2)
    roi = round((total_pnl / total_stake) * 100.0, 2) if total_stake > 0 else 0.0
    return {
        "total": len(history), "open": len(open_items), "settled": len(settled),
        "wins": wins, "losses": losses, "pushes": pushes,
        "total_stake": total_stake, "total_pnl": total_pnl, "roi": roi,
    }


def group_stats(history: list, key: str) -> list:
    settled = [x for x in history if x.get("status") == "SETTLED"]
    groups: dict = {}
    for item in settled:
        g = item.get(key, "unknown") or "unknown"
        groups.setdefault(g, {"count": 0, "stake": 0.0, "pnl": 0.0,
                               "wins": 0, "losses": 0, "pushes": 0})
        d = groups[g]
        d["count"] += 1
        d["stake"] += float(item.get("stake", 0.0))
        d["pnl"]   += float(item.get("pnl", 0.0))
        res = item.get("settled_result")
        if res == "WIN":    d["wins"]   += 1
        elif res == "LOSS": d["losses"] += 1
        elif res == "PUSH": d["pushes"] += 1
    rows = []
    for g, d in groups.items():
        s = round(d["stake"], 2)
        p = round(d["pnl"], 2)
        r = round((p / s) * 100.0, 2) if s > 0 else 0.0
        rows.append((g, d["count"], d["wins"], d["losses"], d["pushes"], s, p, r))
    return sorted(rows, key=lambda x: (x[6], x[1]), reverse=True)


# ── Reports & format helpers ──────────────────────────────────────────────────
def _today_utc_iso() -> str:
    return datetime.now(UTC).date().isoformat()

def _items_for_day(items: list, day_iso: str | None = None) -> list:
    day_iso = day_iso or _today_utc_iso()
    return [i for i in items if str(i.get("generated_at", ""))[:10] == day_iso]

def read_runs() -> list:
    ensure_runs_seed_from_latest()
    if not RUNS_PATH.exists():
        return []
    out = []
    for line in RUNS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out

def load_latest_summary() -> dict | None:
    if not AUDIT_PATH.exists():
        return None
    try:
        return json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None

def format_history(limit=10) -> str:
    runs = read_runs()
    if not runs:
        return "Пока нет history. Сначала запусти /auto"
    return "\n".join(
        f"{r.get('generated_at','')} | accepted {r.get('accepted_count',0)}"
        f" | rejected {r.get('rejected_count',0)} | {r.get('request','')}"
        for r in runs[-limit:][::-1]
    )

def format_global_summary() -> str:
    runs = read_runs()
    if not runs:
        return "Пока нет summary. Сначала запусти /auto"
    wl = load_watchlist()
    hist = summarize_history(load_pick_history())
    return "\n".join([
        f"Runs: {len(runs)}",
        f"Accepted total: {sum(r.get('accepted_count',0) for r in runs)}",
        f"Rejected total: {sum(r.get('rejected_count',0) for r in runs)}",
        f"Watchlist teams: {len(wl)}",
        f"Settled picks: {hist['settled']}",
        f"PnL: {hist['total_pnl']}",
        f"ROI%: {hist['roi']}",
    ])

def format_latest() -> str:
    runs = read_runs()
    if not runs:
        return "Пока нет latest. Сначала запусти /auto"
    r = runs[-1]
    return "\n".join([
        f"Run: {r.get('run_id','')}", f"Generated: {r.get('generated_at','')}",
        f"Request: {r.get('request','')}", f"Accepted: {r.get('accepted_count',0)}",
        f"Rejected: {r.get('rejected_count',0)}", f"Report: {r.get('report_path','')}",
    ])

def format_watchlist() -> str:
    wl = load_watchlist()
    if not wl:
        return "Watchlist пуст. Добавь командой /watch chelsea"
    return "\n".join(["WATCHLIST"] + [
        f"{i+1}) {item.get('team','')} | sport {item.get('sport','')}"
        for i, item in enumerate(wl)
    ])

def format_open_picks(limit=20) -> str:
    history = load_pick_history()
    open_items = [x for x in history if x.get("status") == "OPEN"]
    if not open_items:
        return "OPEN PICKS\n- none"
    lines = ["OPEN PICKS"]
    for item in open_items[-limit:][::-1]:
        lines.append(
            f"- {item['pick_id']} | {item['match']} | {item['selection']}"
            f" | {item['decision']} | odds {item['best_odds']} | stake {item['stake']}"
        )
    return "\n".join(lines)

def format_stats_report_compact() -> str:
    history = load_pick_history()
    meta = summarize_history(history)
    lines = [
        "STATS",
        f"Total picks: {meta['total']}",
        f"Open: {meta['open']} | Settled: {meta['settled']}",
        f"W/L/P: {meta['wins']}/{meta['losses']}/{meta['pushes']}",
        f"Stake: {meta['total_stake']} | PnL: {meta['total_pnl']} | ROI%: {meta['roi']}", "",
    ]
    for title, key in [("By market","market"),("By profile","profile"),
                        ("By decision","decision"),("By sport","sport")]:
        rows = group_stats(history, key)
        lines.append(title.upper())
        if not rows:
            lines.append("- none")
        else:
            for row in rows[:5]:
                lines.append(
                    f"- {row[0]} | n {row[1]} | W/L/P {row[2]}/{row[3]}/{row[4]}"
                    f" | pnl {row[6]} | roi {row[7]}%"
                )
        lines.append("")
    return "\n".join(lines).strip()

def format_top_picks(summary: dict) -> str:
    accepted = [x for x in best_per_match(summary["results"]) if x["decision"] != "PASS"]
    if not accepted:
        return "TOP PICKS\n- none"
    lines = ["TOP PICKS"]
    for item in accepted[:5]:
        lines.append(
            f"- {item['match']} | {item['decision']} | {item['selection']}"
            f" | odds {item['best_odds']} | stake {item['stake']}"
            f" | grade {item['scorecard']['grade']} | {item['scorecard']['profile']}"
        )
    return "\n".join(lines)

def format_match_report(summary: dict) -> str:
    picks = best_per_match(summary["results"])
    if not picks:
        return "NO MATCHES FOUND"
    lines = ["MATCH REPORT"]
    for best in picks[:10]:
        if best["decision"] == "PASS":
            lines.append(f"- {best['match']} -> PASS ({', '.join(best['reasons'][:2])})")
        else:
            lines.append(
                f"- {best['match']} -> {best['decision']} | {best['selection']}"
                f" | odds {best['best_odds']} | stake {best['stake']}"
                f" | grade {best['scorecard']['grade']} | {best['scorecard']['profile']}"
            )
    return "\n".join(lines)

def format_summary(summary: dict) -> str:
    req = summary["request"]
    lines = [
        f"Run: {summary['run_id']}", f"Mode: {req['mode']}",
        f"Sports: {', '.join(req['sports'])}",
        f"Teams: {', '.join(req['team_filters']) if req['team_filters'] else 'all'}",
        f"Status: {summary['message']}",
        f"Candidates: {summary['candidates_count']}",
        f"Accepted: {summary['accepted_count']}",
        f"Rejected: {summary['rejected_count']}", "",
    ]
    for item in summary["results"][:10]:
        if item["decision"] == "PASS":
            lines.append(f"PASS | {item['match']} | {item['selection']} | {', '.join(item['reasons'][:2])}")
        else:
            lines.append(
                f"{item['decision']} | {item['match']} | {item['selection']}"
                f" | odds {item['best_odds']} | stake {item['stake']}"
                f" | EV {item['ev_calibrated']} | CI {item['ci_low']}"
                f" | grade {item['scorecard']['grade']} | {item['scorecard']['profile']}"
            )
    return "\n".join(lines)

def format_quick() -> str:
    summary = load_latest_summary()
    if not summary:
        return "QUICK\n- no runs yet"
    picks = best_per_match(summary.get("results", []))
    accepted = [x for x in picks if x.get("decision") != "PASS"]
    req = summary.get("request", {})
    lines = [
        "QUICK",
        f"Run: {summary.get('run_id','')} | mode {req.get('mode','')}",
        f"Sports: {', '.join(req.get('sports',[]))} | candidates {summary.get('candidates_count',0)}"
        f" | accepted {summary.get('accepted_count',0)}", "",
    ]
    if accepted:
        for idx, item in enumerate(accepted[:5], 1):
            lines.append(
                f"{idx}) {item['match']} | {item['decision']} | {item['selection']}"
                f" | {item['best_odds']} | stake {item['stake']}"
            )
    else:
        lines.append("- no picks (all PASS)")
    return "\n".join(lines)

def format_day_summary(day_iso: str | None = None) -> str:
    day_iso = day_iso or _today_utc_iso()
    history = load_pick_history()
    today = _items_for_day(history, day_iso)
    if not today:
        return f"DAY SUMMARY {day_iso}\n- no picks today"
    settled = [x for x in today if x.get("status") == "SETTLED"]
    open_i  = [x for x in today if x.get("status") == "OPEN"]
    wins   = sum(1 for x in settled if x.get("settled_result") == "WIN")
    losses = sum(1 for x in settled if x.get("settled_result") == "LOSS")
    pushes = sum(1 for x in settled if x.get("settled_result") == "PUSH")
    stake  = round(sum(float(x.get("stake", 0.0)) for x in settled), 2)
    pnl    = round(sum(float(x.get("pnl", 0.0)) for x in settled), 2)
    roi    = round((pnl / stake) * 100.0, 2) if stake > 0 else 0.0
    lines = [
        f"DAY SUMMARY {day_iso}",
        f"Picks: total {len(today)} | settled {len(settled)} | open {len(open_i)}",
        f"W/L/P: {wins}/{losses}/{pushes} | stake {stake} | PnL {pnl} | ROI {roi}%", "",
        "BY SPORT",
    ]
    sg: dict = {}
    for item in settled:
        sp = item.get("sport", "unknown") or "unknown"
        sg.setdefault(sp, {"n": 0, "stake": 0.0, "pnl": 0.0})
        sg[sp]["n"] += 1
        sg[sp]["stake"] += float(item.get("stake", 0.0))
        sg[sp]["pnl"]   += float(item.get("pnl", 0.0))
    if not sg:
        lines.append("- no settled picks yet")
    else:
        for sp, d in sorted(sg.items()):
            s = round(d["stake"], 2); p = round(d["pnl"], 2)
            r = round((p / s) * 100.0, 2) if s > 0 else 0.0
            lines.append(f"- {sp} | n {d['n']} | pnl {p} | roi {r}%")
    return "\n".join(lines)

def write_report(summary: dict) -> str:
    fp = REPORTS_DIR / f"{datetime.now().date()}_{summary['run_id']}_report.txt"
    rows = [
        f"DAILY REPORT | {datetime.now().date()}",
        f"Request: {summary['request']['raw_text']}",
        f"Mode: {summary['request']['mode']}",
        f"Sports: {', '.join(summary['request']['sports'])}",
        f"Team filters: {', '.join(summary['request']['team_filters']) or 'all'}",
        f"Candidates: {summary['candidates_count']}",
        f"Accepted: {summary['accepted_count']}",
        f"Rejected: {summary['rejected_count']}",
        f"Status: {summary['message']}", "",
        format_top_picks(summary), "",
        format_match_report(summary), "",
    ]
    for idx, item in enumerate(summary["results"], 1):
        rows += [
            f"{idx}) {item['match']}",
            f"Sport: {item['sport']} | Market: {item['market']}",
            f"Selection: {item['selection']} | Odds: {item['best_odds']} | Avg: {item['avg_odds']}",
            f"Bookmaker: {item['bookmaker']} | Books: {item['book_count']}",
            f"ModelProb: {item['model_prob']} | Implied: {item['implied_prob']}",
            f"EV raw: {item['ev_raw']} | EV cal: {item['ev_calibrated']} | CI low: {item['ci_low']}",
            f"Decision: {item['decision']} | Stake: {item['stake']} | Risk cap: {item['risk_cap']}",
            f"Grade: {item['scorecard']['grade']} | Score: {item['scorecard']['score']} | Profile: {item['scorecard']['profile']}",
            f"Reasons: {', '.join(item['reasons'])}", "",
        ]
    fp.write_text("\n".join(rows), encoding="utf-8")
    return str(fp)

def write_audit(summary: dict) -> str:
    fp = LOGS_DIR / f"{datetime.now().date()}_{summary['run_id']}_audit.json"
    fp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(fp)
