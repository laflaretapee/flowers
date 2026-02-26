from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="TelegramFSMState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("bot_id", models.BigIntegerField()),
                ("chat_id", models.BigIntegerField()),
                ("user_id", models.BigIntegerField()),
                ("thread_id", models.BigIntegerField(blank=True, null=True)),
                ("destiny", models.CharField(default="default", max_length=32)),
                ("state", models.CharField(blank=True, max_length=255, null=True)),
                ("data", models.JSONField(blank=True, default=dict)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "FSM состояние Telegram",
                "verbose_name_plural": "FSM состояния Telegram",
                "indexes": [
                    models.Index(fields=["chat_id", "user_id"], name="telegram_bo_chat_id_ebe789_idx"),
                    models.Index(fields=["updated_at"], name="telegram_bo_updated_4d6bad_idx"),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="telegramfsmstate",
            constraint=models.UniqueConstraint(
                fields=("bot_id", "chat_id", "user_id", "thread_id", "destiny"),
                name="telegram_fsm_state_unique_key",
            ),
        ),
    ]
