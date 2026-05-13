import os
import time
import math
import asyncio
import threading
import subprocess
import humanize
from flask import Flask
from pyrogram import Client, filters
from pyrogram.errors import FloodWait

# --- CRITICAL EVENT LOOP FIX ---
try:
    asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

# --- CONFIG ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHUNK_SIZE = 19 * 1024 * 1024 
RENDER_LIMIT = 400 * 1024 * 1024 # 400MB Safety Threshold

bot = Client("splitter_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
app = Flask(__name__)

@app.route('/')
def health(): return "Bot Active", 200

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
    if diff < 1.5: return 
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

# --- CORE LOGIC ---
@bot.on_message(filters.video | filters.document)
async def main_handler(client, message):
    media = message.video or message.document
    f_size = media.file_size
    f_name = os.path.splitext(media.file_name or "video.mp4")[0]
    total_parts = math.ceil(f_size / CHUNK_SIZE)
    
    status = await message.reply("📡 **Analyzing File...**")
    
    try:
        # STEP 1: SAFETY WARNING & MODE SELECTION
        if f_size > RENDER_LIMIT:
            await status.edit(f"⚠️ **Big File Warning ({humanize.naturalsize(f_size)})!**\nSwitching to 'Binary Stream' to prevent Render crash. Files might have a slight glitch at start.")
            mode = "CHUNK"
        else:
            await status.edit(f"✅ **Safe Size detected.**\nUsing 'Smooth FFmpeg Mode' for perfect playback.")
            mode = "SMOOTH"

        # STEP 2: PROCESSING
        if mode == "SMOOTH":
            # FULL DOWNLOAD -> FFMPEG SPLIT
            temp_path = await client.download_media(message, progress=progress_func, progress_args=(status, time.time(), "📥 **Downloading Full Video**"))
            duration = await get_duration(temp_path)
            part_time = duration / total_parts

            for i in range(total_parts):
                part_no = i + 1
                out_name = f"{f_name} Part {part_no} of {total_parts}.mp4"
                await status.edit(f"✂️ **Smooth Cutting Part {part_no}/{total_parts}...**")
                
                cmd = ["ffmpeg", "-ss", str(i * part_time), "-t", str(part_time), "-i", temp_path, "-c", "copy", "-map", "0", "-avoid_negative_ts", "make_zero", out_name]
                subprocess.run(cmd, capture_output=True)
                
                await client.send_document(chat_id=message.chat.id, document=out_name, caption=f"**{out_name}**\n\n⚜️ Powered By : @TeluguSerialsZone")
                os.remove(out_name)
            os.remove(temp_path)

        else:
            # BINARY CHUNK DOWNLOAD (NO FULL DOWNLOAD)
            for i in range(total_parts):
                part_no = i + 1
                out_name = f"{f_name} Part {part_no} of {total_parts}.mp4"
                
                chunk_path = await client.download_media(message, file_name=out_name, offset=i*CHUNK_SIZE, limit=CHUNK_SIZE, progress=progress_func, progress_args=(status, time.time(), f"📥 **Downloading Part {part_no}**"))
                
                await client.send_document(chat_id=message.chat.id, document=chunk_path, caption=f"**{out_name}**\n\n⚜️ Powered By : @TeluguSerialsZone")
                os.remove(chunk_path)

        await status.edit(f"✨ **All {total_parts} parts sent successfully!**")

    except Exception as e:
        await message.reply(f"❌ **Error:** `{str(e)}`")

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run()
