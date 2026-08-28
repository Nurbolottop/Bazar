import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='MarketPlan',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Обновлено')),
                ('name', models.CharField(default='Основной план', max_length=100, verbose_name='Название')),
                ('background', models.ImageField(blank=True, null=True, upload_to='market_plans/', verbose_name='Подложка (чертёж)')),
                ('width', models.FloatField(default=2000, verbose_name='Ширина холста')),
                ('height', models.FloatField(default=1200, verbose_name='Высота холста')),
                ('is_active', models.BooleanField(default=True, verbose_name='Действует')),
            ],
            options={
                'verbose_name': 'План рынка',
                'verbose_name_plural': 'Планы рынка',
            },
        ),
        migrations.CreateModel(
            name='MapPosition',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Обновлено')),
                ('x', models.FloatField(verbose_name='X')),
                ('y', models.FloatField(verbose_name='Y')),
                ('width', models.FloatField(verbose_name='Ширина')),
                ('height', models.FloatField(verbose_name='Высота')),
                ('rotation', models.FloatField(default=0, verbose_name='Поворот')),
                ('shape', models.CharField(default='rect', max_length=20, verbose_name='Форма')),
                ('plan', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='positions', to='catalog.marketplan', verbose_name='План')),
                ('spot', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='map_position', to='catalog.spot', verbose_name='Торговое место')),
            ],
            options={
                'verbose_name': 'Позиция на карте',
                'verbose_name_plural': 'Позиции на карте',
            },
        ),
        migrations.AddConstraint(
            model_name='mapposition',
            constraint=models.CheckConstraint(
                check=models.Q(('width__gt', 0), ('height__gt', 0)),
                name='mapposition_size_positive'),
        ),
    ]
