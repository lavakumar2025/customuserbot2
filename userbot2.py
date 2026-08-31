import asyncio
import json
import os
import re
from datetime import datetime, time, timedelta
from aiohttp import web, ClientSession
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# Import credentials and settings from configuser2.py
import configuser2 as cfg

PROCESSED_MSG_IDS = set()
STATE_FILE = "userbot2_state.json"

# Default tracking state structure
state = {
    "last_processed_id": 177,         # Default starting boundary ID
    "last_processed_episode": 17,     # Stores highest episode processed
    "links": {
        "1080p": "",
        "720p": "",
        "480p": ""
    }
}

# Load saved state if available
if os.path.exists(STATE_FILE):
    try:
        with open(STATE_FILE, "r") as f:
            state.update(json.load(f))
    except Exception as e:
        print(f"[⚠️ STATE LOAD ERROR] Could not load state: {e}")


def save_state():
    """Saves tracking state to a local JSON file."""
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        print(f"[⚠️ STATE SAVE ERROR] Could not save state: {e}")


# ==========================================
# 0. RENDER HEALTH CHECK WEB SERVER & KEEP ALIVE
# ==========================================
async def handle_health_check(request):
    """Responds with 200 OK to keep hosting service health checks active."""
    return web.Response(text="Userbot 2 is running healthy!", status=200)


async def start_web_server():
    """Starts a minimal asynchronous HTTP server for port checks."""
    app = web.Application()
    app.router.add_get('/', handle_health_check)
    app.router.add_get('/health', handle_health_check)

    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"  [🌐 WEB SERVER] Userbot 2 health check listening on port {port}")


async def self_ping_task():
    """Pings the app's own URL every 5 minutes to prevent sleep."""
    render_url = os.environ.get("RENDER_EXTERNAL_URL") or getattr(cfg, "RENDER_EXTERNAL_URL", None)

    if not render_url:
        print("  [⚠️ KEEP ALIVE] RENDER_EXTERNAL_URL not set. Self-ping inactive.")
        return

    print(f"  [⏰ KEEP ALIVE] Initializing self-ping worker for: {render_url}")

    async with ClientSession() as session:
        while True:
            await asyncio.sleep(300)  # Ping every 5 minutes
            try:
                async with session.get(render_url) as resp:
                    print(f"  [🏓 KEEP ALIVE PING] Ping sent to {render_url} | Status: {resp.status}")
            except Exception as e:
                print(f"  [⚠️ KEEP ALIVE ERROR] Failed to ping URL: {e}")


# ==========================================
# STRICT TITLE + QUALITY EXTRACTION
# ==========================================
# Matches: Bigg Boss Agnipariksha S02E<digits>  (allows space, dot, underscore, dash as separators)
TITLE_PATTERN = re.compile(
    r'\bBigg[\s._-]+Boss[\s._-]+Agnipariksha[\s._-]+S(?:0?2)E(\d{1,3})\b',
    re.IGNORECASE
)

VALID_VIDEO_EXTENSIONS = ('.mkv', '.mp4', '.avi', '.mov', '.webm', '.flv', '.m4v')


def extract_strict_title_info(text):
    """
    STRICT CHECK:
    - Requires exact format: Bigg Boss Agnipariksha S02E<digits> or S2E<digits>
    - Uses strict word boundaries (\\b) so 'S02 480p' is NOT misread as Episode 480.
    - Allows space/dot/underscore/dash between words to match real filenames like
      'Bigg.Boss.Agnipariksha.S02E18.480p...'

    Correct Match:
      - 'Bigg Boss Agnipariksha S02E18 480p JHS WEB-DL ....mkv' -> Episode 18
    """
    match = TITLE_PATTERN.search(text)
    if match:
        return int(match.group(1))
    return None


def extract_quality(text):
    """
    Returns the quality tag (e.g. '720p') found in text, using strict word
    boundaries so '1720p' or 'x480p' style false matches are avoided.
    Returns None if no allowed quality tag is present.
    """
    for q in cfg.ALLOWED_QUALITIES:
        if re.search(r'\b' + re.escape(q) + r'\b', text, re.IGNORECASE):
            return q
    return None


def has_valid_video_extension(file_name):
    """Confirms the filename ends with a known video extension."""
    if not file_name:
        return False
    return file_name.lower().endswith(VALID_VIDEO_EXTENSIONS)


# Initialize Telethon client
client = TelegramClient(
    StringSession(cfg.SESSION_STRING),
    cfg.API_ID,
    cfg.API_HASH,
    auto_reconnect=True,
    connection_retries=10
)


async def log_msg(text):
    """Prints status to terminal, and forwards log to DEBUG_CHANNEL_ID."""
    print(text)
    if getattr(cfg, 'DEBUG_MODE', False) and getattr(cfg, 'DEBUG_CHANNEL_ID', None):
        try:
            formatted_text = f"<code>{text}</code>"
            await client.send_message(
                entity=cfg.DEBUG_CHANNEL_ID,
                message=formatted_text,
                parse_mode='html'
            )
        except Exception as e:
            print(f"[⚠️ DEBUG LOG ERROR] Failed to send log to debug channel: {e}")


