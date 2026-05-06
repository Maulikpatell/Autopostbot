import re
import asyncio
import logging
from datetime import datetime, timezone
from telethon.errors import FloodWaitError, ChannelPrivateError, ChatWriteForbiddenError
from config import Config
from db import Database

logger = logging.getLogger(__name__)


class PostingEngine:
    def __init__(self, userbot, db: Database):
        self.userbot = userbot
        self.db = db
        self._running = False
        self._task = None
        self._empty_cycles = 0

    # ── Lifecycle ───────────────────────────────────────────────
    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("🔁 Posting engine started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("⏹ Posting engine stopped")

    # ── Main Loop ───────────────────────────────────────────────
    async def _loop(self):
        await asyncio.sleep(3)  # brief settle time after startup
        while self._running:
            try:
                await self._tick()
            except Exception as exc:
                logger.error(f"Error in posting tick: {exc}", exc_info=True)
            await asyncio.sleep(Config.CHECK_INTERVAL)

    async def _tick(self):
        settings = await self.db.get_settings()
        source_id = settings.get("source_channel")
        if not source_id:
            return
        if settings.get("is_paused", False):
            return
        if not self._in_time_window(settings):
            return

        messages = await self._fetch_new(source_id, settings)
        if not messages:
            return

        destinations = await self.db.get_active_channels()
        if not destinations:
            return

        for msg in messages:
            if msg.action or (not msg.text and not msg.media):
                continue
            for dest in destinations:
                try:
                    limit = dest.get("daily_limit", 50)
                    current = await self.db.get_daily_count(dest["channel_id"])
                    if current >= limit:
                        continue
                    await self._post(msg, dest["channel_id"], settings)
                    await asyncio.sleep(Config.POST_DELAY)
                except FloodWaitError as e:
                    logger.warning(f"FloodWait {e.seconds}s for {dest['channel_id']}")
                    await asyncio.sleep(e.seconds)
                except (ChannelPrivateError, ChatWriteForbiddenError) as e:
                    logger.error(f"Cannot write to {dest['channel_id']}: {e}")
                except Exception as e:
                    logger.error(f"Post error to {dest['channel_id']}: {e}")

    # ── Fetch Messages ──────────────────────────────────────────
    async def _fetch_new(self, source_id: int, settings: dict) -> list:
        loop_enabled = settings.get("loop_enabled", False)
        tracking = await self.db.get_post_tracking(source_id)

        if not tracking:
            # First time seeing this source — seed the pointer
            latest = await self.userbot.get_messages(source_id, limit=1)
            if latest and latest[0]:
                if loop_enabled:
                    # For loop mode, try to find the earliest post
                    earliest = await self._find_earliest(source_id)
                    start_id = earliest if earliest else latest[0].id
                    await self.db.set_post_tracking(source_id, start_id, start_id - 1)
                else:
                    # Live mode — start after latest so only NEW posts trigger
                    await self.db.set_post_tracking(source_id, latest[0].id, latest[0].id)
            return []

        start_id = tracking["start_id"]
        current_id = tracking["current_id"]

        messages = await self.userbot.get_messages(
            source_id, min_id=current_id, limit=Config.MAX_FETCH
        )
        messages = [m for m in reversed(list(messages)) if m.id > current_id]

        if not messages and loop_enabled:
            self._empty_cycles += 1
            if self._empty_cycles >= Config.LOOP_EMPTY_CYCLES:
                logger.info("🔄 Loop mode: restarting from beginning")
                await self.db.set_post_tracking(source_id, start_id, start_id - 1)
                self._empty_cycles = 0
        elif messages:
            self._empty_cycles = 0
            await self.db.set_post_tracking(source_id, start_id, messages[-1].id)

        return messages

    async def _find_earliest(self, source_id: int) -> int | None:
        """Try to find the earliest message ID in a channel."""
        try:
            msgs = await self.userbot.get_messages(source_id, limit=1, min_id=0)
            if msgs and msgs[0]:
                return msgs[0].id
        except Exception:
            pass
        return None

    # ── Time Window ─────────────────────────────────────────────
    def _in_time_window(self, settings: dict) -> bool:
        start = settings.get("time_start")
        end = settings.get("time_end")
        if start is None or end is None:
            return True
        now = datetime.now(timezone.utc).hour
        if start <= end:
            return start <= now < end
        else:  # crosses midnight e.g. 22-6
            return now >= start or now < end

    # ── Post Single Message ─────────────────────────────────────
    async def _post(self, message, dest_id: int, settings: dict):
        mode = settings.get("posting_mode", "copy")
        caption = self._build_caption(message, settings)

        if mode == "forward":
            await message.forward_to(dest_id)
        elif mode == "copy":
            if message.media:
                await self.userbot.send_file(
                    dest_id, message.media, caption=caption, parse_mode="html"
                )
            elif caption:
                await self.userbot.send_message(dest_id, caption, parse_mode="html")
        elif mode == "text_only":
            text = message.text or ""
            text = self._process_links(text, settings)
            if settings.get("footer"):
                text += f"\n\n{settings['footer']}"
            if text.strip():
                await self.userbot.send_message(dest_id, text, parse_mode="html")

        await self.db.increment_daily_count(dest_id)

    # ── Caption Builder ─────────────────────────────────────────
    def _build_caption(self, message, settings: dict) -> str:
        text = message.text or ""
        text = self._process_links(text, settings)
        footer = settings.get("footer", "")
        if footer:
            text = f"{text}\n\n{footer}" if text.strip() else footer
        return text.strip()

    # ── Link Processing ─────────────────────────────────────────
    @staticmethod
    def _process_links(text: str, settings: dict) -> str:
        mode = settings.get("link_mode", "keep")
        if mode == "remove":
            text = re.sub(r"https?://t\.me/\S+", "", text)
            text = re.sub(r"t\.me/\S+", "", text)
        elif mode == "replace":
            replacement = settings.get("replace_link", "")
            if replacement:
                text = re.sub(r"https?://t\.me/\S+", replacement, text)
                text = re.sub(r"t\.me/\S+", replacement, text)
        return text
