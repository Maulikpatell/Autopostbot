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

# Maximum time to wait for a single FloodWait before giving up
MAX_FLOODWAIT = 3600  # 1 hour


async def _start_with_floodwait(client, label: str, **kwargs):
    """Start a Telethon client, sleeping through FloodWaits."""
    for attempt in range(5):
        try:
            await client.start(**kwargs)
            return True
        except FloodWaitError as e:
            wait = e.seconds
            if wait > MAX_FLOODWAIT:
                logger.error(
                    f"❌ {label}: FloodWait too long ({wait}s), giving up"
                )
                return False
            logger.warning(
                f"⏳ {label}: FloodWait {wait}s (attempt {attempt + 1}/5)"
            )
            await asyncio.sleep(wait)
        except Exception as e:
            logger.error(f"❌ {label}: Failed to start — {e}")
            return False
    return False


async def main():
    logger.info("🚀 Starting AutoPost Bot...")

    # ── 1. Web server FIRST — health checks pass immediately ───
    await start_web_server(Config.PORT)

    # ── 2. Database ─────────────────────────────────────────────
    db = Database(Config.MONGO_URI)
    await db.connect()
    await db.add_admin(Config.OWNER_ID, "Owner")
    logger.info("✅ Database ready")

    # ── 3. Userbot (optional — missing on first deploy) ────────
    userbot = None
    if Config.SESSION_STRING:
        userbot = TelegramClient(
            StringSession(Config.SESSION_STRING),
            Config.API_ID,
            Config.API_HASH,
        )
        ok = await _start_with_floodwait(userbot, "Userbot")
        if ok:
            me = await userbot.get_me()
            logger.info(
                f"✅ Userbot connected as {me.first_name} (ID {me.id})"
            )
        else:
            logger.warning("⚠️  Userbot failed to connect — disabled")
            userbot = None
    else:
        logger.warning(
            "⚠️  No SESSION_STRING — userbot disabled. "
            "Use /gensession to create one."
        )

    # ── 4. Bot ─────────────────────────────────────────────────
    bot = TelegramClient("bot_session", Config.API_ID, Config.API_HASH)
    ok = await _start_with_floodwait(bot, "Bot", bot_token=Config.BOT_TOKEN)
    if not ok:
        logger.error("❌ Bot failed to start. Exiting.")
        await db.disconnect()
        return

    bot_me = await bot.get_me()
    logger.info(f"✅ Bot connected as @{bot_me.username}")

    # ── 5. Register handlers ───────────────────────────────────
    register_handlers(bot, userbot, db)

    # ── 6. Start posting engine ────────────────────────────────
    poster = None
    if userbot:
        poster = PostingEngine(userbot, db)
        await poster.start()

    logger.info("🟢 Bot is fully running")

    # ── 7. Keep alive until disconnected ───────────────────────
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
    asyncio.run(main())                Config.API_ID,
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
