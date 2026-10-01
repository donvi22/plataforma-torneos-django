# Desarrollo: XP y niveles

La XP se concede únicamente desde la clasificación final de torneos públicos u oficiales. Los torneos privados no conceden XP.

La recompensa usa la base por posición, el factor de tamaño `1 + log2(participantes) / 10`, el coeficiente de dificultad configurado o `1.0` si está vacío, y el multiplicador especial solo para torneos oficiales autorizados. El resultado se redondea al entero más próximo con `ROUND_HALF_UP`.

Los niveles siguen el umbral acumulado `100 * (nivel - 1)^2`. El cálculo de dificultad automático permanece pendiente; mientras tanto, un coeficiente vacío es neutro.

El cierre normal de un torneo público u oficial concede XP automáticamente dentro del procedimiento de cierre. Para un torneo ya finalizado antes de esta fase, usa el comando una sola vez para el identificador revisado:

```powershell
.\.venv\Scripts\python.exe manage.py conceder_xp_torneo --torneo 12
```

El comando verifica que el torneo está finalizado, que su clasificación es completa y coherente, y que no es privado. Es idempotente: si ya existen movimientos históricos equivalentes, no duplica XP.

Cada usuario ve sus movimientos en `/perfil/xp/`; el historial de otra persona no tiene ruta pública.
