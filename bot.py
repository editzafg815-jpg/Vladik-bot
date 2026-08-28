import asyncio
import logging
import os
import sqlite3

from aiohttp import web
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    BusinessConnection,
    BusinessMessagesDeleted,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.exceptions import TelegramAPIError

# =========================================================
# ВСТАВЬ СЮДА ТОКЕН БОТА
# =========================================================

BOT_TOKEN = "8698964419:AAEakXd2JyHmAiJaHhV7EqFzRL_aqtj_s1A"

# =========================================================

DB_FILE = "business_bot.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
router = Router()

dp.include_router(router)

db = sqlite3.connect(
    DB_FILE,
    check_same_thread=False
)

db.execute("PRAGMA journal_mode=WAL")
db.execute("PRAGMA synchronous=NORMAL")

db.execute("""
CREATE TABLE IF NOT EXISTS connections(
    connection_id TEXT PRIMARY KEY,
    owner_id INTEGER NOT NULL
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS chats(
    connection_id TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    muted INTEGER DEFAULT 0,
    clone_enabled INTEGER DEFAULT 0,
    PRIMARY KEY(connection_id, chat_id)
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS messages(
    connection_id TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    user_id INTEGER,
    name TEXT,
    username TEXT,
    text TEXT,
    date TEXT,
    PRIMARY KEY(connection_id, chat_id, message_id)
)
""")

db.commit()


def get_owner(cid):
    row = db.execute(
        "SELECT owner_id FROM connections WHERE connection_id=?",
        (cid,)
    ).fetchone()

    return row[0] if row else None


def set_mute(cid, chat_id, value):

    db.execute("""
        INSERT INTO chats(
            connection_id,
            chat_id,
            muted
        )
        VALUES (?, ?, ?)

        ON CONFLICT(connection_id, chat_id)
        DO UPDATE SET muted=excluded.muted
    """, (
        cid,
        chat_id,
        int(value)
    ))

    db.commit()


def is_muted(cid, chat_id):

    row = db.execute("""
        SELECT muted
        FROM chats
        WHERE connection_id=? AND chat_id=?
    """, (
        cid,
        chat_id
    )).fetchone()

    return bool(row and row[0])


def set_clone(cid, chat_id, value):

    db.execute("""
        INSERT INTO chats(
            connection_id,
            chat_id,
            clone_enabled
        )
        VALUES (?, ?, ?)

        ON CONFLICT(connection_id, chat_id)
        DO UPDATE SET clone_enabled=excluded.clone_enabled
    """, (
        cid,
        chat_id,
        int(value)
    ))

    db.commit()


def is_clone(cid, chat_id):

    row = db.execute("""
        SELECT clone_enabled
        FROM chats
        WHERE connection_id=? AND chat_id=?
    """, (
        cid,
        chat_id
    )).fetchone()

    return bool(row and row[0])


