# Desarrollo: declaración y validación de resultados

Los jugadores declaran resultados desde la ficha de una partida iniciada. El marcador usa el formato `puntos-puntos`, por ejemplo `2-1`; ambos valores son texto competitivo del juego y no se interpreta su significado fuera de ese formato.

Un jugador autenticado abre `/partidas/mias/`, selecciona `Ver partida` y envía el formulario `Declarar resultado`. La primera declaración deja la partida en `PENDIENTE_VALIDACION`; el segundo jugador todavía puede declarar. Las declaraciones no crean resultado oficial ni avanzan el bracket.

El árbitro activo asignado a esa partida, el organizador o un administrador autorizado ven ambas declaraciones y pueden validar desde la misma ficha. Si no coinciden, la validación exige un motivo explícito. La validación oficial usa el servicio central y avanza al ganador; el procesador temporal es el único responsable de abrir después el check-in de la siguiente ronda.

Para simular una declaración individual de una cuenta ficticia local, activa explícitamente el entorno local. El comando solo admite el patrón protegido de cuentas creadas por `crear_jugadores_prueba` y comprueba que la identidad pertenece a la partida.

```powershell
$env:TORNEO_DEV_LOCAL='1'
.\.venv\Scripts\python.exe manage.py declarar_resultado_prueba --partida 3 --usuario bot_prueba_b06560c0a761 --resultado 2-1 --ganador 7
```

No usa contraseñas, no crea resultados oficiales ni cambia fechas, permisos o estados manualmente.
