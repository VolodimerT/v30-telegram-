"""main.py — Telegram bot entry point. Phases 1-6."""
import os
from datetime import datetime, timezone
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from brm import (
    LAST_RUN_PATH, AUDIT_PATH,
    load_watchlist, save_watchlist, load_pick_history,
    settle_pick, format_open_picks, format_stats_report_compact,
    format_quick, format_day_summary, format_watchlist,
    format_latest, format_history, format_global_summary,
)
from pipeline import (
    run_auto_pipeline, run_live_pipeline, build_scanwatch_request,
    normalize_team_token, TEAM_ALIASES, DEFAULT_TEAM_SPORT, parse_request,
)
from brm import format_summary, format_top_picks, format_match_report
from sport_models import get_sport_model_prob

UTC = timezone.utc


# ── Helpers ──────────────────────────────────────────────────────────────────
def split_text(text: str, limit: int = 3500) -> list:
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


async def reply_long(message, text: str) -> None:
    for chunk in split_text(text):
        await message.reply_text(chunk)


# ── Command handlers ──────────────────────────────────────────────────────────
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_long(update.message, (
        "Я готов. Phases 1-6 активны.\n\n"
        "Основные команды:\n"
        "- /auto today epl seriea strict\n"
        "- /live epl          ← Phase 5 in-play\n"
        "- /scanwatch\n"
        "- /openpicks\n"
        "- /settle PICK_ID WIN\n"
        "- /stats\n"
        "- /quick\n"
        "- /day\n"
        "- /model football home_xg=1.6 away_xg=0.9 selection=home\n"
        "- /model basketball home_ortg=114 home_drtg=108 away_ortg=110 away_drtg=112 selection=home\n"
        "- /model tennis surface=clay p1_surface_winrate=0.62 p1_hold_rate=0.72 selection=p1\n"
        "- /model hockey home_goalie_sv=0.920 away_b2b=true selection=over total_line=5.5\n"
        "- /template_morning\n"
        "- /template_evening"
    ))


async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = " ".join(context.args).strip()
    try:
        summary = run_auto_pipeline("AUTO " + args if args else "AUTO today football strict")
        await reply_long(update.message,
            format_summary(summary) + "\n\n" + format_top_picks(summary) + "\n\n" + format_match_report(summary))
    except Exception as e:
        await reply_long(update.message, f"ERROR_REPORT: {type(e).__name__}: {e}")


