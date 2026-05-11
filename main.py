import os
import math
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from flask import Flask
from threading import Thread

# --- CONFIGURATION ---
# Get these from my.telegram.org and @BotFather
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHUNK_SIZE = 19 * 1024 * 1024  # 19MB per part

bot = Client("splitter_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- KEEP-ALIVE SERVER ---
# This prevents Render from sleeping the bot
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is Alive!", 200

def run_flask():
    # Render provides the PORT variable automatically
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# --- BOT LOGIC ---
@bot.on_message(filters.video | filters.document | filters.animation)
async def handle_video(client: Client, message: Message):
    # Determine if it's a video or a document file
    media = message.video or message.document or message.animation
    
    if not media:
        return

    file_size = media.file_size
    if not file_size:
        await message.reply("❌ Could not determine file size.")
        return

    total_parts = math.ceil(file_size / CHUNK_SIZE)
    status_msg = await message.reply(f"📦 File detected: {file_size / (1024*1024):.2f}MB\n✂️ Splitting into {total_parts} parts...")

    try:
        for i in range(total_parts):
            offset = i * CHUNK_SIZE
            # Ensure the last chunk doesn't try to read past the file size
            current_limit = min(CHUNK_SIZE, file_size - offset)
            
            part_number = i + 1
            file_name = f"part_{part_number}_of_{total_parts}.mp4"
            
            # Download specific chunk from Telegram directly to local storage
            temp_path = await client.download_media(
                message,
                file_name=file_name,
                offset=offset,
                limit=current_limit
            )
            
            # Send the chunk back to the user
            await client.send_document(
                chat_id=message.chat.id,
                document=temp_path,
                caption=f"✅ Part {part_number}/{total_parts}",
                file_name=file_name
            )
            
            # DELETE IMMEDIATELY to keep Render disk space clean
            if os.path.exists(temp_path):
                os.remove(temp_path)
            
            # Update status every 2 parts to avoid Telegram flood limits
            if part_number % 2 == 0 or part_number == total_parts:
                await status_msg.edit(f"🚀 Progress: {part_number}/{total_parts} parts sent...")

        await message.reply("✨ Done! All parts have been sent and server storage cleared.")

    except Exception as e:
        await message.reply(f"⚠️ Error: {str(e)}")

# --- STARTUP ---
if __name__ == "__main__":
    # 1. Start the Flask server in a background thread
    Thread(target=run_flask, daemon=True).start()
    
    print("Starting Bot...")
    
    # 2. FIX: Explicitly handle the event loop for Python 3.11/3.12/3.14
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    # 3. Run the bot
    bot.run()
