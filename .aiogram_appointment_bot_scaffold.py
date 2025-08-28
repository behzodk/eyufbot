import asyncio
import csv
import difflib
import logging
import os
import re
import unicodedata
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Dict, List, Tuple

import phonenumbers
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from dotenv import load_dotenv
from supabase import create_client, Client

# ----------------------- Config -----------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
AWARD_CSV = os.getenv("AWARD_CSV", "award_holders.csv")
SUPPORT_CONTACT = os.getenv("SUPPORT_CONTACT", "Email: support@example.com")
UZ_TZ = ZoneInfo("Asia/Tashkent")

if not BOT_TOKEN:
    raise SystemExit("Missing BOT_TOKEN in .env")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise SystemExit("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in .env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("elyurt-register")

sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ----------------------- Whitelist --------------------
APOSTROPHE_VARIANTS = {"\u02bc", "\u02bb", "\u2018", "\u2019", "\u2032", "\uFF07"}

def normalize_name(name: str) -> str:
    s = unicodedata.normalize("NFKC", name or "")
    for ch in APOSTROPHE_VARIANTS:
        s = s.replace(ch, "'")
    return re.sub(r"\s+", " ", s).strip().upper()

def load_award_map(path: str) -> Dict[str, str]:
    mp: Dict[str, str] = {}
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if "name" not in (reader.fieldnames or []):
            raise SystemExit("award_holders.csv must include 'name' header.")
        for row in reader:
            raw = (row.get("name") or "").strip()
            if raw:
                mp[normalize_name(raw)] = raw
    if not mp:
        raise SystemExit("award_holders.csv contains no names.")
    return mp

try:
    AWARD_MAP: Dict[str, str] = load_award_map(AWARD_CSV)
    AWARD_KEYS: List[str] = list(AWARD_MAP.keys())
except FileNotFoundError:
    raise SystemExit(f"award_holders.csv not found at: {AWARD_CSV}")

def best_match_90(input_name: str) -> Optional[Tuple[str, str]]:
    q = normalize_name(input_name)
    cands = difflib.get_close_matches(q, AWARD_KEYS, n=1, cutoff=0.90)
    if not cands:
        return None
    k = cands[0]
    return k, AWARD_MAP[k]

def suggestion_names(input_name: str, n: int = 5) -> List[str]:
    q = normalize_name(input_name)
    return [AWARD_MAP[k] for k in difflib.get_close_matches(q, AWARD_KEYS, n=n, cutoff=0.75)]

# ----------------------- FSM --------------------------
class Reg(StatesGroup):
    full_name = State()
    phone = State()
    email = State()
    country = State()
    university = State()

class BookingFlow(StatesGroup):
    picking_service = State()
    picking_day = State()

# ----------------------- Helpers ----------------------
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def normalize_phone(raw: str) -> Optional[str]:
    try:
        parsed = phonenumbers.parse(raw, "UZ")
        if not phonenumbers.is_valid_number(parsed):
            return None
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except Exception:
        return None

def _is_registered_sync(telegram_user_id: int) -> bool:
    res = sb.table("app_user").select("id").eq("telegram_user_id", telegram_user_id).limit(1).execute()
    return bool(res.data)

async def is_registered(telegram_user_id: int) -> bool:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _is_registered_sync, telegram_user_id)

def _is_name_taken_sync(canonical_full_name: str) -> bool:
    res = sb.table("app_user").select("id").ilike("full_name", canonical_full_name).limit(1).execute()
    return bool(res.data)

async def is_name_taken(canonical_full_name: str) -> bool:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _is_name_taken_sync, canonical_full_name)

def _register_user_sync(telegram_user_id: int, full_name: str, phone: str, email: str, country: str, university: str):
    res = sb.table("app_user").insert({
        "telegram_user_id": telegram_user_id,
        "full_name": full_name.strip(),
        "phone": phone,
        "email": email.lower(),
        "country": country.strip(),
        "university": university.strip(),
    }).execute()
    if not res.data:
        raise RuntimeError("Insert returned no data")
    return res.data[0]

async def register_user(telegram_user_id: int, full_name: str, phone: str, email: str, country: str, university: str):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _register_user_sync, telegram_user_id, full_name, phone, email, country, university)

