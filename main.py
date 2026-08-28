import os
import asyncio
import random
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BusinessMessagesDeleted

# ⚠️ ВСТАВЬ СВОЙ ТОКЕН ОТ BOTFATHER
BOT_TOKEN = "8698964419:AAHt3neQ4J0mHVDv5f4CRT7MiigDn3ThLv0"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

msg_cache = {}       
muted_chats = {}     
antimute_chats = {}  
my_owner_id = None   # Сюда автоматически сохранится твой ID при первой команде

async def delete_cmd(conn_id: str, chat_id: int, msg_id: int):
    try:
        await bot.delete_business_message(conn_id, chat_id, msg_id)
    except Exception:
        pass

# ==================== 1. МУТ И РАЗМУТ (.mute / .unmute) ====================

@dp.business_message(F.text == ".mute")
async def cmd_mute(message: Message):
    global my_owner_id
    if message.from_user:
        my_owner_id = message.from_user.id
        
    conn_id = message.business_connection_id
    chat_id = message.chat.id
    
    await delete_cmd(conn_id, chat_id, message.message_id)
    muted_chats[chat_id] = True

    await bot.send_message(
        chat_id=chat_id,
        text="🔇 **Чат замучен. Сообщения собеседника удаляются.**",
        business_connection_id=conn_id
    )

@dp.business_message(F.text == ".unmute")
async def cmd_unmute(message: Message):
    conn_id = message.business_connection_id
    chat_id = message.chat.id
    
    await delete_cmd(conn_id, chat_id, message.message_id)
    if chat_id in muted_chats:
        del muted_chats[chat_id]

    await bot.send_message(
        chat_id=chat_id,
        text="🔊 **Чат размучен.**",
        business_connection_id=conn_id
    )

# ==================== 2. АНТИМУТ (.antimute) ====================

@dp.business_message(F.text == ".antimute")
async def cmd_antimute(message: Message):
    global my_owner_id
    if message.from_user:
        my_owner_id = message.from_user.id

    conn_id = message.business_connection_id
    chat_id = message.chat.id
    
    await delete_cmd(conn_id, chat_id, message.message_id)

    if chat_id in antimute_chats:
        del antimute_chats[chat_id]
        status = "❌ **Антимут отключен.**"
    else:
        antimute_chats[chat_id] = True
        status = "🛡 **Антимут включен.** Сообщения защищены."

    await bot.send_message(
        chat_id=conn_id,
        text=f"Настройки чата `{chat_id}`:\n{status}"
    )

# ==================== 3. БЫСТРЫЙ СПАМ И АНИМАЦИИ ====================

@dp.business_message(F.text.startswith(".type "))
async def cmd_type(message: Message):
    conn_id = message.business_connection_id
    chat_id = message.chat.id
    text_to_type = message.text[6:][:120]
    
    current_text = ""
    for char in text_to_type:
        current_text += char
        try:
            await bot.edit_general_business_message(
                business_connection_id=conn_id,
                chat_id=chat_id,
                message_id=message.message_id,
                text=current_text + "▒"
            )
            await asyncio.sleep(0.1)
        except Exception:
            pass
            
    try:
        await bot.edit_general_business_message(
            business_connection_id=conn_id,
            chat_id=chat_id,
            message_id=message.message_id,
            text=current_text
        )
    except Exception:
        pass

@dp.business_message(F.text.startswith(".ha"))
async def cmd_ha(message: Message):
    conn_id = message.business_connection_id
    chat_id = message.chat.id
    
    try:
        count = min(int(message.text.split()[1]), 50)
    except Exception:
        count = 5

    await delete_cmd(conn_id, chat_id, message.message_id)

    ha_variants = ["ахахах", "АХАХАХА", "ахахахаххах", "хахаха", "АХАХАХАХАХАХ"]
    for _ in range(count):
        await bot.send_message(
            chat_id=chat_id,
            text=random.choice(ha_variants),
            business_connection_id=conn_id
        )
        await asyncio.sleep(0.05)

@dp.business_message(F.text.startswith(".spam "))
async def cmd_spam(message: Message):
    conn_id = message.business_connection_id
    chat_id = message.chat.id
    
    try:
        parts = message.text.split(maxsplit=2)
        count = min(int(parts[1]), 50)
        spam_text = parts[2]

        await delete_cmd(conn_id, chat_id, message.message_id)

        for _ in range(count):
            await bot.send_message(
                chat_id=chat_id,
                text=spam_text,
                business_connection_id=conn_id
            )
            await asyncio.sleep(0.05)
    except Exception:
        pass

# ==================== 4. ОБРАБОТКА
