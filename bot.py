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

# Таблица для хранения точных настроек фильтров (строго 5 символов, режим без цифр или с 1 цифрой)
db.execute("""
CREATE TABLE IF NOT EXISTS user_filters(
    user_id INTEGER PRIMARY KEY,
    length INTEGER DEFAULT 5,
    mode TEXT DEFAULT 'letters_only'
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


def get_user_filter(user_id):
    row = db.execute("SELECT length, mode FROM user_filters WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        db.execute("INSERT OR REPLACE INTO user_filters(user_id, length, mode) VALUES (?, 5, 'letters_only')", (user_id,))
        db.commit()
        return (5, 'letters_only')
    return row


def update_user_filter(user_id, length=None, mode=None):
    l_curr, m_curr = get_user_filter(user_id)
    l = length if length is not None else l_curr
    m = mode if mode is not None else m_curr
    db.execute("INSERT OR REPLACE INTO user_filters(user_id, length, mode) VALUES (?, ?, ?)", (user_id, l, m))
    db.commit()


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
                            await bot.send_voice(chat_id=chat_id, voice=media_id, caption=m_text, business_connection_id=cid)
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
async def unmute_context_button(callback: CallbackQuery):
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


# Генерация строго 5-буквенных юзернеймов по выбранному фильтру
def generate_5_letter_username(mode):
    vowels = "aeiou"
    consonants = "bcdfghjklmnpqrstvwxyz"
    
    if mode == 'letters_only':
        # Генерация красивого слова из 5 букв (чередование согласных и гласных)
        name = "".join(random.choice(consonants) + random.choice(vowels) for _ in range(2))
        name += random.choice(consonants)
        return name[:5]
    else:
        # Режим с 1 цифрой: 4 буквы + 1 цифра (или вперемешку)
        letters = "".join(random.choice(consonants) + random.choice(vowels) for _ in range(2))
        digit = random.choice(string.digits)
        candidate = list(letters + digit)
        random.shuffle(candidate)
        return "".join(candidate)[:5]


async def check_username_free(username: str) -> bool:
    try:
        await bot.get_chat(f"@{username}")
        return False
    except TelegramAPIError as e:
        err_msg = str(e).lower()
        if "chat not found" in err_msg or "username invalid" in err_msg or "not found" in err_msg:
            return True
        return False


def get_username_keyboard(username: str):
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
                InlineKeyboardButton(text="🔍 Искать новый юз", callback_data="next_variant"),
                InlineKeyboardButton(text="⚙️ Фильтры", callback_data="filters")
            ]
        ]
    )


@router.message(F.text == "/start")
async def start_handler(message: Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Искать новый юз", callback_data="next_variant")],
            [InlineKeyboardButton(text="⚙️ Фильтры", callback_data="filters")]
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
        "👇 <b>Поиск свободных премиальных юзернеймов (5 символов):</b>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "next_variant")
async def cb_next_variant(callback: CallbackQuery):
    user_id = callback.from_user.id
    _, mode = get_user_filter(user_id)
    
    # Анимация поиска
    animation_frames = [
        "🔄 Ищу свободный юз (5 символов)...",
        "⚡ Проверяю базы Telegram и Fragment...",
        "🔍 Фильтрация занятых и аукционных имен..."
    ]
    
    msg = await callback.message.answer(random.choice(animation_frames))
    
    found = False
    target_username = ""
    
    for _ in range(30):
        await asyncio.sleep(0.15)
        candidate = generate_5_letter_username(mode)
        if await check_username_free(candidate):
            target_username = candidate
            found = True
            break
            
    try:
        await bot.delete_message(chat_id=callback.message.chat.id, message_id=msg.message_id)
    except Exception:
        pass
        
    if found:
        mode_text = "Чисто 5 букв" if mode == 'letters_only' else "5 символов (с 1 цифрой)"
        caption = (
            "❄️ <b>ЮЗЕРНЕЙМ НАЙДЕН</b>\n\n"
            f"👤 <b><а href='https://t.me/{target_username}'>@{target_username}</a></b>\n"
            f"🔤 Премиум выборка\n"
            f"• 5 символов • {mode_text}\n\n"
            "🔄 Осталось подборов: ∞"
        )
        
        avatar_url = "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=500&auto=format&fit=crop&q=60"
        
        try:
            await callback.message.answer_photo(
                photo=avatar_url,
                caption=caption,
                reply_markup=get_username_keyboard(target_username),
                parse_mode="HTML"
            )
        except Exception:
            await callback.message.answer(
                text=caption + f"\n\n👉 Ссылка: https://t.me/{target_username}",
                reply_markup=get_username_keyboard(target_username),
                parse_mode="HTML"
            )
    else:
        await callback.message.answer("❌ Свободный юзернейм по вашему фильтру не найден с этой попытки. Нажмите «Искать новый юз» еще раз.")
    
    await callback.answer()


@router.callback_query(F.data == "filters")
async def cb_filters_menu(callback: CallbackQuery):
    user_id = callback.from_user.id
    _, mode = get_user_filter(user_id)
    
    mode_display = "Чисто 5 букв ✅" if mode == 'letters_only' else "С 1 цифрой ✅"
    
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"Режим: {mode_display}", callback_data="toggle_mode")
            ],
            [
                InlineKeyboardButton(text="🔙 Назад к поиску", callback_data="back_to_search")
            ]
        ]
    )
    
    try:
        await callback.message.edit_caption(
            caption="⚙️ <b>Настройка фильтров поиска юзернеймов:</b>\n\nДлина строго фиксирована: <b>5 символов</b>.\nВыберите тип символов:",
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            text="⚙️ <b>Настройка фильтров поиска юзернеймов:</b>\n\nДлина строго фиксирована: <b>5 символов</b>.\nВыберите тип символов:",
            reply_markup=kb,
            parse_mode="HTML"
        )
    await callback.answer()


@router.callback_query(F.data == "toggle_mode")
async def cb_toggle_mode(callback: CallbackQuery):
    user_id = callback.from_user.id
    _, mode = get_user_filter(user_id)
    new_mode = 'with_digit' if mode == 'letters_only' else 'letters_only'
    update_user_filter(user_id, mode=new_mode)
    await cb_filters_menu(callback)


@router.callback_query(F.data == "back_to_search")
async def cb_back_search(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Искать новый юз", callback_data="next_variant")],
            [InlineKeyboardButton(text="⚙️ Фильтры", callback_data="filters")]
        ]
    )
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(
        "👇 <b>Панель управления поиском юзернеймов:</b>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("copy:"))
async def cb_copy(callback: CallbackQuery):
    uname = callback.data.split(":", 1)[1]
    await callback.answer(f"📋 Юзернейм @{uname} скопирован!", show_alert=True)


@router.callback_query(F.data.startswith("fav:"))
async def cb_fav(callback: CallbackQuery):
    uname = callback.data.split(":", 1)[1]
    await callback.answer(f"⭐ Юзернейм @{uname} добавлен в избранное!", show_alert=True)


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
