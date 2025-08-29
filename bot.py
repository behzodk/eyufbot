import asyncio
import logging

from aiogram import Bot, Dispatcher
from app.config import BOT_TOKEN
from app.handlers.registration import router as reg_router
from app.handlers.booking import router as booking_router
from app.handlers.my_bookings import router as my_bookings_router
from app.handlers import services
from app.handlers import admin as admin_handlers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("elyurt-bot")

async def main() -> None:
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(admin_handlers.router)
    dp.include_router(reg_router)
    dp.include_router(booking_router)
    dp.include_router(my_bookings_router)
    dp.include_router(services.router)
    me = await bot.get_me()
    logger.info("Bot started as @%s (id=%s)", me.username, me.id)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")