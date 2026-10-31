"""Мастер создания розыгрыша: шесть шагов и предпросмотр.

Всё, что человек ввёл, живёт в FSM до кнопки «опубликовать» — в базу
розыгрыш попадает только целиком, без полузаполненных черновиков.
"""

from __future__ import annotations

import re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import config
import db
import draw
import giveaways
import keyboards
import texts

router = Router()

#: Запас под строку с отпечатком и временем итогов, которую бот дописывает
#: к посту. Без него пост, влезший в лимит в мастере, не влез бы в канал.
FOOTER_RESERVE = 140
MIN_LENGTH = 5 * 60

#: Кнопки меню посреди мастера — это не ответ на шаг. Без исключения
#: «мои розыгрыши» на шаге поста стало бы текстом розыгрыша.
NOT_MENU = ~F.text.in_({texts.BTN_NEW, texts.BTN_MY, texts.BTN_CHANNELS, texts.BTN_HELP, texts.BTN_CANCEL})


class Wizard(StatesGroup):
    channel = State()
    text = State()
    winners = State()
    subs = State()
    publish = State()
    end = State()
    confirm = State()


# --- вход и выход -----------------------------------------------------

@router.message(F.text == texts.BTN_NEW, F.chat.type == "private")
async def begin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await db.touch_user(message.from_user.id, message.from_user.full_name, message.from_user.username)
    await state.set_state(Wizard.channel)
    await show_channels(message, state)


async def show_channels(message: Message, state: FSMContext) -> None:
    rows = await db.channels(message.chat.id)
    if not rows:
        await message.answer(texts.t(texts.STEP_CHANNEL_EMPTY), reply_markup=keyboards.menu())
        return
    await message.answer(texts.t(texts.STEP_CHANNEL), reply_markup=keyboards.channels_pick(rows))


@router.message(F.text == texts.BTN_CANCEL)
@router.callback_query(F.data == "w:cancel")
async def cancel(event, state: FSMContext) -> None:
    await state.clear()
    message = event.message if isinstance(event, CallbackQuery) else event
    if isinstance(event, CallbackQuery):
        await event.answer()
    await message.answer(texts.t(texts.CANCELLED_WIZARD), reply_markup=keyboards.menu())


