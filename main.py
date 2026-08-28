import asyncio
import random
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BusinessMessagesDeleted

# ⚠️ ВСТАВЬ СЮДА СВОЙ ТОКЕН ОТ BOTFATHER
BOT_TOKEN = "8698964419:AAHt3neQ4J0mHVDv5f4CRT7MiigDn3ThLv0"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

msg_cache = {}       
muted_chats = {}     
antimute_chats = {}  
my_owner_id = None   

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

# ==================== 4. ОБРАБОТКА И МУТ ====================

@dp.business_message()
async def handle_all_business_messages(message: Message):
    global my_owner_id
    conn_id = message.business_connection_id
    chat_id = message.chat.id
    sender_id = message.from_user.id if message.from_user else None

    # При первом вызове команды бот запоминает твой ID
    if my_owner_id is None and message.text and message.text.startswith("."):
        my_owner_id = sender_id

    # Удаляем сообщения собеседника, если чат замучен
    if chat_id in muted_chats:
        if sender_id != my_owner_id:
            await delete_cmd(conn_id, chat_id, message.message_id)
            return

    # Сохраняем сообщения в кэш
    if message.text:
        sender_name = message.from_user.first_name if message.from_user else "Собеседник"
        msg_cache[message.message_id] = {
            "text": message.text,
            "name": sender_name,
            "chat_id": chat_id,
            "from_me": (sender_id == my_owner_id)
        }

@dp.edited_business_message()
async def on_edited_message(message: Message):
    if message.message_id in msg_cache:
        old_text = msg_cache[message.message_id]["text"]
        name = msg_cache[message.message_id]["name"]
        new_text = message.text
        
        if old_text != new_text:
            await bot.send_message(
                chat_id=message.business_connection_id,
                text=f"✏️ **Сообщение было изменено!**\nОт: **{name}**\n\nБыло:\n`{old_text}`\n\nСтало:\n`{new_text}`"
            )
            msg_cache[message.message_id]["text"] = new_text

@dp.deleted_business_messages()
async def on_deleted_messages(event: BusinessMessagesDeleted):
    for msg_id in event.message_ids:
        if msg_id in msg_cache:
            data = msg_cache[msg_id]
            chat_id = data["chat_id"]
            
            if data["from_me"] and chat_id in antimute_chats:
                await bot.send_message(
                    chat_id=chat_id,
                    text=data["text"],
                    business_connection_id=event.connection_id
                )
            else:
                text = (
                    f"🗑 **Это сообщение было удалено:**\n"
                    f"👤 **От:** {data['name']}\n\n"
                    f"💬 {data['text']}"
                )
                await bot.send_message(
                    chat_id=event.connection_id,
                    text=text
                )
            del msg_cache[msg_id]

async def main():
    allowed_updates = [
        "business_message", 
        "edited_business_message", 
        "deleted_business_messages",
        "callback_query"
    ]
    await dp.start_polling(bot, allowed_updates=allowed_updates)

if __name__ == "__main__":
    asyncio.run(main())
