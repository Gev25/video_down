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
import psycopg2
from psycopg2.extras import DictCursor

# ====================== НАСТРОЙКИ ======================
TOKEN = "8704315086:AAEERRGJXDW_7jDDnYark03jX4MqVRIlM1c"
DATABASE_URL = "postgresql://postgres:cbvOUgTdLWosjghWTlvprkFZTrIYwGDy@postgres.railway.internal:5432/railway"
YOUR_ADMIN_ID = 1381500667

# ←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←
# Если хочешь использовать прокси — вставь сюда (формат: http://user:pass@ip:port)
PROXY = None   # Пример: "http://123.45.67.89:8080"
# ←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←

bot = Bot(token=TOKEN)
dp = Dispatcher()

os.makedirs("downloads", exist_ok=True)

# ====================== POSTGRESQL ======================
conn = psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor)
conn.autocommit = True
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY, downloads_today INTEGER DEFAULT 0, 
last_reset DATE DEFAULT CURRENT_DATE, is_premium BOOLEAN DEFAULT FALSE, premium_until TIMESTAMP);
CREATE TABLE IF NOT EXISTS payments (id SERIAL PRIMARY KEY, user_id BIGINT, amount INTEGER, 
tariff TEXT, date TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS temp_links (link_id TEXT PRIMARY KEY, url TEXT, created TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
""")

def get_user(user_id: int):
    cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
    user = cur.fetchone()
    if not user:
        cur.execute("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
        conn.commit()
        return {"user_id": user_id, "downloads_today": 0, "last_reset": datetime.now().date(), "is_premium": False, "premium_until": None}
    return dict(user)

def save_link(url: str):
    link_id = str(uuid4())[:12]
    cur.execute("INSERT INTO temp_links (link_id, url) VALUES (%s, %s)", (link_id, url))
    conn.commit()
    return link_id

def get_link(link_id: str):
    cur.execute("SELECT * FROM temp_links WHERE link_id = %s", (link_id,))
    result = cur.fetchone()
    return dict(result) if result else None

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
        'retries': 10,
        'geo_bypass': True,
        'geo_bypass_country': 'US',
        'extractor_args': {'youtube': {'player_client': ['ios', 'web', 'android']}},
    }

    if PROXY:
        opts['proxy'] = PROXY

    if 'tiktok.com' in url:
        opts.update({'format': 'best'})
    elif 'instagram' in url or 'instagr.am' in url:
        opts['format'] = 'best[height<=1080]'
    else:
        height = {"360": 360, "720": 720, "1080": 1080}.get(quality, 720)
        opts['format'] = f'best[height<={height}]'

    if is_audio:
        opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],
        })

    return opts


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


# ====================== ЛИМИТЫ И HANDLERS (без изменений) ======================
async def check_limit(user_id: int, message) -> bool:
    cur.execute("UPDATE users SET downloads_today = 0 WHERE last_reset < CURRENT_DATE")
    cur.execute("UPDATE users SET last_reset = CURRENT_DATE WHERE last_reset < CURRENT_DATE")
    conn.commit()

    user = get_user(user_id)

    if user_id == YOUR_ADMIN_ID:
        return True
    if user.get("is_premium") and user.get("premium_until") and user["premium_until"] > datetime.now():
        return True
    if user.get("downloads_today", 0) >= 5:
        await message.answer("⛔ Лимит 5 скачиваний в день исчерпан.\n\n/premium")
        return False

    cur.execute("UPDATE users SET downloads_today = downloads_today + 1 WHERE user_id = %s", (user_id,))
    conn.commit()
    return True


# (Остальной код handlers остаётся тот же — start, premium, handle_links, download_callback)

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("🎥 <b>MediaFlow Bot</b>\n\nОтправь ссылку.", parse_mode="HTML")

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
            [InlineKeyboardButton(text="⚡ 360p", callback_data=f"dl:{link_id}:360")],
            [InlineKeyboardButton(text="🎥 720p", callback_data=f"dl:{link_id}:720")],
            [InlineKeyboardButton(text="🎥 1080p", callback_data=f"dl:{link_id}:1080")],
            [InlineKeyboardButton(text="🔊 MP3", callback_data=f"dl:{link_id}:audio")],
        ])
        await message.answer("🔗 Выбери качество:", reply_markup=kb)

@dp.callback_query(F.data.startswith("dl:"))
async def download_callback(callback: types.CallbackQuery):
    if not await check_limit(callback.from_user.id, callback.message):
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

        if not is_audio and file_path.endswith(('.mp4', '.webm')):
            file_path = await compress_video(file_path, progress_msg)
            size_mb = os.path.getsize(file_path) / (1024 * 1024)

        caption = f"✅ {title[:120]}\n📏 {size_mb:.1f} МБ"

        if size_mb > 50:
            await callback.message.answer_document(types.FSInputFile(file_path), caption=caption)
        elif is_audio:
            await callback.message.answer_audio(types.FSInputFile(file_path), caption=caption)
        else:
            await callback.message.answer_video(types.FSInputFile(file_path), caption=caption, supports_streaming=True)

        if os.path.exists(file_path):
            os.remove(file_path)
        await progress_msg.delete()

    except Exception as e:
        await progress_msg.edit_text(f"❌ Ошибка: {str(e)[:150]}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(dp.start_polling(bot))