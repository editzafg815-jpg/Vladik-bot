# bot.py
import os
import asyncio
import sqlite3
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery, BusinessConnection, BusinessMessagesDeleted
from aiogram.exceptions import TelegramBadRequest

# ==============================
# 🔑 ВСТАВЬ ТОКЕН СЮДА
# ==============================
BOT_TOKEN = "8698964419:AAHt3neQ4J0mHVDv5f4CRT7MiigDn3ThLv0"

# ==============================
# ⚙️ НАСТРОЙКИ
# ==============================
MUTE_DELAY = 5

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

db = sqlite3.connect("bot.db", check_same_thread=False)

db.execute("""
CREATE TABLE IF NOT EXISTS connections (
    connection_id TEXT PRIMARY KEY,
    owner_id INTEGER
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS chats (
    connection_id TEXT,
    chat_id INTEGER,
    muted INTEGER DEFAULT 0,
    PRIMARY KEY(connection_id, chat_id)
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS messages (
    connection_id TEXT,
    chat_id INTEGER,
    message_id INTEGER,
    user_id INTEGER,
    name TEXT,
    username TEXT,
    text TEXT,
    date TEXT,
    PRIMARY KEY(connection_id, chat_id, message_id)
)
""")

db.commit()


def owner_id(connection_id):
    x = db.execute(
        "SELECT owner_id FROM connections WHERE connection_id=?",
        (connection_id,)
    ).fetchone()
    return x[0] if x else None


def muted(connection_id, chat_id):
    x = db.execute(
        "SELECT muted FROM chats WHERE connection_id=? AND chat_id=?",
        (connection_id, chat_id)
    ).fetchone()
    return bool(x and x[0])


def set_mute(connection_id, chat_id, value):
    db.execute("""
        INSERT INTO chats(connection_id,chat_id,muted)
        VALUES(?,?,?)
        ON CONFLICT(connection_id,chat_id)
        DO UPDATE SET muted=excluded.muted
    """, (connection_id, chat_id, int(value)))
    db.commit()


@router.business_connection()
async def business_connection(
    connection: BusinessConnection
):
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


# =====================================
# СОХРАНЕНИЕ СООБЩЕНИЙ
# =====================================

@router.business_message()
async def business_message(
    message: Message
):

    cid = message.business_connection_id

    if not cid:
        return

    oid = owner_id(cid)

    if not oid:
        return

    uid = message.from_user.id if message.from_user else 0

    text = message.text or message.caption or ""

    db.execute("""
        INSERT OR REPLACE INTO messages
        VALUES(?,?,?,?,?,?,?,?)
    """, (
        cid,
        message.chat.id,
        message.message_id,
        uid,
        message.from_user.full_name
        if message.from_user else "Unknown",
        message.from_user.username
        if message.from_user else None,
        text,
        str(message.date)
    ))

    db.commit()

    # ===============================
    # .mute
    # ===============================

    if uid == oid and text.strip().lower() == ".mute":

        set_mute(
            cid,
            message.chat.id,
            True
        )

        try:
            await bot.delete_business_messages(
                business_connection_id=cid,
                message_ids=[message.message_id]
            )
        except:
            pass

        keyboard = {
            "inline_keyboard": [[
                {
                    "text": "🔓 Размутить",
                    "callback_data":
                        f"unmute:{cid}:{message.chat.id}"
                }
            ]]
        }

        await bot.send_message(
            message.chat.id,
            "🔇 <b>Вы больше не можете говорить.</b>\n\n"
            "Ваши сообщения будут автоматически удаляться.",
            parse_mode="HTML",
            reply_markup=keyboard,
            business_connection_id=cid
        )

        return

    # ===============================
    # .unmute
    # ===============================

    if uid == oid and text.strip().lower() == ".unmute":

        set_mute(
            cid,
            message.chat.id,
            False
        )

        try:
            await bot.delete_business_messages(
                business_connection_id=cid,
                message_ids=[message.message_id]
            )
        except:
            pass

        await bot.send_message(
            message.chat.id,
            "🔊 <b>Вы снова можете говорить.</b>",
            parse_mode="HTML",
            business_connection_id=cid
        )

        return

    # ===============================
    # AUTO MUTE
    # ===============================

    if uid != oid and muted(cid, message.chat.id):

        await asyncio.sleep(MUTE_DELAY)

        try:
            await bot.delete_business_messages(
                business_connection_id=cid,
                message_ids=[message.message_id]
            )
        except TelegramBadRequest:
            pass
        except:
            pass


# =====================================
# ИЗМЕНЁННЫЕ СООБЩЕНИЯ
# =====================================

@router.edited_business_message()
async def edited_message(message: Message):

    cid = message.business_connection_id

    if not cid:
        return

    oid = owner_id(cid)

    if not oid:
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

    new_text = message.text or message.caption or ""

    if old and old[0] != oid:

        await bot.send_message(
            oid,
            f"✏️ <b>Сообщение изменено</b>\n\n"
            f"👤 От: <b>{old[1]}</b>\n"
            f"🆔 @{old[2] or 'нет'}\n\n"
            f"<b>Было:</b>\n{old[3]}\n\n"
            f"<b>Стало:</b>\n{new_text}",
            parse_mode="HTML"
        )

    db.execute("""
        UPDATE messages
        SET text=?
        WHERE connection_id=?
        AND chat_id=?
        AND message_id=?
    """, (
        new_text,
        cid,
        message.chat.id,
        message.message_id
    ))

    db.commit()


# =====================================
# УДАЛЁННЫЕ СООБЩЕНИЯ
# =====================================

@router.deleted_business_messages()
async def deleted_messages(
    update: BusinessMessagesDeleted
):

    cid = update.business_connection_id

    oid = owner_id(cid)

    if not oid:
        return

    for mid in update.message_ids:

        old = db.execute("""
            SELECT user_id,name,username,text
            FROM messages
            WHERE connection_id=?
            AND chat_id=?
            AND message_id=?
        """, (
            cid,
            update.chat.id,
            mid
        )).fetchone()

        if not old:
            continue

        if old[0] == oid:
            continue

        await bot.send_message(
            oid,
            f"🗑 <b>Сообщение удалено</b>\n\n"
            f"👤 От: <b>{old[1]}</b>\n"
            f"🆔 @{old[2] or 'нет'}\n\n"
            f"💬 {old[3]}",
            parse_mode="HTML"
        )


# =====================================
# КНОПКА РАЗМУТА
# =====================================

@router.callback_query(
    F.data.startswith("unmute:")
)
async def unmute_button(
    callback: CallbackQuery
):

    _, cid, chat_id = callback.data.split(":")

    oid = owner_id(cid)

    # ТОЛЬКО ВЛАДЕЛЕЦ
    if callback.from_user.id != oid:

        await callback.answer(
            "⛔ Размутить может только владелец.",
            show_alert=True
        )

        return

    set_mute(
        cid,
        int(chat_id),
        False
    )

    await callback.answer(
        "🔓 Размучено!"
    )

    await bot.send_message(
        int(chat_id),
        "🔊 <b>Вы снова можете говорить.</b>",
        parse_mode="HTML",
        business_connection_id=cid
    )


# =====================================
# START
# =====================================

async def main():

    await dp.start_polling(
        bot,
        allowed_updates=[
            "business_connection",
            "business_message",
            "edited_business_message",
            "deleted_business_messages",
            "callback_query"
        ]
    )


if __name__ == "__main__":
    asyncio.run(main())