def _get_user_record_sync(telegram_user_id: int) -> Optional[Dict]:
    res = sb.table("app_user").select("id,full_name").eq("telegram_user_id", telegram_user_id).single().execute()
    return res.data if res else None

async def get_user_record(telegram_user_id: int) -> Optional[Dict]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _get_user_record_sync, telegram_user_id)

def _fetch_services_sync() -> List[Dict]:
    return (sb.table("service").select("id,name,duration_min").order("name").execute().data) or []

async def fetch_services() -> List[Dict]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_services_sync)

def _get_service_sync(svc_id: str) -> Optional[Dict]:
    try:
        return sb.table("service").select("id,name,duration_min").eq("id", svc_id).single().execute().data
    except Exception:
        return None

async def get_service(svc_id: str) -> Optional[Dict]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _get_service_sync, svc_id)

def _fetch_bookings_for_day_sync(day_start: datetime, day_end: datetime) -> List[Dict]:
    # Fetch all bookings overlapping the day
    # condition: start_at < day_end AND end_at > day_start
    return (sb.table("booking")
              .select("id,user_id,service_id,start_at,end_at")
              .lt("start_at", day_end.isoformat())
              .gt("end_at", day_start.isoformat())
              .execute()
              .data) or []

async def fetch_bookings_for_day(day_start: datetime, day_end: datetime) -> List[Dict]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_bookings_for_day_sync, day_start, day_end)

def _create_booking_sync(user_id: str, service_id: str, start_at: datetime, end_at: datetime) -> Dict:
    res = sb.table("booking").insert({
        "user_id": user_id,
        "service_id": service_id,
        "start_at": start_at.isoformat(),
        "end_at": end_at.isoformat(),
        "status": "booked",
    }).execute()
    if not res.data:
        raise RuntimeError("Booking insert returned no data")
    return res.data[0]

async def create_booking(user_id: str, service_id: str, start_at: datetime, end_at: datetime) -> Dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _create_booking_sync, user_id, service_id, start_at, end_at)

# ----------------------- Availability -----------------
WORK_WINDOWS = [(time(9,30), time(13,0)), (time(14,0), time(18,0))]
STEP_MIN = 5            # granularity
CAPACITY = 2            # two staff
MIN_AHEAD = timedelta(hours=2)

def ceil_dt_to_step(dt: datetime, step_min: int) -> datetime:
    m = (dt.minute // step_min) * step_min
    dt0 = dt.replace(second=0, microsecond=0, minute=m)
    if dt0 < dt:
        dt0 += timedelta(minutes=step_min)
    return dt0

def iter_window_candidates(day: date, win_start: time, win_end: time, dur: timedelta) -> List[datetime]:
    start_dt = datetime.combine(day, win_start, UZ_TZ)
    end_dt = datetime.combine(day, win_end, UZ_TZ)
    cur = start_dt
    out = []
    while True:
        if cur + dur <= end_dt:
            out.append(cur)
            cur += timedelta(minutes=STEP_MIN)
        else:
            break
    return out

def build_timeline(bookings: List[Dict], day: date) -> Dict[datetime, int]:
    counts: Dict[datetime, int] = {}
    for b in bookings:
        s = datetime.fromisoformat(b["start_at"]).astimezone(UZ_TZ)
        e = datetime.fromisoformat(b["end_at"]).astimezone(UZ_TZ)
        # clamp to day windows span
        cur = s.replace(second=0, microsecond=0)
        cur = ceil_dt_to_step(cur, STEP_MIN)
        while cur < e:
            counts[cur] = counts.get(cur, 0) + 1
            cur += timedelta(minutes=STEP_MIN)
    return counts

def is_candidate_ok(start: datetime, dur: timedelta, counts: Dict[datetime,int]) -> bool:
    cur = start
    end = start + dur
    while cur < end:
        if counts.get(cur, 0) >= CAPACITY:
            return False
        cur += timedelta(minutes=STEP_MIN)
    return True

def list_available_times(day: date, duration_min: int, existing: List[Dict]) -> List[datetime]:
    now_local = datetime.now(UZ_TZ)
    dur = timedelta(minutes=int(duration_min))
    counts = build_timeline(existing, day)
    times: List[datetime] = []

    # convenience for lunch boundaries
    LUNCH_END = time(13, 0)
    LUNCH_EDGE_50 = time(12, 50)
    LUNCH_EDGE_55 = time(12, 55)

    for ws, we in WORK_WINDOWS:
        candidates = iter_window_candidates(day, ws, we, dur)
        for t0 in candidates:
            # >= 2 hours in advance
            if t0 < now_local + MIN_AHEAD:
                continue

            # Special lunch rules:
            if ws == time(9, 30) and we == LUNCH_END:
                if t0.time() == LUNCH_EDGE_55:
                    # never allow 12:55
                    continue
                if t0.time() == LUNCH_EDGE_50 and duration_min > 10:
                    # 12:50 allowed only for ≤10 min service (e.g., Financial guarantee)
                    continue

            # Must fully fit within the window (also blocks too-late starts)
            if t0 + dur > datetime.combine(day, we, UZ_TZ):
                continue

            # Capacity check across the whole duration
            if not is_candidate_ok(t0, dur, counts):
                continue

            times.append(t0)

    return times

# ----------------------- Menus ------------------------
def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📅 Book appointment"), KeyboardButton(text="🗓️ My appointments")],
            [KeyboardButton(text="📋 Available services"), KeyboardButton(text="🆘 Contact support")],
        ],
        resize_keyboard=True,
    )

