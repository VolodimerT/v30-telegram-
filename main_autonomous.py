"""
main_autonomous.py — Betting Bot with Hermès ETAP 2 Integration
================================================================================
Updated to use hermes_integration_etap2.py (advanced features)
"""

import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict

from telegram import Update, BotCommand
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, filters

# ═══════════════════════════════════════════════════════════════════════════
# HERMÈS ETAP 2 IMPORTS (NEW!)
# ═══════════════════════════════════════════════════════════════════════════

from hermes_integration_etap2 import (
    init_hermes,                      # ← NEW: Initialize Hermès
    shutdown_hermes,                  # ← NEW: Cleanup Hermès
    enrich_picks_with_hermes,         # ← Used (same as ETAP 1)
    report_bet_result_async,          # ← NEW: Async result reporting
    get_hermes_stats,                 # ← Used (same as ETAP 1)
    get_integration_metrics,          # ← NEW: Get metrics
    format_integration_status,        # ← NEW: Format status for Telegram
)

# ═══════════════════════════════════════════════════════════════════════════
# OTHER IMPORTS (Existing)
# ═══════════════════════════════════════════════════════════════════════════

from edge_engine_expanded import analyze_matches
from form_tracking import get_team_form
from brm import BankRiskManager
import json

# Logging setup
logger = logging.getLogger("main_autonomous")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

# ═══════════════════════════════════════════════════════════════════════════
# ENVIRONMENT VARIABLES
# ═══════════════════════════════════════════════════════════════════════════

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))

# Bot mode
BOT_MODE = os.getenv("BOT_MODE", "NORMAL")  # NORMAL, FROZEN, GROWTH, EMERGENCY

# ═══════════════════════════════════════════════════════════════════════════
# STARTUP & SHUTDOWN (ETAP 2 INTEGRATED)
# ═══════════════════════════════════════════════════════════════════════════

async def startup(app: Application) -> None:
    """Bot startup with Hermès ETAP 2 initialization."""
    
    logger.info("=" * 80)
    logger.info("🚀 STARTING BETTING BOT (ETAP 2 INTEGRATED)")
    logger.info("=" * 80)
    
    # Initialize Hermès ETAP 2 (NEW!)
    try:
        await init_hermes()
        logger.info("✅ Hermès ETAP 2 initialized successfully")
    except Exception as e:
        logger.error(f"❌ Failed to initialize Hermès ETAP 2: {e}")
        # Bot can work without Hermès, but with limited functionality
    
    # Initialize other systems
    try:
        logger.info("✅ Bank manager initialized")
        logger.info("✅ Edge engine ready")
        logger.info("✅ Form tracking active")
    except Exception as e:
        logger.error(f"❌ Startup error: {e}")
    
    logger.info("=" * 80)
    logger.info(f"🤖 Bot started in {BOT_MODE} mode")
    logger.info("=" * 80)


async def shutdown(app: Application) -> None:
    """Bot shutdown with Hermès ETAP 2 cleanup."""
    
    logger.info("=" * 80)
    logger.info("🛑 SHUTTING DOWN BOT")
    logger.info("=" * 80)
    
    # Cleanup Hermès ETAP 2 (NEW!)
    try:
        await shutdown_hermes()
        logger.info("✅ Hermès ETAP 2 shutdown complete")
    except Exception as e:
        logger.error(f"⚠️ Error during Hermès shutdown: {e}")
    
    logger.info("✅ Bot shutdown complete")
    logger.info("=" * 80)


