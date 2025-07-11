from telethon import TelegramClient
from telethon.sessions import StringSession
from dotenv import load_dotenv
import os

load_dotenv()
api_id   = int(os.getenv("TELEGRAM_API_ID") or 0)
api_hash = os.getenv("TELEGRAM_API_HASH")

if not api_id or not api_hash:
    raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env")

# При первом запуске Telethon спросит номер и код, после чего выведет string session
with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("Ваша строковая сессия (StringSession) →")
    print(client.session.save())
