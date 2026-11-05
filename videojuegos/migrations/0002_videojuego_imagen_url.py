
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('videojuegos', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='videojuego',
            name='imagen_url',
            field=models.URLField(
                blank=True,
                default='',
                max_length=500,
                verbose_name='URL de imagen (IONOS)',
            ),
        ),
    ]
