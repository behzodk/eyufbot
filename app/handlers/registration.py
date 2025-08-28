import asyncio
import logging
from aiogram import F, Router
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

from app.config import AWARD_CSV, SUPPORT_CONTACT
from app.db import is_registered_sync, is_name_taken_sync, register_user_sync
from app.keyboards import main_menu
from app.states import Reg
from app.utils import EMAIL_RE, normalize_phone
from app.whitelist import load_award_map, best_match_90, suggestion_names
from app.constants import BTN_BOOK, BTN_MY, BTN_SERVICES, BTN_SUPPORT

logger = logging.getLogger(__name__)
router = Router()

AWARD_MAP = load_award_map(AWARD_CSV)
AWARD_KEYS = list(AWARD_MAP.keys())

async def is_registered(uid: int) -> bool:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, is_registered_sync, uid)

async def is_name_taken(canon: str) -> bool:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, is_name_taken_sync, canon)

async def register_user(uid: int, full_name: str, phone: str, email: str, country: str, university: str):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, register_user_sync, uid, full_name, phone, email, country, university)

@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    if await is_registered(m.from_user.id):
        await state.clear()
        await m.answer("Siz allaqachon ro‘yxatdan o‘tgansiz. ✅", reply_markup=main_menu())
        return
    await state.set_state(Reg.full_name)
    text = (
        "Assalomu alaykum! 🇺🇿\n\n"
        "El-yurt Umidi ro‘yxatdan o‘tish tizimiga xush kelibsiz.\n"
        "Iltimos, EYUF g'oliblar ro‘yxatidagi <b>to‘liq ismingizni</b> kiriting.\n\n"
        "<i>Faqat 2025-yil 1-tanlov stipendiya sohiblari ro‘yxatdan o‘ta oladi.</i>"
    )

    await m.answer(text, parse_mode="HTML", reply_markup=ReplyKeyboardRemove(), disable_web_page_preview=True)

@router.message(Reg.full_name)
async def ask_phone(m: Message, state: FSMContext):
    user_input = (m.text or "").strip()
    if len(user_input) < 3:
        await m.answer("Ism juda qisqa ko‘rinmoqda. Iltimos, <b>to‘liq ismingizni</b> qayta kiriting.", parse_mode="HTML")
        return
    match = best_match_90(user_input, AWARD_KEYS, AWARD_MAP)
    if not match:
        hints = suggestion_names(user_input, AWARD_KEYS, AWARD_MAP, n=5)
        if hints:
            await m.answer("❌ Ism 90% aniqlik bilan topilmadi. Qayta urinib ko‘ring.\n\nYaqin variantlar:\n" + "\n".join(f"• {h}" for h in hints))
        else:
            await m.answer("❌ Sizning ismingiz stipendiatlar ro‘yxatida topilmadi. Imloni tekshiring yoki texnik yordamga murojaat qiling.")
        return
    _, canonical = match
    if await is_name_taken(canonical):
        await m.answer("⚠️ Bu stipendiat allaqachon ro‘yxatdan o‘tgan. Agar bu siz bo‘lsangiz, texnik yordamga murojaat qiling.")
        return
    await state.update_data(full_name=canonical)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Telefon raqamni ulashish", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await state.set_state(Reg.phone)
    await m.answer("Endi <b>telefon raqamingizni</b> yuboring (yoki tugmani bosing).", reply_markup=kb, parse_mode="HTML")

@router.message(Reg.phone, F.contact)
async def got_contact(m: Message, state: FSMContext):
    phone = normalize_phone(m.contact.phone_number)
    if not phone:
        await m.answer("Telefon raqamni o‘qib bo‘lmadi. Iltimos, +998901234567 ko‘rinishida kiriting.")
        return
    await state.update_data(phone=phone)
    await state.set_state(Reg.email)
    await m.answer("Zo‘r! Endi <b>email manzilingizni</b> yuboring (masalan: name@example.com)", reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")

@router.message(Reg.phone)
async def got_phone_text(m: Message, state: FSMContext):
    phone = normalize_phone((m.text or "").strip())
    if not phone:
        await m.answer("Bu raqam noto‘g‘ri ko‘rinmoqda. +998901234567 shaklida kiriting.")
        return
    await state.update_data(phone=phone)
    await state.set_state(Reg.email)
    await m.answer("Zo‘r! Endi <b>email manzilingizni</b> yuboring (masalan: name@example.com)", reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")

@router.message(Reg.email)
async def got_email(m: Message, state: FSMContext):
    email = (m.text or "").strip()
    if not EMAIL_RE.match(email):
        await m.answer("Email manzil noto‘g‘ri. Masalan: name@example.com ko‘rinishida yuboring.")
        return
    await state.update_data(email=email)
    await state.set_state(Reg.country)
    await m.answer("Endi <b>o‘qish davlatingizni</b> kiriting (masalan: United Kingdom)", parse_mode="HTML")

@router.message(Reg.country)
async def got_country(m: Message, state: FSMContext):
    country = (m.text or "").strip()
    if len(country) < 2:
        await m.answer("Iltimos, to‘g‘ri davlat nomini kiriting.")
        return
    await state.update_data(country=country)
    await state.set_state(Reg.university)
    await m.answer("Endi <b>universitetingiz nomini</b> kiriting (masalan: University of Birmingham)", parse_mode="HTML")

@router.message(Reg.university)
async def got_university(m: Message, state: FSMContext):
    university = (m.text or "").strip()
    if len(university) < 2:
        await m.answer("Iltimos, to‘g‘ri universitet nomini kiriting.")
        return
    if await is_registered(m.from_user.id):
        await state.clear()
        await m.answer("Siz allaqachon ro‘yxatdan o‘tgansiz. ✅", reply_markup=main_menu())
        return
    data = await state.get_data()
    canonical_name = data["full_name"]
    if await is_name_taken(canonical_name):
        await state.clear()
        await m.answer("⚠️ Bu stipendiat allaqachon ro‘yxatdan o‘tgan.", reply_markup=main_menu())
        return
    try:
        rec = await register_user(
            uid=m.from_user.id,
            full_name=canonical_name,
            phone=data["phone"],
            email=data["email"],
            country=data["country"],
            university=university,
        )
    except Exception as e:
        logger.exception("Insert failed: %s", e)
        await m.answer("❌ Ro‘yxatdan o‘tishda xatolik yuz berdi. Birozdan so‘ng qayta urinib ko‘ring.")
        return
    await state.clear()
    await m.answer(
        "✅ Ro‘yxatdan o‘tish muvaffaqiyatli yakunlandi!\n\n"
        f"F.I.O: {rec['full_name']}\n"
        f"Telefon: {rec['phone']}\n"
        f"Email: {rec['email']}\n"
        f"Davlat: {rec['country']}\n"
        f"Universitet: {rec['university']}\n\n"
        "Siz ro‘yxatdan o‘tdingiz. ✅",
        reply_markup=main_menu(),
    )

@router.message(Command("menu"))
async def show_menu(m: Message):
    if not await is_registered(m.from_user.id):
        await m.answer("Iltimos, avval ro‘yxatdan o‘ting. /start buyrug‘ini yuboring.")
        return
    await m.answer("Asosiy menyu:", reply_markup=main_menu())

@router.message(F.text == BTN_SUPPORT)
async def contact_support(m: Message):
    await m.answer(f"Texnik yordam:\n{SUPPORT_CONTACT}")