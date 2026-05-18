import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


def parse_params(args):
    params = {}
    for part in args:
        if "=" in part:
            key, value = part.split("=", 1)
            params[key.upper()] = value
    return params


def decide(odds, bank):
    if odds <= 1.60:
        return "CORE", round(bank * 0.03, 2), "low_odds_stable"
    if odds <= 2.20:
        return "SUPPORT", round(bank * 0.02, 2), "mid_odds_ok"
    if odds <= 3.00:
        return "MICRO", round(bank * 0.01, 2), "high_risk_micro"
    return "PASS", 0.0, "odds_too_high"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot online")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Status online")


async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Use /auto LEAGUE=EPL TEAM=Arsenal MARKET=ML ODDS=1.85 BANK=100")
        return

    params = parse_params(context.args)

    league = params.get("LEAGUE", "UNKNOWN")
    team = params.get("TEAM", "UNKNOWN")
    market = params.get("MARKET", "ML")

    try:
        odds = float(params.get("ODDS", "1.80"))
    except:
        await update.message.reply_text("Bad ODDS")
        return

    try:
        bank = float(params.get("BANK", "100"))
    except:
        await update.message.reply_text("Bad BANK")
        return

    bet_class, stake, reason = decide(odds, bank)

    text = "AUTO " + "league=" + league + " team=" + team + " market=" + market + " odds=" + str(odds) + " bank=" + str(bank) + " class=" + bet_class + " stake=" + str(stake) + " reason=" + reason

    await update.message.reply_text(text)


def main():
    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN not set")

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("auto", auto_cmd))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