@router.callback_query(F.data == "w:restart")
async def restart(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await state.set_state(Wizard.channel)
    await show_channels(call.message, state)


# --- 1. канал ---------------------------------------------------------

@router.callback_query(Wizard.channel, F.data.startswith("w:ch:"))
async def pick_channel(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    chat_id = int(call.data.split(":")[2])
    row = await db.channel(call.from_user.id, chat_id)
    if row is None:
        await call.answer("канала нет в списке", show_alert=True)
        return
    # Права могли отобрать после добавления — проверим сейчас, а не в
    # момент публикации, когда человек уже ушёл и ждёт пост.
    from handlers import check_channel
    chat, error = await check_channel(bot, chat_id, call.from_user.id)
    if error:
        await call.answer()
        await call.message.answer(texts.t(error, title=texts.escape(row["title"])))
        return
    await call.answer()
    await state.update_data(chat_id=chat_id, chat_title=chat.title or row["title"],
                            chat_username=chat.username, chat_is_channel=chat.type == "channel")
    await state.set_state(Wizard.text)
    await call.message.answer(texts.t(texts.STEP_TEXT), reply_markup=keyboards.cancel_only())


# --- 2. пост ----------------------------------------------------------

def _media(message: Message) -> tuple[str | None, str | None]:
    # Гифка приходит и как animation, и как document — animation первым,
    # иначе она уйдёт в канал файлом.
    if message.photo:
        return "photo", message.photo[-1].file_id
    if message.animation:
        return "animation", message.animation.file_id
    if message.video:
        return "video", message.video.file_id
    if message.document:
        return "document", message.document.file_id
    return None, None


@router.message(Wizard.text, NOT_MENU)
async def got_text(message: Message, state: FSMContext) -> None:
    media_type, media_id = _media(message)
    plain = message.text or message.caption or ""
    if not plain.strip() and not media_type:
        await message.answer(texts.t(texts.TEXT_BAD))
        return
    limit = giveaways.caption_limit(media_type) - FOOTER_RESERVE
    if len(plain) > limit:
        await message.answer(texts.t(texts.TEXT_LONG, limit=limit, size=len(plain)))
        return
    html = message.html_text if plain else ""
    await state.update_data(text=html, media_type=media_type, media_id=media_id)
    await state.set_state(Wizard.winners)
    await message.answer(texts.t(texts.STEP_WINNERS, max=config.MAX_WINNERS), reply_markup=keyboards.winners())


# --- 3. победители ----------------------------------------------------

async def _set_winners(message: Message, state: FSMContext, count: int) -> None:
    await state.update_data(winners=count, own_sub=(await state.get_data()).get("chat_is_channel", True),
                            extra=[])
    await state.set_state(Wizard.subs)
    await message.answer("выбери подписки кнопками ниже)", reply_markup=keyboards.sub_picker())
    await show_subs(message, state)


@router.callback_query(Wizard.winners, F.data.startswith("w:win:"))
async def winners_button(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _set_winners(call.message, state, int(call.data.split(":")[2]))


@router.message(Wizard.winners, NOT_MENU)
async def winners_text(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= config.MAX_WINNERS:
        await message.answer(texts.t(texts.WINNERS_BAD, max=config.MAX_WINNERS))
        return
    await _set_winners(message, state, int(raw))


# --- 4. подписки ------------------------------------------------------

def _sub_list(data: dict) -> list[dict]:
    subs = []
    if data.get("own_sub"):
        subs.append({"chat_id": data["chat_id"], "title": data["chat_title"],
                     "username": data.get("chat_username")})
    subs += [s for s in data.get("extra", []) if s["chat_id"] != data["chat_id"]]
    return subs


def _subs_rows(subs: list[dict]) -> str:
    if not subs:
        return texts.SUBS_NONE
    mark = texts.t(texts.SUB_POST_MARK)
    return "\n".join(f"• {texts.chat_link(s['title'], s.get('username'))}{mark if s.get('post') else ''}"
                     for s in subs)


async def show_subs(message: Message, state: FSMContext, edit: bool = False) -> None:
    data = await state.get_data()
    text = texts.t(texts.STEP_SUBS, rows=_subs_rows(_sub_list(data)))
    extras = [s for s in data.get("extra", []) if s["chat_id"] != data["chat_id"]]
    markup = keyboards.subs(bool(data.get("own_sub")), data["chat_title"], extras)
    if edit:
        try:
            await message.edit_text(text, reply_markup=markup, link_preview_options={"is_disabled": True})
            return
        except TelegramBadRequest:
            pass
    await message.answer(text, reply_markup=markup, link_preview_options={"is_disabled": True})


@router.callback_query(Wizard.subs, F.data == "w:own")
async def toggle_own(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.update_data(own_sub=not data.get("own_sub"))
    await call.answer()
    await show_subs(call.message, state, edit=True)


@router.callback_query(Wizard.subs, F.data == "w:subclear")
async def clear_subs(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(extra=[])
    await call.answer()
    await show_subs(call.message, state, edit=True)


_LINK = re.compile(r"^(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{4,32})/?$|^@?([A-Za-z0-9_]{4,32})$")


async def _add_sub(message: Message, state: FSMContext, bot: Bot, ref, fallback_title: str | None) -> None:
    data = await state.get_data()
    if len(_sub_list(data)) >= config.MAX_SUBS:
        await message.answer(texts.t(texts.SUBS_FULL, max=config.MAX_SUBS))
        return
    try:
        chat = await bot.get_chat(ref)
    except TelegramBadRequest:
        await message.answer(texts.t(texts.SUB_BAD_NAME))
        return
    title = chat.title or fallback_title or "канал"
    # Проверять подписку на канал бот может, только будучи там админом:
    # иначе getChatMember отвечает ошибкой, и проверка молча пропускала бы всех.
    try:
        me = await bot.get_chat_member(chat.id, bot.id)
        ok = me.status in {"administrator", "creator"}
    except TelegramBadRequest:
        ok = False
    if not ok:
        await message.answer(texts.t(texts.SUB_NOT_ADMIN, title=texts.escape(title)))
        return
    extra = [s for s in data.get("extra", []) if s["chat_id"] != chat.id]
    extra.append({"chat_id": chat.id, "title": title, "username": chat.username, "post": False})
    await state.update_data(extra=extra)
    await message.answer(texts.t(texts.SUB_ADDED, title=texts.escape(title)))
    await show_subs(message, state)


@router.message(Wizard.subs, F.chat_shared.request_id == keyboards.REQ_SUB)
async def add_sub(message: Message, state: FSMContext, bot: Bot) -> None:
    await _add_sub(message, state, bot, message.chat_shared.chat_id, message.chat_shared.title)


@router.message(Wizard.subs, NOT_MENU, F.text)
async def add_sub_by_name(message: Message, state: FSMContext, bot: Bot) -> None:
    """Спонсорский канал чаще всего чужой: выбрать его кнопкой Telegram не
    даст (там список только своих админок), а @имя прислать можно всегда."""
    match = _LINK.match(message.text.strip())
    if not match:
        await message.answer(texts.t(texts.SUB_BAD_NAME))
        return
    await _add_sub(message, state, bot, "@" + (match.group(1) or match.group(2)), None)


@router.callback_query(Wizard.subs, F.data.startswith("w:post:"))
async def toggle_post(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    chat_id = int(call.data.split(":")[2])
    data = await state.get_data()
    extra = data.get("extra", [])
    sub = next((s for s in extra if s["chat_id"] == chat_id), None)
    if sub is None:
        await call.answer()
        return
    if not sub.get("post"):
        problem = await giveaways.can_mirror(bot, chat_id, call.from_user.id, sub["title"])
        if problem:
            await call.answer(problem[:200], show_alert=True)
            return
    sub["post"] = not sub.get("post")
    await state.update_data(extra=extra)
    await call.answer()
    await show_subs(call.message, state, edit=True)


@router.callback_query(Wizard.subs, F.data == "w:subdone")
async def subs_done(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(Wizard.publish)
    await call.message.answer("ок)", reply_markup=keyboards.cancel_only())
    await call.message.answer(texts.t(texts.STEP_PUBLISH, tz=texts.TZ_NAME), reply_markup=keyboards.publish())


# --- 5. публикация ----------------------------------------------------

async def _set_publish(message: Message, state: FSMContext, moment: int | None) -> None:
    await state.update_data(publish_at=moment)
    await state.set_state(Wizard.end)
    await message.answer(texts.t(texts.STEP_END, tz=texts.TZ_NAME), reply_markup=keyboards.end())


@router.callback_query(Wizard.publish, F.data.startswith("w:pub:"))
async def publish_button(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    raw = call.data.split(":")[2]
    # None — «сейчас»: точный момент станет известен только по кнопке
    # «опубликовать», а человек может просидеть на предпросмотре минуту.
    await _set_publish(call.message, state, None if raw == "now" else db.now() + int(raw))


@router.message(Wizard.publish, NOT_MENU)
async def publish_text(message: Message, state: FSMContext) -> None:
    moment = texts.parse_when(message.text or "", db.now())
    if moment is None:
        await message.answer(texts.t(texts.WHEN_BAD))
        return
    if moment <= db.now():
        await message.answer(texts.t(texts.WHEN_PAST))
        return
    await _set_publish(message, state, moment)


# --- 6. итоги ---------------------------------------------------------

def _publish_base(data: dict) -> int:
    return data.get("publish_at") or db.now()


async def _set_end(message: Message, state: FSMContext, moment: int, relative: bool) -> None:
    data = await state.get_data()
    base = _publish_base(data)
    if moment - base < MIN_LENGTH:
        await message.answer(texts.t(texts.END_TOO_SOON))
        return
    if moment - base > config.MAX_DAYS * 86400:
        await message.answer(texts.t(texts.END_TOO_LATE, days=config.MAX_DAYS))
        return
    # «Через день» при публикации «сейчас» храним длительностью: отсчёт
    # пойдёт от реальной публикации, а не от минуты на предпросмотре.
    # Явное «31.12 23:59» остаётся моментом — его человек и имел в виду.
    duration = moment - base if relative and data.get("publish_at") is None else None
    await state.update_data(end_at=moment, duration=duration)
    await state.set_state(Wizard.confirm)
    await show_preview(message, state)


@router.callback_query(Wizard.end, F.data.startswith("w:end:"))
async def end_button(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    data = await state.get_data()
    await _set_end(call.message, state, _publish_base(data) + int(call.data.split(":")[2]), True)


@router.message(Wizard.end, NOT_MENU)
async def end_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    moment = texts.parse_when(message.text or "", _publish_base(data))
    if moment is None:
        await message.answer(texts.t(texts.WHEN_BAD))
        return
    await _set_end(message, state, moment, bool(texts._SPAN.match(message.text or "")))


# --- предпросмотр -----------------------------------------------------

def _times(data: dict) -> tuple[int, int]:
    publish = data.get("publish_at") or db.now()
    end = publish + data["duration"] if data.get("duration") else data["end_at"]
    return publish, end


async def show_preview(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    publish, end = _times(data)
    sample = {"text": data["text"], "end_at": end, "seed_hash": "0" * 64}
    await giveaways.send_post(message.bot, message.chat.id, giveaways.compose_post(sample),
                              data.get("media_type"), data.get("media_id"),
                              keyboards.join(0, 0))
    subs = _sub_list(data)
    await message.answer(
        texts.t(texts.PREVIEW, chat=texts.chat_link(data["chat_title"], data.get("chat_username")),
                winners=data["winners"],
                subs=", ".join(texts.chat_link(s["title"], s.get("username")) for s in subs) or texts.SUBS_NONE,
                where=", ".join([texts.chat_link(data["chat_title"], data.get("chat_username"))] + [
                    texts.chat_link(s["title"], s.get("username")) for s in subs
                    if s.get("post") and s["chat_id"] != data["chat_id"]]),
                publish="сразу" if data.get("publish_at") is None else texts.when(publish),
                end=texts.when(end)),
        reply_markup=keyboards.confirm(), link_preview_options={"is_disabled": True})


@router.callback_query(Wizard.confirm, F.data == "w:go")
async def go(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()
    await call.answer()
    publish, end = _times(data)
    seed = draw.new_seed()
    gid = await db.create({
        "owner_id": call.from_user.id, "chat_id": data["chat_id"], "chat_title": data["chat_title"],
        "chat_username": data.get("chat_username"), "text": data["text"],
        "media_type": data.get("media_type"), "media_id": data.get("media_id"),
        "winners": data["winners"], "publish_at": publish, "end_at": end,
        "seed": seed, "seed_hash": draw.fingerprint(seed),
    }, _sub_list(data))
    chat = texts.chat_link(data["chat_title"], data.get("chat_username"))
    if data.get("publish_at") is None:
        await call.message.answer(texts.t(texts.CREATED_NOW, chat=chat), reply_markup=keyboards.menu(),
                                  link_preview_options={"is_disabled": True})
        await giveaways.publish(bot, gid)
    else:
        await call.message.answer(texts.t(texts.CREATED_LATER, id=gid, publish=texts.when(publish)),
                                  reply_markup=keyboards.menu())


# Кнопка розыгрыша в предпросмотре — не настоящая (id 0).
@router.callback_query(F.data == "join:0")
async def fake_join(call: CallbackQuery) -> None:
    await call.answer("это предпросмотр — настоящая кнопка будет в канале)", show_alert=True)
