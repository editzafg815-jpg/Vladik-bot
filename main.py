import os
import asyncio
import random
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BusinessMessagesDeleted

# ⚠️ ВСТАВЬ СЮДА СВОЙ ТОКЕН ОТ BOTFATHER
BOT_TOKEN = "8698964419:AAGf5k1EKv-nVjXZtxoxLl3ROLgl3D4eY-A"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

msg_cache = {}       
muted_chats = {}     
antimute_chats = {}  

async def delete_cmd(conn_id: str, chat_id: int, msg_id: int):
    try:
        await bot.delete_business_message(conn_id, chat_id, msg_id)
    except Exception:
        pass

# ==================== 1. МУТ И РАЗМУТ (.mute / .unmute) ====================

@dp.business_message(F.text == ".mute")
async def cmd_mute(message: Message):
    conn_id = message.business_connection_id
    chat_id = message.chat.id
    
    await delete_cmd(conn_id, chat_id, message.message_id)
    muted_chats[chat_id] = True

    # Отправляем уведомление без кнопок, чтобы собеседник не мог размутиться сам
    await bot.send_message(
        chat_id=chat_id,
        text="🔇 **Чат замучен. Ваши сообщения автоматически удаляются.**",
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
        await asyncio.sleep(0.05)  # Максимально быстрый спам

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
            await asyncio.sleep(0.05)  # Максимально быстрый спам
    except Exception:
        pass

# ==================== 4. ОБРАБОТКА И УДАЛЕНИЕ МУТА ====================

@dp.business_message()
async def handle_all_business_messages(message: Message):
    conn_id = message.business_connection_id
    chat_id = message.chat.id

    # В бизнес-сообщениях outgoing=False означает, что пишет СОБЕСЕДНИК
    is_incoming = not message.outgoing

    # Если чат замучен и пишет собеседник — удаляем мгновенно
    if chat_id in muted_chats and is_incoming:
        await delete_cmd(conn_id, chat_id, message.message_id)
        return

    # Сохраняем в кэш для удалёнок/изменений
    if message.text:
        sender_name = message.from_user.first_name if message.from_user else "Собеседник"
        msg_cache[message.message_id] = {
            "text": message.text,
            "name": sender_name,
            "chat_id": chat_id,
            "from_me": message.outgoing
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

# Веб-сервер для Render
async def handle_ping(request):
    return web.Response(text="Bot is alive!")

async def main():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    allowed_updates = [
        "business_message", 
        "edited_business_message", 
        "deleted_business_messages",
        "callback_query"
    ]
    await dp.start_polling(bot, allowed_updates=allowed_updates)

if __name__ == "__main__":
    asyncio.run(main())
