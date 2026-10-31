"""Самопроверка без Telegram: python selfcheck.py → «Всё в порядке».

Поддельный бот записывает, что ему велели отправить, и отвечает на
getChatMember по таблице подписок. Проверяется весь путь розыгрыша:
публикация, вход с проверкой подписки, итоги с отписавшимся
победителем, повтор итогов, доп. победитель и проверка честности.
"""

from __future__ import annotations

import asyncio
import os
import string
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("BOT_TOKEN", "1:selfcheck")
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="solikdraw_")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import card  # noqa: E402
import db  # noqa: E402
import draw  # noqa: E402
import giveaways  # noqa: E402
import texts  # noqa: E402

CHANNEL = -1001234567890
SPONSOR = -1009999999999
OWNER = 777

failures: list[str] = []


def ok(cond: bool, what: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + what)
    if not cond:
        failures.append(what)


class FakeBot:
    id = 42

    def __init__(self):
        self.sent: list[tuple] = []
        self.edits: list[tuple] = []
        self.members: dict[tuple[int, int], str] = {}
        self._mid = 100

    async def me(self):
        return SimpleNamespace(username="solikdraw_bot", id=self.id)

    def _msg(self):
        self._mid += 1
        return SimpleNamespace(message_id=self._mid, photo=None)

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(("text", chat_id, text, kw))
        return self._msg()

    async def send_photo(self, chat_id, photo=None, caption=None, **kw):
        self.sent.append(("photo", chat_id, caption, kw))
        return self._msg()

    send_video = send_animation = send_document = send_photo

    async def edit_message_reply_markup(self, **kw):
        self.edits.append(kw)

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status=self.members.get((chat_id, user_id), "left"), is_member=False,
                               can_post_messages=True)


def user(uid: int, bot=False):
    return SimpleNamespace(id=uid, full_name=f"Игрок {uid}", username=f"p{uid}", is_bot=bot)


def check_templates() -> None:
    print("тексты")
    bad = []
    for name in dir(texts):
        value = getattr(texts, name)
        if not (name.isupper() and isinstance(value, str)):
            continue
        fields = {f for _, f, _, _ in string.Formatter().parse(texts.icons.fill(value)) if f}
        try:
            texts.t(value, **{f: "x" for f in fields})
        except Exception as error:
            bad.append(f"{name}: {error}")
    ok(not bad, "все шаблоны форматируются с иконками " + "; ".join(bad))
    ok("<tg-emoji" in texts.t(texts.START), "в START стоят премиум-эмодзи пака")


def check_time() -> None:
    print("время")
    base = 1767225600  # 01.01.2026 00:00 UTC = 03:00 мск
    ok(texts.parse_when("3ч", base) == base + 3 * 3600, "«3ч» — через три часа")
    ok(texts.parse_when("2д", base) == base + 2 * 86400, "«2д» — через два дня")
    ok(texts.parse_when("18:00", base) == base + 15 * 3600, "«18:00» — сегодня по мск")
    ok(texts.parse_when("02:00", base) == base + 23 * 3600, "прошедшее время — завтра")
    ok(texts.parse_when("05.01 12:00", base) == base + 4 * 86400 + 9 * 3600, "дата с временем")
    ok(texts.parse_when("31.02 10:00", base) is None, "несуществующая дата — None")
    ok(texts.parse_when("завтра", base) is None, "мусор — None")


def check_draw() -> None:
    print("честный рандом")
    seed = "ab" * 16
    ids = list(range(1, 200))
    a, b = draw.order(seed, ids), draw.order(seed, list(reversed(ids)))
    ok(a == b, "очередь не зависит от порядка входа")
    ok(len(set(a)) == len(ids), "очередь без повторов — никто не выигрывает дважды")
    ok(draw.order("cd" * 16, ids) != a, "другой сид — другая очередь")
    ok(len(draw.fingerprint(seed)) == 64, "отпечаток — sha256")
    firsts = [draw.order(draw.new_seed(), range(10))[0] for _ in range(3000)]
    spread = [firsts.count(i) for i in range(10)]
    ok(min(spread) > 220 and max(spread) < 380, f"первое место равномерно: {spread}")


