import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0003_mapzone'),
    ]

    operations = [
        migrations.AlterField(
            model_name='spot',
            name='building',
            field=models.ForeignKey(
                blank=True,
                help_text='Пусто — раздел удалён, место осталось без раздела',
                null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='spots', to='catalog.building',
                verbose_name='Раздел рынка'),
        ),
    ]