async def ensure_registered_or_prompt(m: Message) -> bool:
    if await is_registered(m.from_user.id):
        return True
    await m.answer("Please complete registration first. Send /start to begin.")
    return False

def days_kb(n: int = 10) -> InlineKeyboardMarkup:
    today = datetime.now(UZ_TZ).date()
    rows = []
    for i in range(n):
        d = today + timedelta(days=i)
        rows.append([InlineKeyboardButton(text=d.strftime("%a %d %b"), callback_data=f"book:day:{d.isoformat()}")])
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="book:back:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def times_kb(day: date, slots: List[datetime]) -> InlineKeyboardMarkup:
    if not slots:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="No times available", callback_data="noop")],
            [InlineKeyboardButton(text="⬅️ Back", callback_data=f"book:back:day:{day.isoformat()}")]
        ])
    rows = []
    for t in slots[:40]:
        label = t.strftime("%H:%M")
        # encode tz-aware Asia/Tashkent time as epoch seconds (short)
        epoch = int(t.timestamp())
        rows.append([InlineKeyboardButton(text=label, callback_data=f"book:time:{epoch}")])
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data=f"book:back:day:{day.isoformat()}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ----------------------- Bot UI -----------------------
router = Router()

@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    if await is_registered(m.from_user.id):
        await state.clear()
        await m.answer("You are already registered. ✅", reply_markup=main_menu())
        return
    await state.set_state(Reg.full_name)
    await m.answer(
        "Assalomu alaykum! 🇺🇿\n\n"
        "'El-yurt Umidi' Jamg'armasi uchun xujjatlar topshirish uchun navbat olish.\n"
        "Please send your *Full name* exactly as in the EYUF (award holders) list.\n\n"
        "_Only award holders can register._",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )

@router.message(Reg.full_name)
async def ask_phone(m: Message, state: FSMContext):
    user_input = (m.text or "").strip()
    if len(user_input) < 3:
        await m.answer("Name looks too short. Please re-enter your *Full name*.", parse_mode="Markdown")
        return
    match = best_match_90(user_input)
    if not match:
        hints = suggestion_names(user_input, n=5)
        if hints:
            await m.answer("❌ Name not confidently matched (need ≥ 90%). Try again.\n\nClose matches:\n" + "\n".join(f"• {h}" for h in hints))
        else:
            await m.answer("❌ We couldn't find your name in the award holders list. Check spelling or contact support.")
        return
    _, canonical = match
    if await is_name_taken(canonical):
        await m.answer("⚠️ This award holder is already registered. If this is your record, contact support.")
        return
    await state.update_data(full_name=canonical)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Share phone number", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await state.set_state(Reg.phone)
    await m.answer("Now send your *phone number* (or tap the button to share).", reply_markup=kb, parse_mode="Markdown")

