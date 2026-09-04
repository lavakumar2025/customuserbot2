import asyncio
import json
import os
import re
from datetime import datetime, time, timedelta
from aiohttp import web, ClientSession
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError

# Import credentials and settings from configuser2.py
import configuser2 as cfg

PROCESSED_MSG_IDS = set()
STATE_FILE = "userbot2_state.json"

# Updated tracking state structure
state = {
    "last_processed_id": 177,
    "last_processed_episode": 21,
    "check_episode": 21,
    "qualities_status": {
        "1080p": False,
        "720p": False,
        "480p": False
    },
    "links": {
        "1080p": "",
        "720p": "",
        "480p": ""
    },
    "sent_combinations": [] # e.g. ["E21_1080p", "E21_720p"]
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
    return web.Response(text="Userbot 2 is running healthy!", status=200)


async def start_web_server():
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
    render_url = os.environ.get("RENDER_EXTERNAL_URL") or getattr(cfg, "RENDER_EXTERNAL_URL", None)

    if not render_url:
        print("  [⚠️ KEEP ALIVE] RENDER_EXTERNAL_URL not set. Self-ping inactive.")
        return

    print(f"  [⏰ KEEP ALIVE] Initializing self-ping worker for: {render_url}")

    async with ClientSession() as session:
        while True:
            await asyncio.sleep(300)
            try:
                async with session.get(render_url) as resp:
                    print(f"  [🏓 KEEP ALIVE PING] Ping sent to {render_url} | Status: {resp.status}")
            except Exception as e:
                print(f"  [⚠️ KEEP ALIVE ERROR] Failed to ping URL: {e}")


# ==========================================
# STRICT TITLE + QUALITY EXTRACTION
# ==========================================
# Updated regex pattern to match 'Bigg Boss Agnipariksha S02E21' inside longer filenames
TITLE_PATTERN = re.compile(
    r'Bigg[\s._-]+Boss[\s._-]+Agnipariksha[\s._-]+S(?:0?2)E(\d{1,3})',
    re.IGNORECASE
)

VALID_VIDEO_EXTENSIONS = ('.mkv', '.mp4', '.avi', '.mov', '.webm', '.flv', '.m4v')


def extract_strict_title_info(text):
    if not text:
        return None
    match = TITLE_PATTERN.search(text)
    if match:
        return int(match.group(1))
    return None


def extract_quality(text):
    if not text:
        return None
    for q in cfg.ALLOWED_QUALITIES:
        if re.search(r'\b' + re.escape(q) + r'\b', text, re.IGNORECASE):
            return q
    return None


def has_valid_video_extension(file_name):
    if not file_name:
        return False
    return file_name.lower().endswith(VALID_VIDEO_EXTENSIONS)


client = TelegramClient(
    StringSession(cfg.SESSION_STRING),
    cfg.API_ID,
    cfg.API_HASH,
    auto_reconnect=True,
    connection_retries=10
)


async def log_msg(text):
    print(text)
    if getattr(cfg, 'DEBUG_MODE', False) and getattr(cfg, 'DEBUG_CHANNEL_ID', None):
        try:
            formatted_text = f"<code>{text}</code>"
            await client.send_message(
                entity=cfg.DEBUG_CHANNEL_ID,
                message=formatted_text,
                parse_mode='html'
            )
        except FloodWaitError as e:
            print(f"  [⛔ FLOOD WAIT IN LOG] Pausing debug log for {e.seconds} seconds...")
            await asyncio.sleep(e.seconds)
            try:
                await client.send_message(
                    entity=cfg.DEBUG_CHANNEL_ID,
                    message=formatted_text,
                    parse_mode='html'
                )
            except Exception as err:
                print(f"[⚠️ DEBUG LOG ERROR] Retry failed: {err}")
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
            if message.file.ext.lower() in VALID_VIDEO_EXTENSIONS:
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
        f"  [🎥 QUALITIES STATUS]: {state['qualities_status']}\n"
        f"  [📍 CHECK EPISODE]: E{state.get('check_episode', 0):02d}\n"
        f"  [🎬 LAST PROCESSED EPISODE]: E{state.get('last_processed_episode', 0):02d}\n"
        f"  [📥 MONITORING CHANNEL]: {cfg.TARGET_CHANNEL_ID}\n"
        f"  [📤 DESTINATION CHANNEL]: {cfg.PRIVATE_SOURCE_CHANNEL}\n"
        "=================================================="
        "</code>"
    )
    try:
        await event.reply(status_text, parse_mode='html')
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds)
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
# 3. TARGET CHANNEL MONITORING
# ==========================================
@client.on(events.NewMessage(chats=cfg.TARGET_CHANNEL_ID))
async def target_channel_handler(event):
    if not is_within_time_window():
        return

    message = event.message

    if message.id <= state["last_processed_id"] or message.id in PROCESSED_MSG_IDS:
        return

    if not is_video_file(message):
        return

    file_name = ""
    if message.file and hasattr(message.file, 'name') and message.file.name:
        file_name = message.file.name

    file_name_clean = file_name.strip()
    caption_text = (message.text or "").strip()

    if not has_valid_video_extension(file_name_clean):
        return

    duration_seconds = get_video_duration(message)
    if duration_seconds <= 2700:
        return

    # Extract Episode
    detected_ep = extract_strict_title_info(file_name_clean)
    if detected_ep is None:
        await log_msg(
            f"[⚠️ IGNORED - INVALID FILE TITLE] Message ID {message.id} media filename does not contain "
            f"'Bigg Boss Agnipariksha S02E<digits>'. Filename: '{file_name_clean[:120]}'"
        )
        return

    # Extract Quality
    matched_quality = extract_quality(file_name_clean) or extract_quality(caption_text)
    if not matched_quality:
        return

    check_ep = state.get("check_episode", state.get("last_processed_episode", 0))
    last_ep = state.get("last_processed_episode", 0)

    # Ignore previous episodes lower than current tracking point
    if detected_ep < check_ep:
        await log_msg(f"[⚠️ IGNORED - OLD EPISODE] E{detected_ep:02d} is lower than active check episode E{check_ep:02d}.")
        return

    sent_key = f"E{detected_ep:02d}_{matched_quality}"
    if sent_key in state.get("sent_combinations", []):
        await log_msg(f"[⚠️ IGNORED - DUPLICATE] Combination {sent_key} already sent.")
        return

    # Logical Validation & State Transitions
    if detected_ep == check_ep:
        # Check if active episode already completed all qualities
        if state["qualities_status"].get(matched_quality, False):
            await log_msg(f"[⚠️ IGNORED - QUALITY DONE] E{detected_ep:02d} {matched_quality} is already marked complete.")
            return

    elif detected_ep > check_ep:
        # Check if the existing episode was complete before moving ahead
        all_completed = all(state["qualities_status"].values())
        if not all_completed and last_ep == check_ep:
            await log_msg(
                f"[⚠️ EPISODE SKIPPED/NEW DETECTED] Advanced to E{detected_ep:02d} while E{check_ep:02d} was incomplete. "
                f"Resetting state tracking to E{detected_ep:02d}."
            )

        # Reset states for the new higher episode
        state["check_episode"] = detected_ep
        state["last_processed_episode"] = detected_ep
        state["qualities_status"] = {"1080p": False, "720p": False, "480p": False}
        state["links"] = {"1080p": "", "720p": "", "480p": ""}

    # Execute Delivery
    PROCESSED_MSG_IDS.add(message.id)
    msg_link = get_message_link(event.chat_id, message.id)

    # Update current tracking metadata
    state["last_processed_id"] = message.id
    state["qualities_status"][matched_quality] = True
    state["links"][matched_quality] = msg_link
    state.setdefault("sent_combinations", []).append(sent_key)

    # Auto-advance check_episode if current episode has completed all qualities
    if all(state["qualities_status"].values()):
        await log_msg(f"[🎉 EPISODE COMPLETE] E{detected_ep:02d} has all qualities (1080p, 720p, 480p). Incrementing check_episode.")
        state["check_episode"] = detected_ep + 1
        state["qualities_status"] = {"1080p": False, "720p": False, "480p": False}
        state["links"] = {"1080p": "", "720p": "", "480p": ""}

    save_state()

    # Forward Link to Private Target Channel
    try:
        await client.send_message(
            entity=cfg.PRIVATE_SOURCE_CHANNEL,
            message=msg_link
        )
        await log_msg(f"  [✅ SENT] Direct link forwarded for E{detected_ep:02d} {matched_quality}: {msg_link}")
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds)
        await client.send_message(
            entity=cfg.PRIVATE_SOURCE_CHANNEL,
            message=msg_link
        )
        await log_msg(f"  [✅ SENT] Direct link forwarded after FloodWait pause.")
    except Exception as e:
        await log_msg(f"  [💥 ERROR] Failed to send link: {e}")


async def main():
    await start_web_server()
    await client.start()

    init_summary = (
        "==================================================\n"
        "  [🤖 USERBOT 2 INITIALIZED] Ready\n"
        f"  [📍 CHECK EPISODE]: E{state.get('check_episode', 0):02d}\n"
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
