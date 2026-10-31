"""Мастер и меню через настоящий Dispatcher aiogram, но без сети.

Сессия бота подменена: каждый запрос записывается, а ответ собирается
по типу метода. Так проверяются фильтры, состояния FSM и порядок
роутеров — то, что selfcheck.py не трогает.

    python selfcheck_wizard.py → «Мастер в порядке»
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "1:selfcheck")
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="solikdraw_wiz_")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aiogram import Bot, Dispatcher, methods  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.types import (  # noqa: E402
    CallbackQuery,
    Chat,
    ChatFullInfo,
    ChatMemberAdministrator,
    ChatShared,
    Message,
    PhotoSize,
    Update,
    User,
)

import db  # noqa: E402
import handlers  # noqa: E402
import texts  # noqa: E402
import wizard  # noqa: E402

ME = 123456789
BOT_ID = 42
CHANNEL = -1001234567890
SPONSOR = -1007777777777
failures: list[str] = []


def ok(cond: bool, what: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + what)
    if not cond:
        failures.append(what)


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls: list = []
        self.mid = 1000

    async def close(self):
        pass

    async def stream_content(self, *a, **k):  # pragma: no cover
        yield b""

    def _message(self, chat_id, text=None, caption=None, photo=False):
        self.mid += 1
        return Message(
            message_id=self.mid, date=datetime.now(), text=text, caption=caption,
            chat=Chat(id=chat_id, type="channel" if chat_id < 0 else "private"),
            photo=[PhotoSize(file_id="F", file_unique_id="U", width=1, height=1)] if photo else None,
        )

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, methods.GetMe):
            return User(id=BOT_ID, is_bot=True, first_name="солик", username="solikdraw_bot")
        if isinstance(method, (methods.SendMessage, methods.EditMessageText)):
            return self._message(getattr(method, "chat_id", ME) or ME, text=method.text)
        if isinstance(method, (methods.SendPhoto, methods.SendDocument)):
            return self._message(method.chat_id, caption=method.caption, photo=True)
        if isinstance(method, methods.GetChat):
            # construct без проверки: обязательные поля ChatFullInfo
            # меняются от версии к версии, а боту нужны только эти.
            if isinstance(method.chat_id, str):
                # «@novostislavika» — спонсор, присланный по имени.
                return ChatFullInfo.model_construct(id=SPONSOR, type="channel",
                                                    title="Новости", username=method.chat_id.lstrip("@"))
            return ChatFullInfo.model_construct(id=method.chat_id, type="channel",
                                                title="Solutions", username="solutions")
        if isinstance(method, methods.GetChatMember):
            rights = {n: False for n, f in ChatMemberAdministrator.model_fields.items()
                      if f.is_required() and n not in {"status", "user", "can_be_edited"}}
            rights.update(can_post_messages=True, can_edit_messages=True)
            return ChatMemberAdministrator(
                user=User(id=method.user_id, is_bot=method.user_id == BOT_ID, first_name="x"),
                can_be_edited=False, **rights)
        return True


me_user = User(id=ME, is_bot=False, first_name="Тест", username="tester")
private = Chat(id=ME, type="private")
_uid = 0


def _next() -> int:
    global _uid
    _uid += 1
    return _uid


def msg(text=None, **extra) -> Update:
    return Update(update_id=_next(), message=Message(
        message_id=_next(), date=datetime.now(), chat=private, from_user=me_user, text=text, **extra))


def press(data: str) -> Update:
    anchor = Message(message_id=_next(), date=datetime.now(), chat=private, text="x")
    return Update(update_id=_next(), callback_query=CallbackQuery(
        id=str(_next()), from_user=me_user, chat_instance="c", data=data, message=anchor))


def said(session: FakeSession, since: int) -> str:
    out = []
    for call in session.calls[since:]:
        out.append(str(getattr(call, "text", None) or getattr(call, "caption", None) or ""))
    return "\n".join(out)


async def run() -> None:
    await db.connect()
    session = FakeSession()
    bot = Bot("1:selfcheck", session=session, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.include_router(wizard.router)
    dp.include_router(handlers.router)

    async def step(update: Update) -> str:
        mark = len(session.calls)
        await dp.feed_update(bot, update)
        return said(session, mark)

    print("меню")
    ok("солик)" in await step(msg("/start")), "/start отвечает баннером с приветствием")
    ok("пока ни одного" in await step(msg(texts.BTN_CHANNELS)), "без каналов — подсказка")

    print("канал")
    shared = ChatShared(request_id=1, chat_id=CHANNEL, title="Solutions")
    ok("на месте" in await step(msg(chat_shared=shared)), "канал добавлен кнопкой выбора чата")
    ok((await db.channel(ME, CHANNEL)) is not None, "канал записан в базу")

    print("мастер")
    ok("шаг 1 из 6" in await step(msg(texts.BTN_NEW)), "шаг 1: выбор канала")
    ok("шаг 2 из 6" in await step(press(f"w:ch:{CHANNEL}")), "шаг 2: пост")
    ok("нужен текст" in await step(msg(None)), "сообщение без текста и медиа — переспрос")
    ok("шаг 2" not in await step(msg(texts.BTN_MY)), "кнопка меню на шаге поста — не текст розыгрыша")
    photo = [PhotoSize(file_id="PHOTO", file_unique_id="u", width=10, height=10)]
    ok("шаг 3 из 6" in await step(msg(caption="<b>дарим</b> звёзды", photo=photo)), "шаг 3: победители")
    ok("число от 1" in await step(msg("0")), "0 победителей — отказ")
    ok("шаг 4 из 6" in await step(msg("2")), "шаг 4: подписки")
    out = await step(press("w:own"))
    ok("без обязательных" in out, "подписку на свой канал можно выключить")
    await step(press("w:own"))
    ok("не нашёл" in await step(msg("какой-то текст")), "мусор вместо канала — переспрос")
    ok("добавил" in await step(msg("https://t.me/novostislavika")), "спонсор добавлен ссылкой")
    out = await step(press(f"w:post:{SPONSOR}"))
    ok("пост" in out and "Новости" in out, "у спонсора включён пост")
    ok("шаг 5 из 6" in await step(press("w:subdone")), "шаг 5: публикация")
    ok("не понял" in await step(msg("когда-нибудь")), "непонятное время — переспрос")
    ok("шаг 6 из 6" in await step(press("w:pub:now")), "«сейчас» → шаг 6")
    ok("хотя бы через 5" in await step(msg("1м")), "итоги через минуту — слишком рано")
    out = await step(press("w:end:86400"))
    ok("проверь" in out and "дарим" in out, "предпросмотр поста и сводка")
    ok("пост выйдет в" in out and "novostislavika" in out, "в сводке видно, что пост выйдет и у спонсора")
    ok("отпечаток" in out, "в предпросмотре видна строка с отпечатком")
    preview = [c for c in session.calls if isinstance(c, methods.SendPhoto) and c.chat_id == ME]
    ok(preview and preview[-1].photo == "PHOTO", "предпросмотр с той же картинкой")

    mark = len(session.calls)
    await dp.feed_update(bot, press("w:go"))
    posted = [c for c in session.calls[mark:] if isinstance(c, methods.SendPhoto) and c.chat_id == CHANNEL]
    ok(len(posted) == 1, "по «опубликовать» пост ушёл в канал один раз")
    mirrored = [c for c in session.calls[mark:] if isinstance(c, methods.SendPhoto) and c.chat_id == SPONSOR]
    ok(len(mirrored) == 1, "и копия ушла к спонсору")
    row = (await db.by_owner(ME, 0, 1))[0]
    ok(row["status"] == "active" and row["winners"] == 2, "розыгрыш идёт, 2 победителя")
    ok(85000 < row["end_at"] - row["publish_at"] < 87000, "итоги через сутки от публикации")
    subs = await db.subs(row["id"])
    ok([s["chat_id"] for s in subs] == [CHANNEL, SPONSOR], "подписки: свой канал и спонсор")
    ok([s["post"] for s in subs] == [0, 1], "пост отмечен только у спонсора")

    print("мои розыгрыши")
    out = await step(msg(texts.BTN_MY))
    ok("твои розыгрыши" in out, "список открывается")
    out = await step(press(f"my:open:{row['id']}:0"))
    ok(f"розыгрыш #{row['id']}" in out and "идёт" in out, "карточка розыгрыша")
    ok("подвести итоги" in await step(press(f"my:finish:{row['id']}:0")), "итоги просят подтверждения")
    await step(press(f"my:finish!:{row['id']}:0"))
    ok((await db.get(row["id"]))["status"] == "finished", "итоги по кнопке подведены")
    out = await step(msg(f"/check {row['id']}"))
    ok(row["seed"] in out and "совпадает с отпечатком: <b>да</b>" in out, "/check раскрывает сид и сверяет")
    ok("предпросмотр" in await step(press("join:0")), "кнопка из предпросмотра объясняет, что она ненастоящая")

    print("отмена")
    await step(msg(texts.BTN_NEW))
    ok("отменил" in await step(msg(texts.BTN_CANCEL)), "«отмена» выходит из мастера")
    await db.close()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        asyncio.run(run())
    finally:
        # Без закрытия поток aiosqlite держит процесс после падения.
        asyncio.run(db.close())
    print()
    if failures:
        print(f"Есть ошибки: {len(failures)}")
        sys.exit(1)
    print("Мастер в порядке")


if __name__ == "__main__":
    main()
