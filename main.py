import os
import json
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

LAST_RUN = "No runs yet"
RUNS = []

LAST_RUN_FILE = "last_run.txt"
RUNS_FILE = "runs.txt"
AUDIT_FILE = "audit.json"


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


def build_text(kind, sport, league, team, market, odds, bank, mode, strict_flag, ev, books, data_quality, mins_to_start, lineup, bet_class, stake, reason):
    text = kind + " " + "sport=" + sport + " league=" + league + " team=" + team + " market=" + market + " odds=" + str(odds) + " bank=" + str(bank) + " mode=" + mode + " strict=" + str(strict_flag) + " ev=" + str(ev) + " books=" + str(books) + " data=" + data_quality + " mins=" + str(mins_to_start) + " lineup=" + lineup + " class=" + bet_class + " stake=" + str(stake) + " reason=" + reason
    return text


def add_run(text):
    global RUNS
    RUNS.insert(0, text)
    if len(RUNS) > 5:
        RUNS = RUNS[:5]


def extract_value(text, key):
    marker = key + "="
    parts = text.split(" ")
    for part in parts:
        if part.startswith(marker):
            return part[len(marker):]
    return ""


def save_last_run():
    global LAST_RUN
    try:
        with open(LAST_RUN_FILE, "w", encoding="utf-8") as f:
            f.write(LAST_RUN)
    except:
        pass


def save_runs():
    global RUNS
    try:
        text = " || ".join(RUNS)
        with open(RUNS_FILE, "w", encoding="utf-8") as f:
            f.write(text)
    except:
        pass


def build_audit_dict(text):
    data = {}
    data["raw"] = text
    data["kind"] = text.split(" ")[0] if " " in text else text
    data["sport"] = extract_value(text, "sport")
    data["league"] = extract_value(text, "league")
    data["team"] = extract_value(text, "team")
    data["market"] = extract_value(text, "market")
    data["odds"] = extract_value(text, "odds")
    data["bank"] = extract_value(text, "bank")
    data["mode"] = extract_value(text, "mode")
    data["strict"] = extract_value(text, "strict")
    data["ev"] = extract_value(text, "ev")
    data["books"] = extract_value(text, "books")
    data["data"] = extract_value(text, "data")
    data["mins"] = extract_value(text, "mins")
    data["lineup"] = extract_value(text, "lineup")
    data["class"] = extract_value(text, "class")
    data["stake"] = extract_value(text, "stake")
    data["reason"] = extract_value(text, "reason")
    return data


def save_audit(text):
    try:
        data = build_audit_dict(text)
        with open(AUDIT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=True)
    except:
        pass


def load_files_to_memory():
    global LAST_RUN
    global RUNS

    try:
        if os.path.exists(LAST_RUN_FILE):
            with open(LAST_RUN_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content != "":
                    LAST_RUN = content
    except:
        pass

    try:
        if os.path.exists(RUNS_FILE):
            with open(RUNS_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content != "":
                    RUNS = content.split(" || ")[:5]
    except:
        pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot online")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Status online")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "Commands: /status /auto /dryrun /report /history /summary /audit /files /help ; Format: SPORT=football LEAGUE=EPL TEAM=Arsenal MARKET=ML ODDS=1.85 BANK=100 MODE=normal STRICT=0 EV=6 BOOKS=4 DATA=good MINS=120 LINEUP=yes"
    await update.message.reply_text(text)


async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global LAST_RUN
    await update.message.reply_text(LAST_RUN)


async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global RUNS
    if len(RUNS) == 0:
        await update.message.reply_text("No runs yet")
        return
    text = "HISTORY " + " || ".join(RUNS)
    await update.message.reply_text(text)


async def summary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global RUNS
    if len(RUNS) == 0:
        await update.message.reply_text("SUMMARY No runs yet")
        return

    total = len(RUNS)
    pass_count = 0
    non_pass = []

    for run in RUNS:
        cls = extract_value(run, "class")
        reason = extract_value(run, "reason")
        team = extract_value(run, "team")
        stake = extract_value(run, "stake")

        if cls == "PASS":
            pass_count = pass_count + 1
        else:
            item = team + ":" + cls + ":" + stake + ":" + reason
            non_pass.append(item)

    if pass_count == total:
        text = "SUMMARY NO_BETS ALL_PASS total=" + str(total)
        await update.message.reply_text(text)
        return

    accepted = " | ".join(non_pass)
    text = "SUMMARY total=" + str(total) + " pass=" + str(pass_count) + " active=" + str(len(non_pass)) + " picks=" + accepted
    await update.message.reply_text(text)


async def audit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global LAST_RUN
    if LAST_RUN == "No runs yet":
        await update.message.reply_text("AUDIT No runs yet")
        return

    sport = extract_value(LAST_RUN, "sport")
    cls = extract_value(LAST_RUN, "class")
    reason = extract_value(LAST_RUN, "reason")
    stake = extract_value(LAST_RUN, "stake")
    ev = extract_value(LAST_RUN, "ev")
    books = extract_value(LAST_RUN, "books")
    data_quality = extract_value(LAST_RUN, "data")

    text = "AUDIT " + "sport=" + sport + " class=" + cls + " stake=" + stake + " ev=" + ev + " books=" + books + " data=" + data_quality + " reason=" + reason
    await update.message.reply_text(text)


async def files_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    files = []
    if os.path.exists(LAST_RUN_FILE):
        files.append(LAST_RUN_FILE)
    if os.path.exists(RUNS_FILE):
        files.append(RUNS_FILE)
    if os.path.exists(AUDIT_FILE):
        files.append(AUDIT_FILE)

    if len(files) == 0:
        await update.message.reply_text("FILES none")
        return

    text = "FILES " + " ".join(files)
    await update.message.reply_text(text)


async def run_analysis(update, context, dry_mode):
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

    kind = "AUTO"

    if dry_mode:
        stake = 0.0
        kind = "DRYRUN"

    text = build_text(
        kind,
        sport,
        league,
        team,
        market,
        odds,
        bank,
        mode,
        strict_flag,
        ev,
        books,
        data_quality,
        mins_to_start,
        lineup,
        bet_class,
        stake,
        reason
    )

    LAST_RUN = text
    add_run(text)
    save_last_run()
    save_runs()
    save_audit(text)

    await update.message.reply_text(text)


async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await run_analysis(update, context, False)


async def dryrun_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await run_analysis(update, context, True)


def main():
    load_files_to_memory()

    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN not set")

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("report", report_cmd))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("summary", summary_cmd))
    app.add_handler(CommandHandler("audit", audit_cmd))
    app.add_handler(CommandHandler("files", files_cmd))
    app.add_handler(CommandHandler("auto", auto_cmd))
    app.add_handler(CommandHandler("dryrun", dryrun_cmd))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