def is_within_time_window():
    if cfg.RUN_MODE == 'LIVE':
        return True

    now = datetime.now().time()
    start = datetime.strptime(cfg.START_TIME, "%H:%M").time()
    end = datetime.strptime(cfg.END_TIME, "%H:%M").time()

    if start > end:
        return now >= start or now <= end
    else:
        return start <= now <= end


def get_message_link(channel_id, message_id):
    clean_id = str(channel_id)
    if clean_id.startswith("-100"):
        clean_id = clean_id[4:]
    elif clean_id.startswith("-"):
        clean_id = clean_id[1:]
    return f"https://t.me/c/{clean_id}/{message_id}"


def is_video_file(message):
    if message.video:
        return True
    if message.document:
        mime_type = getattr(message.document, 'mime_type', '') or ''
        if mime_type.startswith('video/'):
            return True
        if message.file and hasattr(message.file, 'ext') and message.file.ext:
            video_extensions = ['.mkv', '.mp4', '.avi', '.mov', '.webm', '.flv', '.m4v']
            if message.file.ext.lower() in video_extensions:
                return True
    return False


def get_video_duration(message):
    if message.file and hasattr(message.file, 'duration') and message.file.duration:
        return message.file.duration
    return 0


# ==========================================
# 1. COMMAND LISTENER: /status in Debug Channel
# ==========================================
@client.on(events.NewMessage(chats=cfg.DEBUG_CHANNEL_ID, pattern=r'^/status$'))
async def status_command_handler(event):
    status_text = (
        "<b>Working fine userbot2</b>\n\n"
        "<code>"
        "==================================================\n"
        "  [🤖 USERBOT 2 INITIALIZED] Debug Logging Active\n"
        f"  [⚙️ RUN MODE]: {cfg.RUN_MODE} ({cfg.START_TIME} to {cfg.END_TIME})\n"
        "  [🔍 STRICT TITLE PATTERN]: 'Bigg Boss Agnipariksha S02E<digits>'\n"
        f"  [🎥 ALLOWED QUALITIES]: {cfg.ALLOWED_QUALITIES}\n"
        f"  [⏳ MIN DURATION]: > 45 minutes (2700s)\n"
        f"  [📍 LAST PROCESSED ID BOUNDARY]: > {state['last_processed_id']}\n"
        f"  [🎬 LAST PROCESSED EPISODE]: E{state.get('last_processed_episode', 0):02d}\n"
        f"  [📥 MONITORING CHANNEL]: {cfg.TARGET_CHANNEL_ID}\n"
        f"  [📤 DESTINATION CHANNEL]: {cfg.PRIVATE_SOURCE_CHANNEL}\n"
        f"  [📦 PROCESSED MSG COUNT]: {len(PROCESSED_MSG_IDS)}\n"
        "=================================================="
        "</code>"
    )
    await event.reply(status_text, parse_mode='html')


# ==========================================
# 2. AUTOMATIC MEMORY CLEANUP TASK (00:31 AM)
# ==========================================
async def auto_clear_memory_task():
    while True:
        now = datetime.now()
        target_time = now.replace(hour=0, minute=31, second=0, microsecond=0)
        if now >= target_time:
            target_time += timedelta(days=1)

        seconds_until_cleanup = (target_time - datetime.now()).total_seconds()
        await asyncio.sleep(seconds_until_cleanup)

        cleared_count = len(PROCESSED_MSG_IDS)
        PROCESSED_MSG_IDS.clear()
        await log_msg(f"[🧹 MEMORY CLEANUP] Cleared {cleared_count} IDs at 00:31 AM.")


