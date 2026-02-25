from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0006_order_ready_photo_botadmin'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='payment_status',
            field=models.CharField(choices=[('not_paid', 'Не оплачен'), ('pending', 'Ожидает оплаты'), ('succeeded', 'Оплачен'), ('canceled', 'Отменен')], default='not_paid', max_length=20, verbose_name='Статус оплаты'),
        ),
        migrations.AddField(
            model_name='order',
            name='payment_id',
            field=models.CharField(blank=True, max_length=100, verbose_name='ID платежа YooKassa'),
        ),
        migrations.AddField(
            model_name='order',
            name='payment_url',
            field=models.URLField(blank=True, verbose_name='Ссылка на оплату'),
        ),
        migrations.AddField(
            model_name='order',
            name='paid_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Дата оплаты'),
        ),
    ]
