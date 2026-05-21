import os
import time
import math
import asyncio
import threading
import humanize
import glob
from flask import Flask

# --- CRITICAL EVENT LOOP FIX FOR PYTHON 3.11+ ---
try:
    asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

from pyrogram import Client, filters
from pyrogram.errors import FloodWait

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

bot = Client("splitter_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
app = Flask(__name__)

# --- GLOBAL TASK MANAGER ---
# Keeps track of which user is processing a file so we can cancel it
active_tasks = {}

# --- WEB SERVER (KEEPS RENDER AWAKE) ---
@app.route('/')
def health(): return "Bot is Alive", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- UI & PROGRESS HELPERS ---
def get_progress_bar(current, total):
    percentage = (current * 100 / total) if total > 0 else 0
    finished = int(percentage / 10)
    return "⬛" * finished + "⬜" * (10 - finished) + f" {percentage:.1f}%"

async def progress_func(current, total, message, start_time, action, user_id):
    # THE KILL SWITCH: If the user typed /stop, this raises an error to abort the download/upload
    if not active_tasks.get(user_id, False):
        raise Exception("CANCELLED_BY_USER")

    now = time.time()
    diff = now - start_time
    if diff < 2.0: return # Prevents Telegram FloodWait error
    
    speed = current / diff if diff > 0 else 0
    bar = get_progress_bar(current, total)
    msg = (f"**{action}**\n`{bar}`\n🚀 **Speed:** {humanize.naturalsize(speed)}/s\n📂 **Done:** {humanize.naturalsize(current)} / {humanize.naturalsize(total)}")
    
    try: await message.edit(msg)
    except: pass

async def get_duration(file_path):
    # ASYNC FFPROBE - Prevents the bot from freezing
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file_path]
    process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await process.communicate()
    return float(stdout.decode().strip()) if stdout else 0

def cleanup_files(uid):
    # Deletes all files related to a specific task ID
    for f in glob.glob(f"vid_{uid}_*"):
        try: os.remove(f)
        except: pass

# --- COMMANDS ---
@bot.on_message(filters.command("start"))
async def start_cmd(client, message):
    await message.reply_text(
        "👋 **Welcome to Serial Splitter Bot!**\n\n"
        "Send me a large video file, and I will split it perfectly into 19MB parts.\n"
        "Need to cancel a job? Just type /stop.\n\n"
        "📢 **Powered By:** @TeluguSerialsZone"
    )

@bot.on_message(filters.command("stop"))
async def stop_cmd(client, message):
    user_id = message.from_user.id
    if active_tasks.get(user_id):
        active_tasks[user_id] = False # Triggers the kill switch
        await message.reply_text("🛑 **Stopping process and cleaning up disk...**")
    else:
        await message.reply_text("⚠️ No active tasks to stop.")

# --- CORE SPLITTER LOGIC ---
@bot.on_message(filters.video | filters.document)
async def main_handler(client, message):
    user_id = message.from_user.id
    
    # Prevent multiple simultaneous tasks from the same user
    if active_tasks.get(user_id):
        return await message.reply("⚠️ You already have a video processing! Please wait or use /stop.")
        
    media = message.video or message.document
    if not media: return
    
    active_tasks[user_id] = True # Mark user as active
    uid = message.id
    input_file = f"vid_{uid}_input.mp4"
    
    f_size = media.file_size
    original_name = media.file_name or "video.mp4"
    f_name_no_ext = os.path.splitext(original_name)[0]
    
    status = await message.reply(f"📡 **Analyzing {humanize.naturalsize(f_size)} file...**")
    
    try:
        # 1. DOWNLOAD (Monitored by Kill Switch)
        start_time = time.time()
        temp_path = await client.download_media(
            message, 
            file_name=input_file, 
            progress=progress_func, 
            progress_args=(status, start_time, "📥 **Downloading Full Video to Disk**", user_id)
        )
        
        await status.edit("⏳ **Calculating perfect frame split times...**")
        
        duration = await get_duration(temp_path)
        if duration == 0: raise Exception("Invalid video file.")
        
        target_size_mb = 17.5
        total_size_mb = f_size / (1024 * 1024)
        num_parts_estimated = math.ceil(total_size_mb / target_size_mb)
        segment_time = duration / num_parts_estimated
        
        await status.edit(f"✂️ **Splitting at exact frames...**\nPlease wait, this takes a moment for large files.")
        
        # 3. ASYNC FFMPEG SPLITTER (Does NOT freeze the bot)
        cmd = [
            "ffmpeg", "-i", temp_path, "-c", "copy", "-f", "segment", 
            "-segment_time", str(segment_time), "-reset_timestamps", "1",
            f"vid_{uid}_part_%03d.mp4"
        ]
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await process.communicate()
        
        # Check if user pressed stop during the FFmpeg split
        if not active_tasks.get(user_id): raise Exception("CANCELLED_BY_USER")

        # 4. UPLOAD PARTS
        parts = sorted(glob.glob(f"vid_{uid}_part_*.mp4"))
        total_parts = len(parts)
        
        for idx, part_file in enumerate(parts):
            # Check kill switch before every upload
            if not active_tasks.get(user_id): raise Exception("CANCELLED_BY_USER")
                
            part_no = idx + 1
            display_name = f"{f_name_no_ext} Part {part_no} of {total_parts}.mp4"
            
            # Use unique path to avoid conflicts, rename just for sending
            upload_path = f"vid_{uid}_ready.mp4"
            os.rename(part_file, upload_path)
            
            caption_text = f"**{display_name}**\n\n⚜️ Powered By : @TeluguSerialsZone"
            
            up_start = time.time()
            await client.send_document(
                chat_id=message.chat.id, 
                document=upload_path, 
                file_name=display_name, # Forces Telegram to show this name
                caption=caption_text,
                progress=progress_func,
                progress_args=(status, up_start, f"📤 **Uploading Part {part_no}/{total_parts}**", user_id)
            )
            os.remove(upload_path)
            
        await status.edit(f"✨ **All {total_parts} parts sent flawlessly!**\n\n🎥 `{f_name_no_ext}`")

    except Exception as e:
        if str(e) == "CANCELLED_BY_USER":
            await status.edit("🛑 **Task successfully cancelled.** Disk cleared.")
        else:
            await status.edit(f"❌ **Error:** `{str(e)}`")
    finally:
        # ALWAYS CLEAN UP, whether it succeeds, fails, or is cancelled
        active_tasks.pop(user_id, None)
        cleanup_files(uid)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run()
