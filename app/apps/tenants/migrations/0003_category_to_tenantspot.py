from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0002_tenant_category_passport'),
    ]

    operations = [
        migrations.RemoveField(model_name='tenant', name='rental_category'),
        migrations.RemoveField(model_name='tenant', name='rents_from_market'),
        migrations.AddField(
            model_name='tenantspot',
            name='rental_category',
            field=models.CharField(choices=[('self', 'Сам ведёт деятельность'), ('sublease', 'Сдаёт в субаренду')], default='self', max_length=10, verbose_name='Категория аренды'),
        ),
        migrations.AddField(
            model_name='tenantspot',
            name='rents_from_market',
            field=models.BooleanField(default=False, verbose_name='Арендует непосредственно у рынка'),
        ),
    ]
