import os
import time
import math
import asyncio
import threading
import subprocess
import humanize
import glob
from flask import Flask

# --- CRITICAL EVENT LOOP FIX ---
try:
    asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

from pyrogram import Client, filters
from pyrogram.errors import FloodWait, MessageNotModified

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

bot = Client("splitter_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
app = Flask(__name__)

# --- GLOBAL TASK MANAGERS ---
active_tasks = {}
last_ui_update = {} # Tracks UI updates to prevent FloodWaits

# --- WEB SERVER ---
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

async def _safe_edit(message, text):
    """Edits the message with a strict 5-second timeout so it never freezes the bot."""
    try:
        await asyncio.wait_for(message.edit(text), timeout=5.0)
    except MessageNotModified:
        pass
    except Exception:
        pass

async def progress_func(current, total, message, start_time, action, user_id):
    if not active_tasks.get(user_id, False):
        raise Exception("CANCELLED_BY_USER")

    now = time.time()
    # STRICT 3-SECOND LIMIT: Prevents Telegram from throttling the bot UI
    if now - last_ui_update.get(user_id, 0) < 3.0: 
        return 
    last_ui_update[user_id] = now
    
    diff = now - start_time
    speed = current / diff if diff > 0 else 0
    bar = get_progress_bar(current, total)
    msg = (f"**{action}**\n`{bar}`\n🚀 **Speed:** {humanize.naturalsize(speed)}/s\n📂 **Done:** {humanize.naturalsize(current)} / {humanize.naturalsize(total)}")
    
    # FIRE AND FORGET: Does not wait for Telegram to reply, preventing UI deadlocks
    asyncio.create_task(_safe_edit(message, msg))

async def get_duration(file_path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file_path]
    process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await process.communicate()
    return float(stdout.decode().strip()) if stdout else 0

def cleanup_files(uid):
    for f in glob.glob(f"vid_{uid}_*"):
        try: os.remove(f)
        except: pass

async def send_log(client, chat_id, text):
    """Sends background errors directly to the user's chat."""
    try:
        await client.send_message(chat_id, f"⚠️ **BACKEND LOG:**\n`{text}`")
    except:
        pass

# --- COMMANDS ---
@bot.on_message(filters.command("start"))
async def start_cmd(client, message):
    await message.reply_text(
        "👋 **Welcome to Serial Splitter Bot!**\n\n"
        "Send me a large video file, and I will split it perfectly into 19MB parts.\n"
        "If you need to cancel a job, type /stop.\n\n"
        "📢 **Powered By:** @TeluguSerialsZone"
    )

@bot.on_message(filters.command("stop"))
async def stop_cmd(client, message):
    user_id = message.from_user.id
    if active_tasks.get(user_id):
        active_tasks[user_id] = False
        await message.reply_text("🛑 **Force stopping process and cleaning up...**")
    else:
        await message.reply_text("⚠️ No active tasks to stop.")

# --- CORE SPLITTER LOGIC ---
@bot.on_message(filters.video | filters.document)
async def main_handler(client, message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    if active_tasks.get(user_id):
        return await message.reply("⚠️ You already have a video processing! Please wait or use /stop.")
        
    media = message.video or message.document
    if not media: return
    
    active_tasks[user_id] = True
    last_ui_update[user_id] = 0
    uid = message.id
    input_file = f"vid_{uid}_input.mp4"
    
    f_size = media.file_size
    original_name = media.file_name or "video.mp4"
    f_name_no_ext = os.path.splitext(original_name)[0]
    
    status = await message.reply(f"📡 **Analyzing {humanize.naturalsize(f_size)} file...**")
    
    try:
        start_time = time.time()
        
        # 1. DOWNLOAD
        try:
            temp_path = await asyncio.wait_for(
                client.download_media(
                    message, 
                    file_name=input_file, 
                    progress=progress_func, 
                    progress_args=(status, start_time, "📥 **Downloading Full Video to Disk**", user_id)
                ),
                timeout=7200 # 2 hours max
            )
        except asyncio.TimeoutError:
            await send_log(client, chat_id, "Download Timed Out. Telegram server dropped the connection.")
            raise Exception("Telegram dropped the download connection. Please try again.")
        except Exception as e:
            if "CANCELLED" not in str(e): await send_log(client, chat_id, f"Download Error: {str(e)}")
            raise e
        
        await status.edit("⏳ **Calculating perfect frame split times...**")
        
        duration = await get_duration(temp_path)
        if duration == 0: raise Exception("Invalid video file.")
        
        target_size_mb = 17.5
        total_size_mb = f_size / (1024 * 1024)
        num_parts_estimated = math.ceil(total_size_mb / target_size_mb)
        segment_time = duration / num_parts_estimated
        
        await status.edit(f"✂️ **Splitting at exact frames...**\nPlease wait, this takes a moment for large files.")
        
        cmd = [
            "ffmpeg", "-i", temp_path, "-c", "copy", "-f", "segment", 
            "-segment_time", str(segment_time), "-reset_timestamps", "1",
            f"vid_{uid}_part_%03d.mp4"
        ]
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await process.communicate()
        
        if not active_tasks.get(user_id): raise Exception("CANCELLED_BY_USER")

        # 4. UPLOAD PARTS
        parts = sorted(glob.glob(f"vid_{uid}_part_*.mp4"))
        total_parts = len(parts)
        
        for idx, part_file in enumerate(parts):
            if not active_tasks.get(user_id): raise Exception("CANCELLED_BY_USER")
                
            part_no = idx + 1
            display_name = f"{f_name_no_ext} Part {part_no} of {total_parts}.mp4"
            upload_path = f"vid_{uid}_ready_{part_no}.mp4"
            os.rename(part_file, upload_path)
            caption_text = f"**{display_name}**\n\n⚜️ Powered By : @TeluguSerialsZone"
            
            max_retries = 10
            for attempt in range(max_retries):
                try:
                    up_start = time.time()
                    # 90 SECOND UPLOAD TIMEOUT
                    # If a 19MB file takes more than 1.5 minutes to upload, the connection is dead. Kill it.
                    await asyncio.wait_for(
                        client.send_document(
                            chat_id=message.chat.id, 
                            document=upload_path, 
                            file_name=display_name, 
                            caption=caption_text,
                            progress=progress_func,
                            progress_args=(status, up_start, f"📤 **Uploading Part {part_no}/{total_parts}**", user_id)
                        ),
                        timeout=90 
                    )
                    break # Success!
                    
                except asyncio.TimeoutError:
                    error_msg = f"Timeout on Part {part_no}. Attempt {attempt+1}/{max_retries}. Restarting chunk..."
                    await send_log(client, chat_id, error_msg)
                    await asyncio.sleep(2)
                    
                except FloodWait as e:
                    error_msg = f"FloodWait triggered! Telegram demands we wait {e.value}s."
                    await send_log(client, chat_id, error_msg)
                    await asyncio.sleep(e.value + 5)
                    
                except Exception as e:
                    if "CANCELLED_BY_USER" in str(e): raise e
                    await send_log(client, chat_id, f"Upload Exception on Part {part_no}: {str(e)}")
                    if attempt == max_retries - 1:
                        raise Exception(f"Failed to upload Part {part_no} after {max_retries} attempts.")
                    await asyncio.sleep(5)
            
            if os.path.exists(upload_path):
                os.remove(upload_path)
            
        await status.edit(f"✨ **All {total_parts} parts sent flawlessly!**\n\n🎥 `{f_name_no_ext}`")

    except Exception as e:
        if "CANCELLED_BY_USER" in str(e):
            await status.edit("🛑 **Task successfully cancelled.** Disk cleared.")
        else:
            await status.edit(f"❌ **Error:** `{str(e)}`")
            await send_log(client, chat_id, f"Final Fatal Error: {str(e)}")
    finally:
        active_tasks.pop(user_id, None)
        last_ui_update.pop(user_id, None)
        cleanup_files(uid)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run()
