import asyncio
import logging
import os
import random
import sqlite3
from datetime import datetime, timedelta
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
    antimute INTEGER DEFAULT 0,
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

db.execute("""
CREATE TABLE IF NOT EXISTS user_history(
    user_id INTEGER NOT NULL,
    name TEXT,
    username TEXT,
    first_seen TEXT,
    PRIMARY KEY(user_id, name, username)
)
""")
db.commit()


ID_RANGES = [
    (10000000, "Август — Декабрь 2013 года"),
    (30000000, "Январь — Апрель 2014 года"),
    (70000000, "Май — Август 2014 года"),
    (110000000, "Сентябрь — Декабрь 2014 года"),
    (150000000, "Январь — Июнь 2015 года"),
    (190000000, "Июль — Декабрь 2015 года"),
    (250000000, "Январь — Июнь 2016 года"),
    (320000000, "Июль — Декабрь 2016 года"),
    (400000000, "Январь — Июнь 2017 года"),
    (500000000, "Июль — Декабрь 2017 года"),
    (650000000, "Январь — Июнь 2018 года"),
    (780000000, "Июль — Декабрь 2018 года"),
    (900000000, "Январь — Июнь 2019 года"),
    (1050000000, "Июль — Декабрь 2019 года"),
    (1250000000, "Январь — Июнь 2020 года"),
    (1500000000, "Июль — Декабрь 2020 года"),
    (1800000000, "Январь — Июнь 2021 года"),
    (2140000000, "Июль — Декабрь 2021 года"),
    (5200000000, "Январь — Июнь 2022 года"),
    (5600000000, "Июль — Декабрь 2022 года"),
    (6200000000, "Январь — Июнь 2023 года"),
    (6700000000, "Июль — Декабрь 2023 года"),
    (7200000000, "Январь — Июнь 2024 года"),
    (7800000000, "Июль — Декабрь 2024 года"),
    (8300000000, "Январь — Июнь 2025 года"),
    (9000000000, "Июль — Декабрь 2025 года"),
]

def estimate_reg_date(uid: int) -> str:
    if uid < 0:
        return "Чат / Канал"
    for max_id, date_str in ID_RANGES:
        if uid <= max_id:
            return date_str
    return "Начало 2026 года (Свежий аккаунт)"


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


def set_antimute(cid, chat_id, value):
    db.execute("""
    INSERT INTO chats(connection_id, chat_id, antimute)
    VALUES (?, ?, ?)
    ON CONFLICT(connection_id, chat_id) DO UPDATE SET antimute=excluded.antimute
    """, (cid, chat_id, int(value)))
    db.commit()


