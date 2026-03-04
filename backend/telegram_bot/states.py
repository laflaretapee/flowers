from aiogram.fsm.state import State, StatesGroup


class OrderStates(StatesGroup):
    waiting_for_quantity = State()
    waiting_for_name = State()
    waiting_for_phone = State()
    waiting_for_address = State()
    waiting_for_comment = State()


class CustomBouquetStates(StatesGroup):
    waiting_for_style = State()
    waiting_for_budget = State()
    waiting_for_deadline = State()


class PreOrderStates(StatesGroup):
    waiting_for_datetime = State()


class AdminStates(StatesGroup):
    waiting_for_ready_photo = State()
    waiting_for_transfer_details = State()


class ReviewStates(StatesGroup):
    waiting_for_review = State()
    waiting_for_review_text = State()
