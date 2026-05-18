import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

LAST_RUN = "No runs yet"


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


def to_int(value, default_value):
    try:
        return int(float(value))
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


def normalize_data(value):
    v = str(value).lower()
    if v in ["good", "ok", "fresh"]:
        return "good"
    if v in ["bad", "stale", "poor"]:
        return "bad"
    return "unknown"


def normalize_lineup(value):
    v = str(value).lower()
    if v in ["yes", "true", "1", "confirmed"]:
        return "yes"
    return "no"


def normalize_sport(value):
    v = str(value).lower()
    if v in ["football", "basketball", "hockey", "tennis"]:
        return v
    return "unknown"


def lower_class(bet_class):
    if bet_class == "CORE":
        return "SUPPORT"
    if bet_class == "SUPPORT":
        return "MICRO"
    if bet_class == "MICRO":
        return "PASS"
    return "PASS"


def decide(odds, bank, mode, strict_flag, ev, books, data_quality, mins_to_start, lineup, sport):
    if mins_to_start <= 0:
        return "PASS", 0.0, "EXPIRED_EVENT"

    if mins_to_start < 10:
        return "PASS", 0.0, "TOO_CLOSE"

    if books < 2:
        return "PASS", 0.0, "LOW_BOOK_COUNT"

    if data_quality == "bad":
        return "PASS", 0.0, "LOW_DATA_QUALITY"

    if ev < 0:
        return "PASS", 0.0, "EV_NEGATIVE"

    if strict_flag == 1 and ev < 5:
        return "PASS", 0.0, "STRICT_EV_TOO_LOW"

    if mode == "emergency" and ev < 4:
        return "PASS", 0.0, "EMERGENCY_EV_TOO_LOW"

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

    if ev >= 8 and bet_class == "SUPPORT":
        bet_class = "CORE"

    if ev < 3 and bet_class == "CORE":
        bet_class = "SUPPORT"

    if lineup == "no":
        if mode == "emergency":
            return "PASS", 0.0, "LINEUP_PENDING"
        bet_class = lower_class(bet_class)
        reason = "LINEUP_NOT_CONFIRMED"

    if sport == "unknown":
        bet_class = lower_class(bet_class)
        reason = "UNKNOWN_SPORT"

    if bet_class == "PASS":
        return "PASS", 0.0, reason

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


async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global LAST_RUN
    await update.message.reply_text(LAST_RUN)


async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global LAST_RUN

    if not context.args:
        await update.message.reply_text("Use /auto SPORT=football LEAGUE=EPL TEAM=Arsenal MARKET=ML ODDS=1.85 BANK=100 MODE=normal STRICT=0 EV=6 BOOKS=4 DATA=good MINS=120 LINEUP=yes")
        return

    params = parse_params(context.args)

    sport = normalize_sport(params.get("SPORT", "unknown"))
    league = params.get("LEAGUE", "UNKNOWN")
    team = params.get("TEAM", "UNKNOWN")
    market = params.get("MARKET", "ML")

    odds = to_float(params.get("ODDS", "1.80"), 1.80)
    bank = to_float(params.get("BANK", "100"), 100.0)
    mode = normalize_mode(params.get("MODE", "normal"))
    strict_flag = normalize_strict(params.get("STRICT", "0"))
    ev = to_float(params.get("EV", "0"), 0.0)
    books = to_int(params.get("BOOKS", "1"), 1)
    data_quality = normalize_data(params.get("DATA", "unknown"))
    mins_to_start = to_int(params.get("MINS", "999"), 999)
    lineup = normalize_lineup(params.get("LINEUP", "no"))

    bet_class, stake, reason = decide(
        odds,
        bank,
        mode,
        strict_flag,
        ev,
        books,
        data_quality,
        mins_to_start,
        lineup,
        sport
    )

    text = "AUTO " + "sport=" + sport + " league=" + league + " team=" + team + " market=" + market + " odds=" + str(odds) + " bank=" + str(bank) + " mode=" + mode + " strict=" + str(strict_flag) + " ev=" + str(ev) + " books=" + str(books) + " data=" + data_quality + " mins=" + str(mins_to_start) + " lineup=" + lineup + " class=" + bet_class + " stake=" + str(stake) + " reason=" + reason

    LAST_RUN = text

    await update.message.reply_text(text)


def main():
    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN not set")

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("report", report_cmd))
    app.add_handler(CommandHandler("auto", auto_cmd))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
