import asyncio
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
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

BOT_TOKEN = "8698964419:AAGBF8RLS2fOHMCPAqpibJIUxxjd64mD6a8"

DB_FILE = "business_bot.db"
MUTE_DELETE_DELAY = 0

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

db = sqlite3.connect(DB_FILE, check_same_thread=False)

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
    PRIMARY KEY(connection_id,chat_id)
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
    PRIMARY KEY(connection_id,chat_id,message_id)
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
        INSERT INTO chats(connection_id,chat_id,muted)
        VALUES(?,?,?)
        ON CONFLICT(connection_id,chat_id)
        DO UPDATE SET muted=excluded.muted
    """, (cid, chat_id, int(value)))
    db.commit()


def is_muted(cid, chat_id):
    row = db.execute("""
        SELECT muted FROM chats
        WHERE connection_id=? AND chat_id=?
    """, (cid, chat_id)).fetchone()
    return bool(row and row[0])


def set_clone(cid, chat_id, value):
    db.execute("""
        INSERT INTO chats(connection_id,chat_id,clone_enabled)
        VALUES(?,?,?)
        ON CONFLICT(connection_id,chat_id)
        DO UPDATE SET clone_enabled=excluded.clone_enabled
    """, (cid, chat_id, int(value)))
    db.commit()


def is_clone(cid, chat_id):
    row = db.execute("""
        SELECT clone_enabled FROM chats
        WHERE connection_id=? AND chat_id=?
    """, (cid, chat_id)).fetchone()
    return bool(row and row[0])


def save_message(message):
    cid = message.business_connection_id

    if not cid:
        return

    user_id = message.from_user.id if message.from_user else None
    name = message.from_user.full_name if message.from_user else "Unknown"
    username = message.from_user.username if message.from_user else None
    text = message.text or message.caption or ""

    db.execute("""
        INSERT OR REPLACE INTO messages
        VALUES(?,?,?,?,?,?,?,?)
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


@router.business_connection()
async def connection_handler(connection: BusinessConnection):

    db.execute("""
        INSERT INTO connections(connection_id,owner_id)
        VALUES(?,?)
        ON CONFLICT(connection_id)
        DO UPDATE SET owner_id=excluded.owner_id
    """, (
        connection.id,
        connection.user.id
    ))

    db.commit()


@router.business_message()
async def business_message_handler(message: Message):

    cid = message.business_connection_id

    if not cid:
        return

    owner = get_owner(cid)

    if not owner:
        return

    save_message(message)

    uid = message.from_user.id if message.from_user else None
    text = (message.text or message.caption or "").strip()

    # =========================
    # MUTE
    # =========================

    if uid == owner and text.lower() == ".mute":

        set_mute(cid, message.chat.id, True)

        try:
            await bot.delete_business_messages(
                business_connection_id=cid,
                message_ids=[message.message_id]
            )
        except Exception:
            pass

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔓 Размутить",
                        callback_data=f"unmute:{cid}:{message.chat.id}"
                    )
                ]
            ]
        )

        try:
            await bot.send_message(
                chat_id=message.chat.id,
                text=(
                    "🔇 <b>Вы больше не можете говорить.</b>\n\n"
                    "Ваши сообщения будут автоматически удаляться."
                ),
                parse_mode="HTML",
                reply_markup=keyboard,
                business_connection_id=cid
            )
        except Exception:
            pass

        return

    # =========================
    # UNMUTE
    # =========================

    if uid == owner and text.lower() == ".unmute":

        set_mute(cid, message.chat.id, False)

        try:
            await bot.delete_business_messages(
                business_connection_id=cid,
                message_ids=[message.message_id]
            )
        except Exception:
            pass

        try:
            await bot.send_message(
                chat_id=message.chat.id,
                text="🔊 <b>Вы снова можете говорить.</b>",
                parse_mode="HTML",
                business_connection_id=cid
            )
        except Exception:
            pass

        return

    # =========================
    # CLONE
    # =========================

    if uid == owner and text.lower() == ".clone":

        set_clone(cid, message.chat.id, True)

        try:
            await bot.delete_business_messages(
                business_connection_id=cid,
                message_ids=[message.message_id]
            )
        except Exception:
            pass

        return

    if uid == owner and text.lower() == ".unclone":

        set_clone(cid, message.chat.id, False)

        try:
            await bot.delete_business_messages(
                business_connection_id=cid,
                message_ids=[message.message_id]
            )
        except Exception:
            pass

        return

    # =========================
    # MUTE AUTO DELETE
    # =========================

    if uid != owner and is_muted(cid, message.chat.id):

        try:
            await bot.delete_business_messages(
                business_connection_id=cid,
                message_ids=[message.message_id]
            )
        except Exception:
            pass

        return

    # =========================
    # CLONE
    # =========================

    if uid != owner and is_clone(cid, message.chat.id):

        try:
            await bot.copy_message(
                chat_id=message.chat.id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                business_connection_id=cid
            )
        except Exception:
            pass


