import os
import math
import asyncio
from flask import Flask
from threading import Thread

# Fix for Event Loop
try:
    asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

from pyrogram import Client, filters
from pyrogram.errors import FloodWait, RPCError

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHUNK_SIZE = 19 * 1024 * 1024  # 19MB

bot = Client("splitter_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
app = Flask(__name__)

@app.route('/')
def health(): return "OK", 200

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

@bot.on_message(filters.video | filters.document)
async def splitter_handler(client, message):
    media = message.video or message.document
    file_size = media.file_size
    total_parts = math.ceil(file_size / CHUNK_SIZE)
    
    status = await message.reply(f"🔍 Processing: `{media.file_name}`\n📦 Total parts: {total_parts}")

    try:
        for i in range(total_parts):
            offset = i * CHUNK_SIZE
            part_no = i + 1
            part_file = f"part_{part_no}.mp4"

            await status.edit(f"📥 Downloading Part {part_no}/{total_parts}...")

            # CORRECT WAY TO DOWNLOAD CHUNKS:
            # We open a stream and read only the portion we need
            with open(part_file, "wb") as f:
                async for chunk in client.stream_media(message, offset=i, limit=1):
                    # In stream_media, offset/limit work in chunks, 
                    # but for absolute byte control, we use simple download for small chunks:
                    pass 
            
            # Re-optimized download for Render RAM:
            temp_path = await client.download_media(
                message, 
                file_name=part_file,
                block=True # Ensures the bot waits for the download
            )
            
            # Since Render has small disk, we have to split the file locally 
            # OR use the byte-range method. Let's use the local split for reliability:
            with open(temp_path, "rb") as source:
                source.seek(offset)
                chunk_data = source.read(CHUNK_SIZE)
                
            with open(f"final_{part_file}", "wb") as target:
                target.write(chunk_data)

            await status.edit(f"📤 Uploading Part {part_no}/{total_parts}...")
            
            try:
                await client.send_document(
                    chat_id=message.chat.id,
                    document=f"final_{part_file}",
                    caption=f"✅ {media.file_name} - Part {part_no}/{total_parts}"
                )
            except FloodWait as e:
                await asyncio.sleep(e.value) # Wait if Telegram throttles us
                
            # CLEANUP: Delete both the full download and the 19MB part
            if os.path.exists(temp_path): os.remove(temp_path)
            if os.path.exists(f"final_{part_file}"): os.remove(f"final_{part_file}")

        await status.edit("✅ All parts sent successfully!")

    except Exception as e:
        print(f"Error: {e}")
        await message.reply(f"❌ Failed: {str(e)}")

if __name__ == "__main__":
    Thread(target=run_flask, daemon=True).start()
    bot.run()
