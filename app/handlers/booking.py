# app/handlers/booking.py
import asyncio
import logging
from datetime import datetime, date, time, timedelta
from typing import Dict, List, Optional

from aiogram import F, Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command  # <-- for /all

from app.config import UZ_TZ, sb
from app.constants import BTN_BOOK, BTN_MY, BTN_SPECIAL_SERVICE  # Uzbek button labels
from app.db import (
    is_registered_sync, fetch_services_sync, get_service_sync,
    fetch_bookings_for_day_sync, create_booking_sync, get_user_record_sync
)
from app.keyboards import main_menu, days_kb, times_kb
from app.states import BookingFlow
from app.utils import (
    list_available_times, MIN_AHEAD, WORK_WINDOWS,
    build_timeline, is_candidate_ok
)

logger = logging.getLogger(__name__)
router = Router()

# === Adminlar ro'yxati (ZIP yuborish oluvchilari va /all komandasi uchun) ===
ADMIN_IDS = [
    5647574607,
    7560917268,
    757007519,
    6309016726,
    12302511,
    1069519989,
    840667441
]

# --- SPECIAL SERVICE (skip time flow → require zip upload) ---
SPECIAL_SERVICE_ID = "84db3cdc-62c4-407e-a951-415bfc416e81"
SPECIAL_SERVICE_NAME = "Moliyaviy kafillik xati"

def cancel_kb(booking_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Navbatni bekor qilish", callback_data=f"book:cancel:{booking_id}")]
        ]
    )

# --------- tiny async wrappers over sync DB helpers ----------
async def is_registered(uid: int) -> bool:
    return await asyncio.get_running_loop().run_in_executor(None, is_registered_sync, uid)

async def fetch_services() -> List[Dict]:
    return await asyncio.get_running_loop().run_in_executor(None, fetch_services_sync)

async def get_service(svc_id: str):
    return await asyncio.get_running_loop().run_in_executor(None, get_service_sync, svc_id)

async def fetch_bookings_for_day(day_start: datetime, day_end: datetime):
    return await asyncio.get_running_loop().run_in_executor(None, fetch_bookings_for_day_sync, day_start, day_end)

async def create_booking(user_id: str, service_id: str, start_at: datetime, end_at: datetime):
    return await asyncio.get_running_loop().run_in_executor(None, create_booking_sync, user_id, service_id, start_at, end_at)

async def get_user_record(telegram_user_id: int):
    return await asyncio.get_running_loop().run_in_executor(None, get_user_record_sync, telegram_user_id)

# --------- active booking gate (faqat bitta faol navbat) ----------
async def has_active_booking(user_id: str) -> Optional[Dict]:
    now_tz = datetime.now(UZ_TZ).isoformat()
    res = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: (
            sb.table("booking")
              .select("id,service_id,start_at,end_at,status")
              .eq("user_id", user_id)
              .eq("status", "booked")
              .gt("end_at", now_tz)  # hali tugamagan navbat
              .limit(1)
              .execute()
        )
    )
    if res.data:
        return res.data[0]
    return None

# --------- taqiqlangan sanalar (himoya) ----------
def is_forbidden_date(d: date) -> bool:
    # Dam olish kunlari: Shanba(5), Yakshanba(6)
    if d.weekday() in (5, 6):
        return True
    # 1-sentabr (har yil)
    if d.month == 9 and d.day == 1:
        return True
    return False

