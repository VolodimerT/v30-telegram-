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


def to_float(value, default_value):
    try:
        return float(value)
    except:
        return default_value


def normalize_mode(value):
    v = str(value).lower()
    if v == "emergency":
        return "emergency"
    return "normal"


def normalize_strict(value):
    v = str(value).lower()
    if v in ["1", "true", "yes", "on"]:
        return 1
    return 0


def decide(odds, bank, mode, strict_flag):
    if odds > 3.00:
        return "PASS", 0.0, "ODDS_TOO_HIGH"

    if mode == "emergency" and odds > 2.20:
        return "PASS", 0.0, "EMERGENCY_ODDS_CAP"

    if strict_flag == 1 and odds > 2.00:
        return "PASS", 0.0, "STRICT_ODDS_CAP"

    if odds <= 1.60:
        bet_class = "CORE"
        pct = 0.03
        reason = "LOW_ODDS_STABLE"
    elif odds <= 2.20:
        bet_class = "SUPPORT"
        pct = 0.02
        reason = "MID_ODDS_OK"
    else:
        bet_class = "MICRO"
        pct = 0.01
        reason = "HIGH_RISK_MICRO"

    if mode == "emergency":
        if bet_class == "CORE":
            pct = 0.02
        elif bet_class == "SUPPORT":
            pct = 0.01
        elif bet_class == "MICRO":
            pct = 0.005

    stake = round(bank * pct, 2)

    if mode == "emergency" and stake > 25:
        stake = 25.0

    return bet_class, stake, reason


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot online")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Status online")


async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Use /auto LEAGUE=EPL TEAM=Arsenal MARKET=ML ODDS=1.85 BANK=100 MODE=normal STRICT=0")
        return

    params = parse_params(context.args)

    league = params.get("LEAGUE", "UNKNOWN")
    team = params.get("TEAM", "UNKNOWN")
    market = params.get("MARKET", "ML")

    odds = to_float(params.get("ODDS", "1.80"), 1.80)
    bank = to_float(params.get("BANK", "100"), 100.0)
    mode = normalize_mode(params.get("MODE", "normal"))
    strict_flag = normalize_strict(params.get("STRICT", "0"))

    bet_class, stake, reason = decide(odds, bank, mode, strict_flag)

    text = "AUTO " + "league=" + league + " team=" + team + " market=" + market + " odds=" + str(odds) + " bank=" + str(bank) + " mode=" + mode + " strict=" + str(strict_flag) + " class=" + bet_class + " stake=" + str(stake) + " reason=" + reason

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
