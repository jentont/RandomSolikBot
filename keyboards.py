"""Клавиатуры. Все кнопки — монохромные, без цветных стилей: у бренда
один акцент, и это белый текст на графите."""

from __future__ import annotations

from aiogram.types import (
    ChatAdministratorRights,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestChat,
    ReplyKeyboardMarkup,
)

import texts

#: request_id кнопок выбора чата: по нему понятно, зачем выбирали.
REQ_CHANNEL, REQ_GROUP, REQ_SUB = 1, 2, 3

#: Права, которые Telegram сам предложит выдать боту при выборе канала.
#: Публикация — для поста и итогов, правка — чтобы обновлять счётчик на
#: кнопке. Больше не просим: лишние права пугают и правильно делают.
#: Обязательные поля прав Telegram добавляет почти в каждой версии API,
#: поэтому все обязательные выставляются в False по списку из модели, а
#: не перечисляются руками — иначе бот падал бы на старте после апдейта
#: aiogram.
_BOT_RIGHTS = ChatAdministratorRights(**{
    **{name: False for name, field in ChatAdministratorRights.model_fields.items() if field.is_required()},
    "can_post_messages": True, "can_edit_messages": True,
})
#: В группах и для каналов-подписок боту нужна просто админка: писать в
#: группе может и участник, а подписку проверяет любой админ. Но набор
#: прав без единого True Telegram не принимает (ADMIN_RIGHTS_EMPTY) и
#: роняет всю клавиатуру, поэтому просим «управление чатом» — его имеет
#: любой админ, и ничего сверх админки оно не даёт.
_ADMIN_ONLY = ChatAdministratorRights(**{
    **{name: False for name, field in ChatAdministratorRights.model_fields.items() if field.is_required()},
    "can_manage_chat": True,
})
_GROUP_RIGHTS = _ADMIN_ONLY
#: Права человека обязаны покрывать права бота: выдать боту можно только
#: то, что умеешь сам. С правкой постов у бота, но не у человека Telegram
#: отвечал USER_RIGHTS_MISSING на всё меню — и /start молчал.
_USER_RIGHTS = _BOT_RIGHTS
_USER_GROUP_RIGHTS = _GROUP_RIGHTS


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        resize_keyboard=True,
        keyboard=[
            [KeyboardButton(text=texts.BTN_NEW)],
            [KeyboardButton(text=texts.BTN_MY), KeyboardButton(text=texts.BTN_CHANNELS)],
            [
                KeyboardButton(text=texts.BTN_ADD_CHANNEL, request_chat=KeyboardButtonRequestChat(
                    request_id=REQ_CHANNEL, chat_is_channel=True,
                    user_administrator_rights=_USER_RIGHTS, bot_administrator_rights=_BOT_RIGHTS,
                    request_title=True, request_username=True,
                )),
                KeyboardButton(text=texts.BTN_ADD_GROUP, request_chat=KeyboardButtonRequestChat(
                    request_id=REQ_GROUP, chat_is_channel=False,
                    user_administrator_rights=_USER_GROUP_RIGHTS, bot_administrator_rights=_GROUP_RIGHTS,
                    request_title=True, request_username=True,
                )),
            ],
            [KeyboardButton(text=texts.BTN_HELP)],
        ],
    )


def sub_picker() -> ReplyKeyboardMarkup:
    """Клавиатура шага подписок: выбрать канал, где бот уже админ."""
    return ReplyKeyboardMarkup(
        resize_keyboard=True,
        keyboard=[
            [KeyboardButton(text="+ канал для подписки", request_chat=KeyboardButtonRequestChat(
                request_id=REQ_SUB, chat_is_channel=True,
                user_administrator_rights=_ADMIN_ONLY, bot_administrator_rights=_ADMIN_ONLY,
                request_title=True, request_username=True,
            ))],
            [KeyboardButton(text=texts.BTN_CANCEL)],
        ],
    )


def cancel_only() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[[KeyboardButton(text=texts.BTN_CANCEL)]])


def channels_pick(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(row["title"], f"w:ch:{row['chat_id']}")] for row in rows
    ] + [[_btn(texts.BTN_CANCEL, "w:cancel")]])


def winners() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(str(n), f"w:win:{n}") for n in (1, 2, 3, 5, 10)],
        [_btn(texts.BTN_CANCEL, "w:cancel")],
    ])


def subs(own_on: bool, own_title: str, extra: int) -> InlineKeyboardMarkup:
    mark = "✓" if own_on else "○"
    rows = [[_btn(f"{mark} подписка на {own_title}"[:60], "w:own")]]
    if extra:
        rows.append([_btn("убрать добавленные", "w:subclear")])
    rows.append([_btn("дальше →", "w:subdone")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def publish() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("сейчас", "w:pub:now")],
        [_btn("через 1 ч", "w:pub:3600"), _btn("через 3 ч", "w:pub:10800")],
        [_btn(texts.BTN_CANCEL, "w:cancel")],
    ])


def end() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("1 ч", "w:end:3600"), _btn("1 день", "w:end:86400"), _btn("3 дня", "w:end:259200")],
        [_btn("неделя", "w:end:604800"), _btn("2 недели", "w:end:1209600")],
        [_btn(texts.BTN_CANCEL, "w:cancel")],
    ])


def confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("опубликовать", "w:go")],
        [_btn("начать заново", "w:restart"), _btn(texts.BTN_CANCEL, "w:cancel")],
    ])


def join(gid: int, players: int) -> InlineKeyboardMarkup:
    label = texts.BTN_JOIN_N.format(n=players) if players else texts.BTN_JOIN
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(label, f"join:{gid}")]])


def results_link(players: int, url: str | None, gid: int) -> InlineKeyboardMarkup:
    label = texts.BTN_RESULTS.format(n=players)
    button = InlineKeyboardButton(text=label, url=url) if url else _btn(label, f"join:{gid}")
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


def my_list(rows, page: int, total: int, per_page: int) -> InlineKeyboardMarkup:
    kb = [[_btn(f"{texts.STATUS_SHORT.get(r['status'], '')} #{r['id']} · {r['chat_title']}"[:60],
                f"my:open:{r['id']}:{page}")] for r in rows]
    nav = []
    if page > 0:
        nav.append(_btn("←", f"my:page:{page - 1}"))
    if (page + 1) * per_page < total:
        nav.append(_btn("→", f"my:page:{page + 1}"))
    if nav:
        kb.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=kb)


def card(row, page: int) -> InlineKeyboardMarkup:
    gid, status = row["id"], row["status"]
    kb = []
    if status == "scheduled":
        kb.append([_btn("опубликовать сейчас", f"my:pubnow:{gid}:{page}")])
        kb.append([_btn("отменить", f"my:cancel:{gid}:{page}")])
    elif status == "active":
        kb.append([_btn("подвести итоги сейчас", f"my:finish:{gid}:{page}")])
        kb.append([_btn("обновить", f"my:open:{gid}:{page}"), _btn("участники", f"my:players:{gid}:{page}")])
    elif status == "finished":
        kb.append([_btn("+ ещё победитель", f"my:reroll:{gid}:{page}")])
        kb.append([_btn("участники", f"my:players:{gid}:{page}"), _btn("проверка", f"my:check:{gid}:{page}")])
    kb.append([_btn("← к списку", f"my:page:{page}")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def sure(action: str, gid: int, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        _btn("да", f"my:{action}!:{gid}:{page}"), _btn("нет", f"my:open:{gid}:{page}"),
    ]])
