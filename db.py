import logging
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, uri: str):
        self._client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        self._db = self._client["autopost_bot"]

    # ── Connection ──────────────────────────────────────────────
    async def connect(self):
        await self._client.admin.command("ping")
        logger.info("✅ MongoDB connected")

    async def disconnect(self):
        self._client.close()

    # ── Settings ────────────────────────────────────────────────
    async def get_settings(self) -> dict:
        doc = await self._db["settings"].find_one({"_id": "config"})
        return doc if doc else {}

    async def update_settings(self, updates: dict):
        await self._db["settings"].update_one(
            {"_id": "config"}, {"$set": updates}, upsert=True
        )

    # ── Admins ──────────────────────────────────────────────────
    async def add_admin(self, user_id: int, name: str = "Admin"):
        await self._db["admins"].update_one(
            {"user_id": user_id}, {"$set": {"name": name}}, upsert=True
        )

    async def remove_admin(self, user_id: int):
        await self._db["admins"].delete_one({"user_id": user_id})

    async def is_admin(self, user_id: int) -> bool:
        return await self._db["admins"].find_one({"user_id": user_id}) is not None

    async def get_admins(self) -> list[dict]:
        return await self._db["admins"].find({}).to_list(length=200)

    # ── Destination Channels ────────────────────────────────────
    async def add_channel(self, channel_id: int, channel_name: str, daily_limit: int = 50):
        await self._db["channels"].update_one(
            {"channel_id": channel_id},
            {"$set": {"channel_name": channel_name, "daily_limit": daily_limit, "is_active": True}},
            upsert=True,
        )

    async def remove_channel(self, channel_id: int):
        await self._db["channels"].delete_one({"channel_id": channel_id})

    async def get_active_channels(self) -> list[dict]:
        return await self._db["channels"].find({"is_active": True}).to_list(length=200)

    async def get_all_channels(self) -> list[dict]:
        return await self._db["channels"].find({}).to_list(length=200)

    async def set_channel_limit(self, channel_id: int, limit: int):
        await self._db["channels"].update_one(
            {"channel_id": channel_id}, {"$set": {"daily_limit": limit}}
        )

    async def channel_exists(self, channel_id: int) -> bool:
        return await self._db["channels"].find_one({"channel_id": channel_id}) is not None

    # ── Post Tracking ───────────────────────────────────────────
    async def get_post_tracking(self, source_id: int) -> dict:
        doc = await self._db["post_tracking"].find_one({"source_id": source_id})
        return doc if doc else {}

    async def set_post_tracking(self, source_id: int, start_id: int, current_id: int):
        await self._db["post_tracking"].update_one(
            {"source_id": source_id},
            {"$set": {"start_id": start_id, "current_id": current_id}},
            upsert=True,
        )

    async def delete_post_tracking(self, source_id: int):
        await self._db["post_tracking"].delete_one({"source_id": source_id})

    # ── Daily Counters ──────────────────────────────────────────
    def _today_key(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async def get_daily_count(self, channel_id: int) -> int:
        doc = await self._db["daily_counts"].find_one(
            {"channel_id": channel_id, "date": self._today_key()}
        )
        return doc["count"] if doc else 0

    async def increment_daily_count(self, channel_id: int):
        await self._db["daily_counts"].update_one(
            {"channel_id": channel_id, "date": self._today_key()},
            {"$inc": {"count": 1}},
            upsert=True,
        )
