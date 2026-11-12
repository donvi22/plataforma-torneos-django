# Preparacion para Railway y PostgreSQL

Este documento prepara un despliegue futuro. No se ha desplegado, asociado un
dominio ni cambiado DNS. El codigo de negocio y las migraciones no se modifican.

## Desarrollo y demo

Sin variables adicionales se mantienen `DEBUG=True`, `db.sqlite3`, media local
y `python manage.py runserver`. `TORNEO_DEMO=1` selecciona `db_demo.sqlite3` como
antes. No configures variables PG en esa consola si quieres usar SQLite.
La combinacion demo/PostgreSQL se rechaza para proteger las dos bases locales.

El middleware WhiteNoise esta detras de SecurityMiddleware, pero el storage
de desarrollo sigue siendo StaticFilesStorage: no necesitas collectstatic para
trabajar con runserver. STATIC_URL y MEDIA_URL conservan sus valores.

`.env.example` es documentacion, no un archivo cargado automaticamente. No se
ha anadido python-dotenv. Exporta las variables en la consola o configuralas
en el panel de Railway; nunca incluyas sus valores reales en Git.

## Runtime y dependencias

`.python-version` fija Python 3.14.7. Railway debe usar un entorno Linux con
esa version. Se mantienen Django 6.1.1 y Pillow 12.3.0; se anaden Gunicorn
26.2.0, WhiteNoise 6.12.0 y psycopg[binary] 3.3.8.
El builder debe instalar `requirements.txt`; no hacen falta librerias de
PostgreSQL del sistema al usar el extra binary de psycopg.
Gunicorn no se ejecuta nativamente en Windows: alli se conserva runserver.

## Variables de produccion

| Variable | Configuracion |
| --- | --- |
| DJANGO_DEBUG | `False`; admite true/false, 1/0, on/off y yes/no sin distinguir mayusculas |
| DJANGO_SECRET_KEY | Clave privada aleatoria de al menos 50 caracteres; no usar el fallback ni ejemplos |
| DJANGO_ALLOWED_HOSTS | Hosts reales separados por comas, sin esquema ni asteriscos |
| DJANGO_CSRF_TRUSTED_ORIGINS | Origenes HTTPS reales separados por comas |
| PGHOST | Referencia a PGHOST del servicio PostgreSQL |
| PGPORT | Referencia a PGPORT del servicio; por defecto 5432 |
| PGDATABASE | Referencia a PGDATABASE del servicio |
| PGUSER | Referencia a PGUSER del servicio |
| PGPASSWORD | Referencia secreta a PGPASSWORD del servicio |
| PGSSLMODE | `require` por defecto; ajustar solo segun la configuracion TLS del proveedor |
| TORNEO_DEMO | Omitir o `0`; nunca `1` en produccion |
| TORNEO_DEV_LOCAL | Omitir o `0` |
| PORT | La proporciona Railway; no hay que fijarla manualmente |
| DJANGO_EMAIL_HOST / DJANGO_EMAIL_PORT | Servidor SMTP y puerto; por defecto localhost y 587, sin entrega configurada |
| DJANGO_EMAIL_HOST_USER / DJANGO_EMAIL_HOST_PASSWORD | Credenciales del proveedor SMTP, solo en Variables |
| DJANGO_EMAIL_USE_TLS | STARTTLS, True por defecto |
| DJANGO_DEFAULT_FROM_EMAIL | Remitente autorizado por el proveedor |

Usa referencias al servicio PostgreSQL real en Railway, no copies contrasenas
en archivos del repositorio. Se usan PG* directamente: DATABASE_URL no se lee.
Para hosts propios se podran configurar `rankedkingdom.com` y
`www.rankedkingdom.com`; sus origenes seran `https://rankedkingdom.com` y
`https://www.rankedkingdom.com`. Para la primera validacion, anade el host
temporal que Railway asigne realmente y su origen HTTPS. No se presupone
ningun subdominio temporal ni se requiere cambiar DNS para probarlo.

