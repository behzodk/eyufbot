from aiogram.fsm.state import State, StatesGroup

class Reg(StatesGroup):
    full_name = State()
    phone = State()
    email = State()
    country = State()
    university = State()

class BookingFlow(StatesGroup):
    picking_service = State()
    picking_day = State()
    uploading_zip = State()

