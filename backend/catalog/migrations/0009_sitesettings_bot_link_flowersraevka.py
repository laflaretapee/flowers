from django.db import migrations, models


def update_default_bot_link(apps, schema_editor):
    SiteSettings = apps.get_model('catalog', 'SiteSettings')
    settings = SiteSettings.objects.filter(pk=1).first()
    if not settings:
        return

    old_links = {
        '',
        'https://t.me/testikbotick_bot',
        'https://t.me/testikbotick_bot/',
        'http://t.me/testikbotick_bot',
        'http://t.me/testikbotick_bot/',
    }
    current = (settings.telegram_bot_link or '').strip()
    if current in old_links:
        settings.telegram_bot_link = 'https://t.me/flowersraevka_bot'
        settings.save(update_fields=['telegram_bot_link'])


def noop(apps, schema_editor):
    return


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0008_promo_banner_march_offer'),
    ]

    operations = [
        migrations.AlterField(
            model_name='sitesettings',
            name='telegram_bot_link',
            field=models.URLField(
                blank=True,
                default='https://t.me/flowersraevka_bot',
                verbose_name='Ссылка на Telegram бота'
            ),
        ),
        migrations.RunPython(update_default_bot_link, noop),
    ]