Con DEBUG=False la clave y los hosts son obligatorios; PostgreSQL tambien.
Las configuraciones incompletas fallan claramente en vez de utilizar SQLite.
CONN_MAX_AGE es 60 segundos y se comprueba la salud de conexiones reutilizadas.

## Build, pre-deploy y start

Configura tres pasos distintos en el servicio de aplicacion de Railway:

**Build Command**

```bash
python -m config.railway build
```

Valida que se esta usando configuracion de produccion PostgreSQL y ejecuta
`python manage.py collectstatic --noinput`. No conecta a la base ni migra.
Las variables de configuracion deben estar disponibles durante el build.
Los archivos de staticfiles deben conservarse en la imagen final del builder.

**Pre-deploy Command**

```bash
python -m config.railway predeploy
```

Vuelve a validar el entorno y ejecuta `python manage.py migrate --noinput`.
Este paso debe ejecutarse donde PostgreSQL sea accesible, despues del build y
antes de iniciar la nueva version. No lo pongas en el build ni en el start de
cada replica. Revisa las migraciones y prepara backups antes de aplicarlas;
si una migracion falla, no continues el despliegue.

**Start Command**

```bash
gunicorn config.wsgi:application --bind 0.0.0.0:$PORT
```

El mismo comando queda en Procfile. No contiene migraciones, seed_demo ni
runserver. No exportes ni cargues la base local/demo en produccion.

## Estaticos y media

STATIC_ROOT es `staticfiles/`, excluido de Git. En produccion, WhiteNoise usa
CompressedManifestStaticFilesStorage para versiones con hash, compresion y
cache. No suprimas los errores de manifiesto: corrige las referencias rotas
antes de desplegar.

Los avatares conservan su almacenamiento IONOS. Las nuevas imágenes de torneos,
videojuegos y premios usan PersistentMediaStorage: IONOS en producción y disco
local en desarrollo. Las referencias locales antiguas conservan MEDIA_ROOT y
MEDIA_URL; no se migran ni se supone que existan en el servidor remoto.
WhiteNoise no sirve multimedia de usuarios. No se ha añadido otro proveedor.

Los avatares mantienen IONOS_SFTP_USERNAME / IONOS_SFTP_PASSWORD. Los nuevos
medios requieren IONOS_MEDIA_SFTP_USERNAME / IONOS_MEDIA_SFTP_PASSWORD de un
acceso independiente, sin fallback al de avatares. No cambiar el directorio de
la cuenta existente.

Antes de integrar este cambio, verificar y configurar la raíz SFTP absoluta y
su correspondencia pública. Sin ese mapeo las subidas nuevas fallan de forma
explícita, sin fallback al disco efímero. Ver el inventario, la política de
compatibilidad y los pasos de integración NO ejecutados en
[almacenamiento-multimedia.md](almacenamiento-multimedia.md).

## HTTPS y primera comprobacion

Con DEBUG=False se activan SESSION_COOKIE_SECURE, CSRF_COOKIE_SECURE y
SECURE_SSL_REDIRECT. Django reconoce HTTPS con X-Forwarded-Proto del proxy de
Railway. El proceso Gunicorn debe estar detras de ese proxy: no expongas un
servidor directo que acepte ese header de clientes no confiables.

HSTS permanece en cero; tampoco se activa preload ni se fuerzan subdominios.
`manage.py check --deploy` puede advertir security.W004 por esta decision
deliberada. Considera HSTS solo despues de validar HTTPS y dominios; no se
silencia la advertencia.

Antes de la primera publicacion, verifica acceso por HTTPS, login/CSRF,
estaticos y media. Configura tambien backups y el correo real si vas a usarlo:
en local sigue siendo de consola; en produccion es SMTP configurable mediante
MAILERS/OPTIONS, como requiere Django 6.1. El valor localhost es solo un
predeterminado sin entrega configurada: proporciona un servidor, credenciales
y remitente reales antes de enviar correo. No se ha conectado ningun proveedor.
La preparacion local no sustituye una prueba de conexion, migraciones y
arranque con PostgreSQL real en el entorno Linux de Railway.