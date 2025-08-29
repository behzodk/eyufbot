# app/handlers/admin.py
import asyncio
import logging
from datetime import datetime, date, time, timedelta
from typing import Dict, List

from aiogram import F, Router
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from app.config import UZ_TZ, sb
from app.keyboards import admin_days_kb, admin_main_menu
from app.constants import BTN_ALL_APPTS, BTN_ALL_STUDENTS, BTN_NOTIFY_ALL

logger = logging.getLogger(__name__)
router = Router()

# Must match the list you already use in booking.py
ADMIN_IDS = [
    5647574607,
    7560917268,
    757007519,
    6309016726,
    12302511,
    1069519989,
    840667441,
    2094323731
]

class AdminBroadcast(StatesGroup):
    waiting_text = State()

def _is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

# ========== Admin Home ==========
@router.message(F.text == BTN_ALL_APPTS)
async def admin_pick_day(m: Message):
    if not _is_admin(m.from_user.id):
        await m.answer("Ushbu bo‘lim faqat administratorlar uchun.")
        return
    await m.answer("Kun tanlang (admin):", reply_markup=admin_days_kb(14))

@router.callback_query(F.data.startswith("all:day:"))
async def admin_all_day(cq: CallbackQuery):
    if not _is_admin(cq.from_user.id):
        await cq.answer("Ruxsat yo‘q.", show_alert=True)
        return

    day_iso = cq.data.split(":", 2)[2]
    d = date.fromisoformat(day_iso)

    day_start = datetime.combine(d, time(0, 0), UZ_TZ)
    day_end = day_start + timedelta(days=1)

    try:
        res = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: (
                sb.table("booking")
                  .select("id,user_id,service_id,start_at,end_at,status")
                  .gte("start_at", day_start.isoformat())
                  .lt("start_at", day_end.isoformat())
                  .order("start_at")
                  .execute()
            )
        )
        rows: List[Dict] = res.data or []

        if not rows:
            await cq.message.edit_text(f"{d:%A, %d %b %Y} — bu kunda navbat yo‘q.")
            await cq.answer()
            return

        svc_ids = list({r["service_id"] for r in rows})
        user_ids = list({r["user_id"] for r in rows})

        svc_map: Dict[str, str] = {}
        if svc_ids:
            svc_rows = await asyncio.get_running_loop().run_in_executor(
                None, lambda: sb.table("service").select("id,name").in_("id", svc_ids).execute()
            )
            svc_map = {r["id"]: r["name"] for r in (svc_rows.data or [])}

        user_map: Dict[str, str] = {}
        if user_ids:
            user_rows = await asyncio.get_running_loop().run_in_executor(
                None, lambda: sb.table("app_user").select("id,full_name,email").in_("id", user_ids).execute()
            )
            user_map = {r["id"]: (r.get("full_name") or "Foydalanuvchi") for r in (user_rows.data or [])}

        lines: List[str] = [f"*{d:%A, %d %b %Y}* — kun bo‘yicha barcha navbatlar:"]
        for r in rows:
            s = datetime.fromisoformat(r["start_at"]).astimezone(UZ_TZ)
            e = datetime.fromisoformat(r["end_at"]).astimezone(UZ_TZ)
            nm = svc_map.get(r["service_id"], "Xizmat")
            uname = user_map.get(r["user_id"], "Foydalanuvchi")
            lines.append(f"• {s:%H:%M}–{e:%H:%M} — {nm} — {uname} ({r['status']})")

        await cq.message.edit_text("\n".join(lines), parse_mode="Markdown")
        await cq.answer()
    except Exception as e:
        logger.exception("Admin /all kun yuklashda xatolik: %s", e)
        await cq.answer("Xatolik yuz berdi.", show_alert=True)

# ===== All students =====
@router.message(F.text == BTN_ALL_STUDENTS)
async def admin_all_students(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        await m.answer("Ushbu bo‘lim faqat administratorlar uchun.")
        return
    try:
        res = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: sb.table("app_user")
                      .select("id,full_name,email,telegram_user_id,created_at")
                      .order("created_at")
                      .execute()
        )
        rows: List[Dict] = res.data or []
        if not rows:
            await m.answer("Talabalar bazasi bo‘sh.")
            return

        # Build all lines first
        header = f"Jami talabalar: {len(rows)}\n"
        lines = []
        for idx, r in enumerate(rows, start=1):
            fn = r.get("full_name") or "—"
            em = r.get("email") or "—"
            tid = r.get("telegram_user_id") or "—"
            lines.append(f"{idx}. {fn} — {em} — TG:{tid}")

        # Telegram limit safety: chunk by characters (< 3800) and by count (<= 100 per message)
        MAX_CHARS = 3800
        MAX_ROWS = 100

        chunk, chunk_chars, chunk_rows, page = [], 0, 0, 1
        # send the count header once on the first page
        prefix = header

        async def flush():
            nonlocal chunk, chunk_chars, chunk_rows, page, prefix
            if not chunk:
                return
            text = prefix + "\n".join(chunk)
            await m.answer(text)
            # after first page, remove header to save space
            prefix = ""
            chunk.clear()
            chunk_chars = 0
            chunk_rows = 0
            page += 1

        for line in lines:
            # +1 for newline if added
            addition = (1 if chunk else 0) + len(line)
            if chunk_rows >= MAX_ROWS or (len(prefix) + chunk_chars + addition) > MAX_CHARS:
                await flush()
            chunk.append(line)
            chunk_chars += addition
            chunk_rows += 1

        await flush()

    except Exception as e:
        logger.exception("Admin: talabalar ro'yxati xatolik: %s", e)
        await m.answer("Talabalarni yuborish davomida xatolik yuz berdi (uzun ro‘yxat).")

# ===== Notify all =====
@router.message(F.text == BTN_NOTIFY_ALL)
async def admin_notify_all(m: Message, state: FSMContext):
    if not _is_admin(m.from_user.id):
        await m.answer("Ushbu bo‘lim faqat administratorlar uchun.")
        return
    await state.set_state(AdminBroadcast.waiting_text)
    await m.answer("Yuboriladigan xabar matnini kiriting (hammaga jo‘natiladi).")

@router.message(AdminBroadcast.waiting_text)
async def admin_do_broadcast(m: Message, state: FSMContext):
    if not _is_admin(m.from_user.id):
        await m.answer("Ruxsat yo‘q.")
        return

    text = m.text or ""
    await state.clear()

    # Get recipients from app_user (only those with telegram_user_id)
    try:
        res = await asyncio.get_running_loop().run_in_executor(
            None, lambda: sb.table("app_user").select("telegram_user_id").not_.is_("telegram_user_id", "null").execute()
        )
        ids = [row["telegram_user_id"] for row in (res.data or []) if isinstance(row.get("telegram_user_id"), int)]
        # optionally exclude admins
        ids = [i for i in ids if i not in ADMIN_IDS]

        sent, failed = 0, 0
        for uid in ids:
            try:
                await m.bot.send_message(chat_id=uid, text=text)
                sent += 1
            except (TelegramForbiddenError, TelegramBadRequest) as e:
                failed += 1
            except Exception as e:
                failed += 1

        await m.answer(f"Yuborildi: {sent} ta ✅\nMuvaffaqiyatsiz: {failed} ta ❌", reply_markup=admin_main_menu())
    except Exception as e:
        logger.exception("Broadcast xatolik: %s", e)
        await m.answer("Jo‘natish vaqtida xatolik yuz berdi.", reply_markup=admin_main_menu())