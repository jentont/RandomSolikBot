"""Точка входа: база, восстановление статусов, таймер розыгрышей, поллинг."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, ErrorEvent

import config
import db
import giveaways
import handlers
import icons
import texts
import wizard

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("solikdraw")

COMMANDS = (
    ("start", "Меню"),
    ("check", "Проверить честность розыгрыша"),
    ("help", "Как это работает"),
)


async def run() -> None:
    config.check()
    await db.connect()
    await giveaways.recover()

    session = AiohttpSession(proxy=config.PROXY) if config.PROXY else AiohttpSession()
    if config.PROXY:
        log.info("Telegram через прокси %s", config.PROXY)
    bot = Bot(token=config.BOT_TOKEN, session=session,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot.session.middleware(icons.PremiumFallback())
    dispatcher = Dispatcher()
    # Мастер раньше общего роутера: на его шагах свои ответы на текст.
    dispatcher.include_router(wizard.router)
    dispatcher.include_router(handlers.router)

    @dispatcher.errors()
    async def on_error(event: ErrorEvent) -> None:
        # Без этого любая ошибка Telegram оставляет человека с тишиной в
        # ответ — ровно так /start «молчал» из-за отвергнутой клавиатуры.
        log.error("апдейт %s сорвался: %s", event.update.update_id, event.exception)
        message = event.update.message
        call = event.update.callback_query
        try:
            if message and message.chat.type == "private":
                await message.answer(texts.t(texts.OOPS))
            elif call:
                await call.answer(texts.OOPS_SHORT, show_alert=True)
        except Exception:
            pass

    me = await bot.me()
    log.info("запущен как @%s", me.username)
    await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in COMMANDS])

    timer = asyncio.create_task(giveaways.loop(bot), name="timer")
    try:
        # my_chat_member по умолчанию не приходит — без него бот не
        # заметит, что его добавили в канал руками.
        await dispatcher.start_polling(
            bot, allowed_updates=["message", "callback_query", "my_chat_member"])
    finally:
        timer.cancel()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit) as stop:
        log.info("остановлен: %s", stop or "Ctrl+C")