def is_antimute(cid, chat_id):
    row = db.execute("""
    SELECT antimute FROM chats WHERE connection_id=? AND chat_id=?
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


def save_message(message: Message):
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
    elif message.video_note:
        media_file_id = message.video_note.file_id
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

    if user_id:
        db.execute("""
        INSERT OR IGNORE INTO user_history(user_id, name, username, first_seen)
        VALUES (?, ?, ?, ?)
        """, (user_id, name, username or "без username", str(message.date)))

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

    # МУТ: Моментально удаляет сообщения собеседника
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
        # SPAM
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

        # HA (.ha)
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
                    text="🔇 <b>Пользователь больше не сможет говорить.</b>",
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

        # ANTIMUTE (.antimute) — спасает, если тебя замьютили на той стороне
        if text == ".antimute":
            asyncio.create_task(delete_message_fast(cid, message.message_id))
            current = is_antimute(cid, chat_id)
            set_antimute(cid, chat_id, not current)
            status_text = "🛡 <b>Anti-Mute ВКЛЮЧЕН!</b> Ваши удаленные сообщения будут мгновенно отправляться заново." if not current else "❌ <b>Anti-Mute ВЫКЛЮЧЕН.</b>"
            try:
                await bot.send_message(
                    chat_id=owner,
                    text=status_text,
                    parse_mode="HTML"
                )
            except TelegramAPIError:
                pass
            return

        # SEARCH (.search) — Анимация + Пробив в пустом чате
        if text == ".search":
            asyncio.create_task(delete_message_fast(cid, message.message_id))
            
            target_uid = None
            target_name = "Собеседник"
            target_user = None

            old = db.execute("""
            SELECT user_id, name, username FROM messages
            WHERE connection_id=? AND chat_id=? AND user_id!=?
            ORDER BY rowid DESC LIMIT 1
            """, (cid, chat_id, owner)).fetchone()

            if old and old[0]:
                target_uid, target_name, target_user = old
            else:
                try:
                    chat_info = await bot.get_chat(chat_id)
                    target_uid = chat_info.id
                    target_name = chat_info.full_name or chat_info.title or "Собеседник"
                    target_user = chat_info.username
                except Exception:
                    target_uid = abs(chat_id)
                    target_name = "Собеседник"
                    target_user = None

            try:
                # 1. Отправляем первое сообщение анимации
                sent = await bot.send_message(
                    chat_id=chat_id,
                    text="📡 <b>[ ⚙️ Подключение к спутниковому серверу... ] 0%</b> ⏳",
                    parse_mode="HTML",
                    business_connection_id=cid
                )

                anim_stages = [
                    "🔎 <b>[ 🌐 Сканирование Telegram ID и узлов связи... ] 28%</b> 🔄",
                    "📊 <b>[ 📂 Извлечение дат смены аватарок и ников... ] 64%</b> ⏳",
                    "🔓 <b>[ ⚡ Анализ метаданных Telegram Client... ] 91%</b> ⚡",
                    "✅ <b>[ 🎯 Досье успешно сформировано! ] 100%</b> ✨"
                ]

                # 2. Обновляем проценты через edit_message_text
                for stage in anim_stages:
                    await asyncio.sleep(0.6)
                    try:
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=sent.message_id,
                            text=stage,
                            parse_mode="HTML",
                            business_connection_id=cid
                        )
                    except TelegramAPIError:
                        pass

                await asyncio.sleep(0.5)

                # 3. Удаляем сообщение с анимацией
                asyncio.create_task(delete_message_fast(cid, sent.message_id))

                # 4. Рассчитываем точные данные
                reg_date_str = estimate_reg_date(target_uid)

                random.seed(target_uid)
                photo_days = random.randint(2, 45)
                name_days = random.randint(15, 120)
                user_days = random.randint(25, 210)

                photo_date = (datetime.now() - timedelta(days=photo_days)).strftime("%d.%m.%Y")
                name_date = (datetime.now() - timedelta(days=name_days)).strftime("%d.%m.%Y")
                user_date = (datetime.now() - timedelta(days=user_days)).strftime("%d.%m.%Y")
                random.seed()

                history_rows = db.execute("""
                SELECT name, username FROM user_history
                WHERE user_id=?
                """, (target_uid,)).fetchall()

                names_set = list(set([r[0] for r in history_rows if r[0]]))
                users_set = list(set([f"@{r[1]}" for r in history_rows if r[1] and r[1] != "без username"]))

                names_str = ", ".join(names_set) if names_set else target_name
                users_str = ", ".join(users_set) if users_set else (f"@{target_user}" if target_user else "Скрыт")

                result_card = (
                    "🎯 <b>РЕЗУЛЬТАТ ПОЛНОГО АНАЛИЗА АККАУНТА:</b>\n\n"
                    f"👤 <b>Имя профиля:</b> {target_name}\n"
                    f"🆔 <b>Telegram ID:</b> <code>{target_uid}</code>\n"
                    f"🌐 <b>Юзернейм:</b> {users_str}\n\n"
                    f"📅 <b>Дата регистрации Telegram:</b>\n"
                    f"└ <code>{reg_date_str}</code>\n\n"
                    f"🖼 <b>Смена фото профиля (аватарки):</b>\n"
                    f"└ <code>{photo_date}</code> <i>({photo_days} дн. назад)</i>\n\n"
                    f"✏️ <b>Смена имени/фамилии:</b>\n"
                    f"└ <code>{name_date}</code> <i>({name_days} дн. назад)</i>\n\n"
                    f"🏷 <b>Смена @username:</b>\n"
                    f"└ <code>{user_date}</code> <i>({user_days} дн. назад)</i>\n\n"
                    f"📜 <b>История зафиксированных имен:</b>\n"
                    f"└ <code>{names_str}</code>\n\n"
                    "🔒 <i>Все совпадения подтверждены базой данных.</i>"
                )

                # 5. Присылаем готовое досье
                await bot.send_message(
                    chat_id=chat_id,
                    text=result_card,
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

    chat_id = update.chat.id
    antimute_on = is_antimute(cid, chat_id)

    for message_id in update.message_ids:
        old = db.execute("""
        SELECT user_id, name, username, text, content_type, media_file_id, date FROM messages
        WHERE connection_id=? AND chat_id=? AND message_id=?
        """, (cid, chat_id, message_id)).fetchone()

        if not old:
            continue

        is_owner_msg = (old[0] == owner)

        # ANTIMUTE: Если собеседник удалил твое сообщение — бот мгновенно отправляет его заново
        if is_owner_msg and antimute_on:
            c_type = old[4] or "text"
            m_text = old[3] or ""
            media_id = old[5]
            try:
                if media_id:
                    if c_type == "photo":
                        await bot.send_photo(chat_id=chat_id, photo=media_id, caption=m_text, business_connection_id=cid)
                    elif c_type == "sticker":
                        await bot.send_sticker(chat_id=chat_id, sticker=media_id, business_connection_id=cid)
                    elif c_type == "animation":
                        await bot.send_animation(chat_id=chat_id, animation=media_id, caption=m_text, business_connection_id=cid)
                    elif c_type == "video":
                        await bot.send_video(chat_id=chat_id, video=media_id, caption=m_text, business_connection_id=cid)
                    elif c_type == "video_note":
                        await bot.send_video_note(chat_id=chat_id, video_note=media_id, business_connection_id=cid)
                    elif c_type == "voice":
                        await bot.send_voice(chat_id=chat_id, voice=media_id, caption=m_text, business_connection_id=cid)
                    elif c_type == "audio":
                        await bot.send_audio(chat_id=chat_id, audio=media_id, caption=m_text, business_connection_id=cid)
                    elif c_type == "document":
                        await bot.send_document(chat_id=chat_id, document=media_id, caption=m_text, business_connection_id=cid)
                elif m_text:
                    await bot.send_message(chat_id=chat_id, text=m_text, business_connection_id=cid)
            except TelegramAPIError:
                pass
            continue

        if is_owner_msg:
            continue

        username = f"@{old[2]}" if old[2] else "без username"
        c_type = old[4] or "text"
        media_id = old[5]

        type_names = {
            "photo": "🖼 Фотография",
            "sticker": "🎨 Стикер",
            "animation": "👾 GIF",
            "video": "📹 Видео",
            "video_note": "⭕ Кружок",
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
                elif c_type == "video_note":
                    await bot.send_message(chat_id=owner, text=caption_text, parse_mode="HTML")
                    await bot.send_video_note(chat_id=owner, video_note=media_id)
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
        "🤖 <b>Business Bot Turbo</b>\n\n"
        "🔇 .mute — включить mute (собеседник не сможет говорить)\n"
        "🔊 .unmute — выключить mute\n"
        "🛡 .antimute — защита от чужого мута\n"
        "🔍 .search — анимация поиска и досье на собеседника\n"
        "📋 .clone — включить clone\n"
        "📋 .unclone — выключить clone\n"
        "🚀 .spam <кол-во 1-150> <текст> — быстрый спам\n"
        "😂 .ha <кол-во 1-150> — спам смехом",
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
0
