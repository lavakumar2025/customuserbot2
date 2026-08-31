import os

# ==========================================
# TELEGRAM API CREDENTIALS
# Get API_ID and API_HASH from https://my.telegram.org
# Generate SESSION_STRING using Telethon StringSession generator
# ==========================================
API_ID = int(os.environ.get("API_ID", 1234567))
API_HASH = os.environ.get("API_HASH", "your_api_hash_here")
SESSION_STRING = os.environ.get("SESSION_STRING", "your_telethon_string_session_here")

# ==========================================
# CHANNEL CONFIGURATIONS
# TARGET_CHANNEL_ID: Public/Private channel userbot2 monitors for video links
# PRIVATE_SOURCE_CHANNEL: The destination channel where userbot2 forwards links
# DEBUG_CHANNEL_ID: Channel where /status and log messages are sent
# ==========================================
TARGET_CHANNEL_ID = int(os.environ.get("TARGET_CHANNEL_ID", -1001234567890))
PRIVATE_SOURCE_CHANNEL = int(os.environ.get("PRIVATE_SOURCE_CHANNEL", -1009876543210))
DEBUG_CHANNEL_ID = int(os.environ.get("DEBUG_CHANNEL_ID", -1001122334455))

# ==========================================
# FILTER & SEARCH SETTINGS
# SEARCH_KEYWORD: Case-insensitive keyword to match in title/caption
# ALLOWED_QUALITIES: List of video qualities to look for
# ==========================================
SEARCH_KEYWORD = os.environ.get("SEARCH_KEYWORD", "Bigg Boss")
ALLOWED_QUALITIES = os.environ.get("ALLOWED_QUALITIES", "1080p,720p,480p").split(",")

# ==========================================
# RUN MODE & TIME WINDOW
# RUN_MODE: 'LIVE' (24/7 processing) or 'TIMED' (only process between START_TIME & END_TIME)
# ==========================================
RUN_MODE = os.environ.get("RUN_MODE", "LIVE")  # Options: 'LIVE' or 'TIMED'
START_TIME = os.environ.get("START_TIME", "18:00")  # HH:MM format (24-hour)
END_TIME = os.environ.get("END_TIME", "23:59")    # HH:MM format (24-hour)

# ==========================================
# DEBUG & SERVER SETTINGS
# ==========================================
DEBUG_MODE = os.environ.get("DEBUG_MODE", "True").lower() in ("true", "1", "t")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "")  # e.g., "https://userbot2.onrender.com"