# --------- "message is not modified" ni oldini olish ----------
async def _safe_edit_day_screen(message, text_md: str, kb):
    try:
        if message.text == text_md:
            try:
                await message.edit_reply_markup(reply_markup=kb)
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e):
                    raise
        else:
            await message.edit_text(text_md, parse_mode="Markdown", reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise

# =========================
# ===== USER HANDLERS =====
# =========================

@router.message(F.text == BTN_SPECIAL_SERVICE)
async def special_service_entry(m: Message, state: FSMContext):
    # jump straight into the special ZIP flow
    await state.set_state(BookingFlow.uploading_zip)
    await state.update_data(svc_id=SPECIAL_SERVICE_ID)
    await m.answer(
        "Jamg'armaga kelish shart emas\n\n"
        "Xizmat: *Moliyaviy kafillik xati*\n\n"
        "Iltimos, quyidagi hujjatlarni *bitta ZIP faylga* joylab yuboring:\n"
        "• Fuqarolik pasporti nusxasi\n"
        "• Xalqaro (qizil) pasport nusxasi\n"
        "• Qabul xati (Acceptance letter)\n\n"
        "ZIP faylni yuborganingizdan so‘ng, biz faylni qabul qilib, "
        "u bilan bog‘liq jarayonni boshlaymiz. Tayyor bo‘lgach, natijani elektron pochtangizga yuboramiz.",
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )

@router.message(F.text == BTN_BOOK)
async def book_appointment(m: Message, state: FSMContext):
    if not await is_registered(m.from_user.id):
        await m.answer("Iltimos, avval ro‘yxatdan o‘ting. Boshlash uchun /start yuboring.")
        return

    user = await get_user_record(m.from_user.id)
    if not user:
        await m.answer("Iltimos, /start orqali ro‘yxatdan o‘ting.")
        return

    services = await fetch_services()
    if not services:
        await m.answer("Hozircha xizmatlar sozlanmagan.")
        return

    # Gate: faqat bitta faol navbat — LEKIN maxsus xizmat (onlayn) doim ruxsat
    active = await has_active_booking(user["id"])
    if active:
        # Faol navbat detali
        svc_active = await get_service(active["service_id"])
        s = datetime.fromisoformat(active["start_at"]).astimezone(UZ_TZ).strftime("%Y-%m-%d %H:%M")
        e = datetime.fromisoformat(active["end_at"]).astimezone(UZ_TZ).strftime("%H:%M")
        nm = svc_active["name"] if svc_active else "Xizmat"

        # 1) Faol navbat haqida xabar + bekor qilish tugmasi
        await m.answer(
            "Sizda allaqachon faol navbat bor. Agar kerak bo‘lsa, uni bekor qilishingiz mumkin.\n\n"
            f"• {s}–{e} — {nm} ({active['status']})",
            reply_markup=cancel_kb(active["id"])
        )

        # 2) Shunga qaramay, MAXSUS onlayn xizmatni taklif qilamiz
        special = next((x for x in services if x.get("id") == SPECIAL_SERVICE_ID or (x.get("name") or "").strip() == SPECIAL_SERVICE_NAME), None)
        if special:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=f"{special['name']} (onlayn) — ZIP yuborish", callback_data=f"book:svc:{special['id']}")]
                ]
            )
            await state.set_state(BookingFlow.picking_service)
            await m.answer(
                "Faol navbatingiz bo‘lsa ham, quyidagi onlayn xizmatdan foydalanishingiz mumkin:",
                reply_markup=kb,
                disable_web_page_preview=True,
            )
        else:
            await m.answer("Maxsus onlayn xizmat hozircha mavjud emas.")
        return

    # Hech qanday faol navbat yo‘q — faqat oddiy xizmatlarni ko‘rsatamiz (SPECIAL chiqarib tashlanadi)
    visible_services = [
        s for s in services
        if not (s.get("id") == SPECIAL_SERVICE_ID or (s.get("name") or "").strip() == SPECIAL_SERVICE_NAME)
    ]

    if not visible_services:
        await m.answer("Hozircha navbat bilan band qilinadigan xizmatlar yo‘q.")
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"{s['name']} (~{s.get('duration_min','?')} daqiqa)", callback_data=f"book:svc:{s['id']}")]
            for s in visible_services
        ]
    )
    await state.set_state(BookingFlow.picking_service)
    await m.answer("Xizmatni tanlang:", reply_markup=kb, disable_web_page_preview=True)

@router.callback_query(F.data.startswith("book:cancel:"))
async def cancel_active_booking(cq: CallbackQuery):
    booking_id = cq.data.split(":", 2)[2]
    user = await get_user_record(cq.from_user.id)
    if not user:
        await cq.answer("Avval /start orqali ro‘yxatdan o‘ting.", show_alert=True)
        return

    now_iso = datetime.now(UZ_TZ).isoformat()
    try:
        upd = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: (
                sb.table("booking")
                  .update({"status": "cancelled"})
                  .eq("id", booking_id)
                  .eq("user_id", user["id"])
                  .eq("status", "booked")
                  .gt("end_at", now_iso)
                  .execute()
            )
        )
        if not upd.data:
            await cq.answer("Bekor qilishning imkoni bo‘lmadi.", show_alert=True)
            return

        await cq.message.edit_text("✅ Faol navbatingiz bekor qilindi.")
        await cq.answer("Bekor qilindi.")
    except Exception as e:
        logger.exception("Bekor qilishda xatolik: %s", e)
        await cq.answer("Xatolik yuz berdi.", show_alert=True)