@router.message(Reg.phone, F.contact)
async def got_contact(m: Message, state: FSMContext):
    phone = normalize_phone(m.contact.phone_number)
    if not phone:
        await m.answer("Could not read that phone. Please type it like +998901234567")
        return
    await state.update_data(phone=phone)
    await state.set_state(Reg.email)
    await m.answer("Great! Now enter your *email* (e.g., name@example.com)", reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")

@router.message(Reg.phone)
async def got_phone_text(m: Message, state: FSMContext):
    phone = normalize_phone((m.text or "").strip())
    if not phone:
        await m.answer("That doesn't look valid. Try format +998901234567")
        return
    await state.update_data(phone=phone)
    await state.set_state(Reg.email)
    await m.answer("Great! Now enter your *email* (e.g., name@example.com)", reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")

@router.message(Reg.email)
async def got_email(m: Message, state: FSMContext):
    email = (m.text or "").strip()
    if not EMAIL_RE.match(email):
        await m.answer("Email format seems invalid. Please try again (e.g., name@example.com)")
        return
    await state.update_data(email=email)
    await state.set_state(Reg.country)
    await m.answer("Enter your *Country of study* (e.g., United Kingdom)", parse_mode="Markdown")

@router.message(Reg.country)
async def got_country(m: Message, state: FSMContext):
    country = (m.text or "").strip()
    if len(country) < 2:
        await m.answer("Please enter a valid country name.")
        return
    await state.update_data(country=country)
    await state.set_state(Reg.university)
    await m.answer("Enter your *University* (e.g., University of Birmingham)", parse_mode="Markdown")

@router.message(Reg.university)
async def got_university(m: Message, state: FSMContext):
    university = (m.text or "").strip()
    if len(university) < 2:
        await m.answer("Please enter a valid university name.")
        return
    if await is_registered(m.from_user.id):
        await state.clear()
        await m.answer("You are already registered. ✅", reply_markup=main_menu())
        return
    data = await state.get_data()
    canonical_name = data["full_name"]
    if await is_name_taken(canonical_name):
        await state.clear()
        await m.answer("⚠️ This award holder has already been registered.", reply_markup=main_menu())
        return
    try:
        rec = await register_user(
            telegram_user_id=m.from_user.id,
            full_name=canonical_name,
            phone=data["phone"],
            email=data["email"],
            country=data["country"],
            university=university,
        )
    except Exception as e:
        logger.exception("Insert failed: %s", e)
        await m.answer("❌ Could not save your registration. Please try again in a moment.")
        return
    await state.clear()
    await m.answer(
        "✅ Registered!\n\n"
        f"Full name: {rec['full_name']}\n"
        f"Phone: {rec['phone']}\n"
        f"Email: {rec['email']}\n"
        f"Country: {rec['country']}\n"
        f"University: {rec['university']}\n\n"
        "You are registered. ✅",
        reply_markup=main_menu(),
    )

# -------- Menu actions --------
@router.message(F.text == "📅 Book appointment")
async def book_appointment(m: Message, state: FSMContext):
    if not await ensure_registered_or_prompt(m):
        return
    procedure = (
        "Procedure:\n"
        "1) Submit *Birlamchi hujjatlar*:\n"
        "   • Passport copy\n"
        "   • Travel passport copy\n"
        "   • Photo\n"
        "   • Letter from financial sponsor\n"
        "   • Letter from sponsor confirming a job upon return\n"
        "   • Acceptance letter\n"
        "2) Then apply for *Financial Guarantee Letter*.\n"
        "3) Apply for *Visa*.\n"
        "4) After visa, visit for *4 tomonlama shartnoma*.\n\n"
        "Select a service:"
    )
    services = await fetch_services()
    if not services:
        await m.answer(procedure + "\n\n(No services configured yet.)", parse_mode="Markdown")
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"{s['name']} (~{s.get('duration_min','?')} min)", callback_data=f"book:svc:{s['id']}")]
            for s in services
        ]
    )
    await state.set_state(BookingFlow.picking_service)
    await m.answer(procedure, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data.startswith("book:svc:"))
