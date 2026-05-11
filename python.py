from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice
import asyncio
import logging
import yt_dlp
import os
import subprocess
from uuid import uuid4
from datetime import datetime, timedelta
from pymongo import MongoClient

# ====================== НАСТРОЙКИ ======================
TOKEN = "8704315086:AAEERRGJXDW_7jDDnYark03jX4MqVRIlM1c"
MONGO_URI = "mongodb://localhost:27017/"
YOUR_ADMIN_ID = 1381500667

bot = Bot(token=TOKEN)
dp = Dispatcher()

os.makedirs("downloads", exist_ok=True)

client = MongoClient(MONGO_URI)
db = client["downloader_bot"]
users_col = db["users"]
payments_col = db["payments"]
links_col = db["temp_links"]

# ====================== MONGO ======================
def get_user(user_id: int):
    user = users_col.find_one({"user_id": user_id})
    if not user:
        user = {"user_id": user_id, "downloads_today": 0, "last_reset": datetime.now().date().isoformat(),
                "is_premium": False, "premium_until": None}
        users_col.insert_one(user)
    return user

def reset_daily_downloads():
    today = datetime.now().date().isoformat()
    users_col.update_many({"last_reset": {"$lt": today}}, {"$set": {"downloads_today": 0, "last_reset": today}})

def save_link(url: str):
    link_id = str(uuid4())[:12]
    links_col.insert_one({"link_id": link_id, "url": url, "created": datetime.now()})
    return link_id

def get_link(link_id: str):
    return links_col.find_one({"link_id": link_id})

# ====================== DOWNLOAD FUNCTIONS ======================
async def progress_hook(d, msg):
    if d['status'] == 'downloading':
        try:
            percent = d.get('_percent_str', '0%')
            speed = d.get('_speed_str', '')
            await msg.edit_text(f"⏳ Скачивание... {percent} | {speed}")
        except:
            pass


def get_ydl_opts(url: str, quality="720", is_audio=False):
    opts = {
        'outtmpl': f'downloads/{uuid4()}.%(ext)s',
        'quiet': False,
        'noplaylist': False,
        'concurrent_fragment_downloads': 12,
        'retries': 5,
        'cookiefile': 'cookies.txt',           # создай cookies.txt для Instagram
    }

    # TikTok
    if 'tiktok.com' in url:
        opts.update({
            'format': 'best',
            'extractor_args': {'TikTok': {'api_hostname': 'api16-normal-c-useast1a.tiktokv.com'}},
        })
    # Instagram
    elif 'instagram' in url or 'instagr.am' in url:
        opts['format'] = 'best[height<=1080]'
    # YouTube и остальные
    else:
        if is_audio:
            opts['format'] = 'bestaudio/best'
        else:
            height = {"360": 360, "720": 720, "1080": 1080}.get(quality, 720)
            opts['format'] = f'best[height<={height}]'

    if is_audio:
        opts.update({
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],
        })

    return opts


