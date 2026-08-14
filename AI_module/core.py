from config import config
import os
import aiosqlite
import time
from openai import AsyncOpenAI
from AI_module import database

DEEPSEEK_API_KEY = config.AI_api_token.get_secret_value()
ai_client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com",)

async def get_deepseek_response(chat_id: int, bot_name: str, extra_prompt: str = ""):
    async with aiosqlite.connect(database.DB_NAME) as db:
        async with db.execute("SELECT summary, personality FROM chat_meta WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            summary = row[0] if row and row[0] else "История пуста. Диалог только начался."
            default_p = "Отвечай коротко, непринужденно, используй разговорный русский язык, шути, если уместно."
            personality = row[1] if row and row[1] else default_p
        async with db.execute("SELECT role, user_name, text FROM chat_history WHERE chat_id = ? ORDER BY id ASC LIMIT 50", (chat_id,)) as cursor:
            rows = await cursor.fetchall()

    system_instruction = (
        f"Ты — активный и живой участник этого группового чата. Твое имя {bot_name}.\n"
        f"Вот краткая выжимка того, что происходило в чате ранее (твоя глобальная память):\n{summary}\n\n"
        f"{personality}\n"
        f"{extra_prompt}\n"
    )

    api_messages = [{"role": "system", "content": system_instruction}]
    for role, name, text in rows:
        content = f"{name}: {text}" if role == "user" else text
        api_messages.append({"role": role, "content": content})

    try:
        response = await ai_client.chat.completions.create(
            model="deepseek-chat",  
            messages=api_messages,
            temperature=0.7,        
            max_tokens=400          
        )
        if response and response.choices:
            first_choice = response.choices[0]
            if hasattr(first_choice, 'message'):
                return first_choice.message.content
            elif isinstance(first_choice, dict):
                return first_choice.get('message', {}).get('content', '')
                
        return "Не удалось прочитать ответ от ИИ."
        
    except Exception as e:
        print(f"Ошибка при запросе к DeepSeek API: {e}")
        return "Ой, что-то мои нейроны заклинило... Повтори еще раз?"

'''    
async def compress_history_to_summary(chat_id: int):
    async with aiosqlite.connect(database.DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM chat_history WHERE chat_id = ?", (chat_id,)) as cursor:
            count = (await cursor.fetchone())[0]
        if count < 100:
            return

        async with db.execute("SELECT id, user_name, text FROM chat_history WHERE chat_id = ? ORDER BY id ASC LIMIT 50", (chat_id,)) as cursor:
            old_messages = await cursor.fetchall()
        
        async with db.execute("SELECT summary FROM chat_meta WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            current_summary = row[0] if row and row[0] else ""

        history_chunk = "\n".join([f"{msg[1]}: {msg[2]}" for msg in old_messages])
        
        prompt = (
            f"Текущее краткое содержание прошлых бесед:\n{current_summary}\n\n"
            f"Вот новые сообщения, которые нужно объединить со старым содержанием:\n{history_chunk}\n\n"
            f"Напиши обновленное краткое содержание чата. Укажи ключевые темы, важные имена, договоренности, факты, своё отношение"
        )
        
        response = await ai_client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}]
        )

        new_summary = current_summary
        if response and response.choices:
            first_choice = response.choices[0]
            if hasattr(first_choice, 'message'):
                new_summary = first_choice.message.content
            elif isinstance(first_choice, dict):
                new_summary = first_choice.get('message', {}).get('content', current_summary)
        else:
            new_summary = current_summary

        await db.execute("INSERT INTO chat_meta (chat_id, summary) VALUES (?, ?) ON CONFLICT(chat_id) DO UPDATE SET summary = excluded.summary", (chat_id, new_summary))

        ids_to_delete = [msg[0] for msg in old_messages]
        await db.execute(
            f"DELETE FROM chat_history WHERE id IN ({','.join(['?']*len(ids_to_delete))})", 
            ids_to_delete
        )
        await db.commit()
'''

async def compress_history_to_summary(chat_id: int):
    async with aiosqlite.connect(database.DB_NAME) as db:
        # 1. Проверяем количество сообщений
        async with db.execute("SELECT COUNT(*) FROM chat_history WHERE chat_id = ?", (chat_id,)) as cursor:
            count = (await cursor.fetchone())[0]
        if count < 100:
            return

        # 2. Забираем старые сообщения и текущее саммари
        async with db.execute("SELECT id, user_name, text FROM chat_history WHERE chat_id = ? ORDER BY id ASC LIMIT 50", (chat_id,)) as cursor:
            old_messages = await cursor.fetchall()
        
        async with db.execute("SELECT summary FROM chat_meta WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            current_summary = row[0] if row and row[0] else ""

        # 3. КРИТИЧЕСКИЙ ШАГ: Удаляем сообщения из базы ДО запроса к ИИ и делаем коммит!
        # Это защитит базу от блокировки при быстром флуде в чате.
        ids_to_delete = [msg[0] for msg in old_messages]
        await db.execute(
            f"DELETE FROM chat_history WHERE id IN ({','.join(['?']*len(ids_to_delete))})", 
            ids_to_delete
        )
        await db.commit() # База свободна, другие сообщения могут записываться!

    # 4. Формируем текст (база закрыта, работаем в памяти)
    history_chunk = "\n".join([f"{msg[1]}: {msg[2]}" for msg in old_messages])
    
    prompt = (
        f"Текущее краткое содержание прошлых бесед:\n{current_summary}\n\n"
        f"Вот новые сообщения, которые нужно объединить со старым содержанием:\n{history_chunk}\n\n"
        f"Напиши обновленное краткое содержание чата. Укажи ключевые темы, важные имена, договоренности, факты, своё отношение"
    )
    
    # 5. Долгий запрос к ИИ (теперь он никому не мешает)
    response = await ai_client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}]
    )

    new_summary = current_summary
    if response and response.choices:
        first_choice = response.choices[0]
        if hasattr(first_choice, 'message'):
            new_summary = first_choice.message.content
        elif isinstance(first_choice, dict):
            new_summary = first_choice.get('message', {}).get('content', current_summary)

    # 6. Снова открываем базу на полсекунды только чтобы сохранить новое саммари
    async with aiosqlite.connect(database.DB_NAME) as db:
        await db.execute(
            "INSERT INTO chat_meta (chat_id, summary) VALUES (?, ?) ON CONFLICT(chat_id) DO UPDATE SET summary = excluded.summary", 
            (chat_id, new_summary)
        )
        await db.commit()

async def set_personality(chat_id: int, personality: str = ""):
    """
    Если personality передано — сохраняет личность для конкретного чата.
    Если не передано — возвращает текущую личность этого чата из базы.
    """
    async with aiosqlite.connect(database.DB_NAME) as db:
        if personality.strip():
            await db.execute("INSERT INTO chat_meta (chat_id, personality) VALUES (?, ?) ON CONFLICT(chat_id) DO UPDATE SET personality = ?", (chat_id, personality.strip(), personality.strip()))
            await db.commit()
            return personality.strip()
        else:
            async with db.execute("SELECT personality FROM chat_meta WHERE chat_id = ?", (chat_id,)) as cursor:
                row = await cursor.fetchone()
                if row and row[0]:
                    return row[0]
                return "Отвечай коротко, непринужденно, в дружеском стиле."