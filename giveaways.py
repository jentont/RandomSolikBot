"""Жизнь розыгрыша: публикация, участие, счётчик на кнопке, итоги.

Таймер (loop) раз в несколько секунд публикует созревшие розыгрыши и
подводит итоги закончившихся. Кнопки «сейчас» в меню зовут те же
функции — двойной запуск отсекает db.claim.
"""

from __future__ import annotations

import asyncio
import logging
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, ReplyParameters

import card
import db
import draw
import keyboards
import texts

log = logging.getLogger("solikdraw.giveaways")

#: Статусы, при которых человек числится в канале. restricted — это
#: участник с ограничениями (например, без права писать в чате), он
#: подписан, если is_member. Исходный RandomGodBot считал «member» и
#: «creator» неподписанными — и не пускал вообще никого.
_IN = {"member", "administrator", "creator"}

#: Сколько секунд копить новые входы, прежде чем перерисовать счётчик на
#: кнопке. Правка сообщения на каждый клик упирается в лимиты Telegram
#: уже на сотне участников в минуту, и бот начинает ловить 429.
COUNTER_EVERY = 4

_dirty: set[int] = set()
_bot_username = ""


def post_url(chat_id: int, username: str | None, message_id: int | None) -> str | None:
    if not message_id:
        return None
    if username:
        return f"https://t.me/{username}/{message_id}"
    raw = str(chat_id)
    if raw.startswith("-100"):
        return f"https://t.me/c/{raw[4:]}/{message_id}"
    return None


def caption_limit(media_type: str | None) -> int:
    return 1024 if media_type else 4096


def compose_post(row) -> str:
    return row["text"] + texts.t(texts.POST_FOOTER, end=texts.when(row["end_at"]), hash=row["seed_hash"][:16])


async def send_post(bot: Bot, chat_id: int, text: str, media_type: str | None,
                    media_id: str | None, markup=None):
    """Отправить пост с медиа или без. Возвращает Message."""
    kwargs = {"chat_id": chat_id, "reply_markup": markup}
    if media_type == "photo":
        return await bot.send_photo(photo=media_id, caption=text, **kwargs)
    if media_type == "video":
        return await bot.send_video(video=media_id, caption=text, **kwargs)
    if media_type == "animation":
        return await bot.send_animation(animation=media_id, caption=text, **kwargs)
    if media_type == "document":
        return await bot.send_document(document=media_id, caption=text, **kwargs)
    return await bot.send_message(text=text, link_preview_options={"is_disabled": True}, **kwargs)


async def _tell(bot: Bot, user_id: int, text: str, **kwargs) -> bool:
    try:
        await bot.send_message(user_id, text, **kwargs)
        return True
    except (TelegramForbiddenError, TelegramBadRequest):
        return False


def _chat(row) -> str:
    return texts.chat_link(row["chat_title"], row["chat_username"])


# --- публикация -------------------------------------------------------

async def publish(bot: Bot, gid: int) -> bool:
    if not await db.claim(gid, "scheduled", "publishing"):
        return False
    row = await db.get(gid)
    try:
        message = await send_post(bot, row["chat_id"], compose_post(row), row["media_type"],
                                  row["media_id"], keyboards.join(gid, 0))
    except TelegramRetryAfter:
        # Не ошибка канала — Telegram попросил подождать. Вернём в очередь,
        # таймер попробует снова через пару тиков.
        await db.set_fields(gid, status="scheduled")
        return False
    except Exception as error:  # нет прав, канал удалён, медиа протухло
        log.warning("розыгрыш #%s не опубликован: %s", gid, error)
        await db.set_fields(gid, status="failed")
        await _tell(bot, row["owner_id"], texts.t(
            texts.OWNER_PUBLISH_FAIL, id=gid, chat=_chat(row), error=escape(str(error)[:200])))
        return False
    await db.set_fields(gid, status="active", message_id=message.message_id, publish_at=db.now())
    await _publish_mirrors(bot, row)
    await _tell(bot, row["owner_id"], texts.t(
        texts.OWNER_PUBLISHED, id=gid, chat=_chat(row), end=texts.when(row["end_at"])),
        link_preview_options={"is_disabled": True})
    return True


async def _publish_mirrors(bot: Bot, row) -> None:
    """Копии поста в каналах спонсоров. Основной пост уже вышел, поэтому
    отказ спонсорского канала розыгрыш не валит — владелец узнаёт, а
    кнопка в основном канале продолжает работать."""
    gid = row["id"]
    for sub in await db.subs(gid):
        if not sub["post"] or sub["chat_id"] == row["chat_id"]:
            continue
        try:
            message = await send_post(bot, sub["chat_id"], compose_post(row), row["media_type"],
                                      row["media_id"], keyboards.join(gid, 0))
        except Exception as error:
            log.warning("копия #%s в %s не вышла: %s", gid, sub["chat_id"], error)
            await _tell(bot, row["owner_id"], texts.t(
                texts.OWNER_MIRROR_FAIL, id=gid, chat=texts.chat_link(sub["title"], sub["username"]),
                error=escape(str(error)[:200])), link_preview_options={"is_disabled": True})
            continue
        await db.add_post(gid, sub["chat_id"], message.message_id, sub["title"], sub["username"])


