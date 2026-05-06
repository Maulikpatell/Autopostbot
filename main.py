import asyncio
import logging
from telethon import TelegramClient
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


async def main():
    logger.info("🚀 Starting AutoPost Bot...")

    # ── Database ────────────────────────────────────────────────
    db = Database(Config.MONGO_URI)
    await db.connect()
    await db.add_admin(Config.OWNER_ID, "Owner")
    logger.info("✅ Database ready")

    # ── Web Server ──────────────────────────────────────────────
    await start_web_server(Config.PORT)

    # ── Userbot (optional — may be missing on first deploy) ─────
    userbot = None
    if Config.SESSION_STRING:
        try:
            userbot = TelegramClient(
                StringSession(Config.SESSION_STRING),
                Config.API_ID,
                Config.API_HASH,
            )
            await userbot.start()
            me = await userbot.get_me()
            logger.info(f"✅ Userbot connected as {me.first_name} (ID {me.id})")
        except Exception as e:
            logger.error(f"❌ Userbot failed to start: {e}")
            userbot = None
    else:
        logger.warning("⚠️  No SESSION_STRING — userbot disabled. Use /gensession to create one.")

    # ── Bot ─────────────────────────────────────────────────────
    bot = TelegramClient("bot_session", Config.API_ID, Config.API_HASH)
    await bot.start(bot_token=Config.BOT_TOKEN)
    bot_me = await bot.get_me()
    logger.info(f"✅ Bot connected as @{bot_me.username}")

    # ── Register command handlers ───────────────────────────────
    register_handlers(bot, userbot, db)

    # ── Start posting engine ────────────────────────────────────
    poster = None
    if userbot:
        poster = PostingEngine(userbot, db)
        await poster.start()

    logger.info("🟢 Bot is fully running")

    # ── Keep alive ──────────────────────────────────────────────
    try:
        await bot.run_until_disconnected()
    finally:
        if poster:
            await poster.stop()
        if userbot:
            await userbot.disconnect()
        await db.disconnect()
        logger.info("🛑 Bot shut down")


if __name__ == "__main__":
    asyncio.run(main())