async def live_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Phase 5 — in-play scan."""
    args = " ".join(context.args).strip()
    try:
        summary = run_live_pipeline("LIVE " + args if args else "LIVE epl")
        header = "[LIVE] " + ("✅ OK" if summary["accepted_count"] > 0 else "🔕 ALL PASS")
        await reply_long(update.message,
            header + "\n\n" + format_summary(summary) + "\n\n"
            + format_top_picks(summary) + "\n\n" + format_match_report(summary))
    except Exception as e:
        await reply_long(update.message, f"ERROR_REPORT (LIVE): {type(e).__name__}: {e}")


async def dryrun_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = " ".join(context.args).strip()
    try:
        summary = run_auto_pipeline("AUTO " + args if args else "AUTO today football strict", dry_run=True)
        await reply_long(update.message,
            "[DRYRUN]\n" + format_summary(summary) + "\n\n"
            + format_top_picks(summary) + "\n\n" + format_match_report(summary))
    except Exception as e:
        await reply_long(update.message, f"ERROR_REPORT: {type(e).__name__}: {e}")


async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_long(update.message,
        LAST_RUN_PATH.read_text(encoding="utf-8") if LAST_RUN_PATH.exists() else "Пока нет last_run.txt")


async def audit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_long(update.message,
        AUDIT_PATH.read_text(encoding="utf-8") if AUDIT_PATH.exists() else "Пока нет audit.json")


async def latest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_long(update.message, format_latest())


async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_long(update.message, format_history())


async def summary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_long(update.message, format_global_summary())


async def watch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = " ".join(context.args).strip().lower()
    if not args:
        await reply_long(update.message, "Используй так: /watch chelsea")
        return
    key = normalize_team_token(args)
    sport = DEFAULT_TEAM_SPORT.get(key, "epl")
    watchlist = load_watchlist()
    if any(item.get("team") == key for item in watchlist):
        await reply_long(update.message, "Уже в watchlist: " + key)
        return
    watchlist.append({"team": key, "sport": sport})
    save_watchlist(watchlist)
    await reply_long(update.message, f"Добавил в watchlist: {key} | sport {sport}")


async def unwatch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = " ".join(context.args).strip().lower()
    if not args:
        await reply_long(update.message, "Используй так: /unwatch chelsea")
        return
    key = normalize_team_token(args)
    save_watchlist([i for i in load_watchlist() if i.get("team") != key])
    await reply_long(update.message, "Удалил из watchlist: " + key)


async def watchlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_long(update.message, format_watchlist())


async def scanwatch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    request_text = build_scanwatch_request()
    if not request_text:
        await reply_long(update.message, "Watchlist пуст. Добавь командой /watch")
        return
    try:
        summary = run_auto_pipeline(request_text)
        await reply_long(update.message,
            format_summary(summary) + "\n\n" + format_top_picks(summary) + "\n\n" + format_match_report(summary))
    except Exception as e:
        await reply_long(update.message, f"ERROR_REPORT: {type(e).__name__}: {e}")


async def openpicks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_long(update.message, format_open_picks())


async def settle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await reply_long(update.message, "Используй так: /settle PICK_ID WIN")
        return
    ok, result = settle_pick(context.args[0].strip(), context.args[1].strip())
    if not ok:
        await reply_long(update.message, str(result))
        return
    item = result
    await reply_long(update.message,
        f"SETTLED | {item['pick_id']} | {item['match']} | {item['selection']}"
        f" | {item['settled_result']} | pnl {item['pnl']}")


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_long(update.message, format_stats_report_compact())


async def quick_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_long(update.message, format_quick())


async def day_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_long(update.message, format_day_summary())


async def model_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Phase 4 — manual sport model probe.
    Usage: /model football home_xg=1.6 away_xg=0.9 selection=home
    """
    if not context.args:
        await reply_long(update.message,
            "Использование:\n"
            "/model football home_xg=1.6 away_xg=0.9 selection=home\n"
            "/model basketball home_ortg=114 home_drtg=108 away_ortg=110 away_drtg=112 selection=home\n"
            "/model tennis p1_surface_winrate=0.62 p1_hold_rate=0.72 selection=p1\n"
            "/model hockey home_goalie_sv=0.920 away_b2b=true selection=over total_line=5.5"
        )
        return
    parts = context.args
    sport = parts[0].lower()
    meta: dict = {}
    selection = "home"
    for part in parts[1:]:
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip().lower()
        if k == "selection":
            selection = v.strip().lower()
        else:
            try:
                meta[k] = float(v)
            except Exception:
                meta[k] = True if v.lower() in ("true","1","yes") else v.strip()
    ctx = {"sport": sport, "market": "h2h", "selection": selection,
            "best_odds": 2.0, "sport_meta": meta}
    prob = get_sport_model_prob(ctx)
    if prob == 0.0:
        await reply_long(update.message,
            f"MODEL {sport.upper()} → не хватает мета-данных.\nПроверь параметры: {meta}")
    else:
        await reply_long(update.message,
            f"MODEL {sport.upper()} | selection={selection}\n"
            f"Вероятность: {prob:.2f}%  ({prob/100:.4f})")


async def template_morning_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_long(update.message,
        "TEMPLATE MORNING\nСкопируй и отправь:\n\n/auto today epl seriea strict")


async def template_evening_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_long(update.message,
        "TEMPLATE EVENING\nСкопируй и отправь:\n\n/scanwatch")


# ── Bot registration ──────────────────────────────────────────────────────────
def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN or BOT_TOKEN is not set")
    app = Application.builder().token(token).build()
    handlers = [
        ("start",            start_cmd),
        ("auto",             auto_cmd),
        ("live",             live_cmd),       # Phase 5
        ("dryrun",           dryrun_cmd),
        ("report",           report_cmd),
        ("audit",            audit_cmd),
        ("latest",           latest_cmd),
        ("history",          history_cmd),
        ("summary",          summary_cmd),
        ("watch",            watch_cmd),
        ("unwatch",          unwatch_cmd),
        ("watchlist",        watchlist_cmd),
        ("scanwatch",        scanwatch_cmd),
        ("openpicks",        openpicks_cmd),
        ("settle",           settle_cmd),
        ("stats",            stats_cmd),
        ("quick",            quick_cmd),
        ("day",              day_cmd),
        ("model",            model_cmd),      # Phase 4
        ("template_morning", template_morning_cmd),
        ("template_evening", template_evening_cmd),
    ]
    for name, handler in handlers:
        app.add_handler(CommandHandler(name, handler))
    print("Bot started — Phases 1-6 active")
    app.run_polling()


if __name__ == "__main__":
    main()
