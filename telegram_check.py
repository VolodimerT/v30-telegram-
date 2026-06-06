import os
import asyncio
from telegram import Bot

async def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("CHAT_ID", "").strip()

    print("TG_CHECK: TOKEN_SET", bool(token), flush=True)
    print("TG_CHECK: CHAT_ID", chat_id, flush=True)

    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing")

    bot = Bot(token)
    me = await bot.get_me()
    print("TG_CHECK: GET_ME_OK", me.username, me.id, flush=True)

    await bot.delete_webhook(drop_pending_updates=True)
    print("TG_CHECK: WEBHOOK_DELETED", flush=True)

    if chat_id:
        await bot.send_message(chat_id=chat_id, text="✅ Railway Telegram API test OK")
        print("TG_CHECK: SEND_OK", flush=True)
    else:
        print("TG_CHECK: CHAT_ID_EMPTY_SKIP_SEND", flush=True)

asyncio.run(main())
