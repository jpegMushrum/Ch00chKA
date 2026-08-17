from __future__ import annotations

import logging

from aiogram import types
from aiogram.enums import MessageEntityType

from filters import filters
from handlers import load_video

logger = logging.getLogger(__name__)


class LegacyMediaAdapter:
    """Keeps existing download implementations outside the new AI pipeline."""

    async def handle(self, message: types.Message) -> None:
        text = message.text or message.caption or ""
        entities = message.entities or message.caption_entities or ()
        for entity in entities:
            if entity.type not in (MessageEntityType.URL, MessageEntityType.TEXT_LINK):
                continue
            url = entity.url or text[entity.offset : entity.offset + entity.length]
            if filters.check_youtube_URL_message(url):
                await load_video.download_youtube_video(url, message=message)
            elif filters.check_tiktok_URL_message(url):
                logger.info("TikTok URL detected; provider is not implemented yet")
            elif filters.check_pornhub_URL_message(url):
                logger.info("PornHub URL detected; provider is disabled")