@router.edited_business_message()
async def edited_message_handler(message: Message):

    cid = message.business_connection_id

    if not cid:
        return

    owner = get_owner(cid)

    if not owner:
        return

    old = db.execute("""
        SELECT user_id,name,username,text
        FROM messages
        WHERE connection_id=?
        AND chat_id=?
        AND message_id=?
    """, (
        cid,
        message.chat.id,
        message.message_id
    )).fetchone()

    new_text = message.text or message.caption or "[медиа]"

    if old and old[0] != owner:

        username = f"@{old[2]}" if old[2] else "без username"

        await bot.send_message(
            chat_id=owner,
            text=(
                "✏️ <b>Сообщение изменено</b>\n\n"
                f"👤 От: <b>{old[1]}</b>\n"
                f"🆔 {username}\n"
                f"🕐 {message.date}\n\n"
                f"<b>Было:</b>\n{old[3] or '[пусто]'}\n\n"
                f"<b>Стало:</b>\n{new_text}"
            ),
            parse_mode="HTML"
        )

    save_message(message)


@router.deleted_business_messages()
async def deleted_messages_handler(
    update: BusinessMessagesDeleted
):

    cid = update.business_connection_id
    owner = get_owner(cid)

    if not owner:
        return

    for message_id in update.message_ids:

        old = db.execute("""
            SELECT user_id,name,username,text
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

        username = f"@{old[2]}" if old[2] else "без username"

        await bot.send_message(
            chat_id=owner,
            text=(
                "🗑 <b>Сообщение удалено</b>\n\n"
                f"👤 От: <b>{old[1]}</b>\n"
                f"🆔 {username}\n"
                f"🕐 {old[3] or ''}\n\n"
                f"💬 {old[3] or '[медиа]'}"
            ),
            parse_mode="HTML"
        )


@router.callback_query(F.data.startswith("unmute:"))
async def unmute_button(callback: CallbackQuery):

    try:
        _, cid, chat_id = callback.data.split(":", 2)
        chat_id = int(chat_id)
    except Exception:
        await callback.answer(
            "Ошибка кнопки",
            show_alert=True
        )
        return

    owner = get_owner(cid)

    if not owner or callback.from_user.id != owner:

        await callback.answer(
            "⛔ Размутить может только владелец.",
            show_alert=True
        )

        return

    set_mute(cid, chat_id, False)

    await callback.answer("🔓 Размучено!")

    try:
        await bot.send_message(
            chat_id=chat_id,
            text="🔊 <b>Вы снова можете говорить.</b>",
            parse_mode="HTML",
            business_connection_id=cid
        )
    except Exception:
        pass


@router.message(F.text == "/start")
async def start_handler(message: Message):

    await message.answer(
        "🤖 <b>Business Bot</b>\n\n"
        ".mute — включить автоудаление\n"
        ".unmute — выключить автоудаление\n"
        ".clone — включить clone\n"
        ".unclone — выключить clone",
        parse_mode="HTML"
    )


async def health(request):
    return web.Response(text="OK")


async def web_server():

    app = web.Application()

    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    port = int(os.getenv("PORT", "10000"))

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port
    )

    await site.start()


async def main():

    await web_server()

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