@router.callback_query(F.data.startswith("book:svc:"))
async def pick_service(cq: CallbackQuery, state: FSMContext):
    svc_id = cq.data.split(":", 2)[2]
    svc = await get_service(svc_id)
    if not svc:
        await cq.answer("Xizmat topilmadi.", show_alert=True)
        return

    # Faol navbat bo‘lsa, faqat MAXSUS onlayn xizmatga ruxsat beramiz
    user = await get_user_record(cq.from_user.id)
    if user:
        active = await has_active_booking(user["id"])
    else:
        active = None

    if active and not (svc_id == SPECIAL_SERVICE_ID or (svc.get("name") or "").strip() == SPECIAL_SERVICE_NAME):
        await cq.answer("Sizda faol navbat bor. Hozir faqat ‘Moliyaviy kafillik xati’ (onlayn) xizmatidan foydalanishingiz mumkin.", show_alert=True)
        return

    # ---- SPECIAL BRANCH: Moliyaviy kafillik xati → ZIP so'raymiz, vaqt tanlash emas
    if svc_id == SPECIAL_SERVICE_ID or (svc.get("name") or "").strip() == SPECIAL_SERVICE_NAME:
        await state.update_data(svc_id=svc_id)
        await state.set_state(BookingFlow.uploading_zip)
        await cq.message.edit_text(
            "Jamg'armaga kelish shart emas\n\n"
            "Xizmat: *Moliyaviy kafillik xati*\n\n"
            "Iltimos, quyidagi hujjatlarni *bitta ZIP faylga* joylab yuboring:\n"
            "• Fuqarolik pasporti nusxasi\n"
            "• Xalqaro (qizil) pasport nusxasi\n"
            "• Qabul xati (Acceptance letter)\n\n"
            "ZIP faylni yuborganingizdan so‘ng, biz faylni qabul qilib, "
            "u bilan bog‘liq jarayonni boshlaymiz. Tayyor bo‘lgach, natijani elektron pochtangizga yuboramiz.",
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        await cq.answer()
        return

    # ---- STANDARD FLOW
    await state.update_data(svc_id=svc_id)
    await state.set_state(BookingFlow.picking_day)
    await cq.message.edit_text(
        f"Xizmat: *{svc['name']}* (~{svc['duration_min']} daqiqa)\nKun tanlang:",
        parse_mode="Markdown",
        reply_markup=days_kb(10),  # dam olish kunlari va 1-sentabr yashirilgan
    )
    await cq.answer()

@router.message(BookingFlow.uploading_zip, F.document)
async def receive_special_zip(m: Message, state: FSMContext):
    """
    ZIP ni qabul qilamiz, adminlarga yuboramiz, foydalanuvchiga tasdiq beramiz.
    """
    doc = m.document
    file_name = (doc.file_name or "").lower()
    if not (file_name.endswith(".zip") or (doc.mime_type or "").endswith("/zip")):
        await m.answer("Iltimos, hujjatlarni *ZIP* formatida bitta fayl qilib yuboring.", parse_mode="Markdown")
        return

    # Foydalanuvchi rekordi (email uchun)
    user = await get_user_record(m.from_user.id)
    user_email = (user or {}).get("email") or "—"
    user_name = (user or {}).get("full_name") or m.from_user.full_name

    caption = (
        f"Yangi ZIP (Moliyaviy kafillik xati)\n"
        f"Foydalanuvchi: {user_name}\n"
        f"Email: {user_email}\n"
        f"Telegram ID: {m.from_user.id}"
    )

    # Har bir admin user_id ga yuboramiz
    for admin_id in ADMIN_IDS:
        try:
            await m.bot.send_document(
                chat_id=admin_id,
                document=doc.file_id,
                caption=caption
            )
        except Exception as e:
            logger.warning("Admin %s ga yuborib bo'lmadi: %s", admin_id, e)

    await state.clear()
    await m.answer(
        "✅ ZIP faylingiz qabul qilindi.\n\n"
        "Hujjatlaringiz ko‘rib chiqiladi. Jarayon yakunlangach, natija elektron pochtangizga yuboriladi.\n"
        "Savollar bo‘lsa, shu yerda yozib qoldiring.",
        disable_web_page_preview=True,
    )

@router.message(BookingFlow.uploading_zip)
async def require_zip_only(m: Message):
    await m.answer("Iltimos, faqat *ZIP* fayl yuboring (hujjatlar bitta arxivda).", parse_mode="Markdown")

@router.callback_query(F.data.startswith("book:day:"))
async def pick_day(cq: CallbackQuery, state: FSMContext):
    day_iso = cq.data.split(":", 2)[2]
    d = date.fromisoformat(day_iso)

    # Himoya: taqiqlangan sanalarni rad etish
    if is_forbidden_date(d):
        await cq.answer("Bu sanada navbat yo‘q. Iltimos, boshqa kun tanlang.", show_alert=True)
        return

    data = await state.get_data()
    svc = await get_service(data.get("svc_id", ""))
    if not svc:
        await cq.answer("Xizmat topilmadi.", show_alert=True)
        return

    day_start = datetime.combine(d, time(0, 0), UZ_TZ)
    day_end = day_start + timedelta(days=1)
    existing = await fetch_bookings_for_day(day_start, day_end)

    valid_times = list_available_times(d, int(svc["duration_min"]), existing)
    kb = times_kb(d, valid_times)

    new_text = (
        f"Xizmat: *{svc['name']}* (~{svc['duration_min']} daqiqa)\n"
        f"Sana: {d.strftime('%A, %d %b')}\n"
        f"Boshlanish vaqtini tanlang:"
    )
    await _safe_edit_day_screen(cq.message, new_text, kb)
    await cq.answer()

@router.callback_query(F.data.startswith("book:back:day:"))
async def back_to_day(cq: CallbackQuery, state: FSMContext):
    day_iso = cq.data.split(":", 3)[3]
    d = date.fromisoformat(day_iso)

    if is_forbidden_date(d):
        await cq.answer("Bu sanada navbat yo‘q. Iltimos, boshqa kun tanlang.", show_alert=True)
        return

    data = await state.get_data()
    svc = await get_service(data.get("svc_id", ""))
    if not svc:
        await cq.answer("Xizmat topilmadi.", show_alert=True)
        return

    day_start = datetime.combine(d, time(0, 0), UZ_TZ)
    day_end = day_start + timedelta(days=1)
    existing = await fetch_bookings_for_day(day_start, day_end)
    valid_times = list_available_times(d, int(svc["duration_min"]), existing)
    kb = times_kb(d, valid_times)

    new_text = (
        f"Xizmat: *{svc['name']}* (~{svc['duration_min']} daqiqa)\n"
        f"Sana: {d.strftime('%A, %d %b')}\n"
        f"Boshlanish vaqtini tanlang:"
    )
    await _safe_edit_day_screen(cq.message, new_text, kb)
    await cq.answer()

@router.callback_query(F.data == "book:back:menu")
async def back_to_menu(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    await cq.message.edit_text("Menyu sahifasiga qaytdingiz. Quyidagi tugmalardan foydalaning.")
    await cq.message.answer("Asosiy menyu:", reply_markup=main_menu())
    await cq.answer()

@router.callback_query(F.data.startswith("book:time:"))
async def pick_time(cq: CallbackQuery, state: FSMContext):
    try:
        epoch = int(cq.data.split(":", 2)[2])
    except Exception:
        await cq.answer("Vaqt noto‘g‘ri.", show_alert=True)
        return

    data = await state.get_data()
    svc_id = data.get("svc_id")
    if not svc_id:
        await cq.answer("Sessiya muddati tugadi. Avval xizmatni qayta tanlang.", show_alert=True)
        return

    svc = await get_service(svc_id)
    if not svc:
        await cq.answer("Xizmat topilmadi.", show_alert=True)
        return

    start_local = datetime.fromtimestamp(epoch, UZ_TZ)

    # Taqiqlangan sanani yakuniy tekshirish
    if is_forbidden_date(start_local.date()):
        await cq.answer("Bu sanada navbat yo‘q. Iltimos, boshqa kun tanlang.", show_alert=True)
        return

    # Kamida 2 soat oldin
    if start_local < datetime.now(UZ_TZ) + MIN_AHEAD:
        await cq.answer("Juda yaqin vaqt. Biroz keyinroq vaqtni tanlang.", show_alert=True)
        return

    dur = timedelta(minutes=int(svc["duration_min"]))
    end_local = start_local + dur

    in_morning = WORK_WINDOWS[0][0] <= start_local.time() < WORK_WINDOWS[0][1]
    in_afternoon = WORK_WINDOWS[1][0] <= start_local.time() < WORK_WINDOWS[1][1]
    if in_morning and end_local.time() > WORK_WINDOWS[0][1]:
        await cq.answer("Tushlikdan oldin yetarli vaqt yo‘q. Ilgariroq vaqtni tanlang.", show_alert=True)
        return
    if in_afternoon and end_local.time() > WORK_WINDOWS[1][1]:
        await cq.answer("Yopilishdan oldin yetarli vaqt yo‘q. Ilgariroq vaqtni tanlang.", show_alert=True)
        return
    if not (in_morning or in_afternoon):
        await cq.answer("Ish vaqtidan tashqarida.", show_alert=True)
        return

    d = start_local.date()
    day_start = datetime.combine(d, time(0, 0), UZ_TZ)
    day_end = day_start + timedelta(days=1)
    existing = await fetch_bookings_for_day(day_start, day_end)

    counts = build_timeline(existing, d)
    if not is_candidate_ok(start_local, dur, counts):
        await cq.answer("Bu vaqt endi band bo‘ldi. Boshqa vaqtni tanlang.", show_alert=True)
        valid_times = list_available_times(d, int(svc["duration_min"]), existing)
        kb = times_kb(d, valid_times)
        new_text = (
            f"Xizmat: *{svc['name']}* (~{svc['duration_min']} daqiqa)\n"
            f"Sana: {d.strftime('%A, %d %b')}\n"
            f"Boshlanish vaqtini tanlang:"
        )
        await _safe_edit_day_screen(cq.message, new_text, kb)
        return

    user = await get_user_record(cq.from_user.id)
    if not user:
        await cq.answer("Iltimos, /start orqali ro‘yxatdan o‘ting.", show_alert=True)
        return

    # Yakuniy darajada: faqat bitta faol navbat (bu branch faqat oddiy xizmatlar uchun)
    active = await has_active_booking(user["id"])
    if active:
        svc_active = await get_service(active["service_id"])
        s = datetime.fromisoformat(active["start_at"]).astimezone(UZ_TZ).strftime("%Y-%m-%d %H:%M")
        e = datetime.fromisoformat(active["end_at"]).astimezone(UZ_TZ).strftime("%H:%M")
        nm = svc_active["name"] if svc_active else "Xizmat"
        await cq.answer(
            f"Sizda faol navbat bor ({s}–{e} — {nm}). Uni yakunlang.",
            show_alert=True,
        )
        return

    # Ixtiyoriy: bir kunda bitta xizmatni faqat bir marta
    same_day_dup = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: (
            sb.table("booking")
              .select("id")
              .eq("user_id", user["id"])
              .eq("service_id", svc_id)
              .gte("start_at", day_start.isoformat())
              .lt("start_at", day_end.isoformat())
              .limit(1)
              .execute()
        )
    )
    if same_day_dup.data:
        await cq.answer("Bu xizmatni shu kunda allaqachon band qilgansiz.", show_alert=True)
        return

    try:
        _ = await create_booking(user_id=user["id"], service_id=svc_id, start_at=start_local, end_at=end_local)
    except Exception as e:
        logger.exception("Navbat yaratishda xatolik: %s", e)
        await cq.answer("Navbat yaratib bo‘lmadi. Boshqa vaqtni tanlab ko‘ring.", show_alert=True)
        return

    local_s = start_local.strftime("%Y-%m-%d %H:%M")
    local_e = end_local.strftime("%H:%M")
    await cq.message.edit_text(
        "✅ Navbat tasdiqlandi!\n\n"
        f"Xizmat: *{svc['name']}*\n"
        f"Vaqt: {local_s}–{local_e} (Asia/Tashkent)\n",
        parse_mode="Markdown",
    )
    # Manzil/ko‘rsatma
    await cq.message.answer(
        "📍 Manzil: https://maps.app.goo.gl/phhE5byYBabpnfx97\n"
        "⏰ Iltimos, *30 daqiqa oldin* keling.\n"
        "🏢 Xona: *302*.\n\n"
        "⚠️ Belgilangan vaqtda kelmasangiz, bu keyingi navbatlaringiz va KPI ko‘rsatkichingizga salbiy ta’sir qiladi.",
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )
    await cq.answer()

@router.message(F.text == BTN_MY)
async def my_appointments(m: Message):
    if not await is_registered(m.from_user.id):
        await m.answer("Iltimos, avval ro‘yxatdan o‘ting. Boshlash uchun /start yuboring.")
        return

    user = await get_user_record(m.from_user.id)
    if not user:
        await m.answer("Iltimos, /start orqali ro‘yxatdan o‘ting.")
        return

    try:
        res = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: (
                sb.table("booking")
                  .select("id,service_id,start_at,end_at,status")
                  .eq("user_id", user["id"])
                  .order("start_at")
                  .execute()
            )
        )
        rows = res.data or []

        svc_ids = list({r["service_id"] for r in rows})
        svc_map: Dict[str, str] = {}
        if svc_ids:
            svc_rows = await asyncio.get_running_loop().run_in_executor(
                None, lambda: sb.table("service").select("id,name").in_("id", svc_ids).execute()
            )
            svc_map = {r["id"]: r["name"] for r in (svc_rows.data or [])}

        now_tz = datetime.now(UZ_TZ)
        lines = []
        for r in rows:
            s = datetime.fromisoformat(r["start_at"]).astimezone(UZ_TZ)
            e = datetime.fromisoformat(r["end_at"]).astimezone(UZ_TZ)
            if e < now_tz:
                continue
            nm = svc_map.get(r["service_id"], "Xizmat")
            lines.append(f"• {s:%Y-%m-%d %H:%M}–{e:%H:%M} — {nm} ({r['status']})")

        await m.answer("\n".join(lines) if lines else "Yaqinlashib kelayotgan navbatlar yo‘q.")
    except Exception as e:
        logger.exception("Navbatlarni yuklashda xatolik: %s", e)
        await m.answer("Navbatlar topilmadi yoki yuklab bo‘lmadi.")

