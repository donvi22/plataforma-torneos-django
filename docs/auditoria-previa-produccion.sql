-- Solo con autorización posterior, preferentemente sobre clon restaurado.
-- Ejecutar con psql -X -v ON_ERROR_STOP=1 -f este_archivo, usuario solo lectura.
-- No cambia filas/esquema, no muestra nombres, correos, claves ni contraseñas.
-- Requiere esquema MVP hasta partidas.0005; compatible después de 0006.
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '30s';
SET LOCAL lock_timeout = '3s';

-- AUDIT QUERY START
WITH
limites AS (
    SELECT id, CASE
        WHEN max_participantes <= 8 THEN 1 WHEN max_participantes <= 16 THEN 2
        WHEN max_participantes <= 32 THEN 3 WHEN max_participantes <= 48 THEN 4
        WHEN max_participantes <= 64 THEN 5 WHEN max_participantes <= 96 THEN 6
        WHEN max_participantes <= 128 THEN 8 END AS plazas
    FROM torneos_torneo WHERE tipo = 'PUBLICO'
),
cadenas_xp AS (
    SELECT h.*, lag(xp_posterior) OVER (PARTITION BY usuario_id ORDER BY fecha, id) AS previo,
           row_number() OVER (PARTITION BY usuario_id ORDER BY fecha DESC, id DESC) AS ultimo
    FROM usuarios_historialxp h
),
cadenas_arbitraje AS (
    SELECT h.*, lag(arbitro_nuevo_id) OVER (PARTITION BY partida_id ORDER BY fecha, id) AS previo,
           row_number() OVER (PARTITION BY partida_id ORDER BY fecha, id) AS numero,
           row_number() OVER (PARTITION BY partida_id ORDER BY fecha DESC, id DESC) AS ultimo
    FROM arbitraje_historialasignacionarbitro h
),
medios AS (
    SELECT imagen AS nombre FROM videojuegos_videojuego
    UNION ALL SELECT imagen_banner FROM torneos_torneo
    UNION ALL SELECT imagen FROM torneos_premiotorneo
)
SELECT 'bloqueante_declaraciones_duplicadas' AS indicador, count(*) AS total
FROM (SELECT partida_id, usuario_id FROM partidas_declaracionresultado
      GROUP BY partida_id, usuario_id HAVING count(*) > 1) d
UNION ALL
SELECT 'xp_aritmetica_incoherente', count(*) FROM usuarios_historialxp
WHERE xp_posterior <> xp_anterior + xp_concedida
UNION ALL
SELECT 'xp_cadena_discontinua_revisar', count(*) FROM cadenas_xp
WHERE previo IS NOT NULL AND xp_anterior <> previo
UNION ALL
SELECT 'xp_saldo_distinto_ultimo_movimiento_revisar', count(*)
FROM cadenas_xp h JOIN usuarios_usuario u ON u.id = h.usuario_id
WHERE h.ultimo = 1 AND h.xp_posterior <> u.xp_total
UNION ALL
SELECT 'xp_nivel_incompatible_revisar', count(*) FROM usuarios_usuario
WHERE nivel <> floor(sqrt(xp_total::numeric / 100)) + 1
UNION ALL
SELECT 'xp_relaciones_incoherentes', count(*)
FROM usuarios_historialxp h JOIN torneos_inscripciontorneo i ON i.id = h.inscripcion_id
JOIN torneos_clasificaciontorneo c ON c.id = h.clasificacion_id
WHERE h.usuario_id <> i.usuario_id OR h.torneo_id <> i.torneo_id
   OR c.inscripcion_id <> h.inscripcion_id OR c.torneo_id <> h.torneo_id
UNION ALL
SELECT 'xp_candidatos_sin_recompensa_revisar', count(*)
FROM torneos_clasificaciontorneo c JOIN torneos_torneo t ON t.id = c.torneo_id
JOIN torneos_inscripciontorneo i ON i.id = c.inscripcion_id
JOIN usuarios_usuario u ON u.id = i.usuario_id
WHERE t.estado = 'FINALIZADO' AND t.tipo IN ('PUBLICO', 'OFICIAL')
  AND u.is_active AND u.estado_cuenta = 'ACTIVA'
  AND NOT EXISTS (SELECT 1 FROM usuarios_historialxp h WHERE h.inscripcion_id = i.id)
UNION ALL
SELECT 'arbitraje_plazas_aceptadas_excedidas', count(*) FROM limites l
WHERE (SELECT count(*) FROM arbitraje_arbitrotorneo a WHERE a.torneo_id = l.id
       AND a.estado_invitacion = 'ACEPTADA' AND a.activo_en_torneo) > l.plazas
UNION ALL
SELECT 'arbitraje_pendientes_excedidas', count(*) FROM limites l
WHERE (SELECT count(*) FROM arbitraje_arbitrotorneo a WHERE a.torneo_id = l.id
       AND a.estado_invitacion = 'PENDIENTE') > 2 * l.plazas
