import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery, Message,
    InlineKeyboardButton, InlineKeyboardMarkup,
)
from asgiref.sync import sync_to_async
from django.core.files.base import ContentFile
from django.utils import timezone

from catalog.models import Review

from ..states import ReviewStates
from ..keyboards import get_main_keyboard
from ..services import fetch_user_avatar_bytes

logger = logging.getLogger(__name__)

router = Router()


@router.message(F.text == "📝 Оставить отзыв")
async def start_review(message: Message, state: FSMContext):
    text = (
        "📝 <b>Оставьте отзыв о нашем сервисе!</b>\n\n"
        "Выберите оценку, затем напишите отзыв."
    )
    await state.set_state(ReviewStates.waiting_for_review)
    await message.answer(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⭐️", callback_data="rate_1"),
                 InlineKeyboardButton(text="⭐️", callback_data="rate_2"),
                 InlineKeyboardButton(text="⭐️", callback_data="rate_3"),
                 InlineKeyboardButton(text="⭐️", callback_data="rate_4"),
                 InlineKeyboardButton(text="⭐️", callback_data="rate_5")]
            ]
        ),
    )


@router.callback_query(F.data.startswith("rate_"))
async def rate_review(callback: CallbackQuery, state: FSMContext):
    rating = int(callback.data.split("_")[1])
    rating = max(1, min(5, rating))
    await state.update_data(rating=rating)
    await state.set_state(ReviewStates.waiting_for_review_text)

    filled = "🌟" * rating
    empty = "⭐️" * (5 - rating)
    stars = filled + empty

    await callback.message.edit_text(
        f"📝 <b>Оставьте отзыв о нашем сервисе!</b>\n\n"
        f"Оценка: {stars}\n\n"
        "Теперь напишите отзыв текстом.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🌟" if i < rating else "⭐️",
                        callback_data=f"rate_{i+1}",
                    )
                    for i in range(5)
                ]
            ]
        ),
    )
    await callback.answer()


@router.message(ReviewStates.waiting_for_review)
async def review_waiting_for_rating(message: Message):
    await message.answer("Сначала выберите оценку кнопками ⭐️.")


@router.message(ReviewStates.waiting_for_review_text)
async def process_review_text(message: Message, state: FSMContext):
    text = message.text
    if not text:
        await message.answer("Пожалуйста, отправьте отзыв текстом.")
        return

    try:
        data = await state.get_data()
        rating = int(data.get('rating', 5))
        rating = max(1, min(5, rating))
        review_text = text

        user = message.from_user

        avatar_bytes = await fetch_user_avatar_bytes(user.id)

        @sync_to_async
        def create_review():
            review = Review(
                name=user.first_name or "Аноним",
                telegram_user_id=user.id,
                text=review_text,
                rating=rating,
                is_published=True,
            )
            if avatar_bytes:
                ext = "jpg"
                filename = f"tg_{user.id}_{int(timezone.now().timestamp())}.{ext}"
                review.avatar.save(filename, ContentFile(avatar_bytes), save=False)
            review.save()
            return review

        await create_review()

        stars = "🌟" * rating + "⭐️" * (5 - rating)
        await state.clear()
        await message.answer(
            f"✅ <b>Спасибо за ваш отзыв!</b>\n\n"
            f"Оценка: {stars}\n"
            f"Отзыв опубликован.",
            reply_markup=get_main_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.error("Ошибка обработки отзыва: %s", e)
        await message.answer(
            "❌ Произошла ошибка. Попробуйте еще раз.",
            parse_mode=ParseMode.HTML,
        )
