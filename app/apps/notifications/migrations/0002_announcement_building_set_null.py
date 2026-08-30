import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0001_initial'),
        ('catalog', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='announcement',
            name='building',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to='catalog.building', verbose_name='Корпус'),
        ),
    ]
