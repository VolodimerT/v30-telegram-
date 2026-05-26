"""main_autonomous.py — Updated Telegram bot with Phase 7 autonomy."""
import os
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from brm import (
    LAST_RUN_PATH, AUDIT_PATH, load_pick_history,
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

# NEW: Import autonomous modules
try:
    from scheduler import AutonomousScheduler, AdaptiveThresholds
    from form_tracking import TeamFormTracker, SettlementAutomation
    AUTONOMY_AVAILABLE = True
except ImportError:
    AUTONOMY_AVAILABLE = False
    print("⚠️ Autonomy modules not available. Install with: pip install apscheduler")

UTC = timezone.utc


# ── Global state for scheduler ────────────────────────────────────────────
scheduler_instance = None
telegram_app = None
user_chat_id = None  # Will store user's chat ID for notifications


# ── Helpers ──────────────────────────────────────────────────────────────
def split_text(text: str, limit: int = 3500) -> list:
    """Split long text into Telegram-friendly chunks."""
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
    """Send long message in chunks."""
    for chunk in split_text(text):
        await message.reply_text(chunk)


async def send_notification(text: str) -> None:
    """Send autonomous notification to user."""
    global user_chat_id, telegram_app
    if not user_chat_id or not telegram_app:
        return
    try:
        for chunk in split_text(text):
            await telegram_app.bot.send_message(chat_id=user_chat_id, text=chunk)
    except Exception as e:
        print(f"Notification error: {e}")


# ── Autonomous scheduler wrapper ──────────────────────────────────────────
class PipelineRunner:
    """Wrapper for running pipelines in autonomous mode."""
    
    def __call__(self, command: str) -> dict:
        """Execute pipeline command synchronously."""
        try:
            if command.strip().upper().startswith("LIVE"):
                return run_live_pipeline(command)
            else:
                return run_auto_pipeline(command)
        except Exception as e:
            print(f"Pipeline error: {e}")
            return {"error": str(e), "results": []}


def start_autonomous_scheduler():
    """Initialize and start autonomous scheduler."""
    global scheduler_instance, AUTONOMY_AVAILABLE
    
    if not AUTONOMY_AVAILABLE:
        print("❌ Autonomy modules not available")
        return False
    
    try:
        scheduler_instance = AutonomousScheduler(
            pipeline_runner=PipelineRunner(),
            telegram_notifier=send_notification
        )
        scheduler_instance.start()
        print("✅ Autonomous scheduler started")
        return True
    except Exception as e:
        print(f"Failed to start scheduler: {e}")
        return False


# ── Command handlers ──────────────────────────────────────────────────────

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start command - show help."""
    global user_chat_id
    user_chat_id = update.effective_chat.id
    
    msg = (
        "🤖 BettingBot Phases 1-7 AUTONOMOUS\n\n"
        "⚡ AUTO COMMANDS (no manual input needed):\n"
        "- /auto_on   ← Start autonomous 24/7 scanning\n"
        "- /auto_off  ← Stop autonomous mode\n"
        "- /auto_jobs ← Show scheduled tasks\n"
        "- /auto_status ← Current autonomy status\n\n"
        "📊 MANUAL COMMANDS:\n"
        "- /auto today epl seriea strict\n"
        "- /live epl\n"
        "- /scanwatch\n"
        "- /openpicks\n"
        "- /settle PICK_ID WIN\n"
        "- /stats\n"
        "- /quick\n"
        "- /day\n"
        "- /form chelsea     ← Check team form\n"
        "- /auto_settle      ← Auto-settle finished matches\n\n"
        "🧮 MODELS:\n"
        "- /model football home_xg=1.6 away_xg=0.9 selection=home\n"
        "- /model basketball home_ortg=114 home_drtg=108 away_ortg=110 away_drtg=112 selection=home\n"
    )
    await reply_long(update.message, msg)


async def auto_on_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Turn on autonomous mode."""
    global scheduler_instance, AUTONOMY_AVAILABLE
    
    if not AUTONOMY_AVAILABLE:
        await reply_long(update.message, "❌ Autonomy not available. Install: pip install apscheduler")
        return
    
    if scheduler_instance and scheduler_instance.is_running:
        await reply_long(update.message, "✅ Autonomous mode already ACTIVE\n\nSchedules:\n"
                        "- 08:00 UTC: Morning scan (EPL, LaLiga, SerieA)\n"
                        "- 12:00 UTC: Lunch scan (+ NBA)\n"
                        "- 18:00 UTC: Evening scan + watchlist\n"
                        "- Every 30min: Live in-play scan\n"
                        "- Monday 20:00: Weekly stats")
        return
    
    if start_autonomous_scheduler():
        await reply_long(update.message,
            "✅ AUTONOMOUS MODE ACTIVATED\n\n"
            "🌅 08:00 UTC - Morning scan\n"
            "☀️ 12:00 UTC - Lunch scan\n"
            "🌆 18:00 UTC - Evening + watchlist\n"
            "🔴 Every 30min - Live opportunities\n"
            "📊 Monday 20:00 - Weekly report\n\n"
            "You'll receive notifications for every pick found!")
    else:
        await reply_long(update.message, "❌ Failed to start scheduler")


async def auto_off_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Turn off autonomous mode."""
    global scheduler_instance
    
    if not scheduler_instance:
        await reply_long(update.message, "ℹ️ Autonomous mode not running")
        return
    
    scheduler_instance.stop()
    scheduler_instance = None
    await reply_long(update.message, "⛔ AUTONOMOUS MODE DISABLED\n"
                    "Bot is now in manual mode only")


async def auto_jobs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show scheduled jobs."""
    global scheduler_instance
    
    if not scheduler_instance or not scheduler_instance.is_running:
        await reply_long(update.message, "❌ Scheduler not running")
        return
    
    jobs = scheduler_instance.scheduler.get_jobs()
    msg = "📅 SCHEDULED JOBS:\n\n"
    for job in jobs:
        msg += f"- {job.name}\n  Trigger: {job.trigger}\n  Next run: {job.next_run_time}\n\n"
    
    await reply_long(update.message, msg or "No jobs scheduled")


async def auto_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show autonomy status and stats."""
    global scheduler_instance
    
    from brm import summarize_history, load_pick_history
    
    status = "✅ ACTIVE" if (scheduler_instance and scheduler_instance.is_running) else "⛔ INACTIVE"
    history = load_pick_history()
    meta = summarize_history(history)
    
    msg = f"""📊 AUTONOMY STATUS: {status}

📈 OVERALL STATS:
- Total picks: {meta['total']}
- Open: {meta['open']} | Settled: {meta['settled']}
- W/L/P: {meta['wins']}/{meta['losses']}/{meta['pushes']}
- ROI: {meta['roi']}%
- Profit: {meta['total_pnl']} ({meta['total_stake']} staked)

💾 RECENT MODE:
- Scheduler: {'running' if scheduler_instance and scheduler_instance.is_running else 'stopped'}
- Form tracking: available
- Auto-settle: available
- Adaptive thresholds: available
"""
    await reply_long(update.message, msg)


async def form_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check team form."""
    global AUTONOMY_AVAILABLE
    
    if not AUTONOMY_AVAILABLE:
        await reply_long(update.message, "❌ Form tracking requires apscheduler")
        return
    
    if not context.args:
        await reply_long(update.message, "Usage: /form chelsea")
        return
    
    try:
        tracker = TeamFormTracker()
        team = " ".join(context.args).lower()
        
        # Detect sport
        sport = DEFAULT_TEAM_SPORT.get(team.replace(" ", ""), "football")
        form = tracker.get_team_form(team, sport=sport, matches_count=5)
        
        if not form:
            await reply_long(update.message, f"❌ Form data not found for {team}")
            return
        
        matches = form.get("matches", [])
        stats = form.get("stats", {})
        
        msg = f"📊 FORM: {form['team'].upper()}\n\n"
        msg += f"Strength: {stats.get('strength', 'N/A')} | W/L: {stats.get('wins', 0)}/{stats.get('losses', 0)}\n"
        msg += f"Win rate: {stats.get('win_rate', 0):.1%}\n\n"
        msg += "Last 5 matches:\n"
        for m in matches[-5:]:
            msg += f"- {m['date']} | vs {m['opponent']} | {m['result']} {m['score']} {'🏠' if m['home'] else '✈️'}\n"
        
        await reply_long(update.message, msg)
    except Exception as e:
        await reply_long(update.message, f"❌ Error: {e}")


async def auto_settle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Auto-settle finished matches."""
    global AUTONOMY_AVAILABLE
    
    if not AUTONOMY_AVAILABLE:
        await reply_long(update.message, "❌ Auto-settle requires form_tracking module")
        return
    
    try:
        tracker = TeamFormTracker()
        settler = SettlementAutomation(tracker)
        result = settler.auto_settle_pending_picks()
        
        msg = f"✅ SETTLED {result['settled_count']} picks\n"
        msg += f"Still pending: {result['still_pending']}\n\n"
        
        if result['settled_picks']:
            msg += "SETTLED:\n"
            for pick in result['settled_picks']:
                msg += f"- {pick['match']} | {pick['result']} | PnL {pick['pnl']}\n"
        
        if result['errors']:
            msg += f"\n❌ Errors: {len(result['errors'])}\n"
            for err in result['errors'][:3]:
                msg += f"- {err}\n"
        
        await reply_long(update.message, msg)
    except Exception as e:
        await reply_long(update.message, f"❌ Error: {e}")


async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manual auto scan."""
    args = " ".join(context.args).strip()
    try:
        summary = run_auto_pipeline("AUTO " + args if args else "AUTO today football strict")
        await reply_long(update.message,
            format_summary(summary) + "\n\n" + format_top_picks(summary) + "\n\n" + format_match_report(summary))
    except Exception as e:
        await reply_long(update.message, f"ERROR: {type(e).__name__}: {e}")


async def live_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manual live scan."""
    args = " ".join(context.args).strip()
    try:
        summary = run_live_pipeline("LIVE " + args if args else "LIVE epl")
        header = "[LIVE] " + ("✅ OK" if summary["accepted_count"] > 0 else "🔕 ALL PASS")
        await reply_long(update.message,
            header + "\n\n" + format_summary(summary) + "\n\n"
            + format_top_picks(summary) + "\n\n" + format_match_report(summary))
    except Exception as e:
        await reply_long(update.message, f"ERROR (LIVE): {type(e).__name__}: {e}")


async def openpicks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_long(update.message, format_open_picks())


async def settle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await reply_long(update.message, "Usage: /settle PICK_ID WIN")
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
    """Test sport models."""
    if not context.args:
        await reply_long(update.message,
            "Usage:\n"
            "/model football home_xg=1.6 away_xg=0.9 selection=home\n"
            "/model basketball home_ortg=114 home_drtg=108 away_ortg=110 away_drtg=112 selection=home"
        )
        return
    
    parts = context.args
    sport = parts[0].lower()
    meta, selection = {}, "home"
    
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
                meta[k] = True if v.lower() in ("true", "1", "yes") else v.strip()
    
    ctx = {"sport": sport, "market": "h2h", "selection": selection,
           "best_odds": 2.0, "sport_meta": meta}
    prob = get_sport_model_prob(ctx)
    
    if prob == 0.0:
        await reply_long(update.message, f"MODEL {sport.upper()} → Missing meta data\nParams: {meta}")
    else:
        await reply_long(update.message,
            f"MODEL {sport.upper()} | {selection}\nProbability: {prob:.2f}%")


# ── Bot registration ──────────────────────────────────────────────────────

def main() -> None:
    global telegram_app, scheduler_instance
    
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN or BOT_TOKEN is not set")
    
    telegram_app = Application.builder().token(token).build()
    
    handlers = [
        ("start",        start_cmd),
        ("auto_on",      auto_on_cmd),        # 🆕 Autonomy
        ("auto_off",     auto_off_cmd),       # 🆕 Autonomy
        ("auto_jobs",    auto_jobs_cmd),      # 🆕 Autonomy
        ("auto_status",  auto_status_cmd),    # 🆕 Autonomy
        ("form",         form_cmd),           # 🆕 Form tracking
        ("auto_settle",  auto_settle_cmd),    # 🆕 Auto-settle
        ("auto",         auto_cmd),
        ("live",         live_cmd),
        ("openpicks",    openpicks_cmd),
        ("settle",       settle_cmd),
        ("stats",        stats_cmd),
        ("quick",        quick_cmd),
        ("day",          day_cmd),
        ("model",        model_cmd),
    ]
    
    for name, handler in handlers:
        telegram_app.add_handler(CommandHandler(name, handler))
    
    print("🤖 Bot started — Phases 1-7 with AUTONOMY")
    
    # Optionally start scheduler on bot startup
    if os.getenv("AUTO_START", "false").lower() == "true":
        print("⚡ Auto-starting autonomous scheduler...")
        start_autonomous_scheduler()
    
    telegram_app.run_polling()


if __name__ == "__main__":
    main()
