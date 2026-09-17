-- Migración: salud de las cámaras
-- ================================================================
--
-- Añade el seguimiento de salud de cámara al esquema existente.
--
-- Para bases NUEVAS no hace falta: `schema.sql` ya lo incluye.
-- Este archivo es para bases que YA existen (como la tuya).
--
-- Ejecutar en Supabase → SQL Editor. Es idempotente: se puede
-- ejecutar varias veces sin efectos secundarios.
--
-- Qué habilita:
--   • POST /api/concentrador/estado  guarda `camara_salud` y `camara_motivo`
--   • GET  /api/camaras              consulta la salud de todas las cámaras
--
-- Sin esta migración, el heartbeat sigue funcionando pero el INSERT
-- fallaría al no existir la columna `alertas`.

-- ── workers: salud de la cámara ────────────────────────────────────
-- ok | congelada | sin_senal | worker_caido
ALTER TABLE workers
    ADD COLUMN IF NOT EXISTS camara_salud  TEXT,
    ADD COLUMN IF NOT EXISTS camara_motivo TEXT;

COMMENT ON COLUMN workers.camara_salud IS
    'ok | congelada | sin_senal | worker_caido (del último heartbeat)';
COMMENT ON COLUMN workers.camara_motivo IS
    'Detalle legible del problema; vacío si la cámara está ok';

-- ── heartbeats: alertas del ciclo ──────────────────────────────────
-- Lista de cámaras con problemas, ya filtrada por el concentrador.
-- Es un atajo para que un dashboard no tenga que recorrer `workers`.
ALTER TABLE heartbeats
    ADD COLUMN IF NOT EXISTS alertas JSONB DEFAULT '[]'::jsonb;

COMMENT ON COLUMN heartbeats.alertas IS
    'Cámaras con salud != ok en este heartbeat: [{worker_id, salud, motivo}]';

-- ── Índice para la consulta de salud ───────────────────────────────
-- `GET /api/camaras` busca el heartbeat más reciente de cada
-- concentrador: este índice evita el scan completo.
CREATE INDEX IF NOT EXISTS idx_heartbeats_conc_fecha
    ON heartbeats (concentrador_id, timestamp DESC);

-- ── Permisos (obligatorio en Supabase) ─────────────────────────────
-- Sin el GRANT, el rol service_role no puede leer/escribir las columnas
-- nuevas y la API falla con "permission denied".
GRANT SELECT, INSERT, UPDATE, DELETE ON workers     TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON heartbeats  TO service_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO service_role;

-- ── Verificación ───────────────────────────────────────────────────
-- Debe devolver 2 columnas en `workers`, 1 en `heartbeats` y 1 índice:
--
--   SELECT table_name, column_name, data_type
--   FROM information_schema.columns
--   WHERE (table_name = 'workers'    AND column_name LIKE 'camara_%')
--      OR (table_name = 'heartbeats' AND column_name = 'alertas');
--
--   SELECT indexname FROM pg_indexes
--   WHERE indexname = 'idx_heartbeats_conc_fecha';
