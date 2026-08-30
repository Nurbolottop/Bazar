from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0003_category_to_tenantspot'),
    ]

    operations = [
        migrations.AddField(
            model_name='tenantspot',
            name='rental_term',
            field=models.CharField(choices=[('long', 'Долгосрочная аренда'), ('partial', 'Частичная аренда')], default='long', max_length=10, verbose_name='Вид аренды'),
        ),
    ]