# ==========================================
# 3. TARGET CHANNEL MONITORING (FIXED & HARDENED)
# ==========================================
@client.on(events.NewMessage(chats=cfg.TARGET_CHANNEL_ID))
async def target_channel_handler(event):
    if not is_within_time_window():
        current_time_str = datetime.now().strftime("%H:%M:%S")
        await log_msg(f"[⏰ OUTSIDE WINDOW] Message received at {current_time_str}. Ignoring...")
        return

    message = event.message

    # 1. Message ID Boundary Check
    if message.id <= state["last_processed_id"]:
        return

    # 2. Duplicate Processing Check
    if message.id in PROCESSED_MSG_IDS:
        return

    # 3. Must be a valid Video File
    if not is_video_file(message):
        return

    # 4. File Extension Check (extra hard gate — rejects non-video containers
    #    even if Telegram mis-tags the mime type)
    file_name = ""
    if message.file and hasattr(message.file, 'name') and message.file.name:
        file_name = message.file.name

    if not has_valid_video_extension(file_name):
        await log_msg(
            f"[⚠️ IGNORED - BAD EXTENSION] Message ID {message.id} filename '{file_name}' "
            f"has no valid video extension. Skipping..."
        )
        return

    # 5. Duration Check (> 45 mins / 2700s)
    duration_seconds = get_video_duration(message)
    duration_minutes = round(duration_seconds / 60, 2)

    if duration_seconds <= 2700:
        await log_msg(
            f"[⏱️ IGNORED - SHORT DURATION] Message ID {message.id} Duration: {duration_minutes} mins ({duration_seconds}s). Skipping..."
        )
        return

    # Consolidate Text & File Name
    message_text = message.text or ""
    full_text = f"{message_text} {file_name}".strip()

    # 6. STRICT TITLE CHECK (Hard Barrier)
    # If full_text does not contain 'Bigg Boss Agnipariksha S02E<digits>',
    # extract_strict_title_info returns None and we STOP here.
    detected_ep = extract_strict_title_info(full_text)
    if detected_ep is None:
        await log_msg(
            f"[⚠️ IGNORED - INVALID TITLE] Message ID {message.id} does not contain "
            f"'Bigg Boss Agnipariksha S02E<digits>'. Full text: '{full_text[:120]}'"
        )
        return

    # 7. Quality Match Check (1080p, 720p, 480p)
    matched_quality = extract_quality(full_text)
    if not matched_quality:
        await log_msg(
            f"[⚠️ IGNORED - NO QUALITY TAG] Strict title matched E{detected_ep:02d}, but missing "
            f"resolution tag (1080p/720p/480p). Full text: '{full_text[:120]}'"
        )
        return

    # 8. Episode Boundary Check
    last_ep = state.get("last_processed_episode", 0)
    if detected_ep < last_ep:
        await log_msg(
            f"[⚠️ IGNORED - OLD EPISODE] Detected E{detected_ep:02d} is lower than last processed E{last_ep:02d}."
        )
        return

    # --- ALL FILTERS PASSED ---
    # Debug: log the EXACT text that triggered a match, so any future false
    # positive can be diagnosed immediately from the debug channel history.
    await log_msg(
        f"[🔎 TITLE MATCHED] Message ID {message.id} | Episode E{detected_ep:02d} | "
        f"Quality {matched_quality} | Full text: '{full_text}'"
    )

    PROCESSED_MSG_IDS.add(message.id)
    msg_link = get_message_link(event.chat_id, message.id)

    # Update state tracking variables
    state["last_processed_id"] = message.id
    state["last_processed_episode"] = max(last_ep, detected_ep)
    state["links"][matched_quality] = msg_link

    save_state()

    log_summary = (
        "==================================================\n"
        f"  [🎥 VIDEO DETECTED] Processing Message ID: {message.id}\n"
        f"  [⏱️ DURATION]: {duration_minutes} mins ({duration_seconds}s)\n"
        f"  [🎬 EXTRACTED EPISODE]: E{detected_ep:02d}\n"
        f"  [📺 QUALITY MATCH]: {matched_quality}\n"
        f"  [🔗 LINK]: {msg_link}\n"
        "=================================================="
    )
    await log_msg(log_summary)

    try:
        # Send ONLY the link to private destination channel
        await client.send_message(
            entity=cfg.PRIVATE_SOURCE_CHANNEL,
            message=msg_link
        )

        await log_msg("  [✅ SENT] Direct video link forwarded to private source channel!\n")

    except Exception as e:
        await log_msg(f"  [💥 ERROR] Failed to send link: {e}\n")


async def main():
    # Start Web Server for Render/Koyeb health check
    await start_web_server()

    await client.start()

    init_summary = (
        "==================================================\n"
        "  [🤖 USERBOT 2 INITIALIZED] Ready on New Host\n"
        f"  [⚙️ RUN MODE]: {cfg.RUN_MODE} ({cfg.START_TIME} to {cfg.END_TIME})\n"
        "  [🔍 STRICT TITLE PATTERN]: 'Bigg Boss Agnipariksha S02E<digits>'\n"
        f"  [🎥 ALLOWED QUALITIES]: {cfg.ALLOWED_QUALITIES}\n"
        f"  [⏳ MIN DURATION]: > 45 minutes (2700s)\n"
        f"  [📍 THRESHOLD ID]: > {state['last_processed_id']}\n"
        f"  [🎬 LAST PROCESSED EPISODE]: E{state.get('last_processed_episode', 0):02d}\n"
        f"  [📥 MONITORING CHANNEL]: {cfg.TARGET_CHANNEL_ID}\n"
        f"  [📤 DESTINATION CHANNEL]: {cfg.PRIVATE_SOURCE_CHANNEL}\n"
        "=================================================="
    )
    await log_msg(init_summary)

    asyncio.create_task(auto_clear_memory_task())
    asyncio.create_task(self_ping_task())

    await client.run_until_disconnected()


if __name__ == '__main__':
    asyncio.run(main())
