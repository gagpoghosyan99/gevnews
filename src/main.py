# src/main.py

import os
import asyncio

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiohttp import ClientSession

# ─── Load environment ──────────────────────────────────────────────────────────
load_dotenv()

API_ID       = int(os.getenv("TELEGRAM_API_ID"))
API_HASH     = os.getenv("TELEGRAM_API_HASH")
SESSION_STR  = os.getenv("TELETHON_SESSION")   # Telethon StringSession
BOT_TOKEN    = os.getenv("BOT_TOKEN")
CHANNEL_ID   = int(os.getenv("CHANNEL_ID"))
OWNER_ID     = int(os.getenv("OWNER_ID"))
NEWSAPI_KEY  = os.getenv("NEWSAPI_KEY")

# ─── Clients & scheduler ───────────────────────────────────────────────────────
tele_client = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)
bot         = Bot(token=BOT_TOKEN)
dp          = Dispatcher(bot)
scheduler   = AsyncIOScheduler()

# ─── Helper for channel posts ──────────────────────────────────────────────────
async def send_channel_message(text: str):
    await tele_client.send_message(CHANNEL_ID, text)

# ─── Commands ─────────────────────────────────────────────────────────────────
@dp.message.register(Command("start"), Command("help"))
async def cmd_start(message: types.Message):
    await message.reply(
        "Բարև ձեզ!\n"
        "/latest      — Վերջին 5 նորություններ\n"
        "/testnotify  — Փորձարկել կյանար փոփոխումները"
    )

@dp.message.register(Command("latest"))
async def cmd_latest(message: types.Message):
    async with ClientSession() as session:
        url = (
            f"https://newsapi.org/v2/top-headlines?"
            f"apiKey={NEWSAPI_KEY}&language=en&pageSize=5"
        )
        resp = await session.get(url)
        data = await resp.json()
    titles = [f"• {a['title']}" for a in data.get("articles", [])]
    text = "📰 Վերջին նորություններ:\n" + ("\n".join(titles) or "Չկա տվյալ")
    await message.reply(text)
    await send_channel_message(f"📰 Պատրաստ են թոփ նորություններ:\n{text}")

@dp.message.register(Command("testnotify"))
async def cmd_testnotify(message: types.Message):
    await message.reply("📤 Ուղարկում եմ փորձնական հայտարարություն…")
    try:
        await send_channel_message("✅ Channel notification is working!")
        await message.reply("✅ Հաջողվեց ուղարկել channel-ին։")
    except Exception as e:
        await message.reply(f"❌ Վերադարձավ սխալ՝ {e!r}")

# ─── Scheduled heartbeat ───────────────────────────────────────────────────────
def schedule_jobs():
    scheduler.add_job(
        lambda: asyncio.create_task(send_channel_message("⏰ Still alive!")),
        trigger="interval",
        hours=1
    )
    scheduler.start()

# ─── Startup routine ───────────────────────────────────────────────────────────
async def on_startup():
    # Start Telethon so send_channel_message works
    await tele_client.start(bot_token=BOT_TOKEN)
    # Schedule recurring jobs
    schedule_jobs()
    # Notify owner
    await bot.send_message(OWNER_ID, "🤖 Bot is now online!")

# ─── Entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    dp.run_polling(
        on_startup=on_startup,
        skip_updates=True
    )
