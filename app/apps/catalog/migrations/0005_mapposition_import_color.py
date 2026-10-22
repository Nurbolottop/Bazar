from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0004_spot_building_nullable'),
    ]

    operations = [
        migrations.AddField(
            model_name='mapposition',
            name='import_color',
            field=models.CharField(
                blank=True, default='', max_length=9,
                help_text='HEX-заливка из исходной схемы; пусто — цвет по статусу оплаты',
                verbose_name='Цвет из импорта'),
        ),
    ]
