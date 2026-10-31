"""Меню, каналы, «мои розыгрыши», кнопка участия и /check."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    ChatMemberUpdated,
    Message,
)

import card
import config
import db
import draw
import giveaways
import keyboards
import texts

log = logging.getLogger("solikdraw.handlers")
router = Router()

PER_PAGE = 6
_banner: bytes | None = None
#: file_id баннера после первой отправки: не гонять картинку каждый /start.
_banner_id: str | None = None


async def _remember(message_or_call) -> None:
    user = message_or_call.from_user
    await db.touch_user(user.id, user.full_name, user.username)


@router.message(CommandStart(), F.chat.type == "private")
async def start(message: Message, state: FSMContext) -> None:
    global _banner, _banner_id
    await state.clear()
    await _remember(message)
    caption = texts.t(texts.START)
    photo = _banner_id
    if photo is None:
        _banner = _banner or card.banner()
        photo = BufferedInputFile(_banner, "solik.jpg")
    sent = await message.answer_photo(photo, caption=caption, reply_markup=keyboards.menu())
    if _banner_id is None and sent.photo:
        _banner_id = sent.photo[-1].file_id


@router.message(Command("help"), F.chat.type == "private")
@router.message(F.text == texts.BTN_HELP, F.chat.type == "private")
async def help_(message: Message) -> None:
    await message.answer(texts.t(texts.HELP), reply_markup=keyboards.menu())


# --- каналы -----------------------------------------------------------

async def check_channel(bot: Bot, chat_id: int, user_id: int):
    """(чат, ошибка). Бот должен уметь писать, человек — быть админом."""
    try:
        chat = await bot.get_chat(chat_id)
        me = await bot.get_chat_member(chat_id, bot.id)
    except TelegramBadRequest:
        return None, texts.CHANNEL_NO_RIGHTS
    title = chat.title or "чат"
    can_post = me.status == "creator" or (
        me.status == "administrator" and (chat.type != "channel" or me.can_post_messages))
    if not can_post:
        return chat, texts.CHANNEL_NO_RIGHTS
    try:
        human = await bot.get_chat_member(chat_id, user_id)
    except TelegramBadRequest:
        return chat, texts.CHANNEL_NOT_ADMIN_YOU
    if human.status not in {"administrator", "creator"}:
        return chat, texts.CHANNEL_NOT_ADMIN_YOU
    return chat, None


async def register_channel(bot: Bot, chat_id: int, user_id: int) -> str:
    chat, error = await check_channel(bot, chat_id, user_id)
    title = (chat.title if chat else None) or "чат"
    if error:
        return texts.t(error, title=texts.escape(title))
    await db.add_channel(user_id, chat_id, title, chat.username)
    return texts.t(texts.CHANNEL_ADDED, title=texts.escape(title))


@router.message(F.chat_shared.request_id.in_({keyboards.REQ_CHANNEL, keyboards.REQ_GROUP}))
async def chat_shared(message: Message, bot: Bot, state: FSMContext) -> None:
    await _remember(message)
    await message.answer(await register_channel(bot, message.chat_shared.chat_id, message.from_user.id),
                         reply_markup=keyboards.menu())
    # Если человек добавлял канал посреди мастера на первом шаге —
    # сразу покажем обновлённый список, а не заставим начинать заново.
    from wizard import Wizard, show_channels
    if await state.get_state() == Wizard.channel.state:
        await show_channels(message, state)


@router.my_chat_member()
async def bot_membership(update: ChatMemberUpdated, bot: Bot) -> None:
    """Бота добавили в канал руками или выгнали оттуда."""
    if update.chat.type == "private":
        return
    status = update.new_chat_member.status
    if status in {"left", "kicked"}:
        await db.drop_channel(update.chat.id)
        return
    if status != "administrator" or update.from_user.is_bot:
        return
    await db.touch_user(update.from_user.id, update.from_user.full_name, update.from_user.username)
    text = await register_channel(bot, update.chat.id, update.from_user.id)
    try:
        await bot.send_message(update.from_user.id, text)
    except Exception:
        # Человек ещё не нажимал /start — написать ему нельзя, но канал
        # всё равно записан и появится в списке при первом заходе.
        pass


@router.message(F.text == texts.BTN_CHANNELS, F.chat.type == "private")
async def my_channels(message: Message) -> None:
    rows = await db.channels(message.from_user.id)
    if not rows:
        await message.answer(texts.t(texts.CHANNELS_EMPTY), reply_markup=keyboards.menu())
        return
    lines = "\n".join(f"• {texts.chat_link(r['title'], r['username'])}" for r in rows)
    await message.answer(texts.t(texts.CHANNELS, rows=lines), reply_markup=keyboards.menu(),
                         link_preview_options={"is_disabled": True})


# --- участие ----------------------------------------------------------

@router.callback_query(F.data.startswith("join:"))
async def join(call: CallbackQuery, bot: Bot) -> None:
    gid = int(call.data.split(":")[1])
    try:
        text = await giveaways.join(bot, gid, call.from_user)
    except TelegramRetryAfter:
        text = "солик) перегрелся от наплыва, нажми ещё раз через пару секунд"
    await call.answer(text, show_alert=True)


# --- мои розыгрыши ----------------------------------------------------

def _subs_line(subs) -> str:
    if not subs:
        return texts.SUBS_NONE
    mark = texts.t(texts.SUB_POST_MARK)
    return ", ".join(texts.chat_link(s["title"], s["username"]) + (mark if s["post"] else "") for s in subs)


def _preview(row, limit: int = 300) -> str:
    import re
    plain = re.sub(r"<[^>]+>", "", row["text"] or "")
    plain = texts.escape(texts.unescape(plain))
    return plain[:limit] + ("…" if len(plain) > limit else "")


async def card_text(row) -> str:
    tail = ""
    if row["status"] == "active":
        tail = texts.CARD_LEFT.format(left=texts.left(row["end_at"] - db.now()))
    elif row["status"] == "scheduled":
        tail = " · публикация через " + texts.left(row["publish_at"] - db.now())
    return texts.t(
        texts.CARD, id=row["id"], status=texts.t(texts.STATUS.get(row["status"], row["status"])),
        chat=texts.chat_link(row["chat_title"], row["chat_username"]), winners=row["winners"],
        subs=_subs_line(await db.subs(row["id"])), players=await db.count_players(row["id"]),
        publish=texts.when(row["publish_at"]), end=texts.when(row["end_at"]), tail=tail,
        preview=_preview(row),
    )


async def _show_list(target: Message, user_id: int, page: int, edit: bool) -> None:
    total = await db.count_owner(user_id)
    if not total:
        await target.answer(texts.t(texts.MY_EMPTY), reply_markup=keyboards.menu())
        return
    rows = await db.by_owner(user_id, page * PER_PAGE, PER_PAGE)
    text = texts.t(texts.MY_LIST, total=total)
    markup = keyboards.my_list(rows, page, total, PER_PAGE)
    if edit:
        try:
            await target.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest:
            pass
    await target.answer(text, reply_markup=markup)


@router.message(F.text == texts.BTN_MY, F.chat.type == "private")
async def my_list(message: Message) -> None:
    await _show_list(message, message.from_user.id, 0, edit=False)


async def _own(call: CallbackQuery, gid: int):
    row = await db.get(gid)
    if row is None or row["owner_id"] != call.from_user.id:
        await call.answer("это не твой розыгрыш", show_alert=True)
        return None
    return row


@router.callback_query(F.data.startswith("my:"))
async def my_actions(call: CallbackQuery, bot: Bot) -> None:
    parts = call.data.split(":")
    action = parts[1]
    if action == "page":
        await call.answer()
        await _show_list(call.message, call.from_user.id, int(parts[2]), edit=True)
        return
    gid, page = int(parts[2]), int(parts[3])
    row = await _own(call, gid)
    if row is None:
        return

    async def show(row=None, note: str | None = None) -> None:
        row = row or await db.get(gid)
        try:
            await call.message.edit_text(await card_text(row), reply_markup=keyboards.card(row, page),
                                         link_preview_options={"is_disabled": True})
        except TelegramBadRequest:
            pass
        await call.answer(note or "")

    if action == "open":
        await show(row)
    elif action in {"finish", "cancel", "reroll"}:
        prompt = {"finish": texts.CONFIRM_FINISH, "cancel": texts.CONFIRM_CANCEL,
                  "reroll": texts.CONFIRM_REROLL}[action]
        await call.message.edit_text(texts.t(prompt, id=gid), reply_markup=keyboards.sure(action, gid, page))
        await call.answer()
    elif action == "finish!":
        await call.answer("подвожу итоги…")
        await giveaways.finish(bot, gid)
        await show()
    elif action == "cancel!":
        ok = await db.claim(gid, "scheduled", "cancelled")
        await show(note=None if ok else "уже не отменить — он вышел")
    elif action == "pubnow":
        await call.answer("публикую…")
        await db.set_fields(gid, publish_at=min(row["publish_at"], db.now()))
        await giveaways.publish(bot, gid)
        await show()
    elif action == "reroll!":
        if row["status"] != "finished":
            await show(note="розыгрыш ещё не завершён")
            return
        winner = await giveaways.reroll(bot, gid)
        await show(note=f"новый победитель: {winner['name']}" if winner else texts.REROLL_NONE)
    elif action == "players":
        await call.answer()
        players = await db.players(gid)
        lines = [f"{p['user_id']}\t@{p['username'] or '-'}\t{p['name']}" for p in players]
        data = ("\n".join(lines) or "пусто").encode("utf-8")
        await call.message.answer_document(
            BufferedInputFile(data, f"giveaway_{gid}_players.txt"),
            caption=texts.PLAYERS_FILE.format(id=gid, n=len(players)))
    elif action == "check":
        await call.answer()
        await call.message.answer(await check_text(row, call.from_user.id))


# --- проверка ---------------------------------------------------------

async def check_text(row, viewer_id: int) -> str:
    status = texts.t(texts.STATUS.get(row["status"], row["status"]))
    if row["status"] != "finished":
        return texts.t(texts.CHECK_OPEN, id=row["id"], status=status, hash=row["seed_hash"])
    players = await db.players(row["id"])
    queue = draw.order(row["seed"], [p["user_id"] for p in players])
    you = ""
    if viewer_id in queue:
        you = texts.t(texts.CHECK_YOU, place=queue.index(viewer_id) + 1, total=len(queue))
    match = "да" if draw.fingerprint(row["seed"]) == row["seed_hash"] else "НЕТ"
    snippet = draw.VERIFY_SNIPPET.format(seed=row["seed"], hash=row["seed_hash"])
    return texts.t(texts.CHECK_DONE, id=row["id"], hash=row["seed_hash"], seed=row["seed"],
                   match=match, players=len(players), you=you, snippet=texts.escape(snippet))


@router.message(Command("check"))
async def check(message: Message, command: CommandObject) -> None:
    arg = (command.args or "").strip().lstrip("#")
    if not arg.isdigit():
        await message.answer(texts.t(texts.CHECK_USAGE))
        return
    row = await db.get(int(arg))
    if row is None or row["status"] in {"cancelled", "failed"}:
        await message.answer(texts.t(texts.CHECK_NONE))
        return
    await message.answer(await check_text(row, message.from_user.id))


@router.message(Command("stats"), F.from_user.id.in_(config.ADMIN_IDS))
async def stats(message: Message) -> None:
    await message.answer(texts.t(texts.STATS, **await db.stats()))
