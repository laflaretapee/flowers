from aiogram import Router

from . import start, catalog, order, admin, reviews, menu, payments

all_routers: list[Router] = [
    start.router,
    catalog.router,
    order.router,
    admin.router,
    reviews.router,
    payments.router,
    # menu.router must be last -- it contains the catch-all unknown handler
    menu.router,
]
