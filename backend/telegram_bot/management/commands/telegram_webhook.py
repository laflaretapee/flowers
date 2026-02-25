import json

from django.core.management.base import BaseCommand, CommandError

from telegram_bot.webhook import build_webhook_url, delete_webhook, get_webhook_info, setup_webhook_url


class Command(BaseCommand):
    help = "Manage Telegram webhook (set/info/delete). Polling mode is disabled."

    def add_arguments(self, parser):
        parser.add_argument('action', choices=['set', 'info', 'delete'])
        parser.add_argument(
            '--drop-pending-updates',
            action='store_true',
            help='Drop pending updates on Telegram while setting/deleting webhook.',
        )
        parser.add_argument(
            '--strict',
            action='store_true',
            help='Return non-zero exit code on any webhook setup error.',
        )

    def handle(self, *args, **options):
        action = options['action']
        strict = bool(options['strict'])
        drop_pending_updates = bool(options['drop_pending_updates'])

        try:
            if action == 'set':
                webhook_url = build_webhook_url()
                if not webhook_url:
                    message = (
                        "WEBHOOK_HOST is not configured. Set WEBHOOK_HOST=https://your-domain "
                        "before running webhook setup."
                    )
                    if strict:
                        raise CommandError(message)
                    self.stderr.write(self.style.WARNING(message))
                    return

                ok, data = setup_webhook_url(drop_pending_updates=drop_pending_updates)
                if ok:
                    self.stdout.write(self.style.SUCCESS(f"Webhook set to: {webhook_url}"))
                else:
                    message = f"Failed to set webhook: {data}"
                    if strict:
                        raise CommandError(message)
                    self.stderr.write(self.style.WARNING(message))
                self.stdout.write(json.dumps(data, ensure_ascii=False, indent=2))
                return

            if action == 'delete':
                ok, data = delete_webhook(drop_pending_updates=drop_pending_updates)
                if ok:
                    self.stdout.write(self.style.SUCCESS("Webhook deleted"))
                else:
                    message = f"Failed to delete webhook: {data}"
                    if strict:
                        raise CommandError(message)
                    self.stderr.write(self.style.WARNING(message))
                self.stdout.write(json.dumps(data, ensure_ascii=False, indent=2))
                return

            ok, data = get_webhook_info()
            if ok:
                self.stdout.write(self.style.SUCCESS("Webhook info fetched"))
            else:
                message = f"Failed to fetch webhook info: {data}"
                if strict:
                    raise CommandError(message)
                self.stderr.write(self.style.WARNING(message))
            self.stdout.write(json.dumps(data, ensure_ascii=False, indent=2))

        except Exception as exc:
            if strict:
                raise CommandError(str(exc)) from exc
            self.stderr.write(self.style.WARNING(f"Webhook command skipped: {exc}"))
