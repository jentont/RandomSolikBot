"""SQLite: люди, их каналы, розыгрыши, обязательные подписки, участники.

Одно соединение на весь процесс. Время — секунды UTC (int): сравнивать
их с «сейчас» можно без часовых поясов, а пояс нужен только при выводе.
"""

from __future__ import annotations

import json
import time
from typing import Any

import aiosqlite

import config

_db: aiosqlite.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id   INTEGER PRIMARY KEY,
    name      TEXT,
    username  TEXT,
    created   INTEGER
);
CREATE TABLE IF NOT EXISTS channels (
    owner_id  INTEGER,
    chat_id   INTEGER,
    title     TEXT,
    username  TEXT,
    added     INTEGER,
    PRIMARY KEY (owner_id, chat_id)
);
CREATE TABLE IF NOT EXISTS giveaways (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id    INTEGER,
    chat_id     INTEGER,
    chat_title  TEXT,
    chat_username TEXT,
    text        TEXT,
    media_type  TEXT,
    media_id    TEXT,
    winners     INTEGER,
    publish_at  INTEGER,
    end_at      INTEGER,
    -- scheduled → active → finished; либо cancelled / failed
    status      TEXT,
    message_id  INTEGER,
    seed        TEXT,
    seed_hash   TEXT,
    result      TEXT,
    created     INTEGER
);
CREATE INDEX IF NOT EXISTS giveaways_owner ON giveaways(owner_id, id);
CREATE INDEX IF NOT EXISTS giveaways_status ON giveaways(status);
CREATE TABLE IF NOT EXISTS subs (
    giveaway_id INTEGER,
    chat_id     INTEGER,
    title       TEXT,
    username    TEXT,
    PRIMARY KEY (giveaway_id, chat_id)
);
-- Копии поста розыгрыша в каналах спонсоров. Основной пост живёт в
-- giveaways.message_id, здесь только дополнительные.
CREATE TABLE IF NOT EXISTS posts (
    giveaway_id INTEGER,
    chat_id     INTEGER,
    message_id  INTEGER,
    title       TEXT,
    username    TEXT,
    PRIMARY KEY (giveaway_id, chat_id)
);
CREATE TABLE IF NOT EXISTS players (
    giveaway_id INTEGER,
    user_id     INTEGER,
    name        TEXT,
    username    TEXT,
    joined      INTEGER,
    PRIMARY KEY (giveaway_id, user_id)
);
"""


def now() -> int:
    return int(time.time())


async def connect(path=None) -> None:
    global _db
    _db = await aiosqlite.connect(str(path or config.DB_PATH))
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.executescript(SCHEMA)
    # Базы, созданные до копий поста, не знают колонки subs.post. ALTER
    # вместо пересоздания: на хостинге уже идут розыгрыши с участниками.
    async with _db.execute("PRAGMA table_info(subs)") as cur:
        if "post" not in {row[1] for row in await cur.fetchall()}:
            await _db.execute("ALTER TABLE subs ADD COLUMN post INTEGER DEFAULT 0")
    await _db.commit()


async def close() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None


def _conn() -> aiosqlite.Connection:
    assert _db is not None, "db.connect() не вызван"
    return _db


async def _one(sql: str, *args) -> aiosqlite.Row | None:
    async with _conn().execute(sql, args) as cur:
        return await cur.fetchone()


async def _all(sql: str, *args) -> list[aiosqlite.Row]:
    async with _conn().execute(sql, args) as cur:
        return list(await cur.fetchall())


async def _run(sql: str, *args) -> int:
    cur = await _conn().execute(sql, args)
    await _conn().commit()
    return cur.rowcount


# --- люди и каналы ----------------------------------------------------

async def touch_user(user_id: int, name: str, username: str | None) -> None:
    await _run(
        "INSERT INTO users(user_id, name, username, created) VALUES (?,?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET name=excluded.name, username=excluded.username",
        user_id, name, username, now(),
    )


async def add_channel(owner_id: int, chat_id: int, title: str, username: str | None) -> None:
    await _run(
        "INSERT INTO channels(owner_id, chat_id, title, username, added) VALUES (?,?,?,?,?) "
        "ON CONFLICT(owner_id, chat_id) DO UPDATE SET title=excluded.title, username=excluded.username",
        owner_id, chat_id, title, username, now(),
    )


async def channels(owner_id: int) -> list[aiosqlite.Row]:
    return await _all("SELECT * FROM channels WHERE owner_id=? ORDER BY added", owner_id)


async def channel(owner_id: int, chat_id: int) -> aiosqlite.Row | None:
    return await _one("SELECT * FROM channels WHERE owner_id=? AND chat_id=?", owner_id, chat_id)


async def drop_channel(chat_id: int) -> None:
    """Бота выгнали из канала — у всех владельцев он пропадает из списка."""
    await _run("DELETE FROM channels WHERE chat_id=?", chat_id)


# --- розыгрыши --------------------------------------------------------

async def create(data: dict[str, Any], subs: list[dict[str, Any]]) -> int:
    cur = await _conn().execute(
        "INSERT INTO giveaways(owner_id, chat_id, chat_title, chat_username, text, media_type, "
        "media_id, winners, publish_at, end_at, status, seed, seed_hash, created) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,'scheduled',?,?,?)",
        (data["owner_id"], data["chat_id"], data["chat_title"], data.get("chat_username"),
         data["text"], data.get("media_type"), data.get("media_id"), data["winners"],
         data["publish_at"], data["end_at"], data["seed"], data["seed_hash"], now()),
    )
    gid = cur.lastrowid
    for sub in subs:
        await _conn().execute(
            "INSERT OR IGNORE INTO subs(giveaway_id, chat_id, title, username, post) VALUES (?,?,?,?,?)",
            (gid, sub["chat_id"], sub["title"], sub.get("username"), int(bool(sub.get("post")))),
        )
    await _conn().commit()
    return gid


async def get(gid: int) -> aiosqlite.Row | None:
    return await _one("SELECT * FROM giveaways WHERE id=?", gid)


async def by_owner(owner_id: int, offset: int, limit: int) -> list[aiosqlite.Row]:
    return await _all(
        "SELECT * FROM giveaways WHERE owner_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
        owner_id, limit, offset,
    )


async def count_owner(owner_id: int) -> int:
    row = await _one("SELECT COUNT(*) FROM giveaways WHERE owner_id=?", owner_id)
    return row[0]


async def due_publish(moment: int) -> list[aiosqlite.Row]:
    return await _all(
        "SELECT * FROM giveaways WHERE status='scheduled' AND publish_at<=? ORDER BY publish_at", moment
    )


async def due_finish(moment: int) -> list[aiosqlite.Row]:
    return await _all(
        "SELECT * FROM giveaways WHERE status='active' AND end_at<=? ORDER BY end_at", moment
    )


async def claim(gid: int, before: str, after: str) -> bool:
    """Перевести статус, только если он ещё прежний.

    Публикацию и итоги может запустить и таймер, и кнопка «сейчас» —
    одновременно. Выигрывает тот, чей UPDATE сработал; второй видит 0
    строк и ничего не делает. Иначе пост ушёл бы в канал дважды.
    """
    return await _run("UPDATE giveaways SET status=? WHERE id=? AND status=?", after, gid, before) == 1


async def set_fields(gid: int, **fields: Any) -> None:
    keys = ", ".join(f"{k}=?" for k in fields)
    await _run(f"UPDATE giveaways SET {keys} WHERE id=?", *fields.values(), gid)


async def set_result(gid: int, result: dict[str, Any]) -> None:
    await set_fields(gid, result=json.dumps(result, ensure_ascii=False))


def result(row) -> dict[str, Any]:
    try:
        return json.loads(row["result"] or "{}")
    except ValueError:
        return {}


async def subs(gid: int) -> list[aiosqlite.Row]:
    return await _all("SELECT * FROM subs WHERE giveaway_id=? ORDER BY rowid", gid)


async def add_post(gid: int, chat_id: int, message_id: int, title: str, username: str | None) -> None:
    await _run(
        "INSERT OR REPLACE INTO posts(giveaway_id, chat_id, message_id, title, username) VALUES (?,?,?,?,?)",
        gid, chat_id, message_id, title, username,
    )


async def posts(gid: int) -> list[aiosqlite.Row]:
    return await _all("SELECT * FROM posts WHERE giveaway_id=? ORDER BY rowid", gid)


# --- участники --------------------------------------------------------

async def join(gid: int, user_id: int, name: str, username: str | None) -> bool:
    """True — записан впервые, False — уже был."""
    return await _run(
        "INSERT OR IGNORE INTO players(giveaway_id, user_id, name, username, joined) VALUES (?,?,?,?,?)",
        gid, user_id, name, username, now(),
    ) == 1


async def count_players(gid: int) -> int:
    row = await _one("SELECT COUNT(*) FROM players WHERE giveaway_id=?", gid)
    return row[0]


async def players(gid: int) -> list[aiosqlite.Row]:
    return await _all("SELECT * FROM players WHERE giveaway_id=? ORDER BY joined, rowid", gid)


async def player(gid: int, user_id: int) -> aiosqlite.Row | None:
    return await _one("SELECT * FROM players WHERE giveaway_id=? AND user_id=?", gid, user_id)


async def stats() -> dict[str, int]:
    out = {}
    for key, sql in {
        "users": "SELECT COUNT(*) FROM users",
        "channels": "SELECT COUNT(DISTINCT chat_id) FROM channels",
        "giveaways": "SELECT COUNT(*) FROM giveaways",
        "active": "SELECT COUNT(*) FROM giveaways WHERE status IN ('scheduled','active')",
        "players": "SELECT COUNT(*) FROM players",
    }.items():
        out[key] = (await _one(sql))[0]
    return out
