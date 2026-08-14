import os
import json
import asyncio
import re
from playwright.async_api import async_playwright

async def download_tiktok_perfect(url: str, output_path: str = "tiktok.mp4") -> str | None:
    print("🚀 Запускаю браузерный контекст с подгрузкой живой сессии JSON...")
    
    cookies_file = "tiktok_cookies.json"
    if not os.path.exists(cookies_file):
        print(f"🔴 Критическая ошибка: Создайте файл {cookies_file} и вставьте туда JSON-куки!")
        return None

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 720},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        )
        
        # Загружаем куки вашего Яндекс Браузера в сессию Playwright
        try:
            with open(cookies_file, "r", encoding="utf-8") as f:
                cookies = json.load(f)
                await context.add_cookies(cookies)
            print("✅ Авторизованные куки аккаунта успешно внедрены в браузер.")
        except Exception as e:
            print(f"🔴 Ошибка чтения куков: {e}")
            await browser.close()
            return None

        page = await context.new_page()
        
        try:
            print("🌐 Перехожу по короткой ссылке под вашей учетной записью...")
            # Переходим по ссылке. Благодаря кукам TikTok честно развернет мобильный URL в длинный адрес видео
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(4000)  # Даем 4 секунды на завершение всех редиректов
            
            resolved_url = page.url
            print(f"🔗 Адресная строка успешно зафиксирована: {resolved_url}")

            # Извлекаем цифровой ID видео из длинной строки
            video_id_match = re.search(r'/video/(\d+)', resolved_url)
            if not video_id_match:
                print("🔴 TikTok снова сбросил сессию на главную. Проверьте актуальность playwright_cookies.json")
                await browser.close()
                return None
                
            video_id = video_id_match.group(1)
            print(f"🎯 Успешно пробит блок защиты! ID видео: {video_id}")
            
            # Официальный беспарольный эндпоинт мобильного азиатского API TikTok
            official_api = f"https://tiktokv.com{video_id}"

            print("🌐 Запрашиваю прямую ссылку через официальный мобильный контур...")
            response = await page.request.get(official_api, headers={
                'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15'
            })
            
            if response.status != 200:
                print(f"🔴 Мобильный контур вернул ошибку HTTP {response.status}")
                await browser.close()
                return None
                
            res_json = await response.json()
            aweme_list = res_json.get("aweme_list", [])
            
            if not aweme_list or len(aweme_list) == 0:
                print("🔴 Видео отсутствует в базе API (возможно, поврежден токен запроса).")
                await browser.close()
                return None
                
            # Забираем блок видеофайла
            video_data = aweme_list[0].get("video", {})
            url_list = video_data.get("play_addr", {}).get("url_list", [])
            
            if not url_list or len(url_list) == 0:
                print("🔴 В ответе API отсутствует прямая ссылка на медиапоток.")
                await browser.close()
                return None
                
            # Извлекаем финальный чистый URL
            video_url = url_list[0]
            print("🎯 Прямая ссылка на CDN TikTok получена!")
            print("📥 Скачиваю байты MP4 напрямую с серверов хранения...")
            
            async with page.request.get(video_url) as video_response:
                if video_response.status == 200:
                    video_bytes = await video_response.body()
                    with open(output_path, "wb") as f:
                        f.write(video_bytes)
                        
                    await browser.close()
                    
                    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                        print("✅ Видео успешно скачано напрямую через мобильное API!")
                        return output_path
                else:
                    print(f"🔴 CDN сервер хранения вернул ошибку HTTP {video_response.status}")
                    
            await browser.close()
            return None
            
        except Exception as e:
            print(f"🔴 Критическая ошибка конвейера: {e}")
            await browser.close()
            return None

# Блок запуска теста
async def test_run():
    TEST_URL = "https://tiktok.com"
    result = await download_tiktok_perfect(TEST_URL, "test_tiktok.mp4")
    
    if result:
        print(f"\n🎉 ТЕСТ УСПЕШЕН! Файл сохранен как: {result}")
    else:
        print("\n❌ ТЕСТ ПРОВАЛЕН. Видео не скачалось.")

if __name__ == "__main__":
    asyncio.run(test_run())







async def send_video(message: types.Message, url: str, vid_queue=deque()):
    filename = f"video_{time.time()}_{message.chat.id}_{message.from_user.id}.mp4"
    loop = asyncio.get_event_loop()
    downloaded_file = await loop.run_in_executor(None, download_youtube_video, url, filename)
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