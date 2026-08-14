#Декоратор, добавляющий поле filter заполненное данными из path.
from functools import wraps

def get_filter_list(path: str = None):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if (not hasattr(func, "filter")) or (not (path is None)):
                if path is None:
                    raise ValueError("Фильтр еще не инициализирован. При первом вызове необходимо передать аргумент 'path'.")
                with open(path, "r", encoding="utf-8") as f:
                    wrapper.filter = [line.strip() for line in f if line.strip() and not line.strip().startswith('#')]
                    func.filter = [line.strip() for line in f if line.strip() and not line.strip().startswith('#')]
            return func(*args, **kwargs)
        def reload():
            if hasattr(wrapper, "filter"):
                delattr(wrapper, "filter")
        wrapper.reload = reload
        return wrapper
    return decorator

@get_filter_list("filters/youtube.txt")
def check_youtube_URL_message(url_text: str):
    if any(i in url_text for i in check_youtube_URL_message.filter):
        return True

@get_filter_list("filters/tiktok.txt")
def check_tiktok_URL_message(url_text: str):
    if any(i in url_text for i in check_tiktok_URL_message.filter):
        return True
    
@get_filter_list("filters/pornhub.txt")
def check_pornhub_URL_message(url_text: str):
    if any(i in url_text for i in check_pornhub_URL_message.filter):
            return True