async def all_posts(row) -> list[dict]:
    """Все посты розыгрыша: основной первым, потом копии у спонсоров."""
    out = [{"chat_id": row["chat_id"], "message_id": row["message_id"],
            "title": row["chat_title"], "username": row["chat_username"], "main": True}]
    for post in await db.posts(row["id"]):
        out.append({"chat_id": post["chat_id"], "message_id": post["message_id"],
                    "title": post["title"], "username": post["username"], "main": False})
    return [p for p in out if p["message_id"]]


# --- участие ----------------------------------------------------------

async def subscribed(bot: Bot, chat_id: int, user_id: int) -> bool | None:
    """True/False — подписан ли. None — проверить не вышло (бота лишили
    админки). Неизвестность не считается отпиской: иначе снятая админка в
    одном канале выбила бы всех победителей разом."""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramBadRequest as error:
        if "user not found" in str(error).lower() or "participant_id_invalid" in str(error).lower():
            return False
        return None
    except TelegramForbiddenError:
        return None
    if member.status in _IN:
        return True
    return member.status == "restricted" and bool(getattr(member, "is_member", False))


async def missing_subs(bot: Bot, gid: int, user_id: int) -> list:
    out = []
    for sub in await db.subs(gid):
        if await subscribed(bot, sub["chat_id"], user_id) is False:
            out.append(sub)
    return out


async def join(bot: Bot, gid: int, user) -> str:
    """Попытка войти в розыгрыш. Возвращает текст всплывашки."""
    row = await db.get(gid)
    if row is None or row["status"] in {"finished", "finishing", "cancelled", "failed"}:
        return texts.JOIN_CLOSED
    if row["status"] != "active":
        return texts.JOIN_NOT_YET
    if user.is_bot:
        return texts.JOIN_BOT
    if await db.player(gid, user.id):
        return texts.JOIN_AGAIN
    need = await missing_subs(bot, gid, user.id)
    if need:
        names = ", ".join(f"@{s['username']}" if s["username"] else s["title"] for s in need)
        return texts.JOIN_NEED.format(chats=names)[:200]
    if not await db.join(gid, user.id, user.full_name, user.username):
        return texts.JOIN_AGAIN
    _dirty.add(gid)
    return texts.JOIN_OK.format(n=await db.count_players(gid), end=texts.when(row["end_at"]))


async def _refresh_counters(bot: Bot) -> None:
    while _dirty:
        gid = _dirty.pop()
        row = await db.get(gid)
        if row is None or row["status"] != "active":
            continue
        markup = keyboards.join(gid, await db.count_players(gid))
        for post in await all_posts(row):
            try:
                await bot.edit_message_reply_markup(
                    chat_id=post["chat_id"], message_id=post["message_id"], reply_markup=markup)
            except TelegramRetryAfter as error:
                _dirty.add(gid)
                await asyncio.sleep(error.retry_after)
                break
            except TelegramBadRequest:
                # «message is not modified» и удалённый пост — счётчик не главное.
                pass


# --- итоги ------------------------------------------------------------

async def pick(bot: Bot, gid: int, count: int, skip: set[int] = frozenset(),
               start: int = 0) -> tuple[list, int, int]:
    """Идти по честной очереди и набрать count подписанных.

    Возвращает (победители, сколько выбыло, позиция, где остановились).
    Позиция нужна для «+ ещё победитель»: следующий берётся с неё же, а
    не с начала, — так дозапрос не может вернуть уже выбывшего.
    """
    players = {p["user_id"]: p for p in await db.players(gid)}
    queue = draw.order((await db.get(gid))["seed"], players)
    subs = await db.subs(gid)
    winners, skipped, pos = [], 0, start
    while pos < len(queue) and len(winners) < count:
        uid = queue[pos]
        pos += 1
        if uid in skip:
            continue
        gone = False
        for sub in subs:
            if await subscribed(bot, sub["chat_id"], uid) is False:
                gone = True
                break
        if gone:
            skipped += 1
            continue
        winners.append(players[uid])
    return winners, skipped, pos


def _winner_rows(winners) -> str:
    return "\n".join(f"{i}. {texts.mention(w['user_id'], w['name'], w['username'])}"
                     for i, w in enumerate(winners, 1))


async def _post_results(bot: Bot, post: dict, text: str, picture: bytes):
    """Карточка итогов ответом на пост. Возвращает сообщение со списком."""
    reply = ReplyParameters(message_id=post["message_id"], allow_sending_without_reply=True)
    message = await bot.send_photo(
        post["chat_id"], BufferedInputFile(picture, "itogi.jpg"),
        caption=text if len(text) <= 1024 else None, reply_parameters=reply)
    if len(text) > 1024:
        # Сотня победителей в подпись к фото не влезает — тогда картинка
        # отдельно, список текстом ответом на неё.
        message = await bot.send_message(
            post["chat_id"], text, link_preview_options={"is_disabled": True},
            reply_parameters=ReplyParameters(message_id=message.message_id,
                                             allow_sending_without_reply=True))
    return message


