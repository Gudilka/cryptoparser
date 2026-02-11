"""Telegram bot entrypoint.

Bot accepts URL from user, downloads page and extracts crypto addresses.
"""

from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import BufferedInputFile, Message

from parser import ParseError, parse_crypto_addresses

logging.basicConfig(level=logging.INFO)

BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"


def render_table(df) -> str:
    """Render dataframe as plain-text table suitable for Telegram messages."""

    if df.empty:
        return "Адреса не найдены."
    return df.to_string(index=False, max_colwidth=80)


async def cmd_start(message: Message) -> None:
    await message.answer(
        "Отправьте ссылку на веб-страницу (http/https), "
        "и я найду BTC / ETH / TRON адреса в тексте страницы."
    )


async def handle_url(message: Message) -> None:
    if not message.text:
        return

    url = message.text.strip()
    status = await message.answer("Загружаю страницу и ищу адреса...")

    try:
        df = await parse_crypto_addresses(url)
    except ParseError as exc:
        await status.edit_text(f"Ошибка: {exc}")
        return
    except Exception:
        logging.exception("Unexpected error while parsing URL")
        await status.edit_text("Внутренняя ошибка при обработке ссылки.")
        return

    # Send preview in message.
    table_text = render_table(df)
    await status.edit_text(f"Найдено адресов: {len(df)}")
    await message.answer(f"<pre>{table_text[:3900]}</pre>", parse_mode="HTML")

    # Send full table as CSV file.
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    await message.answer_document(BufferedInputFile(csv_bytes, filename="crypto_addresses.csv"))


async def main() -> None:
    token = os.getenv(BOT_TOKEN_ENV)
    if not token:
        raise RuntimeError(f"Set {BOT_TOKEN_ENV} env variable before start")

    bot = Bot(token=token)
    dp = Dispatcher()

    dp.message.register(cmd_start, CommandStart())
    dp.message.register(handle_url, F.text)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
