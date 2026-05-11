import os
import time
import math
import asyncio
import threading
import subprocess
import humanize
from flask import Flask

# --- CRITICAL FIX FOR PYTHON 3.11+ ---
try:
    asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

from pyrogram import Client, filters

# --- CONFIG ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

bot = Client("splitter_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
app = Flask(__name__)

@app.route('/')
def health(): return "Bot is live", 200

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
    if diff < 1.5: return # Update every 1.5s to avoid Telegram ban

    speed = current / diff
    bar = get_progress_bar(current, total)
    msg = (
        f"**{action}**\n"
        f"`{bar}`\n"
        f"🚀 **Speed:** {humanize.naturalsize(speed)}/s\n"
        f"📂 **Size:** {humanize.naturalsize(current)} / {humanize.naturalsize(total)}"
    )
    try:
        await message.edit(msg)
    except: pass

async def get_duration(file_path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file_path]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, _ = process.communicate()
    return float(out) if out else 0

# --- MAIN LOGIC ---
@bot.on_message(filters.video | filters.document)
async def handle_split(client, message):
    media = message.video or message.document
    status = await message.reply("📡 **Initializing...**")
    start_time = time.time()

    try:
        # 1. Download
        temp_main = await client.download_media(
            message, 
            progress=progress_func, 
            progress_args=(status, start_time, "📥 **Downloading Original**")
        )
        
        duration = await get_duration(temp_main)
        # Calculate parts to keep each under 19MB
        num_parts = math.ceil(media.file_size / (18.8 * 1024 * 1024))
        part_time = duration / num_parts

        await status.edit(f"✂️ **Splitting into {num_parts} playable parts...**")

        for i in range(num_parts):
            start = i * part_time
            output_part = f"part_{i+1}.mp4"
            
            # 2. FFmpeg Smart Cut (Smooth Playback)
            cmd = [
                "ffmpeg", "-ss", str(start), "-t", str(part_time),
                "-i", temp_main, "-c", "copy", "-map", "0", 
                "-avoid_negative_ts", "make_zero", output_part
            ]
            subprocess.run(cmd, capture_output=True)

            # 3. Upload with Progress Bar
            upload_start = time.time()
            if os.path.exists(output_part):
                await client.send_video(
                    chat_id=message.chat.id,
                    video=output_part,
                    caption=f"✅ **Part {i+1}/{num_parts}**\n🎥 Smooth Playback Enabled",
                    supports_streaming=True,
                    progress=progress_func,
                    progress_args=(status, upload_start, f"📤 **Uploading Part {i+1}/{num_parts}**")
                )
                os.remove(output_part)

        os.remove(temp_main)
        await status.edit(f"✨ **All {num_parts} parts sent successfully!**")

    except Exception as e:
        await message.reply(f"❌ **Error:** `{str(e)}`")

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run()
