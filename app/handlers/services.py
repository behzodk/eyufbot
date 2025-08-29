# app/handlers/services.py
import asyncio
import logging
from typing import Dict, List

from aiogram import F, Router
from aiogram.types import Message

from app.db import fetch_services_sync
from app.constants import BTN_SERVICES

logger = logging.getLogger(__name__)
router = Router()

async def fetch_services() -> List[Dict]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fetch_services_sync)

@router.message(F.text == BTN_SERVICES)
async def available_services(m: Message):
    try:
        services = await fetch_services()
    except Exception as e:
        logger.exception("Xizmatlarni olishda xatolik: %s", e)
        await m.answer("Xizmatlarni hozircha yuklab bo‘lmadi.")
        return

    if not services:
        await m.answer("Hozircha xizmatlar sozlanmagan.")
        return

    # Javob matnini tuzish
    lines = []
    for s in services:
        name = s.get("name", "Xizmat")
        dur = s.get("duration_min")
        if name == "Moliyaviy kafillik xati":
            continue
        if isinstance(dur, int):
            lines.append(f"• {name} — ~{dur} daqiqa")
        else:
            lines.append(f"• {name}")

    lines += [
        "",
        "",
        "ℹ️ Uchrashuvga yozilish uchun *Navbat olish* tugmasidan foydalaning.",
    ]

    await m.answer("\n".join(lines), parse_mode="Markdown")