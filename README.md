# Crypto Telegram Bot

This project is a Telegram bot that provides:
- Top 20 crypto pairs from CoinGecko
- Global market stats from CoinGecko
- Fear & Greed Index from alternative.me
- Coindesk and Cointelegraph RSS news
- Whale alert notifications
- New listings on MEXC

## Prerequisites
- Python 3.8+
- .env file with:
  BOT_TOKEN=...
  CHANNEL_ID=...
  TELEGRAM_API_ID=...
  TELEGRAM_API_HASH=...
  TELETHON_SESSION=...
  OWNER_ID=...

## Installation
```bash
pip install -r requirements.txt
```

## Usage

Generate Telethon session:
```bash
python src/create_session.py
```
Follow prompts and update .env with the generated TELETHON_SESSION.

Run the bot:
```bash
python src/main.py
```

## Deployment on VPS

1. Clone the repository.
2. Copy your `.env`.
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Use `screen` or `tmux`, or set up systemd:

   Create `crypto-bot.service`:
   ```
   [Unit]
   Description=Crypto Telegram Bot
   After=network.target

   [Service]
   Type=simple
   WorkingDirectory=/path/to/reorganized_project
   ExecStart=/usr/bin/python3 src/main.py
   Restart=always
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```

5. Enable and start:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable crypto-bot.service
   sudo systemctl start crypto-bot.service
   ```
