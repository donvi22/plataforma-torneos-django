# Karma y reputacion

El Karma expresa fiabilidad y cumplimiento de responsabilidades; no recompensa habilidad ni resultados competitivos. Los importes se centralizan en `usuarios.karma.KARMA_RECOMPENSAS`:

- Participación completada en torneo público u oficial: +5.
- Declaración coincidente con el resultado oficial: +1 por declaración.
- Árbitro asignado que valida correctamente: +5 por partida.
- Organización completada de torneo público: +10.

Los torneos privados no conceden Karma automático. Un organizador de torneo oficial no recibe recompensa de organización automática. El saldo tiene suelo cero y no tiene máximo. Una sanción solicitada por encima del saldo solo aplica el Karma disponible; el historial conserva tanto el cambio solicitado como el aplicado.

Las concesiones usan `usuarios.karma.registrar_movimiento_karma` o sus procesadores de eventos. La clave de idempotencia evita duplicados. Los movimientos no se editan ni eliminan; el CRUD de `HistorialKarma` en Django Admin es de solo lectura. Los ajustes y sanciones requieren un administrador autorizado y un motivo.

Para completar únicamente los movimientos pendientes de un torneo finalizado concreto:

```powershell
python manage.py conceder_karma_torneo --torneo <ID_TORNEO>
```

El comando rechaza torneos sin finalizar y torneos privados. Es seguro repetirlo; la segunda ejecución no crea movimientos duplicados. No procesa otros torneos históricos automáticamente.