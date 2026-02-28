from __future__ import annotations

import csv
import logging
import re
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

# Формат: (алиасы населенного пункта, цена, человекочитаемая метка)
DEFAULT_DELIVERY_TARIFFS: list[tuple[tuple[str, ...], Decimal | None, str]] = [
    (("раевка", "раевский", "село раевский"), Decimal("250"), "Раевка / Раевский"),
]


def normalize_address_text(value: str) -> str:
    if not value:
        return ""
    normalized = value.lower().replace("ё", "е")
    normalized = re.sub(r"[^a-zа-я0-9\s\-]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _default_tariffs_copy() -> list[tuple[tuple[str, ...], Decimal | None, str]]:
    copied: list[tuple[tuple[str, ...], Decimal | None, str]] = []
    for aliases, cost, label in DEFAULT_DELIVERY_TARIFFS:
        copied.append((tuple(aliases), Decimal(str(cost)) if cost is not None else None, str(label)))
    return copied


def _seed_defaults(
    merged: dict[str, dict],
) -> None:
    for aliases, cost, label in _default_tariffs_copy():
        key = normalize_address_text(label)
        if not key or not aliases:
            continue
        merged[key] = {
            "aliases": set(aliases),
            "cost": cost,
            "label": label,
        }


def _resolve_tariffs_file() -> Path:
    from_setting = (getattr(settings, "DELIVERY_TARIFFS_FILE", "") or "").strip()
    if from_setting:
        return Path(from_setting)
    return Path(__file__).resolve().parent / "data" / "delivery_tariffs.csv"


def parse_cost_value(raw: str) -> Decimal | None:
    text = (raw or "").strip().lower().replace(",", ".")
    if not text:
        return None
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    if not numbers:
        return None
    # Для диапазонов берем верхнюю границу, чтобы не занижать доставку.
    try:
        return max(Decimal(n) for n in numbers)
    except InvalidOperation:
        return None


def build_aliases(place_name: str) -> tuple[str, ...]:
    raw = normalize_address_text(place_name)
    if not raw:
        return tuple()

    aliases = {raw}

    no_brackets = normalize_address_text(re.sub(r"\([^)]*\)", " ", place_name))
    if no_brackets:
        aliases.add(no_brackets)

    # Нормализуем частые сокращения
    expanded = raw
    expanded = re.sub(r"\bс\b", "село", expanded)
    expanded = re.sub(r"\bд\b", "деревня", expanded)
    if expanded:
        aliases.add(expanded)

    shortened = re.sub(r"\b(село|деревня|поселок|пос)\b", " ", raw)
    shortened = re.sub(r"\s+", " ", shortened).strip()
    if shortened:
        aliases.add(shortened)

    return tuple(sorted(aliases, key=len, reverse=True))


@lru_cache(maxsize=1)
def load_delivery_tariffs() -> list[tuple[tuple[str, ...], Decimal | None, str]]:
    path = _resolve_tariffs_file()
    if not path.exists():
        logger.warning("Файл тарифов доставки не найден: %s. Используются тарифы по умолчанию.", path)
        return _default_tariffs_copy()

    merged: dict[str, dict] = {}
    _seed_defaults(merged)
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = set(reader.fieldnames or [])
            flat_format = {"aliases", "cost", "label"}.issubset(fieldnames)
            source_format = {"Населенный_пункт", "Стоимость"}.issubset(fieldnames)
            if not flat_format and not source_format:
                logger.warning(
                    "Неизвестный формат CSV %s. Нужны колонки aliases,cost,label "
                    "или Буква,Населенный_пункт,Стоимость. Используются тарифы по умолчанию.",
                    path,
                )
                return _default_tariffs_copy()

            for idx, row in enumerate(reader, start=2):
                if flat_format:
                    aliases_raw = (row.get("aliases") or "").strip()
                    cost_raw = (row.get("cost") or "").strip()
                    label_raw = (row.get("label") or aliases_raw).strip()
                    aliases = tuple(
                        a for a in (normalize_address_text(part) for part in aliases_raw.split("|")) if a
                    )
                else:
                    place_name = (row.get("Населенный_пункт") or "").strip()
                    cost_raw = (row.get("Стоимость") or "").strip()
                    label_raw = place_name
                    aliases = build_aliases(place_name)

                if not aliases or not label_raw:
                    continue

                cost = parse_cost_value(cost_raw)
                key = normalize_address_text(label_raw)
                if not key:
                    continue

                item = merged.get(key)
                if not item:
                    merged[key] = {
                        "aliases": set(aliases),
                        "cost": cost,
                        "label": label_raw,
                    }
                    continue

                item["aliases"].update(aliases)
                existing_cost = item["cost"]
                if existing_cost is None:
                    item["cost"] = cost
                elif cost is not None:
                    item["cost"] = max(existing_cost, cost)
    except Exception as exc:
        logger.warning("Не удалось прочитать файл тарифов %s: %s. Используются тарифы по умолчанию.", path, exc)
        return _default_tariffs_copy()

    if not merged:
        logger.warning("Файл тарифов %s пустой/некорректный. Используются тарифы по умолчанию.", path)
        return _default_tariffs_copy()

    parsed: list[tuple[tuple[str, ...], Decimal | None, str]] = []
    for item in merged.values():
        aliases = tuple(sorted(item["aliases"], key=len, reverse=True))
        parsed.append((aliases, item["cost"], item["label"]))

    # Сначала самые специфичные алиасы (длиннее), чтобы "уфа аэропорт" не матчился как "уфа".
    parsed.sort(key=lambda x: max((len(a) for a in x[0]), default=0), reverse=True)
    return parsed
