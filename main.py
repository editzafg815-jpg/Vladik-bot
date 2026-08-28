import asyncio
import random
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, 
    BusinessMessagesDeleted, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    CallbackQuery
)

# ⚠️ ВСТАВЬ СЮДА СВОЙ ТОКЕН ОТ BOTFATHER
BOT_TOKEN = "ТВОЙ_ТОКЕН_ОТ_BOTFATHER"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Базы данных в памяти
msg_cache = {}       # Кэш сообщений для отслеживания удалений/изменений
muted_chats = {}     # Замученные чаты {chat_id: True}
antimute_chats = {}  # Чаты с включенным Антимутом {chat_id: True}

# Вспомогательная функция для бесшумного удаления команды
async def delete_cmd(conn_id: str, chat_id: int, msg_id: int):
    try:
        await bot.delete_business_message(conn_id, chat_id, msg_id)
    except:
        pass

# ==================== 1. МУТ И РАЗМУТ (.mute / .unmute) ====================

@dp.business_message(F.text == ".mute")
async def cmd_mute(message: Message):
    conn_id = message.business_connection_id
    chat_id = message.chat.id
    
    await delete_cmd(conn_id, chat_id, message.message_id)
    muted_chats[chat_id] = True

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔊 Размутить", callback_data=f"unmute:{chat_id}")
    ]])

    await bot.send_message(
        chat_id=chat_id,
        text="🔊 **Вы больше не можете писать.**",
        business_connection_id=conn_id,
        reply_markup=kb
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

@dp.callback_query(F.data.startswith("unmute:"))
async def cb_unmute(call: CallbackQuery):
    chat_id = int(call.data.split(":")[1])
    if chat_id in muted_chats:
        del muted_chats[chat_id]
    await call.answer("Чат размучен!")
    await call.message.edit_text("🔊 **Чат размучен.**")

# ==================== 2. АНТИМУТ (.antimute) ====================

@dp.business_message(F.text == ".antimute")
async def cmd_antimute(message: Message):
    conn_id = message.business_connection_id
    chat_id = message.chat.id
    
    # Удаляем саму команду, чтобы никто не видел
    await delete_cmd(conn_id, chat_id, message.message_id)

    # Переключаем режим Антимута для этого чата
    if chat_id in antimute_chats:
        del antimute_chats[chat_id]
        status = "❌ **Антимут отключен.**"
    else:
        antimute_chats[chat_id] = True
        status = "🛡 **Антимут включен.** Теперь ваши сообщения защищены."

    # Уведомление в ЛС бота, а не в общий чат
    await bot.send_message(
        chat_id=conn_id,
        text=f"Настройки чата `{chat_id}`:\n{status}"
    )

# ==================== 3. АНИМАЦИИ И СПАМ (.type, .ha, .spam) ====================

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
            await asyncio.sleep(0.2)
        except:
            pass
            
    await bot.edit_general_business_message(
        business_connection_id=conn_id,
        chat_id=chat_id,
        message_id=message.message_id,
        text=current_text
    )

@dp.business_message(F.text.startswith(".ha"))
async def cmd_ha(message: Message):
    conn_id = message.business_connection_id
    chat_id = message.chat.id
    
    try:
        count = min(int(message.text.split()[1]), 50)
    except:
        count = 5

    await delete_cmd(conn_id, chat_id, message.message_id)

    ha_variants = ["ахахах", "АХАХАХА", "ахахахаххах", "хахаха", "АХАХАХАХАХАХ"]
    for _ in range(count):
        await bot.send_message(
            chat_id=chat_id,
            text=random.choice(ha_variants),
            business_connection_id=conn_id
        )
        await asyncio.sleep(0.4)

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
            await asyncio.sleep(0.3)
    except:
        pass

# ==================== 4. ОБРАБОТКА ВСЕХ ВХОДЯЩИХ СООБЩЕНИЙ ====================

@dp.business_message()
async def handle_all_business_messages(message: Message):
    conn_id = message.business_connection_id
    chat_id = message.chat.id
    
    # 1. Если включен МУТ собеседника — удаляем сообщения собеседника
    if chat_id in muted_chats and message.from_user:
        await delete_cmd(conn_id, chat_id, message.message_id)
        return

    # 2. Кэширование для ловли удалёнок и редактирований
    if message.text:
        sender_name = message.from_user.first_name if message.from_user else "Пользователь"
        msg_cache[message.message_id] = {
            "text": message.text,
            "name": sender_name,
            "chat_id": chat_id,
            "from_me": message.from_user is None # Флаг: отправлено ли тобой
        }

# ==================== 5. ПЕРЕХВАТ ИЗМЕНЕНИЙ И УДАЛЕНИЙ ====================

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

@dp.business_messages_deleted()
async def on_deleted_messages(event: BusinessMessagesDeleted):
    for msg_id in event.message_ids:
        if msg_id in msg_cache:
            data = msg_cache[msg_id]
            chat_id = data["chat_id"]
            
            # АНТИМУТ: Если удалили ТВОЁ сообщение и в этом чате включен .antimute
            if data["from_me"] and chat_id in antimute_chats:
                await bot.send_message(
                    chat_id=chat_id,
                    text=data["text"],
                    business_connection_id=event.connection_id
                )
            else:
                # Обычная ловля удалёнок собеседника
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
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
