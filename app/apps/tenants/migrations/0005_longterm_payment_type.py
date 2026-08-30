from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0004_tenantspot_rental_term'),
    ]

    operations = [
        migrations.RemoveField(model_name='tenantspot', name='rental_term'),
        migrations.AddField(
            model_name='tenantspot',
            name='is_long_term',
            field=models.BooleanField(default=False, verbose_name='Долгосрочная аренда'),
        ),
        migrations.AddField(
            model_name='tenantspot',
            name='contract_until',
            field=models.DateField(blank=True, null=True, verbose_name='Договор до'),
        ),
        migrations.AddField(
            model_name='tenantspot',
            name='payment_type',
            field=models.CharField(blank=True, choices=[('full', 'Полная оплата'), ('partial', 'Частичная оплата')], default='', max_length=10, verbose_name='Оплата'),
        ),
    ]
