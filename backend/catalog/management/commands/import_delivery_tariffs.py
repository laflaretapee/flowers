import csv
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from catalog.delivery_tariffs import parse_cost_value


class Command(BaseCommand):
    help = "Проверить/подготовить CSV тарифов доставки (aliases,cost,label)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            dest="file_path",
            default="catalog/data/delivery_tariffs.csv",
            help="Путь к CSV файлу тарифов.",
        )

    def handle(self, *args, **options):
        file_path = Path(options["file_path"]).resolve()
        if not file_path.exists():
            raise CommandError(f"Файл не найден: {file_path}")

        with file_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = set(reader.fieldnames or [])
            flat_required = {"aliases", "cost", "label"}
            source_required = {"Населенный_пункт", "Стоимость"}
            if not flat_required.issubset(fieldnames) and not source_required.issubset(fieldnames):
                raise CommandError(
                    "CSV должен содержать колонки aliases,cost,label "
                    "или Буква,Населенный_пункт,Стоимость. "
                    f"Сейчас: {reader.fieldnames}"
                )
            flat_format = flat_required.issubset(fieldnames)

            total = 0
            valid = 0
            manual_required = 0
            for idx, row in enumerate(reader, start=2):
                total += 1
                if flat_format:
                    aliases_raw = (row.get("aliases") or "").strip()
                    cost_raw = (row.get("cost") or "").strip()
                    has_place = bool(aliases_raw)
                else:
                    place_name = (row.get("Населенный_пункт") or "").strip()
                    cost_raw = (row.get("Стоимость") or "").strip()
                    has_place = bool(place_name)

                if not has_place:
                    continue
                cost = parse_cost_value(cost_raw)
                if cost is None:
                    manual_required += 1
                else:
                    try:
                        Decimal(str(cost))
                    except Exception:
                        self.stdout.write(self.style.WARNING(f"Строка {idx}: некорректная цена {cost_raw!r}"))
                        continue
                valid += 1

        self.stdout.write(self.style.SUCCESS(
            f"Ок: {valid} валидных строк из {total} в {file_path}. "
            f"Из них ручной расчет: {manual_required}"
        ))