# ===========================
# ===== ADMIN: /all flow ====
# ===========================

def admin_days_kb(n: int = 10) -> InlineKeyboardMarkup:
    """Adminlar uchun kun tanlash (taqiqlangan sanalar yashirilgan)."""
    today = datetime.now(UZ_TZ).date()
    rows = []
    count = 0
    i = 0
    while count < n:
        d = today + timedelta(days=i)
        i += 1
        if is_forbidden_date(d):
            continue
        rows.append([InlineKeyboardButton(text=d.strftime("%a %d %b"), callback_data=f"all:day:{d.isoformat()}")])
        count += 1
    return InlineKeyboardMarkup(inline_keyboard=rows)

@router.message(Command("all"))
async def admin_all(m: Message):
    """Adminlar uchun: kun tanlash oynasi."""
    if m.from_user.id not in ADMIN_IDS:
        await m.answer("Ushbu buyruq faqat administratorlar uchun.")
        return
    await m.answer("Kun tanlang (admin):", reply_markup=admin_days_kb(14))

@router.callback_query(F.data.startswith("all:day:"))
async def admin_all_day(cq: CallbackQuery):
    """Admin: tanlangan kun bo‘yicha barcha navbatlar ro‘yxati."""
    if cq.from_user.id not in ADMIN_IDS:
        await cq.answer("Ruxsat yo‘q.", show_alert=True)
        return

    day_iso = cq.data.split(":", 2)[2]
    d = date.fromisoformat(day_iso)

    # Shu kun oralig‘i
    day_start = datetime.combine(d, time(0, 0), UZ_TZ)
    day_end = day_start + timedelta(days=1)

    try:
        # Kunning barcha bookinglarini olish
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
            await cq.message.edit_text(f"{d.strftime('%A, %d %b %Y')} — bu kunda navbat yo‘q.")
            await cq.answer()
            return

        # Service va User mapping
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

        lines: List[str] = [f"*{d.strftime('%A, %d %b %Y')}* — kun bo‘yicha barcha navbatlar:"]
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