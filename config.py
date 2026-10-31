"""Настройки бота розыгрышей. Всё читается из .env рядом с main.py.

Общий .env в корне папки ботов намеренно не подхватывается: там токен
другого бота, а поллинг с одним токеном может вести только один процесс.
"""

from __future__ import annotations

import os
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

#: Хостинги отдают под данные отдельный том и сообщают о нём DATA_DIR.
#: Без него база ляжет рядом с кодом и сотрётся на первом передеплое —
#: вместе с участниками идущих розыгрышей.
DATA_DIR = Path(os.getenv("DATA_DIR") or BASE_DIR / "data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_PATH = Path(os.getenv("DB_PATH", DATA_DIR / "solikdraw.db"))

#: Часовой пояс, в котором люди вводят время публикации и итогов.
#: Сервер почти всегда живёт в UTC, а «завтра в 18:00» пишут по Москве.
TZ_OFFSET = int(os.getenv("TZ_OFFSET", "3"))


def _ints(raw: str | None) -> set[int]:
    out: set[int] = set()
    for chunk in (raw or "").replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.lstrip("-").isdigit():
            out.add(int(chunk))
    return out


#: Прокси до api.telegram.org. Пусто — берём системный: на ПК владельца
#: Telegram ходит через локальный v2ray (127.0.0.1:10809), и aiohttp, в
#: отличие от urllib, сам его не видит — бот падал на первом getMe. На
#: хостинге системного прокси нет, и подключение идёт напрямую.
PROXY = os.getenv("PROXY", "").strip() or urllib.request.getproxies().get("https", "")

#: Владельцы бота: им открыта /stats. Розыгрыши может делать любой.
ADMIN_IDS = _ints(os.getenv("ADMIN_IDS") or os.getenv("OWNER_ID"))

MAX_WINNERS = int(os.getenv("MAX_WINNERS", "100"))
#: Обязательных подписок на розыгрыш. Каждая — запрос getChatMember на
#: каждого участника, так что сотня каналов положила бы кнопку.
MAX_SUBS = int(os.getenv("MAX_SUBS", "5"))
#: Самый долгий розыгрыш, в днях.
MAX_DAYS = int(os.getenv("MAX_DAYS", "60"))

#: Премиум-эмодзи пака Solutions в текстах. Если Telegram их не примет,
#: бот сам перейдёт на обычные — выключать руками нужно, только если
#: хочется сразу без них.
EMOJI = os.getenv("EMOJI", "1").strip() not in {"0", "false", "no", ""}


def check() -> None:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан: впишите его в .env рядом с main.py")
