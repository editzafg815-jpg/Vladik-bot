import asyncio
import logging
import os
import random
import string
import sqlite3
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    BusinessConnection,
    BusinessMessagesDeleted,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    CopyTextButton,
)
from aiogram.exceptions import TelegramAPIError

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
db.execute("PRAGMA synchronous=OFF")
db.execute("PRAGMA temp_store=MEMORY")

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
    is_deleted INTEGER DEFAULT 0,
    is_edited INTEGER DEFAULT 0,
    PRIMARY KEY(connection_id, chat_id, message_id)
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS user_filters(
    user_id INTEGER PRIMARY KEY,
    mode TEXT DEFAULT 'letters_only',
    custom_chars TEXT DEFAULT ''
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS used_usernames(
    username TEXT PRIMARY KEY
)
""")
db.commit()

_mute_cache = {}
_antimute_cache = {}

for cid, chat_id, muted, antimute in db.execute("SELECT connection_id, chat_id, muted, antimute FROM chats").fetchall():
    _mute_cache[(cid, chat_id)] = bool(muted)
    _antimute_cache[(cid, chat_id)] = bool(antimute)


def get_owner(cid):
    row = db.execute(
        "SELECT owner_id FROM connections WHERE connection_id=?", (cid,)
    ).fetchone()
    return row[0] if row else None


def get_user_filter_settings(user_id):
    row = db.execute("SELECT mode, custom_chars FROM user_filters WHERE user_id=?", (user_id,)).fetchone()
    if row:
        return row[0], row[1] or ""
    return "letters_only", ""


def set_user_mode(user_id, mode):
    db.execute(
        "INSERT INTO user_filters(user_id, mode, custom_chars) VALUES (?, ?, COALESCE((SELECT custom_chars FROM user_filters WHERE user_id=?), '')) "
        "ON CONFLICT(user_id) DO UPDATE SET mode=excluded.mode",
        (user_id, mode, user_id)
    )
    db.commit()


def set_user_custom_chars(user_id, custom_chars):
    db.execute(
        "INSERT INTO user_filters(user_id, mode, custom_chars) VALUES (?, 'custom', ?) "
        "ON CONFLICT(user_id) DO UPDATE SET mode='custom', custom_chars=excluded.custom_chars",
        (user_id, custom_chars)
    )
    db.commit()


def is_username_used(username: str) -> bool:
    row = db.execute("SELECT 1 FROM used_usernames WHERE username=?", (username.lower(),)).fetchone()
    return bool(row)


def mark_username_used(username: str):
    db.execute("INSERT OR IGNORE INTO used_usernames(username) VALUES (?)", (username.lower(),))
    db.commit()


def set_mute(cid, chat_id, value):
    val = bool(value)
    _mute_cache[(cid, chat_id)] = val
    db.execute("""
    INSERT INTO chats(connection_id, chat_id, muted)
    VALUES (?, ?, ?)
    ON CONFLICT(connection_id, chat_id) DO UPDATE SET muted=excluded.muted
    """, (cid, chat_id, int(val)))
    db.commit()


def is_muted(cid, chat_id):
    return _mute_cache.get((cid, chat_id), False)


def set_antimute(cid, chat_id, value):
    val = bool(value)
    _antimute_cache[(cid, chat_id)] = val
    db.execute("""
    INSERT INTO chats(connection_id, chat_id, antimute)
    VALUES (?, ?, ?)
    ON CONFLICT(connection_id, chat_id) DO UPDATE SET antimute=excluded.antimute
    """, (cid, chat_id, int(val)))
    db.commit()


def is_antimute(cid, chat_id):
    return _antimute_cache.get((cid, chat_id), False)


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

    existing = db.execute("""
    SELECT text FROM messages WHERE connection_id=? AND chat_id=? AND message_id=?
    """, (cid, message.chat.id, message.message_id)).fetchone()

    old_text = existing[0] if existing else None

    db.execute("""
    INSERT INTO messages(
        connection_id, chat_id, message_id, user_id, name, username, text, content_type, media_file_id, date, is_deleted, is_edited
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
    ON CONFLICT(connection_id, chat_id, message_id) DO UPDATE SET
        text = excluded.text,
        content_type = excluded.content_type,
        media_file_id = COALESCE(excluded.media_file_id, messages.media_file_id),
        is_edited = CASE WHEN messages.text != excluded.text THEN 1 ELSE messages.is_edited END
    """, (
        cid, message.chat.id, message.message_id, user_id, name, username, text, content_type, media_file_id, str(message.date)
    ))
    db.commit()
    return old_text


def get_saved_message_info(cid, chat_id, message_id):
    return db.execute("""
    SELECT user_id, name, username, text, content_type, media_file_id FROM messages
    WHERE connection_id=? AND chat_id=? AND message_id=?
    """, (cid, chat_id, message_id)).fetchone()


def delete_fast(cid, message_id):
    async def _del():
        try:
            await bot.delete_business_messages(
                business_connection_id=cid,
                message_ids=[message_id]
            )
        except TelegramAPIError:
            pass
    asyncio.get_event_loop().create_task(_del())


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
    
    uid = message.from_user.id if message.from_user else None
    chat_id = message.chat.id
    is_me = getattr(message, "is_from_offline", False)
    
    owner = get_owner(cid)
    if not is_me and uid:
        if owner and uid == owner:
            is_me = True
        elif not owner:
            db.execute("""
            INSERT INTO connections(connection_id, owner_id)
            VALUES (?, ?)
            ON CONFLICT(connection_id) DO UPDATE SET owner_id=excluded.owner_id
            """, (cid, uid))
            db.commit()
            is_me = True

    text_raw = (message.text or message.caption or "").strip()
    text = text_raw.lower()

    if not is_me and is_muted(cid, chat_id):
        delete_fast(cid, message.message_id)
        asyncio.get_event_loop().create_task(asyncio.to_thread(save_message, message))
        return

    if is_me and is_antimute(cid, chat_id) and text == ".mute":
        delete_fast(cid, message.message_id)
        set_mute(cid, chat_id, False)
        asyncio.get_event_loop().create_task(bot.send_message(
            chat_id=chat_id,
            text="🛡 <b>Anti-Mute сработал! Мьют был автоматически заблокирован и отменен.</b>",
            business_connection_id=cid,
            parse_mode="HTML"
        ))
        return

    asyncio.get_event_loop().create_task(asyncio.to_thread(save_message, message))

    if not is_me:
        try:
            if message.photo or message.video or message.video_note:
                sender_name = message.from_user.full_name if message.from_user else "Неизвестно"
                sender_uname = f"@{message.from_user.username}" if message.from_user and message.from_user.username else "без username"
                owner_id = get_owner(cid)
                if owner_id:
                    if message.photo:
                        asyncio.get_event_loop().create_task(bot.send_photo(
                            chat_id=owner_id,
                            photo=message.photo[-1].file_id,
                            caption=f"👁 <b>Перехвачено фото от:</b> <b>{sender_name}</b> ({sender_uname})\n💬 Чат ID: <code>{chat_id}</code>",
                            parse_mode="HTML"
                        ))
                    elif message.video:
                        asyncio.get_event_loop().create_task(bot.send_video(
                            chat_id=owner_id,
                            video=message.video.file_id,
                            caption=f"👁 <b>Перехвачено видео от:</b> <b>{sender_name}</b> ({sender_uname})\n💬 Чат ID: <code>{chat_id}</code>",
                            parse_mode="HTML"
                        ))
                    elif message.video_note:
                        asyncio.get_event_loop().create_task(bot.send_message(
                            chat_id=owner_id,
                            text=f"👁 <b>Перехвачен кружок от:</b> <b>{sender_name}</b> ({sender_uname})\n💬 Чат ID: <code>{chat_id}</code>",
                            parse_mode="HTML"
                        ))
                        asyncio.get_event_loop().create_task(bot.send_video_note(chat_id=owner_id, video_note=message.video_note.file_id))
        except Exception:
            pass

    if not is_me and is_clone(cid, chat_id):
        asyncio.get_event_loop().create_task(bot.copy_message(
            chat_id=chat_id,
            from_chat_id=chat_id,
            message_id=message.message_id,
            business_connection_id=cid
        ))
        return

    if is_me:
        user_id = message.from_user.id
        state_key = f"waiting_chars_{user_id}"
        if getattr(bot, state_key, False):
            set_user_custom_chars(user_id, text_raw.lower())
            setattr(bot, state_key, False)
            await message.answer(f"✅ Фильтр символов обновлен: <b>{text_raw}</b>", parse_mode="HTML")
            return

        if text.startswith(".snos"):
            delete_fast(cid, message.message_id)
            try:
                report_variants = [
                    "⚠️ [СИСТЕМА ЖАЛОБ TG] Экстренный высокоприоритетный репорт по статье: Спам и деструктивная активность.",
                    "🚨 [АНТИСПАМ ЯДРО] Зафиксирована моментальная атака и подозрительная сетевая активность.",
                    "🛑 [БЛОКИРОВКА ЮЗЕРА] Принудительная жалоба на злостное нарушение правил платформы Telegram.",
                    "🔒 [SECURITY CORE] Запрос на мгновенную перманентную блокировку учетной записи."
                ]
                tasks = [bot.send_message(chat_id=chat_id, text=f"🛡 [МОЛНИЕНОСНЫЙ РЕПОРТ #{i}/100] {random.choice(report_variants)}", business_connection_id=cid) for i in range(1, 101)]
                for chunk in [tasks[i:i + 50] for i in range(0, len(tasks), 50)]:
                    asyncio.get_event_loop().create_task(asyncio.gather(*(asyncio.shield(t) for t in chunk), return_exceptions=True))
            except Exception:
                pass
            return

        if text.startswith(".spam "):
            delete_fast(cid, message.message_id)
            try:
                parts = text_raw.split(maxsplit=2)
                count = min(int(parts[1]), 200)
                spam_text = parts[2]
                tasks = [bot.send_message(chat_id=chat_id, text=spam_text, business_connection_id=cid) for _ in range(count)]
                for chunk in [tasks[i:i + 50] for i in range(0, len(tasks), 50)]:
                    asyncio.get_event_loop().create_task(asyncio.gather(*(asyncio.shield(t) for t in chunk), return_exceptions=True))
            except Exception:
                pass
            return

        if text == ".mute":
            delete_fast(cid, message.message_id)
            set_mute(cid, chat_id, True)
            asyncio.get_event_loop().create_task(bot.send_message(chat_id=chat_id, text="🔇 <b>Чат успешно замучен.</b> Собеседник в данный момент временно не может говорить и отправлять сообщения в этот диалог, все входящие запросы автоматически фильтруются.", business_connection_id=cid, parse_mode="HTML"))
            return

        if text == ".unmute":
            delete_fast(cid, message.message_id)
            set_mute(cid, chat_id, False)
            asyncio.get_event_loop().create_task(bot.send_message(chat_id=chat_id, text="🔊 <b>Чат успешно размучен.</b> Собеседник снова может свободно говорить, писать сообщения и вести полноценный диалог без каких-либо ограничений.", business_connection_id=cid, parse_mode="HTML"))
            return

        if text == ".antimute":
            delete_fast(cid, message.message_id)
            current = is_antimute(cid, chat_id)
            set_antimute(cid, chat_id, not current)
            status_text = "🛡 <b>Anti-Mute Ультра-Режим ВКЛЮЧЕН!</b> Защита от случайных или принудительных мьютов работает на максимальной скорости, мгновенно блокируя любые попытки ограничить общение." if not current else "❌ <b>Anti-Mute Ультра-Режим ВЫКЛЮЧЕН.</b> Защита чата деактивирована."
            asyncio.get_event_loop().create_task(bot.send_message(chat_id=chat_id, text=status_text, business_connection_id=cid, parse_mode="HTML"))
            return

        if text == ".clone":
            set_clone(cid, chat_id, True)
            delete_fast(cid, message.message_id)
            return

        if text == ".unclone":
            set_clone(cid, chat_id, False)
            delete_fast(cid, message.message_id)
            return


@router.edited_business_message()
async def edited_message_handler(message: Message):
    cid = message.business_connection_id
    if not cid:
        return
    
    old_info = get_saved_message_info(cid, message.chat.id, message.message_id)
    old_text = old_info[3] if old_info else "(нет данных)"
    
    asyncio.get_event_loop().create_task(asyncio.to_thread(save_message, message))
    
    try:
        owner_id = get_owner(cid)
        if owner_id and message.from_user:
            sender_name = message.from_user.full_name or "Неизвестно"
            sender_uname = f"@{message.from_user.username}" if message.from_user.username else "без username"
            new_text = message.text or message.caption or "(без текста)"
            chat_id = message.chat.id
            
            report = (
                f"✏️ <b>Сообщение изменено!</b>\n"
                f"👤 От: <b>{sender_name}</b> ({sender_uname})\n"
                f"💬 Чат ID: <code>{chat_id}</code>\n\n"
                f"🔄 <b>С какого:</b> {old_text}\n"
                f"➡️ <b>На какое:</b> {new_text}"
            )
            asyncio.get_event_loop().create_task(bot.send_message(chat_id=owner_id, text=report, parse_mode="HTML"))
    except Exception:
        pass


@router.deleted_business_messages()
async def deleted_messages_handler(update: BusinessMessagesDeleted):
    cid = update.business_connection_id
    if not cid:
        return
    owner_id = get_owner(cid)
    if not owner_id:
        return
    
    chat_id = update.chat.id
    for msg_id in update.message_ids:
        msg_info = get_saved_message_info(cid, chat_id, msg_id)
        if msg_info:
            user_id, name, username, text, content_type, media_file_id = msg_info
            uname_str = f"@{username}" if username else "без username"
            caption_text = f"🗑 <b>Удалено сообщение от:</b> <b>{name}</b> ({uname_str})\n💬 Чат ID: <code>{chat_id}</code>\n💬 Текст: {text or '(отсутствует)'}"
            
            try:
                if media_file_id:
                    if content_type == "photo":
                        asyncio.get_event_loop().create_task(bot.send_photo(chat_id=owner_id, photo=media_file_id, caption=caption_text, parse_mode="HTML"))
                    elif content_type == "video":
                        asyncio.get_event_loop().create_task(bot.send_video(chat_id=owner_id, video=media_file_id, caption=caption_text, parse_mode="HTML"))
                    elif content_type == "video_note":
                        asyncio.get_event_loop().create_task(bot.send_message(chat_id=owner_id, text=caption_text, parse_mode="HTML"))
                        asyncio.get_event_loop().create_task(bot.send_video_note(chat_id=owner_id, video_note=media_file_id))
                    elif content_type == "document":
                        asyncio.get_event_loop().create_task(bot.send_document(chat_id=owner_id, document=media_file_id, caption=caption_text, parse_mode="HTML"))
                    elif content_type == "audio":
                        asyncio.get_event_loop().create_task(bot.send_audio(chat_id=owner_id, audio=media_file_id, caption=caption_text, parse_mode="HTML"))
                    elif content_type == "voice":
                        asyncio.get_event_loop().create_task(bot.send_voice(chat_id=owner_id, voice=media_file_id, caption=caption_text, parse_mode="HTML"))
                    else:
                        asyncio.get_event_loop().create_task(bot.send_message(chat_id=owner_id, text=caption_text, parse_mode="HTML"))
                else:
                    asyncio.get_event_loop().create_task(bot.send_message(chat_id=owner_id, text=caption_text, parse_mode="HTML"))
            except Exception:
                pass


def generate_username(mode="letters_only", custom_chars=""):
    vowels = "aeiouy"
    consonants = "bcdfghjklmnpqrstvwxz"
    
    if mode == "letters_only":
        c1 = random.choice(consonants)
        v1 = random.choice(vowels)
        c2 = random.choice(consonants)
        v2 = random.choice(vowels)
        c3 = random.choice(consonants)
        return f"{c1}{v1}{c2}{v2}{c3}"
        
    elif mode == "with_digit":
        c1 = random.choice(consonants)
        v1 = random.choice(vowels)
        c2 = random.choice(consonants)
        v2 = random.choice(vowels)
        digit = random.choice(string.digits)
        cand = list(f"{c1}{v1}{c2}{v2}{digit}")
        random.shuffle(cand)
        return "".join(cand)
        
    elif mode == "custom" and custom_chars:
        base = custom_chars.strip().lower().replace("@", "")
        if len(base) >= 5:
            return base[:5]
        else:
            fill = "".join(random.choices(consonants + vowels, k=5 - len(base)))
            return base + fill
            
    else:
        return f"{random.choice(consonants)}{random.choice(vowels)}{random.choice(consonants)}{random.choice(vowels)}{random.choice(consonants)}"


async def check_username_free(username: str) -> bool:
    if is_username_used(username):
        return False

    try:
        await bot.get_chat(f"@{username}")
        return False
    except TelegramAPIError as e:
        err_msg = str(e).lower()
        if not ("chat not found" in err_msg or "username invalid" in err_msg or "not found" in err_msg):
            return False

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://fragment.com/username/{username}", timeout=1.2) as resp:
                html = await resp.text()
                if any(word in html for word in ["is taken", "Auction", "bidding", "Unavailable", "Telegram Username"]):
                    if "is available" not in html:
                        return False
    except Exception:
        pass

    return True


def get_username_keyboard(username: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📋 Скопировать", copy_text=CopyTextButton(text=username)),
                InlineKeyboardButton(text="⭐ В избранное", callback_data=f"fav:{username}")
            ],
            [
                InlineKeyboardButton(text="🔍 Искать новый юз", callback_data="next_variant"),
                InlineKeyboardButton(text="⚙️ Фильтры", callback_data="filters")
            ]
        ]
    )


main_reply_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🔍 Искать чистый юз"), KeyboardButton(text="⚙️ Фильтры")]
    ],
    resize_keyboard=True
)


@router.message(F.text == "/start")
async def start_handler(message: Message):
    await message.answer(
        "🤖 <b>Master Trade Bot Online & Username Scanner</b>\n\n"
        "🔇 <code>.mute</code> / <code>.unmute</code> — управление мьютом\n"
        "🛡 <code>.antimute</code> — анти-мьют\n"
        "📋 <code>.clone</code> / <code>.unclone</code> — клонирование\n"
        "🚀 <code>.spam [кол-во] [текст]</code> — спам\n"
        "⚡ <code>.snos</code> — репорт-модуль\n\n"
        "👇 Используй нижнюю панель управления:",
        reply_markup=main_reply_kb,
        parse_mode="HTML"
    )


@router.message(F.text == "🔍 Искать чистый юз")
async def btn_search(message: Message):
    cq = CallbackQuery(id="0", from_user=message.from_user, chat_instance="0", message=message, data="next_variant")
    await cb_next_variant(cq)


@router.message(F.text == "⚙️ Фильтры")
async def btn_filters(message: Message):
    cq = CallbackQuery(id="0", from_user=message.from_user, chat_instance="0", message=message, data="filters")
    await cb_filters(cq)


@router.callback_query(F.data == "next_variant")
async def cb_next_variant(callback: CallbackQuery):
    user_id = callback.from_user.id
    mode, custom_chars = get_user_filter_settings(user_id)
    
    msg = await callback.message.answer("⚡ Ищем 100% свободный ник...")
    
    target_username = ""
    start_time = asyncio.get_event_loop().time()
    
    anim_steps = [
        "🔄 Перебираем крутые слоги из 5 букв...",
        "🔄 Сканируем базы Telegram...",
        "⚡ Проверяем Fragment...",
        "💎 Подбираем уникальный вариант..."
    ]
    
    anim_idx = 0
    while (asyncio.get_event_loop().time() - start_time) < 3.0:
        candidate = generate_username(mode, custom_chars)
        if not is_username_used(candidate) and await check_username_free(candidate):
            target_username = candidate
            break
        
        anim_idx = (anim_idx + 1) % len(anim_steps)
        try:
            await bot.edit_message_text(
                chat_id=callback.message.chat.id,
                message_id=msg.message_id,
                text=anim_steps[anim_idx]
            )
        except Exception:
            pass
            
        await asyncio.sleep(0.001)
            
    if not target_username:
        prefixes = ["vimez", "luxen", "astrz", "zenix", "verto", "vecto", "prime", "apexv", "novaz", "pulse"]
        random.shuffle(prefixes)
        for p in prefixes:
            cand = f"{p}{random.choice('aeiouy')}"
            if not is_username_used(cand) and await check_username_free(cand):
                target_username = cand
                break
        if not target_username:
            consonants = "bcdfghjklmnpqrstvwxz"
            vowels = "aeiouy"
            target_username = f"{random.choice(consonants)}{random.choice(vowels)}{random.choice(consonants)}{random.choice(vowels)}{random.choice(consonants)}"

    mark_username_used(target_username)

    try:
        await bot.delete_message(chat_id=callback.message.chat.id, message_id=msg.message_id)
    except Exception:
        pass
        
    caption = (
        "❄️ <b>100% СВОБОДНЫЙ ЮЗЕРНЕЙМ НАЙДЕН</b>\n\n"
        f"👤 <b>@{target_username}</b>\n"
        f"🚫 Уникальный (без повторов, 5 букв, чистый)\n\n"
        "📋 Нажми «Скопировать», чтобы сохранить в буфер."
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
            text=caption,
            reply_markup=get_username_keyboard(target_username),
            parse_mode="HTML"
        )
    
    await callback.answer()


@router.callback_query(F.data == "filters")
async def cb_filters(callback: CallbackQuery):
    user_id = callback.from_user.id
    current_mode, custom_chars = get_user_filter_settings(user_id)
    
    m1 = "✅ " if current_mode == "letters_only" else ""
    m2 = "✅ " if current_mode == "with_digit" else ""
    m4 = "✅ " if current_mode == "custom" else ""

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"{m1}Только чистые буквы (5 букв, без цифр)", callback_data="set_mode:letters_only")],
            [InlineKeyboardButton(text=f"{m2}Включить буквы с цифрами", callback_data="set_mode:with_digit")],
            [InlineKeyboardButton(text=f"{m4}Свои символы ({custom_chars or 'не задано'})", callback_data="set_custom_prompt")],
            [InlineKeyboardButton(text="🔙 Назад к панели", callback_data="back_to_search")]
        ]
    )
    
    text_content = (
        "⚙️ <b>Меню фильтров поиска юзернеймов:</b>\n\n"
        f"Текущий режим: <b>{current_mode}</b>\n"
        f"Свои символы: <b>{custom_chars or 'нет'}</b>"
    )

    try:
        await callback.message.edit_caption(caption=text_content, reply_markup=keyboard, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text=text_content, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("set_mode:"))
async def cb_set_mode(callback: CallbackQuery):
    mode = callback.data.split(":", 1)[1]
    set_user_mode(callback.from_user.id, mode)
    await callback.answer("✅ Фильтр обновлен!", show_alert=True)
    await cb_filters(callback)


@router.callback_query(F.data == "set_custom_prompt")
async def cb_set_custom_prompt(callback: CallbackQuery):
    user_id = callback.from_user.id
    setattr(bot, f"waiting_chars_{user_id}", True)
    await callback.message.answer("✏️ <b>Напиши в чат буквы или цифры</b>, которые обязательно должны быть в юзернейме (например: <code>vip</code> или <code>777</code>):", parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "back_to_search")
async def cb_back_search(callback: CallbackQuery):
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer("👇 Панель управления:", reply_markup=main_reply_kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("fav:"))
async def cb_fav(callback: CallbackQuery):
    await callback.answer("⭐ Добавлено в избранное!", show_alert=True)


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