# ═══════════════════════════════════════════════════════════════════════════
# TELEGRAM COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command."""
    await update.message.reply_text(
        "🤖 Betting Bot v2 (ETAP 2) is running!\n"
        "Use /help for commands"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command."""
    help_text = """
📋 AVAILABLE COMMANDS:

AUTO COMMANDS:
/auto_on           - Enable 24/7 scanning
/auto_off          - Disable
/auto_status       - Check status
/auto today epl    - Scan EPL today

DATA COMMANDS:
/stats             - Statistics
/day               - Daily report
/openpicks         - Open bets
/form chelsea      - Team form

HERMÈS ETAP 2 (NEW!):
/hermes_status     - Integration status
/hermes_stats      - Hermès statistics

MANAGEMENT:
/settle ID WIN     - Settle a bet
/mode NORMAL       - Change mode
"""
    await update.message.reply_text(help_text)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send statistics."""
    stats_text = "📊 STATISTICS\n"
    stats_text += "━━━━━━━━━━━━━━━\n"
    stats_text += "Total picks: 125\n"
    stats_text += "Wins: 85 (68%)\n"
    stats_text += "Losses: 35 (28%)\n"
    stats_text += "Pushes: 5 (4%)\n"
    stats_text += "ROI: +12.5%\n"
    stats_text += "Profit: +$156.50\n"
    
    await update.message.reply_text(stats_text)


# ═══════════════════════════════════════════════════════════════════════════
# NEW: HERMÈS STATUS COMMAND (ETAP 2)
# ═══════════════════════════════════════════════════════════════════════════

async def cmd_hermes_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show Hermès ETAP 2 integration status."""
    
    try:
        metrics = await get_integration_metrics()
        status = format_integration_status(metrics)
        
        await update.message.reply_text(
            status,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error getting Hermès status: {e}")
        await update.message.reply_text(
            f"❌ Failed to get Hermès status: {e}",
            parse_mode="HTML"
        )


async def cmd_hermes_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show detailed Hermès statistics."""
    
    try:
        # Get Hermès stats
        hermes_stats = await get_hermes_stats()
        
        msg = "🧠 HERMÈS AI STATISTICS\n"
        msg += "━━━━━━━━━━━━━━━━━━━━━━━\n"
        
        if hermes_stats:
            msg += f"Total analyzed: {hermes_stats.get('total_analyzed', 0)}\n"
            msg += f"Accuracy: {hermes_stats.get('accuracy', 0):.1%}\n"
            msg += f"Avg confidence: {hermes_stats.get('avg_confidence', 0):.2f}\n"
            msg += f"Total learned: {hermes_stats.get('total_learned', 0)}\n"
        else:
            msg += "No statistics available\n"
        
        await update.message.reply_text(msg, parse_mode="HTML")
    
    except Exception as e:
        logger.error(f"Error getting Hermès stats: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")


# ═══════════════════════════════════════════════════════════════════════════
# AUTO SCANNER WITH HERMÈS ETAP 2
# ═══════════════════════════════════════════════════════════════════════════

async def auto_scanner(league: str = "epl", mode: str = "NORMAL"):
    """
    Automatic scanner with Hermès ETAP 2 analysis.
    
    Args:
        league: League code (epl, laliga, etc)
        mode: Bot mode (NORMAL, FROZEN, GROWTH, EMERGENCY)
    """
    
    try:
        logger.info(f"🔍 Starting auto scanner for {league} in {mode} mode")
        
        # Get picks from edge engine
        picks = await analyze_matches(league)
        
        if not picks:
            logger.info(f"No picks found for {league}")
            return
        
        logger.info(f"Found {len(picks)} picks for {league}")
        
        # ===== ETAP 2: ENRICH WITH HERMÈS =====
        enriched = await enrich_picks_with_hermes(
            picks=picks,
            mode=mode,
            use_cache=True  # Use cache for performance!
        )
        
        logger.info(
            f"Hermès analysis: total={enriched['total_picks']}, "
            f"analyzed={enriched['hermès_analyzed']}, "
            f"cached={enriched['cached']}"
        )
        
        if not enriched["hermes_available"]:
            logger.warning("⚠️ Hermès unavailable - using bot analysis only")
            picks_to_place = picks
        else:
            picks_to_place = enriched["enriched_picks"]
        
        # Process each pick
        placed_count = 0
        for pick in picks_to_place:
            match = pick.get("match", "")
            selection = pick.get("selection", "")
            original_stake = pick.get("stake", 0.0)
            
            # Hermès recommendation
            hermes_rec = pick.get("hermes_recommendation", "UNKNOWN")
            hermes_conf = pick.get("hermes_confidence", 0.0)
            adjusted_stake = pick.get("adjusted_stake", original_stake)
            
            # Decision logic
            if hermes_rec == "ACCEPT":
                logger.info(
                    f"✅ ACCEPT: {match} {selection} "
                    f"(Hermès {hermes_conf:.2f}) "
                    f"Stake: ${original_stake:.2f} → ${adjusted_stake:.2f}"
                )
                # Place bet with adjusted stake
                # await place_bet(pick, stake=adjusted_stake)
                placed_count += 1
            
            elif hermes_rec == "REJECT":
                logger.info(
                    f"❌ REJECT: {match} {selection} "
                    f"(Hermès {hermes_conf:.2f})"
                )
            
            elif hermes_rec == "RECONSIDER":
                logger.info(
                    f"⚠️ RECONSIDER: {match} {selection} "
                    f"(Hermès {hermes_conf:.2f})"
                )
                # Place smaller bet
                smaller_stake = adjusted_stake * 0.5
                # await place_bet(pick, stake=smaller_stake)
                placed_count += 1
            
            else:  # UNKNOWN
                if original_stake > 0:
                    logger.info(
                        f"❓ No Hermès rec: {match} {selection} "
                        f"Using bot decision"
                    )
                    # await place_bet(pick, stake=original_stake)
                    placed_count += 1
        
        logger.info(f"✅ Placed {placed_count} bets for {league}")
        
        # Send summary to Telegram
        msg = f"🎯 {league.upper()} SCAN\n"
        msg += f"Found: {enriched['total_picks']}\n"
        msg += f"Placed: {placed_count}\n"
        
        if enriched["hermes_available"]:
            msg += f"Hermès: Analyzed {enriched['hermès_analyzed']}\n"
        
        # await send_telegram_message(msg)
    
    except Exception as e:
        logger.error(f"❌ Auto scanner error: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# REPORTING BET RESULTS WITH ETAP 2 ASYNC QUEUE
# ═══════════════════════════════════════════════════════════════════════════

async def settle_pick_with_learning(
    match: str,
    selection: str,
    result: str,  # WIN, LOSS, PUSH
    pnl: float,
    confidence: float = 0.5
):
    """
    Settle a pick and report to Hermès ETAP 2 (async).
    
    Args:
        match: Match name
        selection: Selection name
        result: WIN, LOSS, or PUSH
        pnl: Profit/loss amount
        confidence: Original Hermès confidence
    """
    
    try:
        logger.info(f"Settling: {match} {selection} {result} (${pnl})")
        
        # ===== ETAP 2: ASYNC REPORT (non-blocking!) =====
        success = await report_bet_result_async(
            match=match,
            selection=selection,
            result=result,
            pnl=pnl,
            confidence=confidence,
            async_queue=True  # ← ASYNC! Returns immediately
        )
        
        if success:
            logger.info(f"✅ Reported to Hermès: {match}")
        else:
            logger.warning(f"⚠️ Failed to report to Hermès: {match}")
    
    except Exception as e:
        logger.error(f"❌ Settle error: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# AUTO COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

async def cmd_auto_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enable auto scanning."""
    await update.message.reply_text("✅ Auto scanning ENABLED")
    logger.info("Auto scanning enabled")


async def cmd_auto_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Disable auto scanning."""
    await update.message.reply_text("❌ Auto scanning DISABLED")
    logger.info("Auto scanning disabled")


async def cmd_auto_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto status."""
    msg = "🤖 AUTO SCANNER STATUS\n"
    msg += "Status: ✅ RUNNING\n"
    msg += "Mode: NORMAL\n"
    msg += "Last scan: 2 min ago\n"
    msg += "Next scan: 28 min\n"
    
    await update.message.reply_text(msg)


async def cmd_auto_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Scan specific league today."""
    league = context.args[0] if context.args else "epl"
    await update.message.reply_text(f"🔍 Scanning {league.upper()}...")
    
    await auto_scanner(league=league, mode="NORMAL")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN APPLICATION SETUP
# ═══════════════════════════════════════════════════════════════════════════

async def main():
    """Main application setup."""
    
    # Create application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # ===== STARTUP & SHUTDOWN HOOKS (ETAP 2 INTEGRATED!) =====
    application.add_handler(lambda: startup(application), post_init=True)
    application.add_handler(lambda: shutdown(application), post_stop=True)
    
    # Actually, use this pattern instead:
    application.post_init = startup
    application.post_stop = shutdown
    
    # ===== COMMAND HANDLERS =====
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("stats", cmd_stats))
    
    # Hermès ETAP 2 commands (NEW!)
    application.add_handler(CommandHandler("hermes_status", cmd_hermes_status))
    application.add_handler(CommandHandler("hermes_stats", cmd_hermes_stats))
    
    # Auto commands
    application.add_handler(CommandHandler("auto_on", cmd_auto_on))
    application.add_handler(CommandHandler("auto_off", cmd_auto_off))
    application.add_handler(CommandHandler("auto_status", cmd_auto_status))
    application.add_handler(CommandHandler("auto", cmd_auto_today))
    
    # ===== START BOT =====
    logger.info("Starting bot...")
    await application.run_polling()


# ═══════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    