async def pick_service(cq: CallbackQuery, state: FSMContext):
    svc_id = cq.data.split(":", 2)[2]
    svc = await get_service(svc_id)
    if not svc:
        await cq.answer("Service not found", show_alert=True)
        return
    await state.update_data(svc_id=svc_id)
    await state.set_state(BookingFlow.picking_day)
    await cq.message.edit_text(
        f"Service: *{svc['name']}* (~{svc['duration_min']} min)\nChoose a day:",
        parse_mode="Markdown",
        reply_markup=days_kb(10),
    )
    await cq.answer()

@router.callback_query(F.data.startswith("book:day:"))
async def pick_day(cq: CallbackQuery, state: FSMContext):
    day_iso = cq.data.split(":", 2)[2]
    d = date.fromisoformat(day_iso)
    data = await state.get_data()
    svc = await get_service(data["svc_id"])
    if not svc:
        await cq.answer("Service not found", show_alert=True)
        return

    day_start = datetime.combine(d, time(0,0), UZ_TZ)
    day_end = day_start + timedelta(days=1)
    existing = await fetch_bookings_for_day(day_start, day_end)
    valid_times = list_available_times(d, int(svc["duration_min"]), existing)

    kb = times_kb(d, valid_times)
    await cq.message.edit_text(
        f"Service: *{svc['name']}* (~{svc['duration_min']} min)\nDate: {d.strftime('%A, %d %b')}\nSelect a start time:",
        parse_mode="Markdown",
        reply_markup=kb,
    )
    await cq.answer()

@router.callback_query(F.data.startswith("book:back:day:"))
async def back_to_day(cq: CallbackQuery, state: FSMContext):
    day_iso = cq.data.split(":", 3)[3]
    d = date.fromisoformat(day_iso)
    data = await state.get_data()
    svc = await get_service(data["svc_id"])
    if not svc:
        await cq.answer("Service not found", show_alert=True)
        return
    day_start = datetime.combine(d, time(0,0), UZ_TZ)
    day_end = day_start + timedelta(days=1)
    existing = await fetch_bookings_for_day(day_start, day_end)
    valid_times = list_available_times(d, int(svc["duration_min"]), existing)
    kb = times_kb(d, valid_times)
    await cq.message.edit_text(
        f"Service: *{svc['name']}* (~{svc['duration_min']} min)\nDate: {d.strftime('%A, %d %b')}\nSelect a start time:",
        parse_mode="Markdown",
        reply_markup=kb,
    )
    await cq.answer()

