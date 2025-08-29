# app/keyboards.py
from datetime import datetime, timedelta, date
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from app.config import UZ_TZ
from app.constants import (
    BTN_BOOK, BTN_MY, BTN_SERVICES, BTN_SUPPORT, BTN_SPECIAL_SERVICE,
    BTN_ALL_APPTS, BTN_ALL_STUDENTS, BTN_NOTIFY_ALL
)

def main_menu() -> ReplyKeyboardMarkup:
    # User menu (includes the special service)
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BOOK), KeyboardButton(text=BTN_MY)],
            [KeyboardButton(text=BTN_SERVICES), KeyboardButton(text=BTN_SUPPORT)],
            [KeyboardButton(text=BTN_SPECIAL_SERVICE)],
        ],
        resize_keyboard=True,
    )

def admin_main_menu() -> ReplyKeyboardMarkup:
    # Admin-only menu (no registration or booking)
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_ALL_APPTS)],
            [KeyboardButton(text=BTN_ALL_STUDENTS)],
            [KeyboardButton(text=BTN_NOTIFY_ALL)],
        ],
        resize_keyboard=True,
    )

def is_forbidden_date(d: date) -> bool:
    if d.weekday() in (5, 6):
        return True
    if d.month == 9 and d.day == 1:
        return True
    return False

def days_kb(n: int = 10) -> InlineKeyboardMarkup:
    today = datetime.now(UZ_TZ).date()
    rows, count, i = [], 0, 0
    while count < n:
        d = today + timedelta(days=i); i += 1
        if is_forbidden_date(d): continue
        rows.append([InlineKeyboardButton(text=d.strftime("%a %d %b"), callback_data=f"book:day:{d.isoformat()}")])
        count += 1
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="book:back:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def admin_days_kb(n: int = 14) -> InlineKeyboardMarkup:
    today = datetime.now(UZ_TZ).date()
    rows, count, i = [], 0, 0
    while count < n:
        d = today + timedelta(days=i); i += 1
        if is_forbidden_date(d): continue
        rows.append([InlineKeyboardButton(text=d.strftime("%a %d %b"), callback_data=f"all:day:{d.isoformat()}")])
        count += 1
    return InlineKeyboardMarkup(inline_keyboard=rows)

def times_kb(day: date, slots):
    if not slots:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Bo‘sh vaqtlar yo‘q", callback_data="noop")],
            [InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"book:back:day:{day.isoformat()}")]
        ])
    rows = []
    for t in slots[:40]:
        label = t.strftime("%H:%M")
        epoch = int(t.timestamp())
        rows.append([InlineKeyboardButton(text=label, callback_data=f"book:time:{epoch}")])
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"book:back:day:{day.isoformat()}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)