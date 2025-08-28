from datetime import datetime, timedelta, date
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from app.config import UZ_TZ
from app.constants import BTN_BOOK, BTN_MY, BTN_SERVICES, BTN_SUPPORT

def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BOOK), KeyboardButton(text=BTN_MY)],
            [KeyboardButton(text=BTN_SERVICES), KeyboardButton(text=BTN_SUPPORT)],
        ],
        resize_keyboard=True,
    )

def is_forbidden_date(d) -> bool:
    # Dam olish kunlari
    if d.weekday() in (5, 6):  # 5=Shanba, 6=Yakshanba
        return True
    # 1-sentabr (har yil)
    if d.month == 9 and d.day == 1:
        return True
    return False

def days_kb(n: int = 10) -> InlineKeyboardMarkup:
    """
    Keyingi `n` kun uchun sanalarni ko‘rsatish, dam olish kunlari va 1-sentabrni yashirish.
    """
    today = datetime.now(UZ_TZ).date()
    rows = []
    count = 0
    i = 0
    while count < n:
        d = today + timedelta(days=i)
        i += 1
        if is_forbidden_date(d):
            continue
        rows.append([
            InlineKeyboardButton(
                text=d.strftime("%a %d %b"),
                callback_data=f"book:day:{d.isoformat()}"
            )
        ])
        count += 1

    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="book:back:menu")])
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
        epoch = int(t.timestamp())  # qisqa callback
        rows.append([InlineKeyboardButton(text=label, callback_data=f"book:time:{epoch}")])
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"book:back:day:{day.isoformat()}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)