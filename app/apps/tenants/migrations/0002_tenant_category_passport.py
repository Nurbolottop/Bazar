from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='tenant',
            name='passport_photo',
            field=models.ImageField(blank=True, null=True, upload_to='tenant_docs/', verbose_name='Копия/фото паспорта'),
        ),
        migrations.AddField(
            model_name='tenant',
            name='rental_category',
            field=models.CharField(choices=[('self', 'Сам ведёт деятельность'), ('sublease', 'Сдаёт в субаренду')], default='self', max_length=10, verbose_name='Категория аренды'),
        ),
        migrations.AddField(
            model_name='tenant',
            name='rents_from_market',
            field=models.BooleanField(default=False, verbose_name='Арендует непосредственно у рынка'),
        ),
    ]
