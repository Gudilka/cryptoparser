"""Telegram bot entrypoint.

Bot accepts URL, raw text, or text file and extracts crypto addresses.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import BufferedInputFile, Message

from parser import ParseError, extract_crypto_addresses_from_text, parse_crypto_addresses

logging.basicConfig(level=logging.INFO)

BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
ALT_BOT_TOKEN_ENV = "BOT_TOKEN"
MAX_TEXT_FILE_BYTES = 2 * 1024 * 1024


def get_bot_token() -> str:
    """Read bot token from supported environment variables.

    Supports:
    - TELEGRAM_BOT_TOKEN (primary)
    - BOT_TOKEN (fallback)
    """

    token = os.getenv(BOT_TOKEN_ENV) or os.getenv(ALT_BOT_TOKEN_ENV)
    if token:
        return token.strip()

    raise RuntimeError(
        "Bot token is not configured. Set one of env vars: "
        f"{BOT_TOKEN_ENV} or {ALT_BOT_TOKEN_ENV}.\n"
        "Linux/macOS: export TELEGRAM_BOT_TOKEN='<token>'\n"
        "Windows (cmd): set TELEGRAM_BOT_TOKEN=<token>\n"
        "Windows (PowerShell): $env:TELEGRAM_BOT_TOKEN='<token>'"
    )


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def render_table(df, max_rows: int = 15) -> str:
    """Render dataframe as readable plain-text table for Telegram.

    We build explicit `|`-separated columns so the output is always visually
    split into: source_id | address | chain | context_snippet.
    """

    if df.empty:
        return "Адреса не найдены."

    expected_columns = ["source_id", "address", "chain", "context_snippet"]
    view = df.loc[:, expected_columns].copy()

    # Keep preview compact for Telegram while preserving all columns.
    if len(view) > max_rows:
        view = view.head(max_rows)

    view["source_id"] = view["source_id"].astype(str).str.slice(0, 38)
    view["address"] = view["address"].astype(str).str.slice(0, 44)
    view["chain"] = view["chain"].astype(str).str.slice(0, 8)
    view["context_snippet"] = view["context_snippet"].astype(str).str.slice(0, 60)

    widths = {
        "source_id": max(len("source_id"), view["source_id"].map(len).max()),
        "address": max(len("address"), view["address"].map(len).max()),
        "chain": max(len("chain"), view["chain"].map(len).max()),
        "context_snippet": max(len("context_snippet"), view["context_snippet"].map(len).max()),
    }

    def format_row(row: dict[str, str]) -> str:
        return (
            f"{row['source_id']:<{widths['source_id']}} | "
            f"{row['address']:<{widths['address']}} | "
            f"{row['chain']:<{widths['chain']}} | "
            f"{row['context_snippet']:<{widths['context_snippet']}}"
        )

    header = format_row({k: k for k in expected_columns})
    sep = "-" * len(header)
    rows = [
        format_row(
            {
                "source_id": record["source_id"],
                "address": record["address"],
                "chain": record["chain"],
                "context_snippet": record["context_snippet"],
            }
        )
        for record in view.to_dict(orient="records")
    ]

    output = "\n".join([header, sep, *rows])
    if len(df) > len(view):
        output += f"\n... показаны первые {len(view)} из {len(df)} строк"

    return output


async def send_result_table(message: Message, df) -> None:
    """Send preview table and CSV file with normalized columns."""

    table_text = render_table(df)
    await message.answer(f"<pre>{html.escape(table_text)}</pre>", parse_mode="HTML")

    csv_columns = ["source_id", "address", "chain", "context_snippet"]
    csv_df = df.loc[:, csv_columns]
    # `sep=";"` + UTF-8 BOM improves Excel compatibility in RU locales.
    csv_bytes = csv_df.to_csv(index=False, sep=";").encode("utf-8-sig")
    await message.answer_document(BufferedInputFile(csv_bytes, filename="crypto_addresses.csv"))


async def cmd_start(message: Message) -> None:
    await message.answer(
        "Отправьте ссылку, текст или .txt-файл — "
        "я найду BTC / ETH / TRON адреса и верну таблицу + CSV."
    )


async def handle_text(message: Message) -> None:
    if not message.text:
        return

    payload = message.text.strip()
    if not payload:
        return

    status = await message.answer("Обрабатываю сообщение...")

    try:
        if _is_http_url(payload):
            df = await parse_crypto_addresses(payload)
        else:
            df = extract_crypto_addresses_from_text(payload, source_id=f"message:{message.message_id}")
    except ParseError as exc:
        await status.edit_text(f"Ошибка: {exc}")
        return
    except Exception:
        logging.exception("Unexpected error while parsing text payload")
        await status.edit_text("Внутренняя ошибка при обработке сообщения.")
        return

    await status.edit_text(f"Найдено адресов: {len(df)}")
    await send_result_table(message, df)


def _decode_file_content(data: bytes) -> str:
    """Decode text files with tolerant encoding fallback."""

    for encoding in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ParseError("Не удалось декодировать файл как текст.")


async def handle_document(message: Message, bot: Bot) -> None:
    if not message.document:
        return

    doc = message.document
    status = await message.answer("Считываю файл...")

    if doc.file_size and doc.file_size > MAX_TEXT_FILE_BYTES:
        await status.edit_text("Файл слишком большой. Максимум 2 МБ.")
        return

    try:
        file_info = await bot.get_file(doc.file_id)
        file_bytes = await bot.download_file(file_info.file_path)
        raw = file_bytes.read()
        text = _decode_file_content(raw)

        source_id = doc.file_name or f"file:{doc.file_id}"
        df = extract_crypto_addresses_from_text(text=text, source_id=source_id)
    except ParseError as exc:
        await status.edit_text(f"Ошибка: {exc}")
        return
    except Exception:
        logging.exception("Unexpected error while parsing file payload")
        await status.edit_text("Не удалось обработать файл. Пришлите .txt/.log/.md файл с текстом.")
        return

    await status.edit_text(f"Найдено адресов: {len(df)}")
    await send_result_table(message, df)


async def main() -> None:
    token = get_bot_token()

    bot = Bot(token=token)
    dp = Dispatcher()

    dp.message.register(cmd_start, CommandStart())
    dp.message.register(handle_document, F.document)
    dp.message.register(handle_text, F.text)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
