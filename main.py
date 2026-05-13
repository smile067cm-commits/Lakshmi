import os
import time
import math
import asyncio
import threading
import subprocess
import humanize
from flask import Flask

# --- CRITICAL EVENT LOOP FIX FOR PYTHON 3.11+ ---
try:
    asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait

# --- CONFIG ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHUNK_SIZE = 19 * 1024 * 1024 
RENDER_LIMIT = 400 * 1024 * 1024 # 400MB threshold for smooth mode

bot = Client("splitter_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
app = Flask(__name__)

@app.route('/')
def health(): return "Bot is Alive", 200

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

# --- UI HELPERS ---
def get_progress_bar(current, total):
    percentage = current * 100 / total
    finished = int(percentage / 10)
    return "⬛" * finished + "⬜" * (10 - finished) + f" {percentage:.1f}%"

async def progress_func(current, total, message, start_time, action):
    now = time.time()
    diff = now - start_time
    if diff < 2.0: return 
    speed = current / diff
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
        "Send me any video file, and I will split it into **19MB playable parts** "
        "with smooth playback for your serials.\n\n"
        "📢 **Powered By:** @TeluguSerialsZone"
    )

# --- CORE SPLITTER LOGIC ---
@bot.on_message(filters.video | filters.document)
async def main_handler(client, message):
    media = message.video or message.document
    if not media: return
    
    f_size = media.file_size
    # Extract serial name and details from original filename
    original_full_name = media.file_name or "video.mp4"
    f_name_no_ext = os.path.splitext(original_full_name)[0]
    total_parts = math.ceil(f_size / CHUNK_SIZE)
    
    status = await message.reply("📡 **Analyzing Serial File...**")
    
    try:
        # Determine mode based on Render's 512MB RAM/Disk limit[span_1](start_span)[span_1](end_span)
        if f_size > RENDER_LIMIT:
            await status.edit(f"⚠️ **Large File Detected ({humanize.naturalsize(f_size)})**\nUsing 'Binary Stream' to save Render memory. Small glitches may occur at join points.")
            mode = "CHUNK"
        else:
            await status.edit(f"✅ **Safe Size for Render.**\nUsing 'Smooth FFmpeg Mode' for perfect playback.")
            mode = "SMOOTH"

        if mode == "SMOOTH":
            temp_path = await client.download_media(message, progress=progress_func, progress_args=(status, time.time(), "📥 **Downloading Video**"))
            duration = await get_duration(temp_path)
            part_time = duration / total_parts

            for i in range(total_parts):
                part_no = i + 1
                display_name = f"{f_name_no_ext} Part {part_no} of {total_parts}.mp4"
                await status.edit(f"✂️ **Processing {part_no}/{total_parts}...**")
                
                cmd = ["ffmpeg", "-ss", str(i * part_time), "-t", str(part_time), "-i", temp_path, "-c", "copy", "-map", "0", "-avoid_negative_ts", "make_zero", display_name]
                subprocess.run(cmd, capture_output=True)
                
                await client.send_document(
                    chat_id=message.chat.id, 
                    document=display_name, 
                    caption=f"**{display_name}**\n\n⚜️ Powered By : @TeluguSerialsZone",
                    progress=progress_func,
                    progress_args=(status, time.time(), f"📤 **Uploading Part {part_no}**")
                )
                if os.path.exists(display_name): os.remove(display_name)
            if os.path.exists(temp_path): os.remove(temp_path)

        else:
            # Chunking mode to avoid 512MB crash[span_2](start_span)[span_2](end_span)
            for i in range(total_parts):
                part_no = i + 1
                display_name = f"{f_name_no_ext} Part {part_no} of {total_parts}.mp4"
                
                chunk_path = await client.download_media(
                    message, 
                    file_name=display_name, 
                    offset=i*CHUNK_SIZE, 
                    limit=CHUNK_SIZE, 
                    progress=progress_func, 
                    progress_args=(status, time.time(), f"📥 **Fetching Part {part_no}**")
                )
                
                await client.send_document(
                    chat_id=message.chat.id, 
                    document=chunk_path, 
                    caption=f"**{display_name}**\n\n⚜️ Powered By : @TeluguSerialsZone",
                    progress=progress_func,
                    progress_args=(status, time.time(), f"📤 **Uploading Part {part_no}**")
                )
                if os.path.exists(chunk_path): os.remove(chunk_path)

        await status.edit(f"✨ **All {total_parts} parts sent successfully!**\n\n🎥 {f_name_no_ext}")

    except Exception as e:
        await message.reply(f"❌ **Error:** `{str(e)}`")

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run()