def save_message(message):

    cid = message.business_connection_id

    if not cid:
        return

    user_id = (
        message.from_user.id
        if message.from_user
        else None
    )

    name = (
        message.from_user.full_name
        if message.from_user
        else "Unknown"
    )

    username = (
        message.from_user.username
        if message.from_user
        else None
    )

    text = (
        message.text
        or message.caption
        or ""
    )

    db.execute("""
        INSERT OR REPLACE INTO messages(
            connection_id,
            chat_id,
            message_id,
            user_id,
            name,
            username,
            text,
            date
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        cid,
        message.chat.id,
        message.message_id,
        user_id,
        name,
        username,
        text,
        str(message.date)
    ))

    db.commit()


async def delete_message(cid, message_id):

    try:

        await bot.delete_business_messages(
            business_connection_id=cid,
            message_ids=[message_id]
        )

        logging.info(
            "MESSAGE DELETED: %s",
            message_id
        )

    except TelegramAPIError as e:

        logging.error(
            "DELETE ERROR: %s",
            e
        )


@router.business_connection()
async def connection_handler(
    connection: BusinessConnection
):

    db.execute("""
        INSERT INTO connections(
            connection_id,
            owner_id
        )
        VALUES (?, ?)

        ON CONFLICT(connection_id)
        DO UPDATE SET owner_id=excluded.owner_id
    """, (
        connection.id,
        connection.user.id
    ))

    db.commit()

    logging.info(
        "BUSINESS CONNECTED: %s",
        connection.id
    )


@router.business_message()
async def business_message_handler(
    message: Message
):

    cid = message.business_connection_id

    if not cid:
        return

    owner = get_owner(cid)

    if not owner:
        return

    save_message(message)

    uid = (
        message.from_user.id
        if message.from_user
        else None
    )

    text = (
        message.text
        or message.caption
        or ""
    ).strip().lower()

    chat_id = message.chat.id

    # =====================================================
    # MUTE
    # =====================================================

    if uid == owner and text == ".mute":

        set_mute(
            cid,
            chat_id,
            True
        )

        await delete_message(
            cid,
            message.message_id
        )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔓 Размутить",
                        callback_data=(
                            f"unmute:{cid}:{chat_id}"
                        )
                    )
                ]
            ]
        )

        try:

            await bot.send_message(
                chat_id=chat_id,
                text=(
                    "🔇 <b>Пользователь замьючен.</b>\n\n"
                    "Его сообщения будут "
                    "автоматически удаляться."
                ),
                parse_mode="HTML",
                reply_markup=keyboard,
                business_connection_id=cid
            )

        except TelegramAPIError as e:

            logging.error(
                "MUTE MESSAGE ERROR: %s",
                e
            )

        return

    # =====================================================
    # UNMUTE
    # =====================================================

    if uid == owner and text == ".unmute":

        set_mute(
            cid,
            chat_id,
            False
        )

        await delete_message(
            cid,
            message.message_id
        )

        try:

            await bot.send_message(
                chat_id=chat_id,
                text="🔊 <b>Пользователь размьючен.</b>",
                parse_mode="HTML",
                business_connection_id=cid
            )

        except TelegramAPIError as e:

            logging.error(
                "UNMUTE ERROR: %s",
                e
            )

        return

    # =====================================================
    # CLONE
    # =====================================================

    if uid == owner and text == ".clone":

        set_clone(
            cid,
            chat_id,
            True
        )

        await delete_message(
            cid,
            message.message_id
        )

        return

    if uid == owner and text == ".unclone":

        set_clone(
            cid,
            chat_id,
            False
        )

        await delete_message(
            cid,
            message.message_id
        )

        return

    # =====================================================
    # AUTO MUTE DELETE
    # =====================================================

    if uid != owner and is_muted(
        cid,
        chat_id
    ):

        asyncio.create_task(
            delete_message(
                cid,
                message.message_id
            )
        )

        return

    # =====================================================
    # CLONE
    # =====================================================

    if uid != owner and is_clone(
        cid,
        chat_id
    ):

        try:

            await bot.copy_message(
                chat_id=chat_id,
                from_chat_id=chat_id,
                message_id=message.message_id,
                business_connection_id=cid
            )

        except TelegramAPIError as e:

            logging.error(
                "CLONE ERROR: %s",
                e
            )


@router.edited_business_message()
async def edited_message_handler(
    message: Message
):

    cid = message.business_connection_id

    if not cid:
        return

    owner = get_owner(cid)

    if not owner:
        return

    old = db.execute("""
        SELECT
            user_id,
            name,
            username,
            text
        FROM messages
        WHERE connection_id=?
        AND chat_id=?
        AND message_id=?
    """, (
        cid,
        message.chat.id,
        message.message_id
    )).fetchone()

    new_text = (
        message.text
        or message.caption
        or "[медиа]"
    )

    if old and old[0] != owner:

        username = (
            f"@{old[2]}"
            if old[2]
            else "без username"
        )

        try:

            await bot.send_message(
                chat_id=owner,
                text=(
                    "✏️ <b>Сообщение изменено</b>\n\n"
                    f"👤 От: <b>{old[1]}</b>\n"
                    f"🆔 {username}\n\n"
                    f"<b>Было:</b>\n"
                    f"{old[3] or '[пусто]'}\n\n"
                    f"<b>Стало:</b>\n"
                    f"{new_text}"
                ),
                parse_mode="HTML"
            )

        except TelegramAPIError as e:

            logging.error(
                "EDIT ERROR: %s",
                e
            )

    save_message(message)


@router.deleted_business_messages()
async def deleted_messages_handler(
    update: BusinessMessagesDeleted
):

    cid = update.business_connection_id

    if not cid:
        return

    owner = get_owner(cid)

    if not owner:
        return

    for message_id in update.message_ids:

        old = db.execute("""
            SELECT
                user_id,
                name,
                username,
                text,
                date
            FROM messages
            WHERE connection_id=?
            AND chat_id=?
            AND message_id=?
        """, (
            cid,
            update.chat.id,
            message_id
        )).fetchone()

        if not old:
            continue

        if old[0] == owner:
            continue

        username = (
            f"@{old[2]}"
            if old[2]
            else "без username"
        )

        try:

            await bot.send_message(
                chat_id=owner,
                text=(
                    "🗑 <b>Сообщение удалено</b>\n\n"
                    f"👤 От: <b>{old[1]}</b>\n"
                    f"🆔 {username}\n"
                    f"🕐 {old[4]}\n\n"
                    f"💬 {old[3] or '[медиа]'}"
                ),
                parse_mode="HTML"
            )

        except TelegramAPIError as e:

            logging.error(
                "DELETED MESSAGE ERROR: %s",
                e
            )


@router.callback_query(
    F.data.startswith("unmute:")
)
async def unmute_button(
    callback: CallbackQuery
):

    try:

        _, cid, chat_id = (
            callback.data.split(":", 2)
        )

        chat_id = int(chat_id)

    except Exception:

        await callback.answer(
            "Ошибка кнопки",
            show_alert=True
        )

        return

    owner = get_owner(cid)

    if (
        not owner
        or callback.from_user.id != owner
    ):

        await callback.answer(
            "⛔ Только владелец.",
            show_alert=True
        )

        return

    set_mute(
        cid,
        chat_id,
        False
    )

    await callback.answer(
        "🔓 Размучено!"
    )

    try:

        await bot.send_message(
            chat_id=chat_id,
            text="🔊 <b>Пользователь размьючен.</b>",
            parse_mode="HTML",
            business_connection_id=cid
        )

    except TelegramAPIError as e:

        logging.error(
            "BUTTON UNMUTE ERROR: %s",
            e
        )


@router.message(F.text == "/start")
async def start_handler(
    message: Message
):

    await message.answer(
        "🤖 <b>Business Bot</b>\n\n"
        "🔇 .mute — включить mute\n"
        "🔊 .unmute — выключить mute\n"
        "📋 .clone — включить clone\n"
        "📋 .unclone — выключить clone",
        parse_mode="HTML"
    )


async def health(request):

    return web.Response(
        text="OK"
    )


async def web_server():

    app = web.Application()

    app.router.add_get(
        "/",
        health
    )

    app.router.add_get(
        "/health",
        health
    )

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port
    )

    await site.start()


async def main():

    if (
        not BOT_TOKEN
        or BOT_TOKEN == "ВСТАВЬ_СЮДА_ТОКЕН"
    ):

        raise RuntimeError(
            "Вставь токен бота в BOT_TOKEN"
        )

    await web_server()

    logging.info(
        "BOT STARTED"
    )

    await dp.start_polling(
        bot,
        allowed_updates=[
            "business_connection",
            "business_message",
            "edited_business_message",
            "deleted_business_messages",
            "callback_query",
            "message"
        ]
    )


if __name__ == "__main__":
    asyncio.run(main())
