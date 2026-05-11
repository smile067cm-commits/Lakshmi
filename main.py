import os
import math
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from flask import Flask
from threading import Thread

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHUNK_SIZE = 19 * 1024 * 1024  # 19MB

bot = Client("splitter_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- KEEP-ALIVE SERVER ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is running!", 200

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

# --- BOT LOGIC ---
@bot.on_message(filters.video | filters.document)
async def handle_video(client: Client, message: Message):
    file = message.video or message.document
    if not file.file_size:
        await message.reply("Cannot determine file size.")
        return

    total_size = file.file_size
    total_parts = math.ceil(total_size / CHUNK_SIZE)
    
    status_msg = await message.reply(f"Detected {total_size / (1024*1024):.2f}MB. Splitting into {total_parts} parts...")

    try:
        for i in range(total_parts):
            offset = i * CHUNK_SIZE
            # Calculate how much to read for this chunk
            current_limit = min(CHUNK_SIZE, total_size - offset)
            
            part_name = f"part_{i+1}_of_{total_parts}.mp4"
            
            # Download specific chunk from Telegram
            # Note: Pyrogram allows streaming using chunks
            temp_file = await client.download_media(
                message,
                file_name=part_name,
                offset=offset,
                limit=current_limit
            )
            
            # Upload the chunk back to user
            await client.send_document(
                chat_id=message.chat.id,
                document=temp_file,
                caption=f"Part {i+1}/{total_parts}",
                file_name=part_name
            )
            
            # Immediate Cleanup to save space on Render
            if os.path.exists(temp_file):
                os.remove(temp_file)
            
            await status_msg.edit(f"Progress: {i+1}/{total_parts} parts sent.")

        await message.reply("Successfully split and sent all parts!")
    except Exception as e:
        await message.reply(f"An error occurred: {str(e)}")

if __name__ == "__main__":
    # Start Keep-Alive Server
    Thread(target=run_flask).start()
    # Start Bot
    print("Bot is starting...")
    bot.run()