from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0012_transferpaymenttemplate"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesettings",
            name="promo_discount_percent",
            field=models.PositiveIntegerField(
                default=10,
                help_text="Используется только если акция включена.",
                verbose_name="Размер скидки, %",
            ),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="promo_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Включает скидку в боте и промо-баннер на сайте.",
                verbose_name="Акция со скидкой включена",
            ),
        ),
    ]
