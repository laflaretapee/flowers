from __future__ import annotations

import csv
import logging
import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

# Формат: (алиасы населенного пункта, цена, человекочитаемая метка)
DEFAULT_DELIVERY_TARIFFS: list[tuple[tuple[str, ...], Decimal, str]] = [
    (("раевка", "раевский", "село раевский"), Decimal("250"), "Раевка / Раевский"),
]


def normalize_address_text(value: str) -> str:
    if not value:
        return ""
    normalized = value.lower().replace("ё", "е")
    normalized = re.sub(r"[^a-zа-я0-9\s\-]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _default_tariffs_copy() -> list[tuple[tuple[str, ...], Decimal, str]]:
    return [(tuple(aliases), Decimal(str(cost)), str(label)) for aliases, cost, label in DEFAULT_DELIVERY_TARIFFS]


def _resolve_tariffs_file() -> Path:
    from_setting = (getattr(settings, "DELIVERY_TARIFFS_FILE", "") or "").strip()
    if from_setting:
        return Path(from_setting)
    return Path(__file__).resolve().parent / "data" / "delivery_tariffs.csv"


@lru_cache(maxsize=1)
def load_delivery_tariffs() -> list[tuple[tuple[str, ...], Decimal, str]]:
    path = _resolve_tariffs_file()
    if not path.exists():
        logger.warning("Файл тарифов доставки не найден: %s. Используются тарифы по умолчанию.", path)
        return _default_tariffs_copy()

    parsed: list[tuple[tuple[str, ...], Decimal, str]] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader, start=2):
                aliases_raw = (row.get("aliases") or "").strip()
                cost_raw = (row.get("cost") or "").strip().replace(",", ".")
                label_raw = (row.get("label") or aliases_raw).strip()

                if not aliases_raw or not cost_raw:
                    continue

                aliases = tuple(
                    a for a in (normalize_address_text(part) for part in aliases_raw.split("|")) if a
                )
                if not aliases:
                    continue

                try:
                    cost = Decimal(cost_raw)
                except Exception:
                    logger.warning("Некорректная стоимость в строке %s файла %s: %r", idx, path, cost_raw)
                    continue

                parsed.append((aliases, cost, label_raw))
    except Exception as exc:
        logger.warning("Не удалось прочитать файл тарифов %s: %s. Используются тарифы по умолчанию.", path, exc)
        return _default_tariffs_copy()

    if not parsed:
        logger.warning("Файл тарифов %s пустой/некорректный. Используются тарифы по умолчанию.", path)
        return _default_tariffs_copy()

    return parsed

