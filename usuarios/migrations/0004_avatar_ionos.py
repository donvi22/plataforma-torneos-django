
from django.db import migrations, models

import usuarios.storage_ionos


class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0003_historialkarma'),
    ]

    operations = [
        migrations.AlterField(
            model_name='usuario',
            name='avatar',
            field=models.ImageField(
                upload_to='avatars/',
                storage=usuarios.storage_ionos.IonosAvatarStorage(),
                blank=True,
                null=True,
            ),
        ),
    ]
