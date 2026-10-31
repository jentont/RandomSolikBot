"""Премиум-эмодзи пака Solutions в текстах бота.

Текст пишет {e:gift} — и получает <tg-emoji emoji-id="…">🎁</tg-emoji>.
id взяты из solutions-emoji/emoji_ids.json (пак solutions_project) и
лежат копией в assets, чтобы бот не зависел от соседней папки на хостинге.

Отправлять чужие премиум-эмодзи Telegram разрешает не каждому боту. Если
отказал — PremiumFallback ловит отказ, шлёт тот же текст с обычными
эмодзи и выключает иконки до перезапуска, чтобы не биться в ту же стену
на каждом сообщении.
"""

from __future__ import annotations

import json
import logging
import re

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import (
    EditMessageCaption,
    EditMessageText,
    SendAnimation,
    SendDocument,
    SendMessage,
    SendPhoto,
    SendVideo,
)

import config

log = logging.getLogger("solikdraw.icons")

#: ключ иконки → обычный эмодзи, который стоит внутри тега и виден там,
#: где премиальные не показываются.
FALLBACK = {
    "gift": "🎁", "trophy": "🏆", "users": "👥", "user_check": "✅",
    "lock": "🔒", "shield_check": "🛡", "calendar": "📅", "clock": "🕒",
    "timer": "⏳", "check": "✅", "cross": "❌", "plus": "➕",
    "megaphone": "📣", "list": "📋", "star": "⭐", "crown": "👑",
    "sparkle": "✨", "refresh": "🔄", "warning": "⚠️", "info": "ℹ️",
    "back": "◀️", "next": "▶️", "trash": "🗑", "play": "▶️", "stop": "⏹",
    "finish": "🏁", "search": "🔎", "link": "🔗", "file": "📄", "eye": "👁",
    "stats_up": "📈", "rocket": "🚀", "bell": "🔔",
}

_ids: dict[str, str] = {}
try:
    _ids = json.loads((config.BASE_DIR / "assets" / "emoji_ids.json").read_text(encoding="utf-8"))
except (OSError, ValueError):
    log.warning("assets/emoji_ids.json не найден — иконки будут обычными эмодзи")

#: Разрешает ли Telegram этому боту премиум-эмодзи. Узнаётся делом: до
#: первого отказа считаем, что да.
allowed = True

_SLOT = re.compile(r"\{e:([a-z_]+)\}")
_TAG = re.compile(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", re.S)


def e(key: str) -> str:
    plain = FALLBACK.get(key, "")
    custom = _ids.get(key)
    if not (custom and allowed and config.EMOJI):
        return plain
    return f'<tg-emoji emoji-id="{custom}">{plain or "⭐"}</tg-emoji>'


def fill(text: str) -> str:
    """Заменить {e:ключ} на иконки. Незнакомый ключ остаётся как есть."""
    return _SLOT.sub(lambda m: e(m.group(1)) if m.group(1) in FALLBACK else m.group(0), text)


def strip(text: str) -> str:
    """Снять премиум-эмодзи, оставив внутри обычные."""
    return _TAG.sub(lambda m: m.group(1), text)


_FIELDS = {SendMessage: "text", EditMessageText: "text",
           SendPhoto: "caption", SendVideo: "caption", SendAnimation: "caption",
           SendDocument: "caption", EditMessageCaption: "caption"}


def _rejected(error: TelegramBadRequest) -> bool:
    text = str(error).lower()
    # «can't parse entities» сюда не относится: это сломанный HTML, и
    # снятие эмодзи его не починит, а иконки выключило бы зря.
    return "emoji" in text or "document_invalid" in text


class PremiumFallback(BaseRequestMiddleware):
    """Пережить запрет на премиум-эмодзи в любом месте бота разом."""

    async def __call__(self, make_request, bot, method):
        field = next((f for cls, f in _FIELDS.items() if isinstance(method, cls)), None)
        body = getattr(method, field, None) if field else None
        try:
            return await make_request(bot, method)
        except TelegramBadRequest as error:
            if not (isinstance(body, str) and "<tg-emoji" in body and _rejected(error)):
                raise
            global allowed
            if allowed:
                log.warning("премиум-эмодзи не приняты (%s) — дальше обычные", error.message)
            allowed = False
            return await make_request(bot, method.model_copy(update={field: strip(body)}))
