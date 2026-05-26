"""
main_autonomous.py - MINIMAL WORKING VERSION
"""
import os
import logging
from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler
from hermes_integration_etap2 import init_hermes, shutdown_hermes, get_integration_metrics, format_integration_status

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Bot running (ETAP 2)!")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("/hermes_status or /help")

async def cmd_hermes_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        metrics = await get_integration_metrics()
        status = format_integration_status(metrics)
        await update.message.reply_text(status, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

async def cmd_hermes_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🧠 Hermès online")

def main():
    logger.info("=" * 80)
    logger.info("🚀 STARTING BETTING BOT (ETAP 2)")
    logger.info("=" * 80)
    
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    async def post_init(app):
        try:
            await init_hermes()
            logger.info("✅ Hermès ETAP 2 initialized")
        except Exception as e:
            logger.error(f"Hermès init error: {e}")
    
    async def post_stop(app):
        try:
            await shutdown_hermes()
            logger.info("✅ Hermès shutdown")
        except Exception as e:
            logger.error(f"Hermès stop error: {e}")
    
    app.post_init = post_init
    app.post_stop = post_stop
    
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("hermes_status", cmd_hermes_status))
    app.add_handler(CommandHandler("hermes_stats", cmd_hermes_stats))
    
    logger.info("Starting bot polling...")
    app.run_polling()

if __name__ == "__main__":
    main()
    
