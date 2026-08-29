import asyncio
import logging
import os
import random
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

db = sqlite3.connect(DB_FILE, check_same_thread=False)
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
    content_type TEXT,
    media_file_id TEXT,
    date TEXT,
    PRIMARY KEY(connection_id, chat_id, message_id)
)
""")
db.commit()


def get_owner(cid):
    row = db.execute(
        "SELECT owner_id FROM connections WHERE connection_id=?", (cid,)
    ).fetchone()
    return row[0] if row else None


def set_mute(cid, chat_id, value):
    db.execute("""
    INSERT INTO chats(connection_id, chat_id, muted)
    VALUES (?, ?, ?)
    ON CONFLICT(connection_id, chat_id) DO UPDATE SET muted=excluded.muted
    """, (cid, chat_id, int(value)))
    db.commit()


def is_muted(cid, chat_id):
    row = db.execute("""
    SELECT muted FROM chats WHERE connection_id=? AND chat_id=?
    """, (cid, chat_id)).fetchone()
    return bool(row and row[0])


def set_clone(cid, chat_id, value):
    db.execute("""
    INSERT INTO chats(connection_id, chat_id, clone_enabled)
    VALUES (?, ?, ?)
    ON CONFLICT(connection_id, chat_id) DO UPDATE SET clone_enabled=excluded.clone_enabled
    """, (cid, chat_id, int(value)))
    db.commit()


def is_clone(cid, chat_id):
    row = db.execute("""
    SELECT clone_enabled FROM chats WHERE connection_id=? AND chat_id=?
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
    content_type = message.content_type
    media_file_id = None

    if message.photo:
        media_file_id = message.photo[-1].file_id
    elif message.sticker:
        media_file_id = message.sticker.file_id
    elif message.animation:
        media_file_id = message.animation.file_id
    elif message.video:
        media_file_id = message.video.file_id
    elif message.voice:
        media_file_id = message.voice.file_id
    elif message.audio:
        media_file_id = message.audio.file_id
    elif message.document:
        media_file_id = message.document.file_id

    db.execute("""
    INSERT OR REPLACE INTO messages(
        connection_id, chat_id, message_id, user_id, name, username, text, content_type, media_file_id, date
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        cid, message.chat.id, message.message_id, user_id, name, username, text, content_type, media_file_id, str(message.date)
    ))
    db.commit()


async def delete_message_fast(cid, message_id):
    try:
        await bot.delete_business_messages(
            business_connection_id=cid,
            message_ids=[message_id]
        )
    except TelegramAPIError:
        pass


@router.business_connection()
async def connection_handler(connection: BusinessConnection):
    db.execute("""
    INSERT INTO connections(connection_id, owner_id)
    VALUES (?, ?)
    ON CONFLICT(connection_id) DO UPDATE SET owner_id=excluded.owner_id
    """, (connection.id, connection.user.id))
    db.commit()


@router.business_message()
async def business_message_handler(message: Message):
    cid = message.business_connection_id
    if not cid:
        return
    
    owner = get_owner(cid)
    if not owner and message.from_user:
        owner = message.from_user.id
        db.execute("""
        INSERT INTO connections(connection_id, owner_id)
        VALUES (?, ?)
        ON CONFLICT(connection_id) DO UPDATE SET owner_id=excluded.owner_id
        """, (cid, owner))
        db.commit()

    uid = message.from_user.id if message.from_user else None
    chat_id = message.chat.id
    is_me = (uid == owner) or getattr(message, "is_from_offline", False)

    # МГНОВЕННЫЙ МУТ (БЕЗ НАЛАГАНИЯ И ЗАДЕРЖЕК)
    if not is_me and is_muted(cid, chat_id):
        asyncio.create_task(delete_message_fast(cid, message.message_id))
        save_message(message)
        return

    save_message(message)

    text_raw = (message.text or message.caption or "").strip()
    text = text_raw.lower()

    if not is_me and is_clone(cid, chat_id):
        try:
            await bot.copy_message(
                chat_id=chat_id,
                from_chat_id=chat_id,
                message_id=message.message_id,
                business_connection_id=cid
            )
        except TelegramAPIError:
            pass
        return

    if is_me:
        # SPAM (.spam <кол-во> <текст>)
        if text.startswith(".spam "):
            asyncio.create_task(delete_message_fast(cid, message.message_id))
            try:
                parts = text_raw.split(maxsplit=2)
                count = min(int(parts[1]), 150)
                spam_text = parts[2]
                for _ in range(count):
                    await bot.send_message(
                        chat_id=chat_id,
                        text=spam_text,
                        business_connection_id=cid
                    )
            except Exception:
                pass
            return

        # HA (.ha 1-150)
        if text.startswith(".ha"):
            asyncio.create_task(delete_message_fast(cid, message.message_id))
            try:
                parts = text_raw.split()
                count = min(int(parts[1]), 150) if len(parts) > 1 else 5
                ha_variants = [
                    "ахахах", "АХАХАХА", "ахахахаххах", "хахаха", "АХАХАХАХАХАХ",
                    "хвхвхв", "пхахаха", "хвхвдплв", "АХХАХАВХВХ", "хыхыхы",
                    "ахахвхвхв", "вхвхвхвх", "АХАХАХАХАХ", "хвхвхвхвхв", "пхахахвхв"
                ]
                for _ in range(count):
                    await bot.send_message(
                        chat_id=chat_id,
                        text=random.choice(ha_variants),
                        business_connection_id=cid
                    )
            except Exception:
                pass
            return

        # MUTE
        if text == ".mute":
            set_mute(cid, chat_id, True)
            asyncio.create_task(delete_message_fast(cid, message.message_id))
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text="🔓 Размутить",
                        callback_data=f"unmute:{cid}:{chat_id}"
                    )
                ]]
            )
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text="🔇 <b>Пользователь замьючен.</b>\n\nЕго сообщения будут автоматически удаляться.",
                    parse_mode="HTML",
                    reply_markup=keyboard,
                    business_connection_id=cid
                )
            except TelegramAPIError:
                pass
            return

        # UNMUTE
        if text == ".unmute":
            set_mute(cid, chat_id, False)
            asyncio.create_task(delete_message_fast(cid, message.message_id))
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text="🔊 <b>Пользователь размьючен.</b>",
                    parse_mode="HTML",
                    business_connection_id=cid
                )
            except TelegramAPIError:
                pass
            return

        # CLONE
        if text == ".clone":
            set_clone(cid, chat_id, True)
            asyncio.create_task(delete_message_fast(cid, message.message_id))
            return

        if text == ".unclone":
            set_clone(cid, chat_id, False)
            asyncio.create_task(delete_message_fast(cid, message.message_id))
            return


@router.edited_business_message()
async def edited_message_handler(message: Message):
    cid = message.business_connection_id
    if not cid:
        return
    owner = get_owner(cid)
    if not owner:
        return

    old = db.execute("""
    SELECT user_id, name, username, text FROM messages
    WHERE connection_id=? AND chat_id=? AND message_id=?
    """, (cid, message.chat.id, message.message_id)).fetchone()

    new_text = message.text or message.caption or "[медиа]"

    if old and old[0] != owner:
        username = f"@{old[2]}" if old[2] else "без username"
        try:
            await bot.send_message(
                chat_id=owner,
                text=(
                    "✏️ <b>Сообщение изменено</b>\n\n"
                    f"👤 От: <b>{old[1]}</b>\n"
                    f"🆔 {username}\n\n"
                    f"<b>Было:</b>\n"
                    f"{old[3] or '[медиа]'}\n\n"
                    f"<b>Стало:</b>\n"
                    f"{new_text}"
                ),
                parse_mode="HTML"
            )
        except TelegramAPIError:
            pass

    save_message(message)


@router.deleted_business_messages()
async def deleted_messages_handler(update: BusinessMessagesDeleted):
    cid = update.business_connection_id
    if not cid:
        return
    owner = get_owner(cid)
    if not owner:
        return

    for message_id in update.message_ids:
        old = db.execute("""
        SELECT user_id, name, username, text, content_type, media_file_id, date FROM messages
        WHERE connection_id=? AND chat_id=? AND message_id=?
        """, (cid, update.chat.id, message_id)).fetchone()

        if not old:
            continue
        if old[0] == owner:
            continue

        username = f"@{old[2]}" if old[2] else "без username"
        c_type = old[4] or "text"
        media_id = old[5]

        type_names = {
            "photo": "🖼 Фотография",
            "sticker": "🎨 Стикер",
            "animation": "👾 GIF",
            "video": "📹 Видео",
            "voice": "🎙 Голосовое",
            "audio": "🎵 Аудио",
            "document": "📁 Документ"
        }
        
        type_str = type_names.get(c_type, f"📦 {c_type}")

        try:
            caption_text = (
                "🗑 <b>Сообщение удалено</b>\n\n"
                f"👤 От: <b>{old[1]}</b>\n"
                f"🆔 {username}\n"
                f"📌 Тип: <b>{type_str}</b>\n"
                f"🕐 {old[6]}\n\n"
                f"💬 {old[3] or ''}"
            )

            if media_id:
                if c_type == "photo":
                    await bot.send_photo(chat_id=owner, photo=media_id, caption=caption_text, parse_mode="HTML")
                elif c_type == "sticker":
                    await bot.send_message(chat_id=owner, text=caption_text, parse_mode="HTML")
                    await bot.send_sticker(chat_id=owner, sticker=media_id)
                elif c_type == "animation":
                    await bot.send_animation(chat_id=owner, animation=media_id, caption=caption_text, parse_mode="HTML")
                elif c_type == "video":
                    await bot.send_video(chat_id=owner, video=media_id, caption=caption_text, parse_mode="HTML")
                elif c_type == "voice":
                    await bot.send_voice(chat_id=owner, voice=media_id, caption=caption_text, parse_mode="HTML")
                elif c_type == "audio":
                    await bot.send_audio(chat_id=owner, audio=media_id, caption=caption_text, parse_mode="HTML")
                elif c_type == "document":
                    await bot.send_document(chat_id=owner, document=media_id, caption=caption_text, parse_mode="HTML")
            else:
                await bot.send_message(chat_id=owner, text=caption_text, parse_mode="HTML")
        except TelegramAPIError:
            pass


@router.callback_query(F.data.startswith("unmute:"))
async def unmute_button(callback: CallbackQuery):
    try:
        _, cid, chat_id = callback.data.split(":", 2)
        chat_id = int(chat_id)
    except Exception:
        await callback.answer("Ошибка кнопки", show_alert=True)
        return

    owner = get_owner(cid)
    if not owner or callback.from_user.id != owner:
        await callback.answer("⛔ Только владелец.", show_alert=True)
        return

    set_mute(cid, chat_id, False)
    await callback.answer("🔓 Размучено!")
    try:
        await bot.send_message(
            chat_id=chat_id,
            text="🔊 <b>Пользователь размьючен.</b>",
            parse_mode="HTML",
            business_connection_id=cid
        )
    except TelegramAPIError:
        pass


@router.message(F.text == "/start")
async def start_handler(message: Message):
    await message.answer(
        "🤖 <b>Business Bot Fast Mute & Media Restore</b>\n\n"
        "🔇 .mute — включить mute\n"
        "🔊 .unmute — выключить mute\n"
        "📋 .clone — включить clone\n"
        "📋 .unclone — выключить clone\n"
        "🚀 .spam <кол-во 1-150> <текст> — молниеносный спам\n"
        "😂 .ha <кол-во 1-150> — спам разным смехом",
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
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


async def main():
    if not BOT_TOKEN or BOT_TOKEN == "ВСТАВЬ_СЮДА_ТОКЕН":
        raise RuntimeError("Вставь токен бота в BOT_TOKEN")
    await web_server()
    logging.info("BOT STARTED")
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
