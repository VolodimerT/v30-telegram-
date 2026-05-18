import os
import logging
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("/start from chat_id=%s", update.effective_chat.id if update.effective_chat else None)
    await update.message.reply_text(
        "Бот V30 онлайн.
"
        "Команды:
"
        "/status
"
        "/auto LEAGUE=EPL TEAM=Arsenal MARKET=ML ODDS=1.85 BANK=100"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("/status from chat_id=%s", update.effective_chat.id if update.effective_chat else None)
    await update.message.reply_text("Status: online")


async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("/auto triggered, raw text=%s, args=%s", update.message.text if update.message else None, context.args)

    if not context.args:
        await update.message.reply_text(
            "Формат:
"
            "/auto LEAGUE=EPL TEAM=Arsenal MARKET=ML ODDS=1.85 BANK=100"
        )
        return

    params = {}
    for part in context.args:
        if "=" in part:
            key, value = part.split("=", 1)
            params[key.upper()] = value

    league = params.get("LEAGUE", "UNKNOWN")
    team = params.get("TEAM", "UNKNOWN")
    market = params.get("MARKET", "ML")

    try:
        odds = float(params.get("ODDS", "1.80"))
    except ValueError:
        await update.message.reply_text("Ошибка: ODDS должен быть числом. Пример: ODDS=1.85")
        return

    try:
        bank = float(params.get("BANK", "100"))
    except ValueError:
        await update.message.reply_text("Ошибка: BANK должен быть числом. Пример: BANK=100")
        return

    if odds <= 1.6:
        bet_class = "CORE"
        stake_pct = 0.03
        reason = "Низкий коэфф -> более стабильный сценарий."
    elif odds <= 2.2:
        bet_class = "SUPPORT"
        stake_pct = 0.02
        reason = "Средний коэфф -> рабочий, но не ядро."
    elif odds <= 3.0:
        bet_class = "MICRO"
        stake_pct = 0.01
        reason = "Повышенный риск -> микро вход."
    else:
        bet_class = "PASS"
        stake_pct = 0.0
        reason = "Слишком высокий коэфф -> отказ по risk-гейту."

    stake = round(bank * stake_pct, 2)

    reply = (
        "V30 AUTO MOCK
"
        f"Лига: {league}
"
        f"Команда: {team}
"
        f"Рынок: {market}
"
        f"Коэфф: {odds}
"
        f"Банк: {bank}

"
        f"Класс: {bet_class}
"
        f"Ставка: {stake} ({round(stake_pct * 100, 2)}% от банка)
"
        f"Причина: {reason}"
    )

    await update.message.reply_text(reply)


async def echo_debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text if update.message else None
    logger.info("text message received: %s", text)


def main():
    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN not set")

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("auto", auto_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo_debug))

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
