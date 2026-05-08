import asyncio
import logging
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession

from config import Config
from db import Database
from web import start_web_server
from handlers import register_handlers
from poster import PostingEngine

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("autopost")

MAX_FLOODWAIT = 3600


async def _safe_start(client, label: str, **kwargs):
    for attempt in range(5):
        try:
            await client.start(**kwargs)
            return True
        except FloodWaitError as e:
            if e.seconds > MAX_FLOODWAIT:
                logger.error(f"❌ {label}: FloodWait {e.seconds}s — giving up")
                return False
            logger.warning(f"⏳ {label}: FloodWait {e.seconds}s (attempt {attempt+1}/5)")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logger.error(f"❌ {label}: {e}")
            return False
    return False


async def main():
    logger.info("🚀 Starting AutoPost Bot...")

    # 1 — Web server first (health check)
    await start_web_server(Config.PORT)

    # 2 — Database
    db = Database(Config.MONGO_URI)
    await db.connect()
    await db.add_admin(Config.OWNER_ID, "Owner")
    logger.info("✅ Database ready")

    # 3 — Userbot (optional)
    userbot = None
    if Config.SESSION_STRING:
        userbot = TelegramClient(
            StringSession(Config.SESSION_STRING),
            Config.API_ID, Config.API_HASH,
        )
        if await _safe_start(userbot, "Userbot"):
            me = await userbot.get_me()
            logger.info(f"✅ Userbot: {me.first_name} (ID {me.id})")
        else:
            userbot = None
    else:
        logger.warning("⚠️  No SESSION_STRING — use /gensession")

    # 4 — Bot
    bot = TelegramClient("bot_session", Config.API_ID, Config.API_HASH)
    if not await _safe_start(bot, "Bot", bot_token=Config.BOT_TOKEN):
        logger.error("❌ Bot failed. Exiting.")
        await db.disconnect()
        return
    bot_me = await bot.get_me()
    logger.info(f"✅ Bot: @{bot_me.username}")

    # 5 — Posting engine
    poster = None
    if userbot:
        poster = PostingEngine(userbot, db)
        await poster.start()

    # 6 — Handlers (receives poster reference for pause/resume)
    register_handlers(bot, userbot, db, poster)

    logger.info("🟢 Fully running")

    # 7 — Block
    try:
        await bot.run_until_disconnected()
    finally:
        if poster:
            await poster.stop()
        if userbot:
            await userbot.disconnect()
        await db.disconnect()
        logger.info("🛑 Shut down")


if __name__ == "__main__":
    asyncio.run(main())
