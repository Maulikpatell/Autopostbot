import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    API_ID = int(os.environ.get("API_ID", 0))
    API_HASH = os.environ.get("API_HASH", "")
    SESSION_STRING = os.environ.get("SESSION_STRING", "")
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
    MONGO_URI = os.environ.get("MONGO_URI", "")
    OWNER_ID = int(os.environ.get("OWNER_ID", 0))
    PORT = int(os.environ.get("PORT", 8080))

    # Tuning constants
    CHECK_INTERVAL = 10      # seconds between source channel polls
    POST_DELAY = 4           # seconds between posts to different channels
    LOOP_EMPTY_CYCLES = 5    # empty polls before looping restarts
    MAX_FETCH = 30           # max messages fetched per poll cycle
