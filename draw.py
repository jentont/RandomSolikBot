"""Честный выбор победителей, который можно перепроверить.

Схема «обещание — раскрытие»:
1. При создании розыгрыша бот берёт случайный сид и сразу публикует в
   посте его отпечаток sha256. Сам сид никто не видит, так что подогнать
   участников под результат нельзя.
2. На финише каждому участнику считается билет HMAC-SHA256(сид, id).
   Участники сортируются по билету — это и есть очередь победителей.
3. В итогах сид раскрывается. Любой проверит, что sha256(сид) совпадает
   с отпечатком из поста, и пересчитает очередь сам.

Подменить сид после публикации нельзя (отпечаток уже в посте), а
предсказать билеты до раскрытия — тоже.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Iterable


def new_seed() -> str:
    return secrets.token_hex(16)


def fingerprint(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def ticket(seed: str, user_id: int) -> str:
    return hmac.new(seed.encode(), str(user_id).encode(), hashlib.sha256).hexdigest()


def order(seed: str, user_ids: Iterable[int]) -> list[int]:
    """Очередь победителей: все участники по возрастанию билета.

    Первые N — победители. Если кто-то из них отписался к финишу, место
    берёт следующий по очереди, а не новый бросок: так результат остаётся
    однозначным и проверяемым.
    """
    return sorted(set(user_ids), key=lambda uid: (ticket(seed, uid), uid))


VERIFY_SNIPPET = (
    "import hashlib, hmac\n"
    "seed = '{seed}'\n"
    "ids = [...]  # id участников\n"
    "assert hashlib.sha256(seed.encode()).hexdigest() == '{hash}'\n"
    "t = lambda u: hmac.new(seed.encode(), str(u).encode(), hashlib.sha256).hexdigest()\n"
    "print(sorted(set(ids), key=lambda u: (t(u), u)))"
)