async def check_flow() -> None:
    print("жизнь розыгрыша")
    await db.connect()
    bot = FakeBot()
    giveaways._bot_username = "solikdraw_bot"
    seed = draw.new_seed()
    gid = await db.create({
        "owner_id": OWNER, "chat_id": CHANNEL, "chat_title": "Solutions", "chat_username": "solutions",
        "text": "<b>розыгрыш</b> звёзд", "media_type": "photo", "media_id": "PHOTO",
        "winners": 3, "publish_at": db.now() - 1, "end_at": db.now() + 3600,
        "seed": seed, "seed_hash": draw.fingerprint(seed),
    }, [{"chat_id": CHANNEL, "title": "Solutions", "username": "solutions"},
        {"chat_id": SPONSOR, "title": "Спонсор", "username": "sponsor", "post": True}])

    ok(await giveaways.join(bot, gid, user(1)) == texts.JOIN_NOT_YET, "до публикации войти нельзя")
    ok(await giveaways.publish(bot, gid), "розыгрыш опубликован")
    ok(not await giveaways.publish(bot, gid), "второй раз не публикуется")
    post = next(s for s in bot.sent if s[1] == CHANNEL)
    ok(draw.fingerprint(seed)[:16] in post[2], "в посте стоит отпечаток сида")
    ok(seed not in post[2], "сам сид в посте не светится")
    row = await db.get(gid)
    ok(row["status"] == "active" and row["message_id"], "статус «идёт», id поста сохранён")
    mirror = [s for s in bot.sent if s[1] == SPONSOR]
    ok(len(mirror) == 1 and "join:" in str(mirror[0][3]["reply_markup"]), "копия поста с кнопкой вышла у спонсора")
    ok(len(await db.posts(gid)) == 1, "копия записана в базу")

    for uid in range(1, 11):
        bot.members[(CHANNEL, uid)] = "member"
        bot.members[(SPONSOR, uid)] = "member"
    bot.members[(SPONSOR, 3)] = "left"
    bot.members[(CHANNEL, 4)] = "creator"
    bot.members[(SPONSOR, 4)] = "administrator"

    ok("подпишись" in await giveaways.join(bot, gid, user(3)), "без подписки на спонсора — отказ")
    ok(await giveaways.join(bot, gid, user(4)) == texts.JOIN_OK.format(n=1, end=texts.when(row["end_at"])),
       "создатель канала проходит (в оригинале не пускало)")
    ok(await giveaways.join(bot, gid, user(4)) == texts.JOIN_AGAIN, "повторный вход не дублирует")
    ok(await giveaways.join(bot, gid, user(99, bot=True)) == texts.JOIN_BOT, "боты не участвуют")
    for uid in (1, 2, 5, 6, 7, 8, 9, 10):
        await giveaways.join(bot, gid, user(uid))
    ok(await db.count_players(gid) == 9, "9 участников")
    await giveaways._refresh_counters(bot)
    ok(any("· 9" in str(e["reply_markup"]) for e in bot.edits), "счётчик на кнопке обновился")
    ok({e["chat_id"] for e in bot.edits if "· 9" in str(e["reply_markup"])} == {CHANNEL, SPONSOR},
       "счётчик обновился и у спонсора")

    queue = draw.order(seed, [p["user_id"] for p in await db.players(gid)])
    deserter = queue[0]
    bot.members[(SPONSOR, deserter)] = "left"
    bot.sent.clear()
    ok(await giveaways.finish(bot, gid), "итоги подведены")
    ok(not await giveaways.finish(bot, gid), "повторно итоги не подводятся")
    res = db.result(await db.get(gid))
    ok(res["winners"] == queue[1:4], "победители — следующие по очереди после отписавшегося")
    ok(res["skipped"] == 1, "отписавшийся посчитан выбывшим")
    results = [s for s in bot.sent if s[1] == CHANNEL]
    ok(results and seed in results[0][2], "итоги в канале с раскрытым сидом")
    ok(results[0][3]["reply_parameters"].message_id == row["message_id"], "итоги ответом на пост")
    ok(any(s[1] == SPONSOR and seed in (s[2] or "") for s in bot.sent), "итоги вышли и у спонсора")
    ok(any(e["chat_id"] == SPONSOR and "https://t.me/sponsor/" in str(e["reply_markup"]) for e in bot.edits),
       "кнопка копии ведёт на итоги в канале спонсора")
    ok(any(s[1] == OWNER for s in bot.sent), "владелец получил итоги")
    ok(sum(1 for s in bot.sent if s[1] in res["winners"]) == 3, "победителям ушло по сообщению")
    ok(any("https://t.me/solutions/" in str(e["reply_markup"]) for e in bot.edits),
       "кнопка поста ведёт на итоги")

    extra = await giveaways.reroll(bot, gid)
    ok(extra is not None and extra["user_id"] == queue[4], "доп. победитель — следующий в очереди")
    res = db.result(await db.get(gid))
    ok(len(set(res["winners"])) == 4 and deserter not in res["winners"], "доп. победитель не повторяет старых")

    ok(sum(1 for s in bot.sent if s[1] in (CHANNEL, SPONSOR) and "дополнительный" in (s[2] or "")) == 2,
       "доп. победитель объявлен в обоих каналах")

    print("копия без прав")
    seed3 = draw.new_seed()
    gid3 = await db.create({
        "owner_id": OWNER, "chat_id": CHANNEL, "chat_title": "Solutions", "chat_username": None,
        "text": "третий", "winners": 1, "publish_at": db.now() - 1, "end_at": db.now() + 999,
        "seed": seed3, "seed_hash": draw.fingerprint(seed3),
    }, [{"chat_id": SPONSOR, "title": "Спонсор", "username": "sponsor", "post": True}])
    real_send = bot.send_message

    async def picky(chat_id, text, **kw):
        if chat_id == SPONSOR:
            raise RuntimeError("нет прав")
        return await real_send(chat_id, text, **kw)
    bot.send_message = picky
    ok(await giveaways.publish(bot, gid3), "отказ спонсора не валит розыгрыш")
    ok((await db.get(gid3))["status"] == "active", "основной пост идёт")
    ok(any(s[1] == OWNER and "не вышел" in s[2] for s in bot.sent), "владелец узнал про отказ спонсора")
    bot.send_message = real_send

    print("выложить у спонсора уже идущий")
    seed4 = draw.new_seed()
    gid4 = await db.create({
        "owner_id": OWNER, "chat_id": CHANNEL, "chat_title": "Solutions", "chat_username": None,
        "text": "четвёртый", "winners": 1, "publish_at": db.now() - 1, "end_at": db.now() + 999,
        "seed": seed4, "seed_hash": draw.fingerprint(seed4),
    }, [{"chat_id": SPONSOR, "title": "Спонсор", "username": "sponsor"}])
    await giveaways.publish(bot, gid4)
    ok(not await db.posts(gid4), "без отметки копия не выходит")
    await db.join(gid4, 1, "a", None)
    ok("не могу публиковать" in await giveaways.mirror_now(bot, gid4, SPONSOR, OWNER),
       "без админки бота в канале — понятный отказ")
    bot.members[(SPONSOR, FakeBot.id)] = "administrator"
    ok("не админ" in await giveaways.mirror_now(bot, gid4, SPONSOR, 555), "чужой не может выложить в канал спонсора")
    bot.members[(SPONSOR, OWNER)] = "administrator"
    ok("выложил" in await giveaways.mirror_now(bot, gid4, SPONSOR, OWNER), "админ канала выложил уже идущий розыгрыш")
    last = [s for s in bot.sent if s[1] == SPONSOR][-1]
    ok("· 1" in str(last[3]["reply_markup"]), "копия сразу с текущим счётчиком")
    ok(await giveaways.mirror_now(bot, gid4, SPONSOR, OWNER) == texts.MIRROR_ALREADY, "второй раз не дублирует")
    ok([s["post"] for s in await db.subs(gid4)] == [1], "отметка поста сохранена")

    print("сбой посреди итогов")
    seed2 = draw.new_seed()
    gid2 = await db.create({
        "owner_id": OWNER, "chat_id": CHANNEL, "chat_title": "Solutions", "chat_username": None,
        "text": "второй", "winners": 1, "publish_at": db.now() - 10, "end_at": db.now() - 1,
        "seed": seed2, "seed_hash": draw.fingerprint(seed2),
    }, [])
    await giveaways.publish(bot, gid2)
    real = bot.get_chat_member

    async def boom(*a, **k):
        raise RuntimeError("сеть упала")
    await db.join(gid2, 1, "a", None)
    db_subs = db.subs

    async def one_sub(g):
        return [{"chat_id": CHANNEL, "title": "x", "username": None}]
    db.subs = one_sub
    bot.get_chat_member = boom
    ok(not await giveaways.finish(bot, gid2), "исключение в итогах не роняет таймер")
    ok((await db.get(gid2))["status"] == "active", "розыгрыш вернулся в «идёт» для повтора")
    bot.get_chat_member = real
    db.subs = db_subs
    ok(await giveaways.finish(bot, gid2), "со второй попытки итоги прошли")

    await db._run("UPDATE giveaways SET status='publishing' WHERE id=?", gid2)
    await giveaways.recover()
    ok((await db.get(gid2))["status"] == "failed", "зависшая публикация не повторяется вслепую")
    await db.close()


