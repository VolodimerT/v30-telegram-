"""
main_autonomous.py — Betting Bot with Hermès ETAP 2 Integration (FIXED)
"""

import os
import asyncio
import logging

from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler

# ═══════════════════════════════════════════════════════════════════════════
# HERMÈS ETAP 2 IMPORTS
# ═══════════════════════════════════════════════════════════════════════════

from hermes_integration_etap2 import (
    init_hermes,
    shutdown_hermes,
    get_integration_metrics,
    format_integration_status,
)

# Logging
logger = logging.getLogger("main_autonomous")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

# Environment
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# ═══════════════════════════════════════════════════════════════════════════
# TELEGRAM COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command."""
    msg = "🤖 Bot is running (ETAP 2)!\n\n/help for commands"
    await update.message.reply_text(msg)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command."""
    msg = """
📋 COMMANDS:
/hermes_status  - Show Hermès status
/hermes_stats   - Show Hermès stats
/start          - Start
/help           - This message
"""
    await update.message.reply_text(msg)


async def cmd_hermes_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show Hermès ETAP 2 status."""
    try:
        metrics = await get_integration_metrics()
        status = format_integration_status(metrics)
        await update.message.reply_text(status, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_hermes_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show Hermès stats."""
    msg = "🧠 HERMÈS AI\n✅ Online and ready"
    await update.message.reply_text(msg)


# ═══════════════════════════════════════════════════════════════════════════
# MAIN APPLICATION
# ═══════════════════════════════════════════════════════════════════════════

async def main():
    """Main application."""
    
    logger.info("=" * 80)
    logger.info("🚀 STARTING BETTING BOT (ETAP 2)")
    logger.info("=" * 80)
    
    # Create application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Initialize Hermès ETAP 2 on startup
    async def post_init(app: Application) -> None:
        try:
            await init_hermes()
            logger.info("✅ Hermès ETAP 2 initialized")
        except Exception as e:
            logger.error(f"❌ Hermès init error: {e}")
    
    # Cleanup Hermès ETAP 2 on shutdown
    async def post_stop(app: Application) -> None:
        try:
            await shutdown_hermes()
            logger.info("✅ Hermès ETAP 2 shutdown")
        except Exception as e:
            logger.error(f"❌ Hermès shutdown error: {e}")
    
    # Set handlers
    application.post_init = post_init
    application.post_stop = post_stop
    
    # Add command handlers
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("hermes_status", cmd_hermes_status))
    application.add_handler(CommandHandler("hermes_stats", cmd_hermes_stats))
    
    # Start bot
    logger.info("Starting bot polling...")
    try:
        await application.run_polling()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise


# ═══════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped")
    except Exception as e:
        logger.error(f"Fatal: {e}")
        