async def compress_video(input_path: str, progress_msg):
    output_path = input_path.replace(".mp4", "_compressed.mp4")
    if os.path.getsize(input_path) / (1024*1024) <= 1800:
        return input_path

    await progress_msg.edit_text("🔄 Сжимаем видео...")
    subprocess.run([
        'ffmpeg', '-i', input_path, '-vf', 'scale=1280:-2',
        '-c:v', 'libx264', '-crf', '28', '-preset', 'fast',
        '-c:a', 'aac', '-b:a', '128k', '-y', output_path
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    if os.path.exists(output_path) and os.path.getsize(output_path) > 100000:
        os.remove(input_path)
        return output_path
    return input_path


async def download_media(url: str, quality="720", is_audio=False, progress_msg=None):
    ydl_opts = get_ydl_opts(url, quality, is_audio)
    if progress_msg:
        ydl_opts['progress_hooks'] = [lambda d: asyncio.create_task(progress_hook(d, progress_msg))]

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        if is_audio and not filename.endswith('.mp3'):
            filename = filename.rsplit('.', 1)[0] + '.mp3'
        size_mb = os.path.getsize(filename) / (1024 * 1024)
        return filename, info.get('title', 'Медиа'), size_mb


# ====================== ЛИМИТЫ ======================
async def check_limit(user_id: int, message) -> bool:
    reset_daily_downloads()
    user = get_user(user_id)
    
    if user_id == YOUR_ADMIN_ID:
        return True
    
    if user.get("is_premium") and user.get("premium_until"):
        if datetime.fromisoformat(user["premium_until"]) > datetime.now():
            return True

    if user.get("downloads_today", 0) >= 5:
        await message.answer("⛔ Лимит 5 скачиваний в день исчерпан.\n\n/premium")
        return False

    users_col.update_one({"user_id": user_id}, {"$inc": {"downloads_today": 1}})
    return True


# ====================== HANDLERS ======================
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "🎥 <b>MediaFlow Bot</b>\n\n"
        "Отправь ссылку на видео, рилс или плейлист.\n"
        "Поддерживаем: YouTube, Instagram, TikTok",
        parse_mode="HTML"
    )

@dp.message(Command("premium"))
async def premium_cmd(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 месяц — 149 Stars", callback_data="tariff:1m")],
        [InlineKeyboardButton(text="3 месяца — 399 Stars", callback_data="tariff:3m")],
        [InlineKeyboardButton(text="Навсегда — 999 Stars", callback_data="tariff:lifetime")],
    ])
    await message.answer("🌟 Выбери Premium:", reply_markup=kb)

@dp.message(F.text)
async def handle_links(message: types.Message):
    if not await check_limit(message.from_user.id, message):
        return

    urls = [p for line in message.text.splitlines() for p in line.split() if p.startswith("http")]
    if not urls:
        return await message.answer("❌ Не нашел ссылок")

    for url in urls[:3]:
        link_id = save_link(url)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚡ Быстро (360p)", callback_data=f"dl:{link_id}:360")],
            [InlineKeyboardButton(text="🎥 720p (рекомендуется)", callback_data=f"dl:{link_id}:720")],
            [InlineKeyboardButton(text="🎥 1080p", callback_data=f"dl:{link_id}:1080")],
            [InlineKeyboardButton(text="🔊 MP3", callback_data=f"dl:{link_id}:audio")],
        ])
        await message.answer(f"🔗 Выбери качество:", reply_markup=kb)

@dp.callback_query(F.data.startswith("dl:"))
async def download_callback(callback: types.CallbackQuery):
    if not callback.message or not await check_limit(callback.from_user.id, callback.message):
        return

    _, link_id, mode = callback.data.split(":")
    link_data = get_link(link_id)
    if not link_data:
        return await callback.answer("Ссылка устарела", show_alert=True)

    url = link_data["url"]
    progress_msg = await callback.message.edit_text("⏳ Скачиваю...")

    try:
        is_audio = mode == "audio"
        quality = mode if mode in ["360", "720", "1080"] else "720"

        file_path, title, size_mb = await download_media(url, quality, is_audio, progress_msg)

        # Сжатие видео
        if not is_audio and file_path.endswith(('.mp4', '.webm', '.mov')):
            file_path = await compress_video(file_path, progress_msg)
            size_mb = os.path.getsize(file_path) / (1024 * 1024)

        caption = f"✅ {title[:120]}\n📏 Размер: {size_mb:.1f} МБ"

        if size_mb > 50:
            await callback.message.answer_document(types.FSInputFile(file_path), caption=caption)
        elif is_audio or file_path.endswith('.mp3'):
            await callback.message.answer_audio(types.FSInputFile(file_path), caption=caption)
        else:
            await callback.message.answer_video(types.FSInputFile(file_path), caption=caption, supports_streaming=True)

        if os.path.exists(file_path):
            os.remove(file_path)
        await progress_msg.delete()

    except Exception as e:
        error = str(e).lower()
        if "ffmpeg" in error or "ffprobe" in error:
            await progress_msg.edit_text("❌ Для MP3 и сжатия видео нужен ffmpeg.\nУстанови: winget install ffmpeg")
        else:
            await progress_msg.edit_text(f"❌ Ошибка: {str(e)[:150]}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(dp.start_polling(bot))