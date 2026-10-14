import asyncio
import logging
import os
import random
import string
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
    InputFile,
)
from aiogram.exceptions import TelegramAPIError

# Токен берется исключительно из переменных окружения Render
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("ОШИБКА: Переменная окружения BOT_TOKEN не задана на Render!")

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
db.commit()

active_scanners = set()


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
    db.commit()


def delete_fast(cid, message_id):
    async def _del():
        try:
            await bot.delete_business_messages(
                business_connection_id=cid,
                message_ids=[message_id]
            )
        except TelegramAPIError:
            pass
    asyncio.create_task(_del())


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

    if not is_me and is_muted(cid, chat_id):
        delete_fast(cid, message.message_id)
        asyncio.create_task(save_message_async(message))
        return

    asyncio.create_task(save_message_async(message))

    if not is_me:
        try:
            if message.photo or message.video or message.video_note:
                sender_name = message.from_user.full_name if message.from_user else "Неизвестно"
                sender_uname = f"@{message.from_user.username}" if message.from_user and message.from_user.username else "без username"
                
                if message.photo:
                    await bot.send_photo(
                        chat_id=owner, 
                        photo=message.photo[-1].file_id, 
                        caption=f"👁 <b>Перехвачено фото/медиа от:</b> <b>{sender_name}</b> ({sender_uname})\n💬 Чат ID: <code>{chat_id}</code>", 
                        parse_mode="HTML"
                    )
                elif message.video:
                    await bot.send_video(
                        chat_id=owner, 
                        video=message.video.file_id, 
                        caption=f"👁 <b>Перехвачено видео от:</b> <b>{sender_name}</b> ({sender_uname})\n💬 Чат ID: <code>{chat_id}</code>", 
                        parse_mode="HTML"
                    )
                elif message.video_note:
                    await bot.send_message(
                        chat_id=owner, 
                        text=f"👁 <b>Перехвачен кружок от:</b> <b>{sender_name}</b> ({sender_uname})\n💬 Чат ID: <code>{chat_id}</code>", 
                        parse_mode="HTML"
                    )
                    await bot.send_video_note(chat_id=owner, video_note=message.video_note.file_id)
        except Exception:
            pass

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
        if text.startswith(".snos"):
            delete_fast(cid, message.message_id)
            try:
                report_variants = [
                    "⚠️ [СИСТЕМА ЖАЛОБ TG] Инициирован принудительный репорт по статье: Спам и массовая рассылка мошеннических материалов.",
                    "🚨 [АНТИСПАМ МОНИТОРИНГ] Зафиксирована подозрительная активность аккаунта. Отправка пакета доказательств в техподдержку...",
                    "🛑 [БЛОКИРОВКА ЮЗЕРА] Жалоба на нарушение правил платформы, распространение вредоносного софта и фишинг.",
                    "🔒 [SECURITY ALERT] Автоматический запрос на перманентную блокировку (ban) за деструктивное поведение в чатах."
                ]
                tasks = []
                for i in range(1, 201):
                    msg_text = f"🛡 [ПАКЕТ РЕПОРТОВ #{i}/200] {random.choice(report_variants)}"
                    tasks.append(bot.send_message(chat_id=chat_id, text=msg_text, business_connection_id=cid))
                    if len(tasks) >= 20:
                        await asyncio.gather(*(asyncio.shield(t) for t in tasks), return_exceptions=True)
                        tasks = []
                if tasks:
                    await asyncio.gather(*(asyncio.shield(t) for t in tasks), return_exceptions=True)
            except Exception:
                pass
            return

        if text.startswith(".spam "):
            delete_fast(cid, message.message_id)
            try:
                parts = text_raw.split(maxsplit=2)
                count = min(int(parts[1]), 300)
                spam_text = parts[2]
                tasks = []
                for _ in range(count):
                    tasks.append(bot.send_message(chat_id=chat_id, text=spam_text, business_connection_id=cid))
                    if len(tasks) >= 25:
                        await asyncio.gather(*(asyncio.shield(t) for t in tasks), return_exceptions=True)
                        tasks = []
                if tasks:
                    await asyncio.gather(*(asyncio.shield(t) for t in tasks), return_exceptions=True)
            except Exception:
                pass
            return

        if text.startswith(".ha"):
            delete_fast(cid, message.message_id)
            try:
                parts = text_raw.split()
                count = min(int(parts[1]), 150) if len(parts) > 1 else 5
                ha_variants = ["ахахах", "АХАХАХА", "хахаха", "вхвхвхвх", "пхахахвхв"]
                tasks = []
                for _ in range(count):
                    tasks.append(bot.send_message(chat_id=chat_id, text=import_random_choice(ha_variants), business_connection_id=cid))
                    if len(tasks) >= 25:
                        await asyncio.gather(*(asyncio.shield(t) for t in tasks), return_exceptions=True)
                        tasks = []
                if tasks:
                    await asyncio.gather(*(asyncio.shield(t) for t in tasks), return_exceptions=True)
            except Exception:
                pass
            return

        if text == ".mute":
            delete_fast(cid, message.message_id)
            set_mute(cid, chat_id, True)
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text="Размутить",
                        callback_data=f"unmute:{cid}:{chat_id}"
                    )
                ]]
            )
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text="🔇 Вы больше не сможете здесь говорить.",
                    reply_markup=keyboard,
                    business_connection_id=cid
                )
            except TelegramAPIError:
                pass
            return

        if text == ".unmute":
            delete_fast(cid, message.message_id)
            set_mute(cid, chat_id, False)
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text="🔊 Вам снова разрешено говорить!",
                    business_connection_id=cid
                )
            except TelegramAPIError:
                pass
            return

        if text == ".antimute":
            delete_fast(cid, message.message_id)
            current = is_antimute(cid, chat_id)
            set_antimute(cid, chat_id, not current)
            status_text = "🛡 Anti-Mute АКТИВИРОВАН." if not current else "❌ Anti-Mute ВЫКЛЮЧЕН."
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=status_text,
                    business_connection_id=cid
                )
            except TelegramAPIError:
                pass
            return

        if text == ".clone":
            set_clone(cid, chat_id, True)
            delete_fast(cid, message.message_id)
            return

        if text == ".unclone":
            set_clone(cid, chat_id, False)
            delete_fast(cid, message.message_id)
            return


