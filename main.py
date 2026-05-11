import os
import math
import time
import asyncio
import humanize
from flask import Flask
from threading import Thread
from pyrogram import Client, filters
from pyrogram.errors import FloodWait

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MAX_CHUNK_SIZE = 19 * 1024 * 1024  # Strict 19MB Limit

# Create loop before Pyrogram import for Python 3.11+
try:
    asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

bot = Client("splitter_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
app = Flask(__name__)

@app.route('/')
def health(): return "Bot Active", 200

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

# --- PROGRESS BAR UI ---
def get_progress_bar(current, total):
    percentage = current * 100 / total
    finished_blocks = int(percentage / 10)
    unfinished_blocks = 10 - finished_blocks
    return "⬛" * finished_blocks + "⬜" * unfinished_blocks + f" {percentage:.1f}%"

async def progress_callback(current, total, message, start_time, action):
    now = time.time()
    diff = now - start_time
    if diff < 1: return # Update every 1 second to avoid FloodWait
    
    speed = current / diff
    elapsed_time = humanize.precisedelta(int(diff))
    eta = humanize.precisedelta(int((total - current) / speed)) if speed > 0 else "0s"
    
    bar = get_progress_bar(current, total)
    msg = (
        f"**{action}**\n"
        f" `{bar}`\n"
        f"⚡ **Speed:** {humanize.naturalsize(speed)}/s\n"
        f"📂 **Done:** {humanize.naturalsize(current)} / {humanize.naturalsize(total)}\n"
        f"⏳ **ETA:** {eta}"
    )
    try:
        await message.edit(msg)
    except: pass

# --- MAIN HANDLER ---
@bot.on_message(filters.video | filters.document)
async def handle_split(client, message):
    media = message.video or message.document
    if not media: return
    
    file_size = media.file_size
    total_parts = math.ceil(file_size / MAX_CHUNK_SIZE)
    
    status = await message.reply("⚡ **Initializing Serial Splitter...**")
    start_time = time.time()

    try:
        # Download strictly by byte-chunks to keep RAM low for Render
        for i in range(total_parts):
            offset = i * MAX_CHUNK_SIZE
            current_limit = min(MAX_CHUNK_SIZE, file_size - offset)
            part_name = f"Part_{i+1}_of_{total_parts}_{media.file_name}"
            
            # 1. Download only the 19MB chunk
            # Note: We download the chunk with a focus on speed
            chunk_start = time.time()
            temp_path = await client.download_media(
                message,
                file_name=part_name,
                offset=offset,
                limit=current_limit,
                progress=progress_callback,
                progress_args=(status, chunk_start, f"📥 Downloading Part {i+1}/{total_parts}")
            )

            # 2. Upload the chunk
            upload_start = time.time()
            await client.send_document(
                chat_id=message.chat.id,
                document=temp_path,
                caption=f"✅ **Part {i+1}/{total_parts}**\n📦 Size: {humanize.naturalsize(current_limit)}",
                progress=progress_callback,
                progress_args=(status, upload_start, f"📤 Uploading Part {i+1}/{total_parts}")
            )

            # 3. CLEANUP - Crucial for Render
            if os.path.exists(temp_path):
                os.remove(temp_path)

        await status.edit(f"✨ **Success!**\nAll {total_parts} parts sent.\nTotal Time: {humanize.precisedelta(int(time.time() - start_time))}")

    except FloodWait as e:
        await asyncio.sleep(e.value)
    except Exception as e:
        await message.reply(f"❌ **Error:** `{str(e)}`")

if __name__ == "__main__":
    Thread(target=run_flask, daemon=True).start()
    bot.run()
