# Proyecto Torneo

Aplicacion web para organizar torneos competitivos, desde las inscripciones y el bracket hasta la validacion de resultados y la progresion de los jugadores.

## Tecnologias

- Python 3.12 o superior
- Django 6.1.1
- Pillow 12.3.0 para imagenes y avatares
- SQLite para desarrollo local

## Funcionalidades

- Cuentas, perfiles de jugador y perfiles por videojuego.
- Catalogo de videojuegos y formatos competitivos.
- Torneos publicos, privados y oficiales, con inscripciones y clasificacion.
- Brackets, check-in de partidas y validacion de resultados.
- Asignacion de arbitros y notificaciones.
- Recompensas de XP, niveles e historial de progresion.
- Karma de fiabilidad, con movimientos historicos y sanciones administrativas confirmadas; consulta [docs/desarrollo_karma.md](docs/desarrollo_karma.md).

## Instalacion en Windows

Instala Python 3.12 o superior y abre PowerShell en la carpeta del proyecto. Crea y activa un entorno virtual:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Instala las dependencias declaradas:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Aplica las migraciones y crea una cuenta administrativa:

```powershell
python manage.py migrate
python manage.py createsuperuser
```

Inicia el servidor de desarrollo:

```powershell
python manage.py runserver
```

Abre `http://127.0.0.1:8000/`. Para el admin, visita `/admin/`.

SQLite y los archivos de `media/` son datos locales de desarrollo y se excluyen de Git. No se debe subir la base de datos local.

## Pruebas

Ejecuta los checks y la suite completa:

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test --parallel 4
```

## Comandos de datos ficticios

Los comandos `crear_jugadores_prueba`, `confirmar_checkin_prueba` y `declarar_resultado_prueba` son herramientas exclusivas para desarrollo local. Cada uno exige `DEBUG=True` y `TORNEO_DEV_LOCAL=1`; solo operan con cuentas ficticias creadas para pruebas.

Activa la variable en la sesion actual de PowerShell:

```powershell
$env:TORNEO_DEV_LOCAL = '1'
```

Ejemplos con identificadores ficticios:

```powershell
python manage.py crear_jugadores_prueba --cantidad 8
python manage.py confirmar_checkin_prueba --partida <ID_PARTIDA> --usuario bot_prueba_<SUFIJO>
python manage.py declarar_resultado_prueba --partida <ID_PARTIDA> --usuario bot_prueba_<SUFIJO> --resultado 2-1 --ganador <ID_INSCRIPCION>
```

Al terminar, desactiva la variable:

```powershell
Remove-Item Env:TORNEO_DEV_LOCAL
```

## Configuracion y seguridad

La clave de Django se lee de `DJANGO_SECRET_KEY`. Si no se define, se usa un valor publico destinado unicamente al desarrollo local. Configura una clave privada mediante el entorno o un gestor de secretos antes de cualquier despliegue; no guardes claves, contrasenas, tokens ni archivos `.env` en el repositorio.

La configuracion actual esta pensada para desarrollo: usa SQLite y `DEBUG=True`. No publiques el servicio con esa configuracion.