async def check_migration() -> None:
    print("старая база")
    import aiosqlite
    path = Path(os.environ["DATA_DIR"]) / "old.db"
    async with aiosqlite.connect(path) as old:
        await old.execute("CREATE TABLE subs (giveaway_id INTEGER, chat_id INTEGER, title TEXT, "
                          "username TEXT, PRIMARY KEY (giveaway_id, chat_id))")
        await old.execute("INSERT INTO subs VALUES (1, -100, 'старый', NULL)")
        await old.commit()
    await db.connect(path)
    rows = await db.subs(1)
    ok(len(rows) == 1 and rows[0]["post"] == 0, "колонка post добавилась, старые подписки на месте")
    await db.close()


def check_imports() -> None:
    print("сборка")
    import handlers  # noqa: F401
    import main  # noqa: F401
    import wizard  # noqa: F401
    ok(True, "handlers, wizard, main импортируются")
    import keyboards
    for row in keyboards.menu().keyboard + keyboards.sub_picker().keyboard:
        for button in row:
            req = button.request_chat
            for rights in (req.user_administrator_rights, req.bot_administrator_rights) if req else ():
                if rights is not None:
                    ok(any(v is True for v in rights.model_dump().values()),
                       f"«{button.text}»: права не пустые (иначе ADMIN_RIGHTS_EMPTY)")
            if req and req.user_administrator_rights and req.bot_administrator_rights:
                user, bot = req.user_administrator_rights, req.bot_administrator_rights
                lacking = [k for k, v in bot.model_dump().items() if v and not getattr(user, k)]
                ok(not lacking, f"«{button.text}»: права человека покрывают права бота {lacking}")
    ok(len(card.banner()) > 50_000 and len(card.results(1, 1, 1)) > 50_000, "баннер и карточка итогов рисуются")


def main() -> None:
    # Консоль Windows по умолчанию в cp1251 и падает на галочках.
    sys.stdout.reconfigure(encoding="utf-8")
    check_templates()
    check_time()
    check_draw()
    asyncio.run(check_flow())
    asyncio.run(check_migration())
    check_imports()
    print()
    if failures:
        print(f"Есть ошибки: {len(failures)}")
        sys.exit(1)
    print("Всё в порядке")


if __name__ == "__main__":
    main()
