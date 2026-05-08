import re
import asyncio
import logging
from datetime import datetime, timezone
from telethon.errors import (
    FloodWaitError, ChannelPrivateError,
    ChatWriteForbiddenError, BadRequestError,
)
from config import Config
from db import Database

logger = logging.getLogger(__name__)


class PostingEngine:
    def __init__(self, userbot, db: Database):
        self.userbot = userbot
        self.db = db
        self._tasks: dict[int, asyncio.Task] = {}
        self._empty_cycles: dict[int, int] = {}

    # ── Lifecycle ───────────────────────────────────────────────
    async def start(self):
        setups = await self.db.get_all_setups()
        for s in setups:
            if not s.get("is_paused") and s.get("source_channel"):
                await self._start_setup(s["setup_id"])
        logger.info(f"🔁 Posting engine started ({len(self._tasks)} active setups)")

    async def start_setup(self, setup_id: int):
        await self._start_setup(setup_id)

    async def stop_setup(self, setup_id: int):
        await self._stop_setup(setup_id)

    async def _start_setup(self, setup_id: int):
        if setup_id in self._tasks:
            return
        self._empty_cycles[setup_id] = 0
        self._tasks[setup_id] = asyncio.create_task(
            self._loop(setup_id), name=f"setup-{setup_id}"
        )
        logger.info(f"▶️ Setup #{setup_id} engine started")

    async def _stop_setup(self, setup_id: int):
        task = self._tasks.pop(setup_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._empty_cycles.pop(setup_id, None)
        logger.info(f"⏹ Setup #{setup_id} engine stopped")

    async def stop(self):
        for sid in list(self._tasks):
            await self._stop_setup(sid)
        logger.info("⏹ All posting engines stopped")

    # ── Per-Setup Loop ──────────────────────────────────────────
    async def _loop(self, setup_id: int):
        await asyncio.sleep(2)
        while True:
            try:
                setup = await self.db.get_setup(setup_id)
                if not setup:
                    logger.warning(f"Setup #{setup_id} deleted, stopping loop")
                    break
                if setup.get("is_paused"):
                    await asyncio.sleep(5)
                    continue
                await self._tick(setup)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Setup #{setup_id} tick error: {exc}", exc_info=True)
            await asyncio.sleep(Config.CHECK_INTERVAL)

    async def _tick(self, setup: dict):
        source_id = setup.get("source_channel")
        if not source_id:
            return
        if not self._in_time_window(setup):
            return

        messages = await self._fetch_new(setup["setup_id"], source_id, setup)
        if not messages:
            return

        destinations = setup.get("destinations", [])
        if not destinations:
            return

        for msg in messages:
            # Skip service messages and empty messages
            if msg.action or (not msg.text and not msg.media and not msg.grouped_media):
                continue
            for dest in destinations:
                try:
                    limit = dest.get("daily_limit", 50)
                    current = await self.db.get_daily_count(
                        setup["setup_id"], dest["channel_id"]
                    )
                    if current >= limit:
                        continue
                    await self._post(msg, setup, dest["channel_id"])
                    await asyncio.sleep(Config.POST_DELAY)
                except FloodWaitError as e:
                    logger.warning(
                        f"FloodWait {e.seconds}s — setup #{setup['setup_id']}"
                    )
                    await asyncio.sleep(e.seconds)
                except (ChannelPrivateError, ChatWriteForbiddenError) as e:
                    logger.error(f"Cannot write to {dest['channel_id']}: {e}")
                except BadRequestError as e:
                    logger.error(f"Bad request posting to {dest['channel_id']}: {e}")
                except Exception as e:
                    logger.error(f"Post error to {dest['channel_id']}: {e}")

    # ── Fetch Messages ──────────────────────────────────────────
    async def _fetch_new(self, setup_id: int, source_id: int,
                         setup: dict) -> list:
        loop_on = setup.get("loop_enabled", False)
        tracking = await self.db.get_post_tracking(setup_id, source_id)

        if not tracking:
            # First time — seed pointer
            try:
                latest = await self.userbot.get_messages(source_id, limit=1)
                if latest and latest[0]:
                    if loop_on:
                        earliest = await self._find_earliest(source_id)
                        start = earliest if earliest else latest[0].id
                        await self.db.set_post_tracking(
                            setup_id, source_id, start, start - 1
                        )
                    else:
                        await self.db.set_post_tracking(
                            setup_id, source_id,
                            latest[0].id, latest[0].id
                        )
            except Exception as e:
                logger.error(f"Failed to seed tracking for setup #{setup_id}: {e}")
            return []

        start_id = tracking["start_id"]
        current_id = tracking["current_id"]

        messages = await self.userbot.get_messages(
            source_id, min_id=current_id, limit=Config.MAX_FETCH
        )
        messages = [m for m in reversed(list(messages)) if m.id > current_id]

        if not messages and loop_on:
            self._empty_cycles[setup_id] = self._empty_cycles.get(setup_id, 0) + 1
            if self._empty_cycles[setup_id] >= Config.LOOP_EMPTY_CYCLES:
                logger.info(f"🔄 Setup #{setup_id}: looping to start")
                await self.db.set_post_tracking(
                    setup_id, source_id, start_id, start_id - 1
                )
                self._empty_cycles[setup_id] = 0
        elif messages:
            self._empty_cycles[setup_id] = 0
            await self.db.set_post_tracking(
                setup_id, source_id, start_id, messages[-1].id
            )

        return messages

    async def _find_earliest(self, source_id: int) -> int | None:
        try:
            msgs = await self.userbot.get_messages(source_id, limit=1, min_id=0)
            if msgs and msgs[0]:
                return msgs[0].id
        except Exception:
            pass
        return None

    # ── Time Window ─────────────────────────────────────────────
    @staticmethod
    def _in_time_window(setup: dict) -> bool:
        start = setup.get("time_start")
        end = setup.get("time_end")
        if start is None or end is None:
            return True
        now = datetime.now(timezone.utc).hour
        if start <= end:
            return start <= now < end
        return now >= start or now < end

    # ── Post Single Message ─────────────────────────────────────
    async def _post(self, message, setup: dict, dest_id: int):
        mode = setup.get("posting_mode", "copy")
        caption = self._build_caption(message, setup)

        if mode == "forward":
            await message.forward_to(dest_id)

        elif mode == "copy":
            # Handle albums (grouped media)
            if message.grouped_media:
                await self.userbot.send_file(
                    dest_id,
                    file=message.grouped_media,
                    caption=caption,
                    parse_mode="html",
                )
            elif message.media:
                await self.userbot.send_file(
                    dest_id,
                    file=message.media,
                    caption=caption,
                    parse_mode="html",
                )
            elif caption:
                await self.userbot.send_message(
                    dest_id, caption, parse_mode="html"
                )

        elif mode == "text_only":
            text = message.text or ""
            text = self._process_links(text, setup)
            if setup.get("footer"):
                text += f"\n\n{setup['footer']}"
            if text.strip():
                await self.userbot.send_message(
                    dest_id, text, parse_mode="html"
                )

        await self.db.increment_daily_count(setup["setup_id"], dest_id)

    # ── Caption Builder ─────────────────────────────────────────
    def _build_caption(self, message, setup: dict) -> str:
        text = message.text or ""
        text = self._process_links(text, setup)
        footer = setup.get("footer", "")
        if footer:
            text = f"{text}\n\n{footer}" if text.strip() else footer
        return text.strip()

    # ── Link Processing ─────────────────────────────────────────
    @staticmethod
    def _process_links(text: str, setup: dict) -> str:
        mode = setup.get("link_mode", "keep")
        if mode == "remove":
            text = re.sub(r"https?://t\.me/\S+", "", text)
            text = re.sub(r"t\.me/\S+", "", text)
        elif mode == "replace":
            repl = setup.get("replace_link", "")
            if repl:
                text = re.sub(r"https?://t\.me/\S+", repl, text)
                text = re.sub(r"t\.me/\S+", repl, text)
        return text
