from django.db import migrations, models
from django.db.models import Count


def comprobar_duplicados(apps, schema_editor):
    Declaracion = apps.get_model('partidas', 'DeclaracionResultado')
    duplicados = (Declaracion.objects.using(schema_editor.connection.alias)
                  .values('partida_id', 'usuario_id').annotate(total=Count('pk'))
                  .filter(total__gt=1))
    if duplicados.exists():
        raise RuntimeError(
            'Existen declaraciones duplicadas por partida y usuario. '
            'La migración se detiene sin borrar ni elegir declaraciones. '
            'Requiere revisión y resolución autorizada de los datos históricos.'
        )


class Migration(migrations.Migration):
    dependencies = [('partidas', '0005_lobby_y_checkins_invalidados')]
    operations = [
        migrations.RunPython(comprobar_duplicados, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='declaracionresultado',
            constraint=models.UniqueConstraint(
                fields=('partida', 'usuario'), name='unique_declaracion_partida_usuario',
            ),
        ),
    ]
