from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0010_order_service_workflow"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="delivery_price",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10, verbose_name="Стоимость доставки"),
        ),
        migrations.AddField(
            model_name="order",
            name="items_subtotal",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10, verbose_name="Сумма товаров"),
        ),
        migrations.AddField(
            model_name="order",
            name="payment_method",
            field=models.CharField(
                choices=[("transfer", "Перевод"), ("online", "Онлайн")],
                default="transfer",
                max_length=20,
                verbose_name="Способ оплаты",
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="transfer_details",
            field=models.CharField(blank=True, max_length=255, verbose_name="Реквизиты перевода"),
        ),
    ]
