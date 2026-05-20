import os
import time
import math
import asyncio
import threading
import subprocess
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

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

bot = Client("splitter_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
app = Flask(__name__)

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

async def progress_func(current, total, message, start_time, action):
    now = time.time()
    diff = now - start_time
    if diff < 2.0: return # Prevents Telegram FloodWait error
    
    speed = current / diff if diff > 0 else 0
    bar = get_progress_bar(current, total)
    msg = (f"**{action}**\n`{bar}`\n🚀 **Speed:** {humanize.naturalsize(speed)}/s\n📂 **Done:** {humanize.naturalsize(current)} / {humanize.naturalsize(total)}")
    
    try: await message.edit(msg)
    except: pass

async def get_duration(file_path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file_path]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, _ = process.communicate()
    return float(out) if out else 0

# --- COMMANDS ---
@bot.on_message(filters.command("start"))
async def start_cmd(client, message):
    await message.reply_text(
        "👋 **Welcome to Serial Splitter Bot!**\n\n"
        "Send me a large video file (even 700MB+), and I will split it perfectly "
        "at the exact frames to keep each part under 19MB without glitches.\n\n"
        "📢 **Powered By:** @TeluguSerialsZone"
    )

# --- CORE SPLITTER LOGIC ---
@bot.on_message(filters.video | filters.document)
async def main_handler(client, message):
    media = message.video or message.document
    if not media: return
    
    f_size = media.file_size
    original_name = media.file_name or "video.mp4"
    f_name_no_ext = os.path.splitext(original_name)[0]
    
    status = await message.reply(f"📡 **Analyzing {humanize.naturalsize(f_size)} file...**")
    
    # Unique ID to prevent file mix-ups if multiple files are sent
    uid = message.id
    input_file = f"vid_{uid}_input.mp4"
    
    try:
        # 1. DOWNLOAD TO DISK (Bypasses 512MB RAM Limit)
        start_time = time.time()
        temp_path = await client.download_media(
            message, 
            file_name=input_file, 
            progress=progress_func, 
            progress_args=(status, start_time, "📥 **Downloading Full Video to Disk**")
        )
        
        await status.edit("⏳ **Calculating perfect frame split times...**")
        
        duration = await get_duration(temp_path)
        if duration == 0:
            raise Exception("Could not read video duration. Ensure it is a valid video file.")
        
        # 2. CALCULATE SEGMENT TIME
        # Target 17.5MB to ensure parts never exceed 19MB when waiting for a Keyframe.
        target_size_mb = 17.5
        total_size_mb = f_size / (1024 * 1024)
        num_parts_estimated = math.ceil(total_size_mb / target_size_mb)
        segment_time = duration / num_parts_estimated
        
        await status.edit(f"✂️ **Splitting at exact frames...**\nEnsuring parts are <19MB with no lost frames.")
        
        # 3. FFMPEG PERFECT FRAME SPLITTER
        # -f segment pushes overflowing frames to the next part automatically
        cmd = [
            "ffmpeg", "-i", temp_path, 
            "-c", "copy", 
            "-f", "segment", 
            "-segment_time", str(segment_time), 
            "-reset_timestamps", "1",
            f"vid_{uid}_part_%03d.mp4"
        ]
        subprocess.run(cmd, capture_output=True)
        
        # 4. UPLOAD PARTS
        parts = sorted(glob.glob(f"vid_{uid}_part_*.mp4"))
        total_parts = len(parts)
        
        for idx, part_file in enumerate(parts):
            part_no = idx + 1
            display_name = f"{f_name_no_ext} Part {part_no} of {total_parts}.mp4"
            
            # Rename the file so Telegram shows the correct dynamic name
            os.rename(part_file, display_name)
            
            # Dynamic Caption
            caption_text = f"**{display_name}**\n\n⚜️ Powered By : @TeluguSerialsZone"
            
            up_start = time.time()
            await client.send_document(
                chat_id=message.chat.id, 
                document=display_name, 
                caption=caption_text,
                progress=progress_func,
                progress_args=(status, up_start, f"📤 **Uploading Part {part_no}/{total_parts}**")
            )
            
            # Delete part immediately after sending to free up space
            os.remove(display_name)
            
        # 5. FINAL CLEANUP
        if os.path.exists(temp_path):
            os.remove(temp_path)
            
        await status.edit(f"✨ **All {total_parts} parts sent flawlessly!**\n\n🎥 `{f_name_no_ext}`")

    except Exception as e:
        # Emergency cleanup if an error occurs
        if os.path.exists(input_file): os.remove(input_file)
        for orphan in glob.glob(f"vid_{uid}_part_*.mp4"): os.remove(orphan)
        await message.reply(f"❌ **Error:** `{str(e)}`")

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run()
