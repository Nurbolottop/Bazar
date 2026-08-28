import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0002_marketplan_mapposition'),
    ]

    operations = [
        migrations.CreateModel(
            name='MapZone',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Обновлено')),
                ('x', models.FloatField(verbose_name='X')),
                ('y', models.FloatField(verbose_name='Y')),
                ('width', models.FloatField(verbose_name='Ширина')),
                ('height', models.FloatField(verbose_name='Высота')),
                ('building', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='map_zone', to='catalog.building', verbose_name='Раздел рынка')),
                ('plan', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='zones', to='catalog.marketplan', verbose_name='План')),
            ],
            options={
                'verbose_name': 'Контур раздела на карте',
                'verbose_name_plural': 'Контуры разделов на карте',
            },
        ),
        migrations.AddConstraint(
            model_name='mapzone',
            constraint=models.CheckConstraint(
                check=models.Q(('width__gte', 100), ('height__gte', 100)),
                name='mapzone_min_size'),
        ),
    ]
