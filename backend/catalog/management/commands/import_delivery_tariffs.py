import csv
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


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
            required = {"aliases", "cost", "label"}
            if not required.issubset(set(reader.fieldnames or [])):
                raise CommandError(
                    f"CSV должен содержать колонки: {', '.join(sorted(required))}. "
                    f"Сейчас: {reader.fieldnames}"
                )

            total = 0
            valid = 0
            for idx, row in enumerate(reader, start=2):
                total += 1
                aliases_raw = (row.get("aliases") or "").strip()
                cost_raw = (row.get("cost") or "").strip().replace(",", ".")
                if not aliases_raw or not cost_raw:
                    continue
                try:
                    Decimal(cost_raw)
                except Exception:
                    self.stdout.write(self.style.WARNING(f"Строка {idx}: некорректная цена {cost_raw!r}"))
                    continue
                valid += 1

        self.stdout.write(self.style.SUCCESS(
            f"Ок: {valid} валидных строк из {total} в {file_path}"
        ))
