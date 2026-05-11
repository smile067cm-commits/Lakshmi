import os
import math
import asyncio
from flask import Flask
from threading import Thread

# Fix for Pyrogram on newer Python versions: 
# We manually create a loop BEFORE importing pyrogram
try:
    asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

from pyrogram import Client, filters
from pyrogram.types import Message

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHUNK_SIZE = 19 * 1024 * 1024 

bot = Client("splitter_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is Alive!", 200

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

@bot.on_message(filters.video | filters.document)
async def handle_video(client, message):
    media = message.video or message.document
    if not media: return
    
    file_size = media.file_size
    total_parts = math.ceil(file_size / CHUNK_SIZE)
    status_msg = await message.reply(f"📦 Splitting into {total_parts} parts...")

    try:
        for i in range(total_parts):
            offset = i * CHUNK_SIZE
            current_limit = min(CHUNK_SIZE, file_size - offset)
            part_name = f"part_{i+1}.mp4"
            
            temp_path = await client.download_media(message, file_name=part_name, offset=offset, limit=current_limit)
            await client.send_document(chat_id=message.chat.id, document=temp_path, caption=f"Part {i+1}/{total_parts}")
            
            if os.path.exists(temp_path): os.remove(temp_path)
            await status_msg.edit(f"🚀 Progress: {i+1}/{total_parts}")
            
    except Exception as e:
        await message.reply(f"⚠️ Error: {str(e)}")

if __name__ == "__main__":
    Thread(target=run_flask, daemon=True).start()
    print("Bot is starting...")
    bot.run()
