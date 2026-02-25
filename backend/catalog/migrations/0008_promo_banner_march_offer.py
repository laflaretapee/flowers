from django.db import migrations, models


def apply_march_offer(apps, schema_editor):
    PromoBanner = apps.get_model('catalog', 'PromoBanner')
    promo = PromoBanner.objects.filter(pk=1).first()
    if not promo:
        return

    old_title = 'Скидка 10% за подписку!'
    old_text = 'Подпишитесь на нашу группу через бота и получите скидку на первый заказ'
    old_button_text = 'Подписаться'

    should_update = (
        promo.title == old_title
        and promo.text == old_text
        and (promo.button_text == old_button_text or not promo.button_text)
    )
    if not should_update:
        return

    promo.icon = '🌷'
    promo.title = 'Предзаказ тюльпанов к 8 марта 2026'
    promo.text = 'Оформите заказ заранее и получите скидку 10% на праздничные букеты.'
    promo.button_text = 'Предзаказать'
    promo.button_link = promo.button_link or 'catalog.html'
    promo.save(update_fields=['icon', 'title', 'text', 'button_text', 'button_link'])


def noop(apps, schema_editor):
    return


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0007_order_payment_fields'),
    ]

    operations = [
        migrations.AlterField(
            model_name='promobanner',
            name='icon',
            field=models.CharField(default='🌷', max_length=10, verbose_name='Иконка (emoji)'),
        ),
        migrations.AlterField(
            model_name='promobanner',
            name='title',
            field=models.CharField(default='Предзаказ тюльпанов к 8 марта 2026', max_length=200, verbose_name='Заголовок'),
        ),
        migrations.AlterField(
            model_name='promobanner',
            name='text',
            field=models.CharField(
                default='Оформите заказ заранее и получите скидку 10% на праздничные букеты.',
                max_length=300,
                verbose_name='Текст',
            ),
        ),
        migrations.AlterField(
            model_name='promobanner',
            name='button_text',
            field=models.CharField(default='Предзаказать', max_length=100, verbose_name='Текст кнопки'),
        ),
        migrations.AlterField(
            model_name='promobanner',
            name='button_link',
            field=models.CharField(blank=True, default='catalog.html', max_length=200, verbose_name='Ссылка кнопки'),
        ),
        migrations.RunPython(apply_march_offer, noop),
    ]
