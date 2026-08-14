import asyncio
import yt_dlp
from collections import deque
import time
import os
from aiogram import types
from aiogram.types import Message, FSInputFile
from functools import wraps
import inspect

#Декоратор отправщик видео. 
#Ставит видос на скачку и потом отправляет асинхронно.
# Декорируемая ф-я определяет метод скачивания
def send_video(func):
    @wraps(func)
    async def wrapper(*args, message: types.Message, **kwargs):
        url = args[0] if args else kwargs.get("url")
        if not url:
            await message.reply("❌ Ошибка: не удалось найти ссылку на видео.")
            return
        filename = f"video_{time.time()}_{message.chat.id}_{message.from_user.id}.mp4"

        if inspect.iscoroutinefunction(func):
            downloaded_file = await func(url=url, output_path=filename)
        else:
            loop = asyncio.get_event_loop()
            downloaded_file = await loop.run_in_executor(None, lambda: func(url=url, output_path=filename))
            
        if downloaded_file and os.path.exists(downloaded_file):
            video_file = FSInputFile(downloaded_file)
            try:
                await message.reply_video(video=video_file)
            except Exception as e:
                await message.answer(f"❌ Не удалось отправить видео. Ошибка: {e}")
            if os.path.exists(downloaded_file):
                    os.remove(downloaded_file)
        else:
            await message.reply("❌ Не удалось скачать видео. Проверьте ссылку или попробуйте позже.")
    return wrapper

@send_video
def download_youtube_video(url: str, output_path: str = "video.mp4") -> str | None:
    time.sleep(3)
    ydl_opts = {
        'cookiefile': 'youtube_cookies.txt', 
        'remote_components': ['ejs:github'], 
        'js_runtimes': {'node': {}}, 
        'format': 'bestvideo+bestaudio[filesize_approx<=48M]/best[filesize_approx<=48M]',
        'format_sort': ['vcodec:h264', 'acodec:aac'],
        'merge_output_format': 'mp4',
        'extractor_args': {
            'youtube': {
                'player_client': ['mweb', 'android'],
                'formats': ['missing_pot']
            }
        },
        'outtmpl': output_path,
        'quiet': True,
        'no_warnings': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return output_path
    except Exception as e:
        print(f"❌ Ошибка при скачивании: {e}")
        return


@send_video
def download_pornhub_video(url: str, output_path: str = "video.mp4") -> str | None:
    time.sleep(3)
    ydl_opts = {
        'format': 'best[filesize_approx<=48M]',
        'format_sort': ['res'],
        'outtmpl': output_path,
        'quiet': True,
        'no_warnings': True,
        
        # Ссылаемся на сохраненный файл с куками
        'cookiefile': 'pornhub_cookies.txt',
        'referer': 'https://www.pornhub.com/',
        
        'no_check_certificate': True,
        'prefer_insecure': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return output_path
    except Exception as e:
        print(f"❌ Ошибка при скачивании с PH: {e}")
        return None