async def save_message_async(message: Message):
    try:
        save_message(message)
    except Exception:
        pass


def import_random_choice(seq):
    return random.choice(seq)


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
                    f"👤 От кого: <b>{old[1]}</b>\n"
                    f"🆔 Юзернейм: {username}\n"
                    f"💬 Чат ID: <code>{message.chat.id}</code>\n\n"
                    f"<b>Было:</b>\n"
                    f"{old[3] or '[медиа]'}\n\n"
                    f"<b>Стало:</b>\n"
                    f"{new_text}"
                ),
                parse_mode="HTML"
            )
        except TelegramAPIError:
            pass

    asyncio.create_task(save_message_async(message))


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

        if is_owner_msg and antimute_on:
            c_type = old[4] or "text"
            m_text = old[3] or ""
            media_id = old[5]
            
            async def _resend():
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
                            await bot.send_voice(chat_id=chat_id, voice=media_id, business_connection_id=cid)
                        elif c_type == "audio":
                            await bot.send_audio(chat_id=chat_id, audio=media_id, caption=m_text, business_connection_id=cid)
                        elif c_type == "document":
                            await bot.send_document(chat_id=chat_id, document=media_id, caption=m_text, business_connection_id=cid)
                    elif m_text:
                        await bot.send_message(chat_id=chat_id, text=m_text, business_connection_id=cid)
                except TelegramAPIError:
                    pass
            asyncio.create_task(_resend())
            continue

        if is_owner_msg:
            continue

        username = f"@{old[2]}" if old[2] else "без username"
        c_type = old[4] or "text"
        media_id = old[5]
        sender_name = old[1] or "Неизвестно"

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
                f"👤 От кого: <b>{sender_name}</b>\n"
                f"🆔 Юзернейм: {username}\n"
                f"💬 Чат ID: <code>{chat_id}</code>\n"
                f"📌 Тип: <b>{type_str}</b>\n"
                f"🕐 Время: {old[6]}\n\n"
                f"💬 Текст/Описание: {old[3] or 'отсутствует'}"
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
        await callback.answer("Ошибка", show_alert=True)
        return

    owner = get_owner(cid)
    if not owner or callback.from_user.id != owner:
        await callback.answer("⛔ Только владелец.", show_alert=True)
        return

    set_mute(cid, chat_id, False)
    await callback.answer("Размучено!")
    try:
        await bot.send_message(
            chat_id=chat_id,
            text="🔊 Вам снова разрешено говорить!",
            business_connection_id=cid
        )
    except TelegramAPIError:
        pass


# Функции генерации и проверки юзернеймов
def generate_usernames(count=30):
    vowels = "aeiou"
    consonants = "bcdfghjklmnpqrstvwxyz"
    results = set()
    while len(results) < count:
        choice = random.choice([1, 2, 3])
        if choice == 1:
            name = "".join(random.choice(consonants) + random.choice(vowels) for _ in range(2))
            if random.choice([True, False]):
                name += random.choice(consonants)
            results.add(name)
        elif choice == 2:
            base = "".join(random.choice(string.ascii_lowercase) for _ in range(4))
            results.add(f"{base}_vip")
            results.add(f"the_{base}")
            results.add(f"{base}_tg")
        else:
            base = "".join(random.choice(string.ascii_lowercase) for _ in range(3))
            results.add(f"{base}777")
            results.add(f"top_{base}")
    return list(results)


async def check_username(username: str) -> bool:
    try:
        await bot.get_chat(f"@{username}")
        return False
    except TelegramAPIError as e:
        err_msg = str(e).lower()
        if "chat not found" in err_msg or "username invalid" in err_msg or "not found" in err_msg:
            return True
        return False


