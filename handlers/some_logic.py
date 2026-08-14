from aiogram import Router, types, Bot
from aiogram.filters import Command
from aiogram.enums import MessageEntityType
from handlers import load_video
from filters import filters
from filters.filters import get_filter_list
from AI_module import core, database
import time

# Изолированный роутер для групповых обработчиков
router = Router()

# Хэндлеры на команды
@router.message(Command("help"))
async def chat_help_command(message: types.Message):
    if message.chat.type in ["group", "supergroup"]:
        await message.reply(
            "Привет! Я твой кастомный помощник.\n"
            "/set_personality [str] - настроить промпт личности для чата.\n"
            "/get_personality - узнать промпт личности для чата.\n"
            "Если в сообщении есть ссылка на youtube видео, я его скачаю и пришлю в лучшем разрешении.\n"
        )

@router.message(Command("set_personality"))
async def chat_help_command(message: types.Message):
    if message.chat.type in ["group", "supergroup"]:
        new_prompt = message.text.replace("/set_personality", "").strip()
        if new_prompt:
            await core.set_personality(chat_id=message.chat.id, personality=new_prompt)
            await message.reply("Частичная лоботомия проведена")
        else:
            await message.reply("А кто указывать промпт будет?")

@router.message(Command("get_personality"))
async def chat_help_command(message: types.Message):
    if message.chat.type in ["group", "supergroup"]:
        personality = await core.set_personality(chat_id=message.chat.id)
        await message.reply(personality)

#------------------------------
#Функции обработки и общий хэндлер на всё
#Реакция на "баг"
async def check_bug_message(message: types.Message):
    if 'bug' in monitor_group_messages.filter and "баг" in message.text.lower():
        await message.answer(f"🔧 @{message.from_user.username} упомянул баг! Зафиксировано.")
    
#Реакция на любой URL
async def check_URL_message(message: types.Message):
    if not message.entities:
        return
    for entity in message.entities:
        if entity.type in [MessageEntityType.URL, MessageEntityType.TEXT_LINK]:
            url_text = message.text[entity.offset : entity.offset + entity.length]
            if 'youtube' in monitor_group_messages.filter and filters.check_youtube_URL_message(url_text):
                await load_video.download_youtube_video(url_text, message = message)
            if 'tiktok' in monitor_group_messages.filter and filters.check_tiktok_URL_message(url_text):
                #await tt_load.send_video(message, url_text)
                print(f"Tictok пока не поддерживается. Выключи этот функционал в filters/handlers.txt")
            if 'pornhub' in monitor_group_messages.filter and filters.check_pornhub_URL_message(url_text):
                #await load_video.download_pornhub_video(url_text, message = message)
                print(f"PornHub пока не поддерживается. Выключи этот функционал в filters/handlers.txt")

# Хендлер ловит все сообщения в группе (если выключен Privacy Mode в BotFather)
@router.message()
@get_filter_list("filters/handlers.txt")
async def monitor_group_messages(message: types.Message,  bot: Bot):
    if message.chat.type in ["group", "supergroup"]:

        if 'AI_chating_memory' in monitor_group_messages.filter:
            chat_id = message.chat.id
            user_name = message.from_user.full_name
            bot_user = await bot.get_me()
            async with database.aiosqlite.connect(database.DB_NAME) as db:
                await db.execute("INSERT INTO chat_history (chat_id, role, user_name, text) VALUES (?, 'user', ?, ?)",(chat_id, user_name, message.text))
                await db.execute("INSERT INTO chat_meta (chat_id, last_message_time) VALUES (?, ?) ON CONFLICT(chat_id) DO UPDATE SET last_message_time = ?",(chat_id, time.time(), time.time()))
                await db.commit()
            await core.compress_history_to_summary(chat_id)

        if 'AI_chating_responce' in monitor_group_messages.filter:
            SKIP_prompt = 'ВАЖНО: реши, имеешь ли ты что сказать по теме, чтобы это было актуально и интересно. Если да - скажи, если нет - отправь ТОЛЬКО одно слово "SKIP"'
            ai_response = await core.get_deepseek_response(chat_id, bot_user.first_name, SKIP_prompt)
            if not 'SKIP' in ai_response.strip():
                await message.reply(ai_response)
                if 'AI_chating_memory' in monitor_group_messages.filter:
                    async with database.aiosqlite.connect(database.DB_NAME) as db:
                        await db.execute("INSERT INTO chat_history (chat_id, role, user_name, text) VALUES (?, 'assistant', ?, ?)",(chat_id, bot_user.first_name, ai_response))
                        await db.execute("INSERT INTO chat_meta (chat_id, last_message_time) VALUES (?, ?) ON CONFLICT(chat_id) DO UPDATE SET last_message_time = ?",(chat_id, time.time(), time.time()))
                        await db.commit()
            else:
                print('skip')
        await check_bug_message(message)
        await check_URL_message(message)