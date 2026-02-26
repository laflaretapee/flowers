from django.db import models


class TelegramFSMState(models.Model):
    """Persistent FSM state storage for aiogram webhook mode."""

    bot_id = models.BigIntegerField()
    chat_id = models.BigIntegerField()
    user_id = models.BigIntegerField()
    thread_id = models.BigIntegerField(null=True, blank=True)
    destiny = models.CharField(max_length=32, default="default")
    state = models.CharField(max_length=255, blank=True, null=True)
    data = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "FSM состояние Telegram"
        verbose_name_plural = "FSM состояния Telegram"
        constraints = [
            models.UniqueConstraint(
                fields=["bot_id", "chat_id", "user_id", "thread_id", "destiny"],
                name="telegram_fsm_state_unique_key",
            )
        ]
        indexes = [
            models.Index(fields=["chat_id", "user_id"]),
            models.Index(fields=["updated_at"]),
        ]

    def __str__(self) -> str:
        return f"FSM {self.chat_id}:{self.user_id}:{self.state or '-'}"
