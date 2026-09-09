from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sys_core', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='systemsettings',
            name='faq_ru',
            field=models.TextField(blank=True, default='', help_text='Первая строка блока — вопрос, дальше ответ; блоки через пустую строку', verbose_name='Частые вопросы (рус.)'),
        ),
        migrations.AddField(
            model_name='systemsettings',
            name='faq_ky',
            field=models.TextField(blank=True, default='', verbose_name='Частые вопросы (кырг.)'),
        ),
    ]
