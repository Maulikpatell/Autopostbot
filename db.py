import logging
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, uri: str):
        self._client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        self._db = self._client["autopost_bot"]

    async def connect(self):
        await self._client.admin.command("ping")
        logger.info("✅ MongoDB connected")

    async def disconnect(self):
        self._client.close()

    # ═══════════════════════════════════════════════════════════
    #  ADMINS
    # ═══════════════════════════════════════════════════════════
    async def add_admin(self, user_id: int, name: str = "Admin"):
        await self._db["admins"].update_one(
            {"user_id": user_id}, {"$set": {"name": name}}, upsert=True
        )

    async def remove_admin(self, user_id: int):
        await self._db["admins"].delete_one({"user_id": user_id})

    async def is_admin(self, user_id: int) -> bool:
        return await self._db["admins"].find_one({"user_id": user_id}) is not None

    async def get_admins(self):
        return await self._db["admins"].find({}).to_list(length=200)

    # ═══════════════════════════════════════════════════════════
    #  SETUPS
    # ═══════════════════════════════════════════════════════════
    async def create_setup(self) -> int:
        counter = await self._db["counters"].find_one_and_update(
            {"_id": "setup_id"},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        sid = counter["seq"]
        await self._db["setups"].insert_one({
            "setup_id": sid,
            "source_channel": None,
            "source_name": "",
            "destinations": [],
            "posting_mode": "copy",
            "link_mode": "keep",
            "replace_link": "",
            "footer": "",
            "time_start": None,
            "time_end": None,
            "loop_enabled": False,
            "is_paused": False,
        })
        return sid

    async def get_setup(self, setup_id: int) -> dict | None:
        return await self._db["setups"].find_one({"setup_id": setup_id})

    async def get_all_setups(self):
        return await self._db["setups"].find({}).sort("setup_id", 1).to_list(length=200)

    async def delete_setup(self, setup_id: int):
        await self._db["setups"].delete_one({"setup_id": setup_id})
        await self._db["post_tracking"].delete_many({"setup_id": setup_id})
        await self._db["daily_counts"].delete_many({"setup_id": setup_id})

    async def update_setup(self, setup_id: int, updates: dict):
        await self._db["setups"].update_one(
            {"setup_id": setup_id}, {"$set": updates}
        )

    # ═══════════════════════════════════════════════════════════
    #  DESTINATIONS (per-setup)
    # ═══════════════════════════════════════════════════════════
    async def add_destination(self, setup_id: int, channel_id: int,
                              channel_name: str, daily_limit: int = 50):
        await self._db["setups"].update_one(
            {"setup_id": setup_id},
            {"$pull": {"destinations": {"channel_id": channel_id}}},
        )
        await self._db["setups"].update_one(
            {"setup_id": setup_id},
            {"$push": {"destinations": {
                "channel_id": channel_id,
                "channel_name": channel_name,
                "daily_limit": daily_limit,
            }}},
        )

    async def remove_destination(self, setup_id: int, channel_id: int):
        await self._db["setups"].update_one(
            {"setup_id": setup_id},
            {"$pull": {"destinations": {"channel_id": channel_id}}},
        )

    async def set_destination_limit(self, setup_id: int,
                                    channel_id: int, limit: int):
        await self._db["setups"].update_one(
            {"setup_id": setup_id, "destinations.channel_id": channel_id},
            {"$set": {"destinations.$.daily_limit": limit}},
        )

    async def dest_exists_in_setup(self, setup_id: int,
                                   channel_id: int) -> bool:
        doc = await self._db["setups"].find_one({
            "setup_id": setup_id,
            "destinations.channel_id": channel_id,
        })
        return doc is not None

    # ═══════════════════════════════════════════════════════════
    #  USER STATE  (selected setup per user)
    # ═══════════════════════════════════════════════════════════
    async def get_selected_setup(self, user_id: int) -> int | None:
        doc = await self._db["user_state"].find_one({"user_id": user_id})
        return doc["selected_setup"] if doc else None

    async def set_selected_setup(self, user_id: int, setup_id: int):
        await self._db["user_state"].update_one(
            {"user_id": user_id},
            {"$set": {"selected_setup": setup_id}},
            upsert=True,
        )

    async def clear_selected_setup(self, user_id: int):
        await self._db["user_state"].delete_one({"user_id": user_id})

    # ═══════════════════════════════════════════════════════════
    #  POST TRACKING  (per-setup + source)
    # ═══════════════════════════════════════════════════════════
    async def get_post_tracking(self, setup_id: int,
                                source_id: int) -> dict:
        doc = await self._db["post_tracking"].find_one({
            "setup_id": setup_id, "source_id": source_id,
        })
        return doc if doc else {}

    async def set_post_tracking(self, setup_id: int, source_id: int,
                                start_id: int, current_id: int):
        await self._db["post_tracking"].update_one(
            {"setup_id": setup_id, "source_id": source_id},
            {"$set": {"start_id": start_id, "current_id": current_id}},
            upsert=True,
        )

    async def delete_post_tracking(self, setup_id: int, source_id: int):
        await self._db["post_tracking"].delete_one({
            "setup_id": setup_id, "source_id": source_id,
        })

    # ═══════════════════════════════════════════════════════════
    #  DAILY COUNTERS  (per-setup + destination)
    # ═══════════════════════════════════════════════════════════
    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async def get_daily_count(self, setup_id: int,
                              channel_id: int) -> int:
        doc = await self._db["daily_counts"].find_one({
            "setup_id": setup_id,
            "channel_id": channel_id,
            "date": Database._today(),
        })
        return doc["count"] if doc else 0

    async def increment_daily_count(self, setup_id: int,
                                    channel_id: int):
        await self._db["daily_counts"].update_one(
            {"setup_id": setup_id, "channel_id": channel_id,
             "date": Database._today()},
            {"$inc": {"count": 1}},
            upsert=True,
                                   )