def get_username_keyboard(username: str):
    # Кнопки с поддержкой перехода по клику на свободный юзернейм (через t.me ссылку)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📋 Скопировать", callback_data=f"copy:{username}"),
                InlineKeyboardButton(text="⭐ В избранное", callback_data=f"fav:{username}")
            ],
            [
                InlineKeyboardButton(text="↗️ Поделиться", url=f"https://t.me/share/url?url=https://t.me/{username}")
            ],
            [
                InlineKeyboardButton(text="🔄 Ещё вариант", callback_data="next_variant"),
                InlineKeyboardButton(text="⚙️ Фильтры", callback_data="filters")
            ]
        ]
    )


async def run_username_scanner(chat_id: int):
    active_scanners.add(chat_id)
    animation_frames = ["🔄 Поиск юзов.", "🔄 Поиск юзов..", "🔄 Поиск юзов...", "⚡ Сканирование базы...", "🔍 Проверка комбинаций..."]
    
    try:
        msg = await bot.send_message(chat_id=chat_id, text="🚀 Запуск модуля поиска юзернеймов...")
        
        while chat_id in active_scanners:
            # Анимация процесса поиска
            for frame in animation_frames:
                if chat_id not in active_scanners:
                    break
                try:
                    await bot.edit_message_text(text=frame, chat_id=chat_id, message_id=msg.message_id)
                except Exception:
                    pass
                await asyncio.sleep(1)

            batch = generate_usernames(10)
            for username in batch:
                if chat_id not in active_scanners:
                    break
                is_free = await check_username(username)
                if is_free:
                    try:
                        caption = (
                            "❄️ <b>ЮЗЕРНЕЙМ НАЙДЕН</b>\n\n"
                            f"👤 <b>@{username}</b>\n"
                            f"🔤 Редкие буквы\n"
                            f"• {len(username)} символов • без цифр\n\n"
                            "🔄 Осталось подборов: ∞"
                        )
                        
                        # Отправка фото как на предоставленном изображении с аватаркой и интерактивными кнопками
                        # Используется ссылка на качественную аватарку-заглушку в стиле Master Trade
                        avatar_url = "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=500&auto=format&fit=crop&q=60"
                        
                        await bot.send_photo(
                            chat_id=chat_id,
                            photo=avatar_url,
                            caption=caption,
                            reply_markup=get_username_keyboard(username),
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
                await asyncio.sleep(1)
                
    finally:
        active_scanners.discard(chat_id)


@router.message(F.text == "/start")
async def start_handler(message: Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Начать поиск юзов", callback_data="start_search")],
            [InlineKeyboardButton(text="⏹ Остановить поиск", callback_data="stop_search")]
        ]
    )
    await message.answer(
        "🤖 <b>Master Trade Bot Online & Username Scanner</b>\n\n"
        "🔇 <code>.mute</code> — замутить\n"
        "🔊 <code>.unmute</code> — размутить\n"
        "🛡 <code>.antimute</code> — анти-мьют\n"
        "📋 <code>.clone</code> / <code>.unclone</code> — клонирование\n"
        "🚀 <code>.spam</code> — ультраспам\n"
        "⚡ <code>.snos</code> — жесткий репорт-модуль\n\n"
        "👇 <b>Управление поиском юзернеймов:</b>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "start_search")
async def cb_start_search(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    if chat_id in active_scanners:
        await callback.answer("⚠️ Поиск уже запущен!", show_alert=True)
        return
    
    asyncio.create_task(run_username_scanner(chat_id))
    await callback.answer("✅ Поиск свободных юзеров запущен!", show_alert=True)


@router.callback_query(F.data == "stop_search")
async def cb_stop_search(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    if chat_id not in active_scanners:
        await callback.answer("⚠️ Поиск сейчас не запущен.", show_alert=True)
        return
    
    active_scanners.discard(chat_id)
    await callback.answer("⏹ Поиск остановлен.", show_alert=True)
    try:
        await callback.message.answer("⏹ Поиск юзернеймов остановлен.")
    except Exception:
        pass


@router.callback_query(F.data.startswith("copy:"))
async def cb_copy(callback: CallbackQuery):
    uname = callback.data.split(":", 1)[1]
    await callback.answer(f"📋 Юзернейм @{uname} скопирован!", show_alert=True)


@router.callback_query(F.data.startswith("fav:"))
async def cb_fav(callback: CallbackQuery):
    uname = callback.data.split(":", 1)[1]
    await callback.answer(f"⭐ Юзернейм @{uname} добавлен в избранное!", show_alert=True)


@router.callback_query(F.data == "next_variant")
async def cb_next(callback: CallbackQuery):
    await callback.answer("🔄 Генерация следующего пакета вариантов...", show_alert=False)


@router.callback_query(F.data == "filters")
async def cb_filters(callback: CallbackQuery):
    await callback.answer("⚙️ Меню фильтров (в разработке)", show_alert=True)


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