UNION ALL
SELECT 'arbitraje_participantes_confirmados_activos', count(*)
FROM arbitraje_arbitrotorneo a JOIN torneos_inscripciontorneo i
ON i.torneo_id = a.torneo_id AND i.usuario_id = a.usuario_id
WHERE a.estado_invitacion = 'ACEPTADA' AND a.activo_en_torneo AND i.estado = 'CONFIRMADA'
UNION ALL
SELECT 'arbitraje_cadena_discontinua_revisar', count(*) FROM cadenas_arbitraje
WHERE numero > 1 AND arbitro_anterior_id IS DISTINCT FROM previo
UNION ALL
SELECT 'arbitraje_asignacion_distinta_ultimo_historial_revisar', count(*)
FROM cadenas_arbitraje h JOIN partidas_partida p ON p.id = h.partida_id
WHERE ultimo = 1 AND p.arbitro_asignado_id IS DISTINCT FROM h.arbitro_nuevo_id
UNION ALL
SELECT 'arbitraje_responsable_no_elegible_actual_revisar', count(*)
FROM partidas_partida p JOIN torneos_torneo t ON t.id = p.torneo_id
JOIN arbitraje_arbitrotorneo a ON a.id = p.arbitro_asignado_id
JOIN usuarios_usuario u ON u.id = a.usuario_id
WHERE t.estado IN ('PREPARADO', 'EN_CURSO')
  AND p.estado NOT IN ('FINALIZADA', 'CANCELADA')
  AND (a.torneo_id <> p.torneo_id OR a.estado_invitacion <> 'ACEPTADA'
       OR NOT a.activo_en_torneo OR NOT u.is_active OR u.estado_cuenta <> 'ACTIVA')
UNION ALL
SELECT 'checkin_arbitral_responsable_anterior_revisar', count(*)
FROM partidas_checkinpartida c JOIN partidas_partida p ON p.id = c.partida_id
JOIN torneos_torneo t ON t.id = p.torneo_id
LEFT JOIN arbitraje_arbitrotorneo a ON a.id = p.arbitro_asignado_id
WHERE c.tipo = 'ARBITRO' AND c.confirmado AND p.estado IN ('CHECK_IN', 'LISTA_PARA_COMENZAR')
  AND ((a.id IS NULL AND c.usuario_id <> t.organizador_id)
       OR (a.id IS NOT NULL AND (c.usuario_id <> a.usuario_id
           OR a.estado_invitacion <> 'ACEPTADA' OR NOT a.activo_en_torneo)))
UNION ALL
SELECT 'checkin_confirmado_sin_fecha_revisar', count(*) FROM partidas_checkinpartida
WHERE confirmado AND fecha_confirmacion IS NULL
UNION ALL
SELECT 'checkin_lista_incompleta_revisar', count(*)
FROM partidas_partida p JOIN torneos_torneo t ON t.id = p.torneo_id
LEFT JOIN arbitraje_arbitrotorneo a ON a.id = p.arbitro_asignado_id
JOIN usuarios_usuario responsable ON responsable.id = coalesce(a.usuario_id, t.organizador_id)
WHERE p.estado = 'LISTA_PARA_COMENZAR' AND t.tipo IN ('PUBLICO', 'OFICIAL') AND (
    NOT responsable.is_active OR responsable.estado_cuenta <> 'ACTIVA'
    OR (a.id IS NOT NULL AND (a.estado_invitacion <> 'ACEPTADA' OR NOT a.activo_en_torneo))
    OR (SELECT count(*) FROM partidas_participantepartida pp WHERE pp.partida_id = p.id) <> 2
    OR EXISTS (
        SELECT 1 FROM partidas_participantepartida pp
        JOIN torneos_inscripciontorneo i ON i.id = pp.inscripcion_id
        JOIN usuarios_usuario u ON u.id = i.usuario_id
        WHERE pp.partida_id = p.id AND (i.estado <> 'CONFIRMADA' OR NOT u.is_active
            OR u.estado_cuenta <> 'ACTIVA' OR NOT EXISTS (
                SELECT 1 FROM partidas_checkinpartida c WHERE c.partida_id = p.id
                AND c.usuario_id = i.usuario_id AND c.tipo = 'PARTICIPANTE' AND c.confirmado)))
    OR NOT EXISTS (SELECT 1 FROM partidas_checkinpartida c WHERE c.partida_id = p.id
        AND c.tipo = 'ARBITRO' AND c.usuario_id = responsable.id AND c.confirmado))
UNION ALL
SELECT 'resultado_estado_incoherente_revisar', count(*) FROM partidas_partida p
WHERE (p.estado = 'FINALIZADA' AND NOT EXISTS (
    SELECT 1 FROM partidas_resultadopartida r WHERE r.partida_id = p.id))
OR (p.estado <> 'FINALIZADA' AND EXISTS (
    SELECT 1 FROM partidas_resultadopartida r WHERE r.partida_id = p.id))
UNION ALL
SELECT 'bracket_avance_ganador_incoherente_revisar', count(*)
FROM partidas_partida p JOIN partidas_resultadopartida r ON r.partida_id = p.id
WHERE p.siguiente_partida_id IS NOT NULL AND r.ganador_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM partidas_participantepartida pp WHERE pp.partida_id = p.siguiente_partida_id
    AND pp.posicion = p.posicion_en_siguiente_partida AND pp.inscripcion_id = r.ganador_id)
UNION ALL
SELECT 'inventario_medios_locales_heredados', count(*) FROM medios
WHERE nombre IS NOT NULL AND nombre <> '' AND nombre NOT LIKE 'ionos_v1/%'
UNION ALL
SELECT 'inventario_medios_ionos_v1', count(*) FROM medios WHERE nombre LIKE 'ionos_v1/%'
UNION ALL
SELECT 'migracion_0006_registrada', count(*) FROM django_migrations
WHERE app = 'partidas' AND name = '0006_declaracion_unica_por_usuario'
ORDER BY indicador;
-- AUDIT QUERY END
COMMIT;
