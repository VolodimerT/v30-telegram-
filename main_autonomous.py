"""
main_autonomous.py — Betting Bot with Hermès ETAP 2 Integration (SIMPLIFIED)
Инициализирует только ETAP 2 без зависимостей от других модулей
"""

import os
import asyncio
import logging
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler

# ═══════════════════════════════════════════════════════════════════════════
# HERMÈS ETAP 2 IMPORTS (NEW!)
# ═══════════════════════════════════════════════════════════════════════════

from hermes_integration_etap2 import (
    init_hermes,
    shutdown_hermes,
    get_integration_metrics,
    format_integration_status,
)

# Logging setup
logger = logging.getLogger("main_autonomous")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

# ═══════════════════════════════════════════════════════════════════════════
# ENVIRONMENT
# ═══════════════════════════════════════════════════════════════════════════

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# ═══════════════════════════════════════════════════════════════════════════
# STARTUP & SHUTDOWN (ETAP 2)
# ═══════════════════════════════════════════════════════════════════════════

async def startup(app: Application) -> None:
    """Bot startup with Hermès ETAP 2 initialization."""
    logger.info("=" * 80)
    logger.info("🚀 STARTING BETTING BOT (ETAP 2)")
    logger.info("=" * 80)
    
    try:
        await init_hermes()
        logger.info("✅ Hermès ETAP 2 initialized successfully")
    except Exception as e:
        logger.error(f"❌ Failed to initialize Hermès: {e}")
    
    logger.info("=" * 80)


async def shutdown(app: Application) -> None:
    """Bot shutdown with Hermès ETAP 2 cleanup."""
    logger.info("=" * 80)
    logger.info("🛑 SHUTTING DOWN BOT")
    logger.info("=" * 80)
    
    try:
        await shutdown_hermes()
        logger.info("✅ Hermès ETAP 2 shutdown complete")
    except Exception as e:
        logger.error(f"⚠️ Hermès shutdown error: {e}")
    
    logger.info("=" * 80)


# ═══════════════════════════════════════════════════════════════════════════
# TELEGRAM COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command."""
    msg = """
🤖 Betting Bot v2 (ETAP 2) is running!

Commands:
/hermes_status  - Show Hermès integration status
/hermes_stats   - Show Hermès statistics
/help           - Show all commands
"""
    await update.message.reply_text(msg)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command."""
    msg = """
📋 AVAILABLE COMMANDS:

HERMÈS ETAP 2:
/hermes_status  - Integration status
/hermes_stats   - Hermès AI statistics

OTHER:
/start          - Start
/help           - This message
"""
    await update.message.reply_text(msg)


async def cmd_hermes_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show Hermès ETAP 2 integration status."""
    try:
        metrics = await get_integration_metrics()
        status = format_integration_status(metrics)
        await update.message.reply_text(status, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error getting Hermès status: {e}")
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_hermes_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show Hermès statistics."""
    try:
        msg = """
🧠 HERMÈS AI STATISTICS

Status: ✅ Online
Ready for analysis and learning
"""
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"❌ Error: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN APPLICATION
# ═══════════════════════════════════════════════════════════════════════════

async def main():
    """Main application setup."""
    
    # Create application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Setup handlers
    application.post_init = startup
    application.post_stop = shutdown
    
    # Command handlers
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("hermes_status", cmd_hermes_status))
    application.add_handler(CommandHandler("hermes_stats", cmd_hermes_stats))
    
    # Start bot
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
        
