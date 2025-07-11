import os
import logging
import asyncio
import re
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import aiohttp
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.errors import ChannelPrivateError, FloodWaitError
import xml.etree.ElementTree as ET

# APScheduler imports
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# ---------------------- Настройка окружения ----------------------
load_dotenv()
API_TOKEN         = os.getenv("BOT_TOKEN")
CHANNEL_ID        = os.getenv("CHANNEL_ID")         # Например, "-1001234567890"
TELEGRAM_API_ID   = os.getenv("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
TELETHON_SESSION  = os.getenv("TELETHON_SESSION")
OWNER_ID          = int(os.getenv("OWNER_ID"))      # Ваш user_id

# Проверяем, что всё есть
for var_name in ("BOT_TOKEN", "CHANNEL_ID", "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELETHON_SESSION"):
    if not os.getenv(var_name):
        raise RuntimeError(f"{var_name} is not set in .env")

# --- Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------- Константы ----------------------
COINGECKO_MARKETS_URL   = "https://api.coingecko.com/api/v3/coins/markets"
COINGECKO_GLOBAL_URL    = "https://api.coingecko.com/api/v3/global"
FEAR_GREED_URL          = "https://api.alternative.me/fng/"

COINDESK_RSS_URL       = "https://www.coindesk.com/arc/outboundfeeds/rss/"
COINTELEGRAPH_RSS_URL  = "https://cointelegraph.com/rss"

WHALE_ALERT_CHANNEL   = "whale_alert_io"
MEXC_LISTINGS_CHANNEL = "mexc_listings_tracker"

# Временная зона GMT+4
GMT_PLUS_4 = timezone(timedelta(hours=4))

# ---------------------- Инициализация клиентов ----------------------
bot = Bot(token=API_TOKEN)
dp  = Dispatcher()

telethon_client = TelegramClient(
    StringSession(TELETHON_SESSION),
    api_id=int(TELEGRAM_API_ID),
    api_hash=TELEGRAM_API_HASH
)

# ---------------------- Вспомогательные функции ----------------------

async def safe_send(chat_id: int | str, text: str):
    """
    Отправляем сообщение в chat_id (канал или личку).
    При таймаутах или ошибках просто логируем, но не падаем.
    """
    for attempt in range(1, 4):
        try:
            await bot.send_message(chat_id, text, parse_mode="HTML", request_timeout=30)
            return
        except Exception as e:
            logger.warning("safe_send: попытка %d/3 неудачна (%s).", attempt, e)
            await asyncio.sleep(2)
    logger.error("safe_send: все 3 попытки отправки в chat_id=%s не удались.", chat_id)


async def retry_get(session: aiohttp.ClientSession, url: str, params: dict = None,
                    retries: int = 3, delay: int = 5) -> dict | list | None:
    """
    Делаем GET-запрос к url с параметрами params.
    Если не удаётся – повторяем до retries раз с задержкой delay секунд.
    Возвращаем десериализованный JSON (dict или list) либо None.
    """
    for attempt in range(1, retries + 1):
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                # Предпочитаем JSON
                return await resp.json()
        except Exception as e:
            if attempt == retries:
                logger.error("retry_get: не удалось получить %s после %d попыток: %s", url, retries, e)
                return None
            else:
                logger.warning("retry_get: ошибка при запросе %s: %s – повтор через %d сек (попытка %d/%d)",
                               url, e, delay, attempt, retries)
                await asyncio.sleep(delay)


# ---------------------- Основной функционал ----------------------

###########################################
# 1) Top 20 Crypto Pairs (USD)           #
###########################################
async def fetch_top_pairs(chat_id: int | str):
    """
    Получает топ-20 пар из CoinGecko и отправляет в chat_id.
    Формат заголовка: "🔝 Top 20 Crypto Pairs (USD) @Armcryptonews"
    """
    async with aiohttp.ClientSession() as session:
        data = await retry_get(
            session,
            COINGECKO_MARKETS_URL,
            params={
                "vs_currency": "usd",
                "order":       "market_cap_desc",
                "per_page":    20,
                "page":        1,
                "price_change_percentage": "24h"
            },
            retries=3,
            delay=5
        )

    # Проверяем, что вернулся список
    if not isinstance(data, list):
        await safe_send(chat_id, "<b>🔝 Top 20 Crypto Pairs (USD) @Armcryptonews</b>\n\n❌ Неверный формат ответа от CoinGecko.")
        return

    if len(data) == 0:
        await safe_send(chat_id, "<b>🔝 Top 20 Crypto Pairs (USD) @Armcryptonews</b>\n\n• Данных нет.")
        return

    # Собираем текст
    title = "<b>🔝 Top 20 Crypto Pairs (USD) @Armcryptonews</b>\n\n"
    body_lines = []
    for coin in data:
        if not isinstance(coin, dict) or 'symbol' not in coin:
            await safe_send(chat_id, "<b>🔝 Top 20 Crypto Pairs (USD) @Armcryptonews</b>\n\n❌ Неожиданный формат записи.")
            return

        sym   = coin.get('symbol', '').upper()
        price = coin.get('current_price', 0)
        chg   = coin.get('price_change_percentage_24h', 0)
        emoji = "🔴" if chg < 0 else "🟢"
        body_lines.append(f"• <code>{sym}/USD</code>: <b>${price:,.2f}</b> | <i>{chg:+.2f}%</i> {emoji}")

    text = title + "\n".join(body_lines)
    await safe_send(chat_id, text)


###########################################
# 2) Fear & Greed Index (FNG)            #
###########################################
async def fetch_fear_greed(chat_id: int | str):
    """
    Формат заголовка: "😱 Fear & Greed Index @Armcryptonews"
    """
    async with aiohttp.ClientSession() as session:
        try:
            resp = await session.get(FEAR_GREED_URL, timeout=aiohttp.ClientTimeout(total=30))
            data = await resp.json()
        except Exception as e:
            logger.error("fetch_fear_greed error: %s", e)
            await safe_send(chat_id, "<b>😱 Fear & Greed Index @Armcryptonews</b>\n\n❌ Не удалось получить данные.")
            return

    idx   = data.get('data', [{}])[0] if isinstance(data, dict) else {}
    value = idx.get('value', '—')
    cls   = idx.get('value_classification', '—')

    now = datetime.now(timezone.utc).astimezone(GMT_PLUS_4).strftime("%Y-%m-%d %H:%M GMT+4")
    text = (
        "<b>😱 Fear & Greed Index @Armcryptonews</b>\n\n"
        f"<i>{now}</i>\n"
        f"• Current Value: <b>{value}</b>\n"
        f"• Classification: <i>{cls}</i>"
    )
    await safe_send(chat_id, text)


###########################################
# 3) Top 10 Gainers & Losers (24h)       #
###########################################
async def fetch_gainers_losers(chat_id: int | str):
    """
    Формат заголовка: "📊 Top 10 Gainers & Losers (24h) @Armcryptonews"
    """
    async with aiohttp.ClientSession() as session:
        data = await retry_get(
            session,
            COINGECKO_MARKETS_URL,
            params={
                "vs_currency": "usd",
                "order":       "market_cap_desc",
                "per_page":    100,
                "page":        1,
                "price_change_percentage": "24h"
            },
            retries=3,
            delay=5
        )

    if not isinstance(data, list):
        await safe_send(chat_id, "<b>📊 Top 10 Gainers & Losers (24h) @Armcryptonews</b>\n\n❌ Неверный формат ответа от CoinGecko.")
        return

    if len(data) == 0:
        await safe_send(chat_id, "<b>📊 Top 10 Gainers & Losers (24h) @Armcryptonews</b>\n\n• Данных нет.")
        return

    for item in data:
        if not isinstance(item, dict) or 'price_change_percentage_24h' not in item:
            await safe_send(chat_id, "<b>📊 Top 10 Gainers & Losers (24h) @Armcryptonews</b>\n\n❌ Неожиданный формат записи.")
            return

    sorted_data = sorted(data, key=lambda x: x.get('price_change_percentage_24h', 0) or 0)
    losers = sorted_data[:10]
    gainers = sorted_data[-10:][::-1]

    now = datetime.now(timezone.utc).astimezone(GMT_PLUS_4).strftime("%Y-%m-%d %H:%M GMT+4")
    title = f"<b>📊 Top 10 Gainers & Losers (24h) @Armcryptonews | {now}</b>\n\n"

    lines = ["<i>Top 10 Gainers:</i>"]
    for coin in gainers:
        sym   = coin.get('symbol', '').upper()
        chg   = coin.get('price_change_percentage_24h', 0)
        price = coin.get('current_price', 0)
        lines.append(f"• <code>{sym}</code>: <b>{chg:+.2f}%</b> ({price:,.2f} USD) 🟢")

    lines.append("")  # пустая строка разделения
    lines.append("<i>Top 10 Losers:</i>")
    for coin in losers:
        sym   = coin.get('symbol', '').upper()
        chg   = coin.get('price_change_percentage_24h', 0)
        price = coin.get('current_price', 0)
        lines.append(f"• <code>{sym}</code>: <b>{chg:+.2f}%</b> ({price:,.2f} USD) 🔴")

    text = title + "\n".join(lines)
    await safe_send(chat_id, text)


###########################################
# 4) Global Market Cap & 24h Volume      #
###########################################
async def fetch_global_stats(chat_id: int | str):
    """
    Формат заголовка: "🌐 Global Crypto Stats @Armcryptonews"
    """
    async with aiohttp.ClientSession() as session:
        try:
            resp = await session.get(COINGECKO_GLOBAL_URL, timeout=aiohttp.ClientTimeout(total=30))
            data = await resp.json()
        except Exception as e:
            logger.error("fetch_global_stats error: %s", e)
            await safe_send(chat_id, "<b>🌐 Global Crypto Stats @Armcryptonews</b>\n\n❌ Не удалось получить данные.")
            return

    d = data.get('data', {}) if isinstance(data, dict) else {}
    total_mc = d.get('total_market_cap', {}).get('usd', 0)
    mc_change = d.get('market_cap_change_percentage_24h_usd', 0)
    total_vol = d.get('total_volume', {}).get('usd', 0)

    now = datetime.now(timezone.utc).astimezone(GMT_PLUS_4).strftime("%Y-%m-%d %H:%M GMT+4")
    text = (
        "<b>🌐 Global Crypto Stats @Armcryptonews</b>\n\n"
        f"<i>{now}</i>\n"
        f"• Total Market Cap: <b>${total_mc:,.0f}</b>\n"
        f"  • 24h Change: <i>{mc_change:+.2f}%</i>\n\n"
        f"• 24h Total Volume: <b>${total_vol:,.0f}</b>"
    )
    await safe_send(chat_id, text)


##################################
# 5) Whale Alerts (from TG)      #
##################################
async def fetch_whale_alerts_from_tg(chat_id: int | str):
    """
    Формат заголовка: "🐋 Whale Alerts @Armcryptonews"
    """
    if not telethon_client.is_connected():
        await telethon_client.connect()

    try:
        entity = await telethon_client.get_entity(WHALE_ALERT_CHANNEL)
    except ChannelPrivateError:
        await safe_send(chat_id, "<b>🐋 Whale Alerts @Armcryptonews</b>\n\n❌ Cannot access @whale_alert_io.")
        return
    except Exception as e:
        logger.error("Telethon get_entity error: %s", e)
        await safe_send(chat_id, "<b>🐋 Whale Alerts @Armcryptonews</b>\n\n❌ Ошибка доступа к каналу.")
        return

    try:
        messages = await asyncio.wait_for(
            telethon_client.get_messages(entity, limit=5),
            timeout=20
        )
    except asyncio.TimeoutError:
        logger.error("Telethon: timeout while fetching messages")
        await safe_send(chat_id, "<b>🐋 Whale Alerts @Armcryptonews</b>\n\n❌ Таймаут при получении сообщений.")
        return
    except Exception as e:
        logger.error("Telethon get_messages error: %s", e)
        await safe_send(chat_id, "<b>🐋 Whale Alerts @Armcryptonews</b>\n\n❌ Ошибка при получении сообщений.")
        return

    if not messages:
        await safe_send(chat_id, "<b>🐋 Whale Alerts @Armcryptonews</b>\n\n• Недавних алертов нет.")
        return

    now = datetime.now(timezone.utc).astimezone(GMT_PLUS_4).strftime("%Y-%m-%d %H:%M GMT+4")
    lines = []
    for msg in messages:
        dt_gmt4 = msg.date.astimezone(GMT_PLUS_4).strftime("%Y-%m-%d %H:%M GMT+4")
        text = msg.message or ""
        lines.append(f"{dt_gmt4}\n{text}")

    full_text = f"<b>🐋 Whale Alerts @Armcryptonews | {now}</b>\n\n" + "\n\n".join(lines)
    await safe_send(chat_id, full_text)


###################################################
# 6) MEXC Listings (Spot/Futures) from TG        #
###################################################
async def fetch_mexc_listings_from_tg(chat_id: int | str):
    """
    Формат заголовка: "🆕 MEXC Listings @Armcryptonews"
    """
    if not telethon_client.is_connected():
        await telethon_client.connect()

    try:
        entity = await telethon_client.get_entity(MEXC_LISTINGS_CHANNEL)
    except ChannelPrivateError:
        await safe_send(chat_id, "<b>🆕 MEXC Listings @Armcryptonews</b>\n\n❌ Cannot access @mexc_listings_tracker.")
        return
    except Exception as e:
        logger.error("Telethon get_entity error (MEXC): %s", e)
        await safe_send(chat_id, "<b>🆕 MEXC Listings @Armcryptonews</b>\n\n❌ Ошибка доступа.")
        return

    try:
        messages = await asyncio.wait_for(
            telethon_client.get_messages(entity, limit=5),
            timeout=20
        )
    except asyncio.TimeoutError:
        logger.error("Telethon: timeout while fetching MEXC listings")
        await safe_send(chat_id, "<b>🆕 MEXC Listings @Armcryptonews</b>\n\n❌ Таймаут при получении сообщений.")
        return
    except Exception as e:
        logger.error("Telethon get_messages error (MEXC): %s", e)
        await safe_send(chat_id, "<b>🆕 MEXC Listings @Armcryptonews</b>\n\n❌ Ошибка при получении.")
        return

    if not messages:
        await safe_send(chat_id, "<b>🆕 MEXC Listings @Armcryptonews</b>\n\n• Нет недавних листингов.")
        return

    now = datetime.now(timezone.utc).astimezone(GMT_PLUS_4).strftime("%Y-%m-%d %H:%M GMT+4")
    lines = []
    for msg in messages:
        text = msg.message or ""
        dt_gmt4 = msg.date.astimezone(GMT_PLUS_4).strftime("%Y-%m-%d %H:%M GMT+4")
        match = re.search(r"(?:пара|pair)\s+([A-Z0-9]+)", text, re.IGNORECASE)
        if match:
            pair = match.group(1)
            if re.search(r"(?:спот|spot)", text, re.IGNORECASE):
                listing_type = "New spot pair"
            elif re.search(r"(?:фьючерс|futures?)", text, re.IGNORECASE):
                listing_type = "New futures pair"
            else:
                listing_type = "New pair"
            lines.append(f"{dt_gmt4}\n{listing_type} {pair}")
        else:
            cand = re.findall(r"\b([A-Z0-9]{6,12})\b", text)
            for pair in cand:
                if re.search(r"(?:спот|spot)", text, re.IGNORECASE):
                    listing_type = "New spot pair"
                elif re.search(r"(?:фьючерс|futures?)", text, re.IGNORECASE):
                    listing_type = "New futures pair"
                else:
                    listing_type = "New pair"
                lines.append(f"{dt_gmt4}\n{listing_type} {pair}")

    full_text = f"<b>🆕 MEXC Listings @Armcryptonews | {now}</b>\n\n" + ("\n\n".join(lines) if lines else "No valid listings found.")
    await safe_send(chat_id, full_text)


##################################
# 7) Latest Crypto News (RSS)    #
##################################
async def fetch_rss_feed(rss_url: str) -> list[tuple[str, str, str]]:
    """
    Возвращает список из кортежей (title, link, date_str).
    """
    async with aiohttp.ClientSession() as session:
        try:
            resp = await session.get(rss_url, timeout=aiohttp.ClientTimeout(total=30))
            text = await resp.text()
        except Exception as e:
            logger.error("RSS fetch error (%s): %s", rss_url, e)
            return []

    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        logger.error("RSS parse error (%s): %s", rss_url, e)
        return []

    items = []
    for item in root.findall(".//item")[:5]:
        title = item.findtext("title", default="").strip()
        link  = item.findtext("link", default="").strip()
        pub_date = item.findtext("pubDate", default="").strip()
        try:
            dt = datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
            dt_gmt4 = dt.astimezone(GMT_PLUS_4)
            date_str = dt_gmt4.strftime("%Y-%m-%d %H:%M GMT+4")
        except Exception:
            date_str = ""
        items.append((title, link, date_str))
    return items


async def fetch_crypto_rss_news(chat_id: int | str):
    """
    Заголовок: "📰 Latest Crypto News @Armcryptonews"
    """
    coindesk_items      = await fetch_rss_feed(COINDESK_RSS_URL)
    cointelegraph_items = await fetch_rss_feed(COINTELEGRAPH_RSS_URL)

    combined = coindesk_items + cointelegraph_items
    seen = set()
    unique_items = []
    for title, link, date_str in combined:
        if link in seen:
            continue
        seen.add(link)
        unique_items.append((title, link, date_str))
        if len(unique_items) >= 5:
            break

    now = datetime.now(timezone.utc).astimezone(GMT_PLUS_4).strftime("%Y-%m-%d %H:%M GMT+4")
    header = f"<b>📰 Latest Crypto News @Armcryptonews | {now}</b>\n\n"

    if not unique_items:
        await safe_send(chat_id, header + "• No recent RSS articles found.\n")
        return

    body_lines = []
    for headline, link, date_str in unique_items:
        if date_str:
            body_lines.append(f"• {headline} ({date_str})\n  {link}")
        else:
            body_lines.append(f"• {headline}\n  {link}")

    await safe_send(chat_id, header + "\n".join(body_lines))


# ---------------------- Настройка APScheduler ----------------------

scheduler = AsyncIOScheduler(timezone="UTC")

# 07:00 GMT+4 → 03:00 UTC (Top 20 Pairs)
trigger_top_pairs_0700 = CronTrigger(hour=3, minute=0, timezone="UTC")
# 10:00 GMT+4 → 06:00 UTC (Top 20 Pairs повторно)
trigger_top_pairs_1000 = CronTrigger(hour=6, minute=0, timezone="UTC")
# 10:30 GMT+4 → 06:30 UTC (MEXC Listings)
trigger_mexc_1030 = CronTrigger(hour=6, minute=30, timezone="UTC")
# 11:00 GMT+4 → 07:00 UTC (Fear & Greed)
trigger_fear_1100 = CronTrigger(hour=7, minute=0, timezone="UTC")
# 13:00 GMT+4 → 09:00 UTC (Top 10 Gainers & Losers)
trigger_gainers_1300 = CronTrigger(hour=9, minute=0, timezone="UTC")
# 15:00 GMT+4 → 11:00 UTC (Global Market Cap & 24h Volume)
trigger_global_1500 = CronTrigger(hour=11, minute=0, timezone="UTC")
# 16:00 GMT+4 → 12:00 UTC (Whale Alerts)
trigger_whale_1600 = CronTrigger(hour=12, minute=0, timezone="UTC")
# 23:00 GMT+4 → 19:00 UTC (Latest Crypto News)
trigger_news_2300 = CronTrigger(hour=19, minute=0, timezone="UTC")

# Автоматические задачи шлют в канал (CHANNEL_ID)
scheduler.add_job(lambda: fetch_top_pairs(CHANNEL_ID), trigger_top_pairs_0700, id="daily_top_pairs_0700")
scheduler.add_job(lambda: fetch_top_pairs(CHANNEL_ID), trigger_top_pairs_1000, id="daily_top_pairs_1000")
scheduler.add_job(lambda: fetch_mexc_listings_from_tg(CHANNEL_ID), trigger_mexc_1030, id="daily_mexc_listings_1030")
scheduler.add_job(lambda: fetch_fear_greed(CHANNEL_ID), trigger_fear_1100, id="daily_fear_greed_1100")
scheduler.add_job(lambda: fetch_gainers_losers(CHANNEL_ID), trigger_gainers_1300, id="daily_gainers_losers_1300")
scheduler.add_job(lambda: fetch_global_stats(CHANNEL_ID), trigger_global_1500, id="daily_global_stats_1500")
scheduler.add_job(lambda: fetch_whale_alerts_from_tg(CHANNEL_ID), trigger_whale_1600, id="daily_whale_alerts_1600")
scheduler.add_job(lambda: fetch_crypto_rss_news(CHANNEL_ID), trigger_news_2300, id="daily_news_2300")

# Не вызываем scheduler.start() здесь — запустим внутри main()


# ---------------------- Хендлер /start ----------------------

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id

    # Если владелец (OWNER_ID), публикуем сразу в канал
    if user_id == OWNER_ID:
        await fetch_top_pairs(CHANNEL_ID)
        await fetch_fear_greed(CHANNEL_ID)
        await fetch_gainers_losers(CHANNEL_ID)
        await fetch_global_stats(CHANNEL_ID)
        await fetch_whale_alerts_from_tg(CHANNEL_ID)
        await fetch_mexc_listings_from_tg(CHANNEL_ID)
        await fetch_crypto_rss_news(CHANNEL_ID)
        await message.reply("✅ Данные отправлены в канал.")
    else:
        # Всем остальным присылаем лично (в чат пользователя)
        await fetch_top_pairs(message.chat.id)
        await fetch_fear_greed(message.chat.id)
        await fetch_gainers_losers(message.chat.id)
        await fetch_global_stats(message.chat.id)
        await fetch_whale_alerts_from_tg(message.chat.id)
        await fetch_mexc_listings_from_tg(message.chat.id)
        await fetch_crypto_rss_news(message.chat.id)
        await message.reply("✅ Вот вся актуальная информация лично для вас.")


# ---------------------- Запуск бота и APScheduler ----------------------

async def main():
    # Подключаем Telethon
    await telethon_client.connect()

    # Запускаем шедулер (APScheduler) внутри работающего event loop
    scheduler.start()

    # Запускаем Aiogram-поллинг
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