@router.callback_query(F.data == "book:back:menu")
async def back_to_menu(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    await cq.message.edit_text("Back to menu. Use the keyboard below.")
    await cq.message.answer("Main menu:", reply_markup=main_menu())
    await cq.answer()

@router.callback_query(F.data.startswith("book:time:"))
async def pick_time(cq: CallbackQuery, state: FSMContext):
    # callback looks like: book:time:{epoch}
    try:
        epoch = int(cq.data.split(":", 2)[2])
    except Exception:
        await cq.answer("Invalid time.", show_alert=True)
        return

    data = await state.get_data()
    svc_id = data.get("svc_id")
    if not svc_id:
        await cq.answer("Session expired. Please choose a service again.", show_alert=True)
        return

    svc = await get_service(svc_id)
    if not svc:
        await cq.answer("Service not found", show_alert=True)
        return

    start_local = datetime.fromtimestamp(epoch, UZ_TZ)
    # 2h-ahead check (extra safety if user clicked an old button)
    if start_local < datetime.now(UZ_TZ) + MIN_AHEAD:
        await cq.answer("Too soon to book. Pick a later time.", show_alert=True)
        return

    dur = timedelta(minutes=int(svc["duration_min"]))
    end_local = start_local + dur

    # Ensure it stays within one work window
    in_morning = WORK_WINDOWS[0][0] <= start_local.time() < WORK_WINDOWS[0][1]
    in_afternoon = WORK_WINDOWS[1][0] <= start_local.time() < WORK_WINDOWS[1][1]
    if in_morning and end_local.time() > WORK_WINDOWS[0][1]:
        await cq.answer("Not enough time before lunch. Pick an earlier time.", show_alert=True)
        return
    if in_afternoon and end_local.time() > WORK_WINDOWS[1][1]:
        await cq.answer("Not enough time before closing. Pick an earlier time.", show_alert=True)
        return
    if not (in_morning or in_afternoon):
        await cq.answer("Time outside working hours.", show_alert=True)
        return

    # Re-check capacity just before booking
    d = start_local.date()
    day_start = datetime.combine(d, time(0,0), UZ_TZ)
    day_end = day_start + timedelta(days=1)
    existing = await fetch_bookings_for_day(day_start, day_end)
    counts = build_timeline(existing, d)
    if not is_candidate_ok(start_local, dur, counts):
        await cq.answer("This time just got filled. Choose another.", show_alert=True)
        valid_times = list_available_times(d, int(svc["duration_min"]), existing)
        await cq.message.edit_reply_markup(reply_markup=times_kb(d, valid_times))
        return

    user = await get_user_record(cq.from_user.id)
    if not user:
        await cq.answer("Please register first with /start.", show_alert=True)
        return

    try:
        rec = await create_booking(user_id=user["id"], service_id=svc_id, start_at=start_local, end_at=end_local)
    except Exception as e:
        logger.exception("Create booking failed: %s", e)
        await cq.answer("Could not create booking. Try another time.", show_alert=True)
        return

    local_s = start_local.strftime("%Y-%m-%d %H:%M")
    local_e = end_local.strftime("%H:%M")
    await cq.message.edit_text(
        "✅ Booking confirmed!\n\n"
        f"Service: *{svc['name']}*\n"
        f"When: {local_s}–{local_e} (Asia/Tashkent)\n",
        parse_mode="Markdown",
    )
    await cq.answer()

@router.message(F.text == "🗓️ My appointments")
async def my_appointments(m: Message):
    if not await ensure_registered_or_prompt(m):
        return
    user = await get_user_record(m.from_user.id)
    if not user:
        await m.answer("Please register first with /start.")
        return
    try:
        res = (sb.rpc("get_user_bookings_with_service", params={"p_user_id": user["id"]}).execute())
        # If you don't have an RPC, fallback simple select + join emulation:
        if not getattr(res, "data", None):
            res = sb.table("booking").select("id,service_id,start_at,end_at,status").eq("user_id", user["id"]).order("start_at").execute()
            data = res.data or []
            # augment names
            svc_ids = list({r["service_id"] for r in data})
            svc_map = {}
            if svc_ids:
                svc_rows = sb.table("service").select("id,name").in_("id", svc_ids).execute().data or []
                svc_map = {r["id"]: r["name"] for r in svc_rows}
            lines = []
            now_tz = datetime.now(UZ_TZ)
            for r in data:
                s = datetime.fromisoformat(r["start_at"]).astimezone(UZ_TZ)
                e = datetime.fromisoformat(r["end_at"]).astimezone(UZ_TZ)
                if e < now_tz:
                    continue
                nm = svc_map.get(r["service_id"], "Service")
                lines.append(f"• {s:%Y-%m-%d %H:%M}–{e:%H:%M} — {nm} ({r['status']})")
            await m.answer("\n".join(lines) if lines else "No upcoming appointments.")
            return
        else:
            data = res.data
    except Exception:
        # Fallback handled above; if RPC failed and fallback failed, show generic
        await m.answer("No appointments found or failed to fetch.")
        return

@router.message(F.text == "📋 Available services")
async def available_services(m: Message):
    try:
        rows = (sb.table("service").select("name,duration_min").order("name").execute().data) or []
    except Exception as e:
        logger.exception("Service fetch failed: %s", e)
        await m.answer("Couldn't load services right now.")
        return
    if not rows:
        await m.answer("No services configured yet.")
        return
    lines = ["Available services:"]
    for r in rows:
        nm = r.get("name", "Service")
        dur = r.get("duration_min")
        lines.append(f"• {nm} — ~{dur} min" if isinstance(dur, int) else f"• {nm}")
    await m.answer("\n".join(lines))

@router.message(F.text == "🆘 Contact support")
async def contact_support(m: Message):
    await m.answer(f"Support:\n{SUPPORT_CONTACT}")

@router.message(Command("menu"))
async def show_menu(m: Message):
    if not await ensure_registered_or_prompt(m):
        return
    await m.answer("Main menu:", reply_markup=main_menu())

# ----------------------- Bootstrap --------------------
async def main() -> None:
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    me = await bot.get_me()
    logger.info("Bot started as @%s (id=%s)", me.username, me.id)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")