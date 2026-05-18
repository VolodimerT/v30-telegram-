import os
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ /auto =====

def parse_auto_request(text: str) -> dict:
    """
    Очень простое временное парсило:
    /auto LEAGUE=EPL TEAM=Arsenal MARKET=ML ODDS=1.85 BANK=100
    """
    parts = text.strip().split()
    params = {}

    for part in parts:
        if "=" in part:
            key, value = part.split("=", 1)
            params[key.upper()] = value

    return {
        "league": params.get("LEAGUE", "UNKNOWN"),
        "team": params.get("TEAM", "UNKNOWN"),
        "market": params.get("MARKET", "ML"),
        "odds": float(params.get("ODDS", "1.80")),
        "bank": float(params.get("BANK", "100")),
    }


def simple_decision_engine(request: dict) -> dict:
    """
    Фейковый deterministic engine, чтобы был формат:
    возвращает class + stake + причины.
    Логика: чем выше коэффициент, тем ниже класс и доля банка.
    """
    odds = request["odds"]
    bank = request["bank"]

    if odds <= 1.6:
        bet_class = "CORE"
        stake_pct = 0.03
        reason = "Низкий коэфф → более стабильный сценарий."
    elif odds <= 2.2:
        bet_class = "SUPPORT"
        stake_pct = 0.02
        reason = "Средний коэфф → рабочий, но не ядро."
    elif odds <= 3.0:
        bet_class = "MICRO"
        stake_pct = 0.01
        reason = "Повышенный риск → микро вход."
    else:
        bet_class = "PASS"
        stake_pct = 0.0
        reason = "Слишком высокий коэфф → отказ по risk-гейту."

    stake = round(bank * stake_pct, 2)

    return {
        "class": bet_class,
        "stake": stake,
        "stake_pct": round(stake_pct * 100, 2),
        "reason": reason,
    }


# ===== HANDLERS =====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "V30 bot online.
"
        "Команды:
"
        "/status — проверить статус
"
        "/auto LEAGUE=... TEAM=... MARKET=... ODDS=... BANK=...
"
        "Пример:
"
        "/auto LEAGUE=EPL TEAM=Arsenal MARKET=ML ODDS=1.85 BANK=100"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Status: online")


async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Весь текст после /auto
    args_text = update.message.text.replace("/auto", "", 1).strip()

    if not args_text:
        await update.message.reply_text(
            "Нужно передать параметры после /auto.
"
            "Пример:
"
            "/auto LEAGUE=EPL TEAM=Arsenal MARKET=ML ODDS=1.85 BANK=100"
        )
        return

    req = parse_auto_request(args_text)
    decision = simple_decision_engine(req)

    reply = (
        f"V30 AUTO MOCK
"
        f"Лига: {req['league']}
"
        f"Команда: {req['team']}
"
        f"Рынок: {req['market']}
"
        f"Коэфф: {req['odds']}
"
        f"Банк: {req['bank']}

"
        f"Класс: {decision['class']}
"
        f"Ставка: {decision['stake']} ({decision['stake_pct']}% от банка)
"
        f"Причина: {decision['reason']}

"
        f"Это пока только TEST-логика. "
        f"Дальше сюда подключим настоящий V30 engine."
    )

    await update.message.reply_text(reply)


def main():
    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN not set")

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("auto", auto_cmd))

    app.run_polling()


if __name__ == "__main__":
    main()
