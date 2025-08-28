# app/handlers/my_bookings.py
import asyncio
import logging
from collections import defaultdict
from datetime import datetime
from typing import Dict, List
from html import escape as html_escape

from aiogram import F, Router
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from app.config import UZ_TZ, sb
from app.db import get_user_record_sync
from app.keyboards import main_menu
from app.constants import BTN_MY

logger = logging.getLogger(__name__)
router = Router()

# ----------------- DB helpers (async wrappers) -----------------
async def get_user_record(telegram_user_id: int):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_user_record_sync, telegram_user_id)

async def fetch_user_bookings(user_id: str) -> List[Dict]:
    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(
        None,
        lambda: (
            sb.table("booking")
              .select("id,service_id,start_at,end_at,status")
              .eq("user_id", user_id)
              .order("start_at")
              .execute()
        )
    )
    return res.data or []

async def fetch_services_map(ids: List[str]) -> Dict[str, str]:
    if not ids:
        return {}
    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(
        None,
        lambda: (
            sb.table("service")
              .select("id,name")
              .in_("id", ids)
              .execute()
        )
    )
    return {r["id"]: r["name"] for r in (res.data or [])}

# ----------------- Cancel keyboard -----------------
def cancel_kb(booking_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Navbatni bekor qilish", callback_data=f"my:cancel:{booking_id}")]
        ]
    )

# ----------------- Main handler -----------------
# Handle both Uzbek and old English labels to avoid routing collisions
@router.message(F.text.in_([BTN_MY, "🗓️ My appointments"]))
async def my_appointments(m: Message):
    user = await get_user_record(m.from_user.id)
    if not user:
        await m.answer("Iltimos, avval ro‘yxatdan o‘ting. Boshlash uchun /start yuboring.")
        return

    try:
        rows = await fetch_user_bookings(user["id"])
        now_tz = datetime.now(UZ_TZ)

        upcoming = [
            r for r in rows
            if datetime.fromisoformat(r["end_at"]).astimezone(UZ_TZ) >= now_tz
        ]
        logger.info("my_bookings: upcoming=%d for user_id=%s", len(upcoming), user["id"])

        if not upcoming:
            await m.answer("Yaqinlashib kelayotgan navbatlar yo‘q.", reply_markup=main_menu())
            return

        svc_map = await fetch_services_map(list({r["service_id"] for r in upcoming}))

        # Build grouped text and collect cancellable items
        by_day: Dict[str, List[str]] = defaultdict(list)
        cancellable: List[Dict] = []
        for r in upcoming:
            s = datetime.fromisoformat(r["start_at"]).astimezone(UZ_TZ)
            e = datetime.fromisoformat(r["end_at"]).astimezone(UZ_TZ)
            nm = svc_map.get(r["service_id"], "Xizmat")
            status_raw = r.get("status") or ""
            status_str = status_raw.strip()
            by_day[s.strftime("%Y-%m-%d")].append(
                f"• {s:%H:%M}–{e:%H:%M} — {nm} ({status_str})"
            )
            if status_str.lower() == "booked" and e >= now_tz:
                cancellable.append(r)

        logger.info("my_bookings: cancellable=%d for user_id=%s", len(cancellable), user["id"])

        # Main list message (HTML-escaped)
        lines: List[str] = []
        for day in sorted(by_day.keys()):
            human = datetime.strptime(day, "%Y-%m-%d").strftime("%A, %d %b %Y")
            lines.append(f"<b>{html_escape(human)}</b>")
            for item in by_day[day]:
                lines.append(html_escape(item))
        await m.answer("\n".join(lines).strip(), parse_mode="HTML", reply_markup=main_menu())

        # Send a separate message with an inline cancel button for each cancellable booking
        for r in cancellable:
            s = datetime.fromisoformat(r["start_at"]).astimezone(UZ_TZ)
            e = datetime.fromisoformat(r["end_at"]).astimezone(UZ_TZ)
            nm = svc_map.get(r["service_id"], "Xizmat")
            txt = (
                f"🔔 <b>Aktiv navbat:</b>\n"
                f"• {s:%Y-%m-%d %H:%M}–{e:%H:%M} — {html_escape(nm)} (booked)\n\n"
                f"<i>Pastdagi tugma orqali bekor qilishingiz mumkin.</i>"
            )
            await m.answer(txt, parse_mode="HTML", reply_markup=cancel_kb(r["id"]))

    except Exception as e:
        logger.exception("Navbatlarni yuklashda xatolik: %s", e)
        await m.answer("Hozircha navbatlarni yuklab bo‘lmadi.", reply_markup=main_menu())

# ----------------- Cancel callback -----------------
@router.callback_query(F.data.startswith("my:cancel:"))
async def cancel_booking(cq: CallbackQuery):
    booking_id = cq.data.split(":", 2)[2]

    user = await get_user_record(cq.from_user.id)
    if not user:
        await cq.answer("Avval /start orqali ro‘yxatdan o‘ting.", show_alert=True)
        return

    try:
        # Load by id + user (no time filters in SQL)
        sel = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: (
                sb.table("booking")
                  .select("id,service_id,start_at,end_at,status")
                  .eq("id", booking_id)
                  .eq("user_id", user["id"])
                  .limit(1)
                  .execute()
            )
        )
        row = (sel.data or [None])[0]
        if not row:
            await cq.answer("Bekor qilish uchun mos navbat topilmadi.", show_alert=True)
            return

        end_dt = datetime.fromisoformat(row["end_at"]).astimezone(UZ_TZ)
        status_str = (row.get("status") or "").strip().lower()
        if status_str != "booked":
            await cq.answer("Bu navbatni bekor qilib bo‘lmaydi (holati ‘booked’ emas).", show_alert=True)
            return
        if end_dt < datetime.now(UZ_TZ):
            await cq.answer("Bu navbat allaqachon tugagan.", show_alert=True)
            return

        # Atomic update: only if still "booked"
        upd = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: (
                sb.table("booking")
                  .update({"status": "cancelled"})
                  .eq("id", booking_id)
                  .eq("user_id", user["id"])
                  .eq("status", "booked")
                  .execute()
            )
        )
        if not upd.data:
            await cq.answer("Bekor qilishning imkoni bo‘lmadi (ehtimol allaqachon o‘zgargan).", show_alert=True)
            return

        await cq.message.edit_text("✅ Navbatingiz bekor qilindi.")
        await cq.answer("Bekor qilindi.")

    except Exception as e:
        logger.exception("Bekor qilishda xatolik: %s", e)
        await cq.answer("Bekor qilishda xatolik yuz berdi.", show_alert=True)