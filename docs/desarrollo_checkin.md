# Desarrollo: calendario y check-in

El único procesador temporal local es el comando de calendario. Procesa la apertura y el cierre de inscripciones, prepara el bracket cuando corresponde y abre los check-ins de partidas elegibles.

```powershell
.\.venv\Scripts\python.exe manage.py procesar_calendario
.\.venv\Scripts\python.exe manage.py procesar_calendario --loop --interval 30
```

No se inicia ningún procesador desde `runserver`. En desarrollo, ejecuta una vez el comando después de crear o publicar un torneo y, mientras se prueba el flujo temporal, deja el segundo comando en otra terminal.

Flujo público de desarrollo:

1. Publica el torneo con sus fechas de inscripciones.
2. Ejecuta `procesar_calendario` para abrir o cerrar las inscripciones.
3. Cuando las inscripciones estén cerradas definitivamente y el torneo esté completo, el mismo comando prepara el bracket.
4. El mismo comando abre el check-in de las partidas de primera ronda públicas. Las rondas posteriores esperan a tener dos rivales y a que termine `descanso_entre_partidas_min`.

Para confirmar una cuenta ficticia individual mediante el servicio real, activa explícitamente el entorno local y usa su `username` concreto. El comando no admite cuentas reales ni administradoras, y no confirma árbitros automáticamente.

```powershell
$env:TORNEO_DEV_LOCAL='1'
.\.venv\Scripts\python.exe manage.py confirmar_checkin_prueba --partida 1 --usuario bot_prueba_abcd1234
```

En el navegador, un participante real debe iniciar sesión y abrir `/partidas/mias/`, seleccionar `Ver partida` y usar `Confirmar disponibilidad` cuando la ventana esté abierta. Para el torneo de prueba 12, inicia el procesador con el primer comando, abre la partida real desde `http://127.0.0.1:8000/partidas/mias/` y confirma desde esa ficha. No hace falta ni se debe editar directamente el estado o las fechas de la partida.