async def finish(bot: Bot, gid: int) -> bool:
    if not await db.claim(gid, "active", "finishing"):
        return False
    row = await db.get(gid)
    try:
        total = await db.count_players(gid)
        winners, skipped, pos = await pick(bot, gid, row["winners"])
        if winners:
            block = texts.t(texts.RESULTS_WINNERS, rows=_winner_rows(winners))
            if skipped:
                block += texts.t(texts.RESULTS_SKIPPED, n=skipped)
            if len(winners) < row["winners"]:
                block += texts.t(texts.RESULTS_FEW)
        else:
            block = texts.t(texts.RESULTS_NOBODY)
        await db.set_result(gid, {
            "winners": [w["user_id"] for w in winners], "skipped": skipped,
            "pos": pos, "players": total,
        })
        text = texts.t(texts.RESULTS, players=total, winners_block=block,
                       seed=row["seed"], id=gid, bot=_bot_username)

        picture = card.results(len(winners), total, gid)
        errors = []
        posts = await all_posts(row)
        await db.set_fields(gid, status="finished")
        for post in posts:
            try:
                results_message = await _post_results(bot, post, text, picture)
            except Exception as error:
                log.warning("итоги #%s не вышли в %s: %s", gid, post["chat_id"], error)
                errors.append(f"{texts.chat_link(post['title'], post['username'])}: {escape(str(error)[:150])}")
                continue
            url = post_url(post["chat_id"], post["username"], results_message.message_id)
            try:
                await bot.edit_message_reply_markup(
                    chat_id=post["chat_id"], message_id=post["message_id"],
                    reply_markup=keyboards.results_link(total, url, gid))
            except TelegramBadRequest:
                pass
        error_text = "; ".join(errors)

        owner_text = texts.t(texts.OWNER_DONE, id=gid, players=total, winners_block=block)
        if error_text:
            owner_text = texts.t(texts.OWNER_RESULTS_FAIL, id=gid, error=error_text) + "\n\n" + owner_text
        await _tell(bot, row["owner_id"], owner_text, link_preview_options={"is_disabled": True})
        for w in winners:
            await _tell(bot, w["user_id"], texts.t(texts.WINNER_DM, chat=_chat(row)),
                        link_preview_options={"is_disabled": True})
        return True
    except Exception:
        # Упали посреди итогов — вернуть «идёт», чтобы таймер попробовал
        # снова, а не оставить розыгрыш навсегда в «подводится». Ровно это
        # губило оригинал: return из цикла убивал поток итогов целиком.
        log.exception("итоги #%s сорвались, повторю", gid)
        await db.set_fields(gid, status="active")
        return False


async def reroll(bot: Bot, gid: int):
    """Ещё один победитель по той же очереди. Возвращает игрока или None."""
    row = await db.get(gid)
    res = db.result(row)
    winners, skipped, pos = await pick(bot, gid, 1, set(res.get("winners", [])), res.get("pos", 0))
    if not winners:
        return None
    w = winners[0]
    res["winners"] = res.get("winners", []) + [w["user_id"]]
    res["skipped"] = res.get("skipped", 0) + skipped
    res["pos"] = pos
    await db.set_result(gid, res)
    who = texts.mention(w["user_id"], w["name"], w["username"])
    for post in await all_posts(row):
        try:
            await bot.send_message(post["chat_id"], texts.t(texts.REROLL_POST, id=gid, who=who),
                                   reply_parameters=ReplyParameters(message_id=post["message_id"],
                                                                    allow_sending_without_reply=True))
        except Exception as error:
            log.warning("доп. победитель #%s не вышел в %s: %s", gid, post["chat_id"], error)
    await _tell(bot, w["user_id"], texts.t(texts.WINNER_DM, chat=_chat(row)),
                link_preview_options={"is_disabled": True})
    return w


# --- таймер -----------------------------------------------------------

async def recover() -> None:
    """После падения процесса вернуть зависшие промежуточные статусы.

    «publishing» мог успеть уйти в канал, а мог и нет — проверить это
    нельзя, поэтому такой розыгрыш помечается failed и владелец решает
    сам. Повторная публикация вслепую дала бы два поста с разными
    кнопками и половиной участников в каждом.
    """
    for row in await db._all("SELECT id FROM giveaways WHERE status='finishing'"):
        await db.set_fields(row["id"], status="active")
    for row in await db._all("SELECT id FROM giveaways WHERE status='publishing'"):
        await db.set_fields(row["id"], status="failed")


async def loop(bot: Bot) -> None:
    global _bot_username
    _bot_username = (await bot.me()).username
    ticks = 0
    while True:
        try:
            moment = db.now()
            for row in await db.due_publish(moment):
                await publish(bot, row["id"])
            for row in await db.due_finish(moment):
                await finish(bot, row["id"])
            if ticks % COUNTER_EVERY == 0:
                await _refresh_counters(bot)
        except Exception:
            # Таймер не имеет права умереть: без него ни один розыгрыш не
            # выйдет и не закончится, а снаружи бот будет выглядеть живым.
            log.exception("тик таймера сорвался")
        ticks += 1
        await asyncio.sleep(1)
