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
    lines = ["📋 *Mavjud xizmatlar*"]
    for s in services:
        name = s.get("name", "Xizmat")
        dur = s.get("duration_min")
        if isinstance(dur, int):
            lines.append(f"• {name} — ~{dur} daqiqa")
        else:
            lines.append(f"• {name}")

    lines += [
        "",
        "🧭 *Tartib (bosqichlar)*",
        "1) *Birlamchi hujjatlar*ni topshirish:",
        "   • Pasport nusxasi",
        "   • Xorijga chiqish pasporti nusxasi",
        "   • Surat",
        "   • Moliyaviy homiydan xat",
        "   • Qaytgandan keyin ish beruvchi homiydan xat",
        "   • Qabul xati (universitetdan)",
        "2) *Moliyaviy kafolat xati*ga murojaat qilish",
        "3) *Viza*ga murojaat qilish",
        "4) Viza olingandan so‘ng: *4 tomonlama shartnoma*",
        "",
        "ℹ️ Uchrashuvga yozilish uchun *Navbat olish* tugmasidan foydalaning.",
    ]

    await m.answer("\n".join(lines), parse_mode="Markdown")