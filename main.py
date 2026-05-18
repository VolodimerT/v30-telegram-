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
        bet_class = "CORE"
        pct = 0.03
        reason = "low_odds_stable"
    elif odds <= 2.20:
        bet_class = "SUPPORT"
        pct = 0.02
        reason = "mid_odds_ok"
    elif odds <= 3.00:
        bet_class = "MICRO"
        pct = 0.01
        reason = "high_risk_micro"
    else:
        bet_class = "PASS"
        pct = 0.00
        reason = "odds_too_high"

    stake = round(bank * pct, 2)
    return bet_class, stake, reason, pct


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot online. Commands: /status /auto")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Status: online")


async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Use: /auto LEAGUE=EPL TEAM=Arsenal MARKET=ML ODDS=1.85 BANK=100")
        return

    params = parse_params(context.args)

    league = params.get("LEAGUE", "UNKNOWN")
    team = params.get("TEAM", "UNKNOWN")
    market = params.get("MARKET", "ML")

    try:
        odds = float(params.get("ODDS", "1.80"))
    except ValueError:
        await update.message.reply_text("Bad ODDS")
        return

    try:
        bank = float(params.get("BANK", "100"))
    except ValueError:
        await update.message.reply_text("Bad BANK")
        return

    bet_class, stake, reason, pct = decide(odds, bank)

    text = (
        "AUTO RESULT
"
        + "league: " + league + "
"
        + "team: " + team + "
"
        + "market: " + market + "
"
        + "odds: " + str(odds) + "
"
        + "bank: " + str(bank) + "
"
        + "class: " + bet_class + "
"
        + "stake: " + str(stake) + "
"
        + "stake_pct: " + str(round(pct * 100, 2)) + "%
"
        + "reason: " + reason
    )

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
