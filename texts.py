"""Тексты бота голосом солика). Всё в одном месте, чтобы править без логики.

Солик пишет со строчной и улыбается скобкой — как сглыпа) у соседей.
{e:ключ} — премиум-эмодзи пака Solutions, см. icons.py.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from html import escape, unescape  # noqa: F401 — unescape нужен handlers

import config
import icons


def t(template: str, **values) -> str:
    """Подставить иконки и значения.

    Иконки — первыми: {e:gift} для str.format выглядит как поле «e» со
    спецификацией «gift» и роняет форматирование. А подставлять значения
    до иконок нельзя по другой причине: пост пользователя мог бы
    случайно содержать «{e:…}» и превратиться в чужую иконку.
    """
    return icons.fill(template).format(**values)


def plural(number: int, one: str, few: str, many: str) -> str:
    tail = abs(number) % 100
    if 11 <= tail <= 14:
        return many
    tail %= 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


def mention(user_id: int, name: str | None, username: str | None) -> str:
    """Ссылка на человека. Ник надёжнее id-ссылки: tg://user не открывается
    у тех, кто скрыл пересылки, а ник кликается у всех."""
    label = escape((name or "участник").strip() or "участник")
    if username:
        return f'<a href="https://t.me/{escape(username)}">{label}</a>'
    return f'<a href="tg://user?id={user_id}">{label}</a>'


def chat_link(title: str, username: str | None) -> str:
    title = escape(title or "канал")
    return f'<a href="https://t.me/{escape(username)}">{title}</a>' if username else f"<b>{title}</b>"


# --- время ------------------------------------------------------------

TZ = timezone(timedelta(hours=config.TZ_OFFSET))
TZ_NAME = "мск" if config.TZ_OFFSET == 3 else f"UTC{config.TZ_OFFSET:+d}"


def when(moment: int) -> str:
    return datetime.fromtimestamp(moment, TZ).strftime("%d.%m %H:%M") + f" {TZ_NAME}"


def left(seconds: int) -> str:
    seconds = max(0, seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days} д {hours} ч"
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{max(minutes, 1)} мин"


_DATE = re.compile(r"^\s*(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?\s+(\d{1,2})[:.](\d{2})\s*$")
_TIME = re.compile(r"^\s*(\d{1,2})[:.](\d{2})\s*$")
_SPAN = re.compile(r"^\s*(\d+)\s*(м|мин|ч|час|часа|часов|д|дн|день|дня|дней)\s*$", re.I)


def parse_when(raw: str, base: int) -> int | None:
    """«25.12 18:00», «18:00» (ближайшее), «3ч», «2д» → секунды UTC.

    Голое время без даты — это сегодня, а если оно уже прошло — завтра:
    «18:00» в 19:00 почти всегда значит завтрашний вечер, а не ошибку.
    """
    raw = (raw or "").strip().lower()
    span = _SPAN.match(raw)
    if span:
        amount, unit = int(span.group(1)), span.group(2)
        step = 60 if unit.startswith("м") else 3600 if unit.startswith("ч") else 86400
        return base + amount * step
    now_local = datetime.fromtimestamp(base, TZ)
    match = _DATE.match(raw)
    try:
        if match:
            day, month, year, hour, minute = match.groups()
            year = int(year) if year else now_local.year
            if year < 100:
                year += 2000
            moment = datetime(year, int(month), int(day), int(hour), int(minute), tzinfo=TZ)
            # «05.01» в декабре — это январь следующего года.
            if not match.group(3) and moment.timestamp() < base - 86400:
                moment = moment.replace(year=year + 1)
            return int(moment.timestamp())
        match = _TIME.match(raw)
        if match:
            moment = now_local.replace(hour=int(match.group(1)), minute=int(match.group(2)),
                                       second=0, microsecond=0)
            if moment.timestamp() <= base:
                moment += timedelta(days=1)
            return int(moment.timestamp())
    except ValueError:
        return None
    return None


# --- меню -------------------------------------------------------------

START = (
    "{e:sparkle} <b>привет, это солик)</b>\n\n"
    "я провожу честные розыгрыши в твоих каналах и чатах:\n\n"
    "{e:megaphone} сам публикую пост в нужное время\n"
    "{e:shield_check} проверяю подписки — и при входе, и на финише\n"
    "{e:lock} выбираю победителей рандомом, который можно перепроверить\n"
    "{e:trophy} подвожу итоги в канале и пишу тебе\n\n"
    "жми <b>новый розыгрыш</b> — и погнали)"
)

HELP = (
    "{e:info} <b>как это работает</b>\n\n"
    "1. добавь меня админом в канал или чат — кнопкой ниже или руками "
    "(нужно право публиковать сообщения).\n"
    "2. «новый розыгрыш»: пост, число победителей, подписки, время.\n"
    "3. в нужный момент я выложу пост с кнопкой «участвовать».\n"
    "4. в конце выберу победителей и выложу итоги ответом на пост.\n\n"
    "{e:lock} <b>почему это честно</b>\n"
    "в посте стоит отпечаток секретного сида. в итогах сид раскрывается — "
    "по нему любой пересчитает победителей сам: /check номер"
)

BTN_NEW = "новый розыгрыш"
BTN_MY = "мои розыгрыши"
BTN_CHANNELS = "мои каналы"
BTN_HELP = "как это работает"
BTN_ADD_CHANNEL = "добавить канал"
BTN_ADD_GROUP = "добавить чат"
BTN_CANCEL = "отмена"

CHANNELS_EMPTY = (
    "{e:info} пока ни одного канала.\n\n"
    "нажми <b>добавить канал</b> внизу — telegram сам предложит выбрать канал "
    "и выдать мне права. или добавь меня админом руками, я замечу)"
)
CHANNELS = "{e:list} <b>твои каналы и чаты</b>\n\n{rows}\n\nдобавить ещё — кнопками внизу"
CHANNEL_ADDED = "{e:check} <b>{title}</b> на месте — теперь там можно проводить розыгрыши)"
CHANNEL_NO_RIGHTS = (
    "{e:warning} в <b>{title}</b> я не админ или не могу публиковать посты. "
    "выдай право «публикация сообщений» и попробуй ещё раз"
)
CHANNEL_NOT_ADMIN_YOU = "{e:warning} ты не админ в <b>{title}</b> — проводить там розыгрыши не выйдет"
CHANNEL_GONE = "{e:warning} меня убрали из <b>{title}</b> — розыгрыши там больше не опубликую"

# --- мастер -----------------------------------------------------------

STEP_CHANNEL = "{e:megaphone} <b>шаг 1 из 6</b> · где проводим?\n\nвыбери канал или чат:"
STEP_CHANNEL_EMPTY = (
    "{e:megaphone} <b>шаг 1 из 6</b> · где проводим?\n\n"
    "сначала добавь канал — кнопка <b>добавить канал</b> внизу. "
    "как только он появится, продолжим"
)
STEP_TEXT = (
    "{e:file} <b>шаг 2 из 6</b> · пришли пост розыгрыша\n\n"
    "текст, можно с фото, видео, гифкой или файлом. форматирование сохраню. "
    "кнопку «участвовать» добавлю сам)"
)
TEXT_BAD = "{e:warning} нужен текст или медиа с подписью — давай ещё раз"
TEXT_LONG = "{e:warning} длинновато: с медиа telegram пропускает до {limit} символов, а тут {size}. сократи чуть-чуть"
STEP_WINNERS = "{e:crown} <b>шаг 3 из 6</b> · сколько победителей?\n\nвыбери или напиши число до {max}"
WINNERS_BAD = "{e:warning} нужно число от 1 до {max}"
STEP_SUBS = (
    "{e:shield_check} <b>шаг 4 из 6</b> · обязательные подписки\n\n"
    "участвовать смогут только подписчики этих каналов. я проверю их при "
    "входе и ещё раз на финише — отписавшийся победитель выбывает.\n\n"
    "добавить спонсора: пришли <code>@канал</code> или ссылку t.me/… "
    "(я должен быть там админом). отметь «пост и в …» — и розыгрыш выйдет "
    "ещё и там, с той же кнопкой и общими участниками.\n\n{rows}"
)
SUBS_NONE = "<i>без обязательных подписок</i>"
SUBS_FULL = "{e:warning} больше {max} подписок не добавить"
SUB_ADDED = "{e:check} добавил <b>{title}</b>"
SUB_BAD_NAME = "{e:warning} не нашёл такой канал. пришли <code>@имя</code> или ссылку t.me/имя"
SUB_NOT_ADMIN = (
    "{e:warning} в <b>{title}</b> я не админ — проверить подписку не смогу. "
    "попроси владельца добавить меня админом (хватит любых прав) и пришли канал ещё раз"
)
POST_NO_RIGHTS = "в {title} я не могу публиковать — нужно право «публикация сообщений»"
POST_NOT_YOURS = "ты не админ в {title} — выкладывать туда пост может только его админ"
SUB_POST_MARK = " · {e:megaphone} пост"
STEP_PUBLISH = (
    "{e:calendar} <b>шаг 5 из 6</b> · когда опубликовать?\n\n"
    "жми «сейчас» или напиши время: <code>18:00</code>, <code>25.12 18:00</code>, "
    "<code>3ч</code>, <code>2д</code> (время — {tz})"
)
STEP_END = (
    "{e:timer} <b>шаг 6 из 6</b> · когда подводим итоги?\n\n"
    "выбери или напиши: <code>20:00</code>, <code>31.12 23:59</code>, <code>5д</code> (время — {tz})"
)
WHEN_BAD = "{e:warning} не понял время. примеры: <code>18:00</code>, <code>25.12 18:00</code>, <code>3ч</code>"
WHEN_PAST = "{e:warning} это время уже прошло — нужно в будущем"
END_TOO_SOON = "{e:warning} итоги должны быть хотя бы через 5 минут после публикации"
END_TOO_LATE = "{e:warning} дольше {days} дней розыгрыш не держу"

PREVIEW = (
    "{e:eye} <b>проверь, всё ли так</b>\n\n"
    "где: {chat}\n"
    "победителей: <b>{winners}</b>\n"
    "подписки: {subs}\n"
    "пост выйдет в: {where}\n"
    "публикация: <b>{publish}</b>\n"
    "итоги: <b>{end}</b>\n\n"
    "выше — пост, как его увидят участники"
)
CREATED_NOW = "{e:rocket} публикую в {chat}…"
CREATED_LATER = "{e:check} готово! розыгрыш <b>#{id}</b> выйдет {publish}. я напомню)"
CANCELLED_WIZARD = "окей, отменил. жми <b>новый розыгрыш</b>, когда захочешь)"

# --- пост в канале ----------------------------------------------------

#: Строка под постом розыгрыша. Отпечаток сида — обещание, по которому
#: потом проверяют итоги, поэтому он обязан быть в самом посте.
POST_FOOTER = "\n\n<i>итоги: {end} · честный рандом от солика)\nотпечаток: <code>{hash}</code></i>"
BTN_JOIN = "участвовать"
BTN_JOIN_N = "участвовать · {n}"
BTN_RESULTS = "итоги · {n} участников"

JOIN_OK = "ты в игре! участников уже {n}. итоги {end}, удачи)"
JOIN_AGAIN = "ты уже участвуешь — солик) запомнил"
JOIN_CLOSED = "этот розыгрыш уже закончился"
JOIN_NOT_YET = "розыгрыш ещё не начался"
JOIN_NEED = "сначала подпишись: {chats}\nпотом жми ещё раз)"
JOIN_BOT = "ботам участвовать нельзя)"

RESULTS = (
    "{e:trophy} <b>итоги розыгрыша</b>\n\n"
    "участников: <b>{players}</b>\n"
    "{winners_block}\n\n"
    "{e:lock} сид: <code>{seed}</code>\n"
    "проверить: <code>/check {id}</code> в @{bot}"
)
RESULTS_WINNERS = "<b>победители:</b>\n{rows}"
RESULTS_NOBODY = "никто не участвовал — победителей нет("
RESULTS_SKIPPED = "\n<i>выбыли (отписались к финишу): {n}</i>"
RESULTS_FEW = "\n<i>участников меньше, чем мест — выиграли все подходящие</i>"

OWNER_PUBLISHED = "{e:rocket} розыгрыш <b>#{id}</b> опубликован в {chat}. итоги {end}"
OWNER_MIRROR_FAIL = "{e:warning} в {chat} пост розыгрыша <b>#{id}</b> не вышел: {error}. в основном канале всё идёт"
OWNER_PUBLISH_FAIL = (
    "{e:warning} не смог опубликовать розыгрыш <b>#{id}</b> в {chat}: {error}\n\n"
    "проверь, что я админ с правом публикации, и создай розыгрыш заново"
)
OWNER_DONE = "{e:finish} розыгрыш <b>#{id}</b> завершён\n\nучастников: <b>{players}</b>\n{winners_block}"
OWNER_RESULTS_FAIL = "{e:warning} итоги розыгрыша <b>#{id}</b> вышли не везде — {error}. вот они:"
WINNER_DM = "{e:gift} <b>ты выиграл в розыгрыше</b> в {chat}!\n\nорганизатор свяжется с тобой — солик) поздравляет)"

# --- мои розыгрыши ----------------------------------------------------

STATUS = {
    "scheduled": "{e:clock} ждёт публикации",
    "active": "{e:play} идёт",
    "finished": "{e:finish} завершён",
    "cancelled": "{e:cross} отменён",
    "failed": "{e:warning} не опубликован",
}
STATUS_SHORT = {"scheduled": "⏳", "active": "▶", "finished": "✓", "cancelled": "✕", "failed": "!"}

MY_EMPTY = "{e:info} розыгрышей пока нет — жми <b>новый розыгрыш</b>)"
MY_LIST = "{e:list} <b>твои розыгрыши</b> · {total}\n\nвыбери, чтобы открыть:"
CARD = (
    "{e:gift} <b>розыгрыш #{id}</b> · {status}\n\n"
    "где: {chat}\n"
    "победителей: <b>{winners}</b>\n"
    "подписки: {subs}\n"
    "участников: <b>{players}</b>\n"
    "публикация: {publish}\n"
    "итоги: {end}{tail}\n\n"
    "<blockquote expandable>{preview}</blockquote>"
)
CARD_LEFT = " · через {left}"
CONFIRM_FINISH = "{e:warning} подвести итоги розыгрыша <b>#{id}</b> прямо сейчас?"
CONFIRM_CANCEL = "{e:warning} отменить розыгрыш <b>#{id}</b>? пост не выйдет"
CONFIRM_REROLL = (
    "{e:refresh} добавить ещё одного победителя в <b>#{id}</b>?\n\n"
    "возьму следующего по честной очереди и допишу итоги в канал"
)
REROLL_NONE = "больше подходящих участников нет"
REROLL_POST = "{e:refresh} <b>дополнительный победитель</b> розыгрыша #{id}: {who}"
PLAYERS_FILE = "участники розыгрыша #{id}: {n}"

CHECK_USAGE = "{e:search} напиши номер розыгрыша: <code>/check 12</code>"
CHECK_NONE = "{e:warning} розыгрыша с таким номером нет"
CHECK_OPEN = (
    "{e:lock} <b>розыгрыш #{id}</b> · {status}\n\n"
    "отпечаток сида:\n<code>{hash}</code>\n\n"
    "сам сид раскроется в итогах — тогда победителей сможет пересчитать любой"
)
CHECK_DONE = (
    "{e:lock} <b>розыгрыш #{id}</b> · проверка\n\n"
    "отпечаток из поста:\n<code>{hash}</code>\n"
    "сид:\n<code>{seed}</code>\n"
    "sha256(сид) совпадает с отпечатком: <b>{match}</b>\n\n"
    "участников: <b>{players}</b>\n{you}"
    "пересчитать самому:\n<pre><code class=\"language-python\">{snippet}</code></pre>"
)
CHECK_YOU = "твоё место в очереди: <b>{place}</b> из {total}\n\n"

STATS = (
    "{e:stats_up} <b>статистика</b>\n\n"
    "людей: <b>{users}</b>\nканалов: <b>{channels}</b>\n"
    "розыгрышей: <b>{giveaways}</b> (идут: {active})\nучастий: <b>{players}</b>"
)

OOPS = "{e:warning} солик) споткнулся — попробуй ещё раз. если повторится, напиши /start"
OOPS_SHORT = "солик) споткнулся — попробуй ещё раз"
