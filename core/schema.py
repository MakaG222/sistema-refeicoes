"""Schema SQL da base de dados — fonte de verdade para DDL."""

SCHEMA_SQL = r"""
PRAGMA foreign_keys=ON;

DROP TRIGGER IF EXISTS refeicoes_no_holiday_i;
DROP TRIGGER IF EXISTS refeicoes_no_holiday_u;
DROP TRIGGER IF EXISTS capacidade_check_pa_i;
DROP TRIGGER IF EXISTS capacidade_check_pa_u;
DROP TRIGGER IF EXISTS capacidade_check_lanche_i;
DROP TRIGGER IF EXISTS capacidade_check_lanche_u;
DROP TRIGGER IF EXISTS capacidade_check_almoco_i;
DROP TRIGGER IF EXISTS capacidade_check_almoco_u;
DROP TRIGGER IF EXISTS capacidade_check_jantar_i;
DROP TRIGGER IF EXISTS capacidade_check_jantar_u;

-- -----------------------------------------------------------------------
-- TABELAS PRINCIPAIS (criadas se não existirem)
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS utilizadores (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  NII                  TEXT UNIQUE NOT NULL,
  NI                   TEXT UNIQUE NOT NULL,
  Nome_completo        TEXT NOT NULL,
  Palavra_chave        TEXT NOT NULL,
  ano                  INTEGER NOT NULL,
  perfil               TEXT DEFAULT 'aluno',
  locked_until         TEXT,
  must_change_password INTEGER DEFAULT 0,
  password_updated_at  TEXT,
  is_active            INTEGER NOT NULL DEFAULT 1,
  email                TEXT,
  telemovel            TEXT,
  dieta_padrao         TEXT NOT NULL DEFAULT 'Normal'
                         CHECK(dieta_padrao IN ('Normal','Vegetariano','Dieta')),
  data_criacao         TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS refeicoes (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  utilizador_id      INTEGER NOT NULL
                       REFERENCES utilizadores(id) ON DELETE CASCADE,
  data               TEXT NOT NULL,
  pequeno_almoco     BOOLEAN DEFAULT 0,
  lanche             BOOLEAN DEFAULT 0,
  almoco             TEXT CHECK(almoco IN ('Normal','Vegetariano','Dieta')),
  jantar_tipo        TEXT CHECK(jantar_tipo IN ('Normal','Vegetariano','Dieta')),
  jantar_sai_unidade BOOLEAN DEFAULT 0,
  almoco_estufa      BOOLEAN DEFAULT 0,
  jantar_estufa      BOOLEAN DEFAULT 0,
  UNIQUE(utilizador_id, data)
);

CREATE TABLE IF NOT EXISTS login_eventos (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  nii       TEXT NOT NULL,
  sucesso   INTEGER NOT NULL,  -- 0/1
  ip        TEXT,
  criado_em TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_login_eventos_nii_data ON login_eventos(nii, criado_em);
CREATE INDEX IF NOT EXISTS idx_login_eventos_ip_data  ON login_eventos(ip, criado_em);

CREATE TABLE IF NOT EXISTS calendario_operacional (
  data TEXT PRIMARY KEY,
  tipo TEXT NOT NULL CHECK (tipo IN ('normal','fim_semana','feriado','exercicio','outro')),
  nota TEXT
);

-- Ausências prolongadas de utilizadores
CREATE TABLE IF NOT EXISTS ausencias (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  utilizador_id INTEGER NOT NULL REFERENCES utilizadores(id) ON DELETE CASCADE,
  ausente_de   TEXT NOT NULL,
  ausente_ate  TEXT NOT NULL,
  hora_inicio  TEXT,  -- HH:MM (NULL = dia inteiro)
  hora_fim     TEXT,  -- HH:MM (NULL = dia inteiro)
  estufa_almoco INTEGER DEFAULT 0,  -- guardar almoço na estufa durante ausência
  estufa_jantar INTEGER DEFAULT 0,  -- guardar jantar na estufa durante ausência
  motivo       TEXT,
  criado_em    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  criado_por   TEXT
);
CREATE INDEX IF NOT EXISTS idx_ausencias_uid  ON ausencias(utilizador_id);
CREATE INDEX IF NOT EXISTS idx_ausencias_datas ON ausencias(ausente_de, ausente_ate);

-- Detenções de cadetes
CREATE TABLE IF NOT EXISTS detencoes (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  utilizador_id INTEGER NOT NULL REFERENCES utilizadores(id) ON DELETE CASCADE,
  detido_de     TEXT NOT NULL,
  detido_ate    TEXT NOT NULL,
  motivo        TEXT,
  criado_em     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  criado_por    TEXT
);
CREATE INDEX IF NOT EXISTS idx_detencoes_uid   ON detencoes(utilizador_id);
CREATE INDEX IF NOT EXISTS idx_detencoes_datas ON detencoes(detido_de, detido_ate);

-- Licenças de saída
CREATE TABLE IF NOT EXISTS licencas (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  utilizador_id INTEGER NOT NULL REFERENCES utilizadores(id) ON DELETE CASCADE,
  data          TEXT NOT NULL,
  tipo          TEXT NOT NULL CHECK(tipo IN ('antes_jantar','apos_jantar')),
  aprovado_por  TEXT,
  criado_em     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  hora_saida    TEXT,
  hora_entrada  TEXT,
  UNIQUE(utilizador_id, data)
);
CREATE INDEX IF NOT EXISTS idx_licencas_uid  ON licencas(utilizador_id);
CREATE INDEX IF NOT EXISTS idx_licencas_data ON licencas(data);

-- Log de auditoria de alterações de refeições
CREATE TABLE IF NOT EXISTS refeicoes_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  utilizador_id INTEGER NOT NULL,
  data_refeicao TEXT NOT NULL,
  campo         TEXT NOT NULL,
  valor_antes   TEXT,
  valor_depois  TEXT,
  alterado_por  TEXT NOT NULL,
  alterado_em   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_rlog_uid  ON refeicoes_log(utilizador_id);
CREATE INDEX IF NOT EXISTS idx_rlog_data ON refeicoes_log(data_refeicao);
CREATE INDEX IF NOT EXISTS idx_rlog_por  ON refeicoes_log(alterado_por);

CREATE TABLE IF NOT EXISTS menus_diarios (
  data           TEXT PRIMARY KEY,
  pequeno_almoco TEXT,
  lanche         TEXT,
  almoco_normal  TEXT,
  almoco_veg     TEXT,
  almoco_dieta   TEXT,
  jantar_normal  TEXT,
  jantar_veg     TEXT,
  jantar_dieta   TEXT
);

CREATE TABLE IF NOT EXISTS capacidade_refeicao (
  data      TEXT NOT NULL,
  refeicao  TEXT NOT NULL CHECK (refeicao IN ('Pequeno Almoço','Lanche','Almoço','Jantar')),
  max_total INTEGER NOT NULL CHECK (max_total >= 0),
  PRIMARY KEY (data, refeicao)
);

CREATE TABLE IF NOT EXISTS capacidade_excessos (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  data       TEXT NOT NULL,
  refeicao   TEXT NOT NULL,
  ocupacao   INTEGER NOT NULL,
  capacidade INTEGER NOT NULL,
  criado_em  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_capex_data ON capacidade_excessos(data);

CREATE TABLE IF NOT EXISTS admin_audit_log (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  ts     TEXT DEFAULT (datetime('now','localtime')),
  actor  TEXT NOT NULL,
  action TEXT NOT NULL,
  detail TEXT
);

CREATE TABLE IF NOT EXISTS turmas (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  nome      TEXT NOT NULL UNIQUE,
  ano       INTEGER NOT NULL,
  descricao TEXT,
  criado_em TEXT DEFAULT (datetime('now','localtime'))
);

-- Tokens rotativos para QR de check-in (oficial mostra → aluno scan)
CREATE TABLE IF NOT EXISTS checkin_tokens (
  token       TEXT PRIMARY KEY,
  created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  expires_at  TEXT NOT NULL,
  created_by  INTEGER NOT NULL REFERENCES utilizadores(id) ON DELETE CASCADE,
  tipo        TEXT NOT NULL CHECK(tipo IN ('entrada','saida','auto'))
);
CREATE INDEX IF NOT EXISTS idx_checkin_tokens_exp ON checkin_tokens(expires_at);

-- Log de check-ins efectuados (uma linha por aluno×token)
CREATE TABLE IF NOT EXISTS checkin_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  utilizador_id INTEGER NOT NULL REFERENCES utilizadores(id) ON DELETE CASCADE,
  token         TEXT NOT NULL,
  tipo          TEXT NOT NULL CHECK(tipo IN ('entrada','saida')),
  ts            TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  ip            TEXT,
  user_agent    TEXT,
  UNIQUE(utilizador_id, token)
);
CREATE INDEX IF NOT EXISTS idx_checkin_log_uid_ts ON checkin_log(utilizador_id, ts);
CREATE INDEX IF NOT EXISTS idx_checkin_log_token ON checkin_log(token);

CREATE INDEX IF NOT EXISTS idx_refeicoes_data ON refeicoes(data);
CREATE INDEX IF NOT EXISTS idx_refeicoes_user ON refeicoes(utilizador_id);
CREATE INDEX IF NOT EXISTS idx_refeicoes_user_data ON refeicoes(utilizador_id, data);
CREATE INDEX IF NOT EXISTS idx_utilizadores_ano ON utilizadores(ano);
CREATE INDEX IF NOT EXISTS idx_utilizadores_perfil ON utilizadores(perfil);
CREATE INDEX IF NOT EXISTS idx_ausencias_uid_datas ON ausencias(utilizador_id, ausente_de, ausente_ate);
CREATE INDEX IF NOT EXISTS idx_detencoes_uid_datas ON detencoes(utilizador_id, detido_de, detido_ate);
CREATE INDEX IF NOT EXISTS idx_licencas_uid_data ON licencas(utilizador_id, data);
CREATE INDEX IF NOT EXISTS idx_cal_op_data ON calendario_operacional(data);
CREATE INDEX IF NOT EXISTS idx_rlog_uid_data ON refeicoes_log(utilizador_id, data_refeicao);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON admin_audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON admin_audit_log(actor);
CREATE INDEX IF NOT EXISTS idx_capex_data_ref ON capacidade_excessos(data, refeicao);

CREATE VIRTUAL TABLE IF NOT EXISTS utilizadores_fts USING fts5(
  Nome_completo,
  content='utilizadores',
  content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS utilizadores_ai_fts
AFTER INSERT ON utilizadores BEGIN
  INSERT INTO utilizadores_fts(rowid, Nome_completo) VALUES (NEW.id, NEW.Nome_completo);
END;
CREATE TRIGGER IF NOT EXISTS utilizadores_ad_fts
AFTER DELETE ON utilizadores BEGIN
  INSERT INTO utilizadores_fts(utilizadores_fts, rowid) VALUES('delete', OLD.id);
END;
CREATE TRIGGER IF NOT EXISTS utilizadores_au_fts
AFTER UPDATE ON utilizadores BEGIN
  INSERT INTO utilizadores_fts(utilizadores_fts, rowid) VALUES('delete', OLD.id);
  INSERT INTO utilizadores_fts(rowid, Nome_completo) VALUES (NEW.id, NEW.Nome_completo);
END;

-- Limpar refeicoes_log quando utilizador é removido (simula FK CASCADE)
CREATE TRIGGER IF NOT EXISTS rlog_cleanup_on_user_delete
AFTER DELETE ON utilizadores BEGIN
  DELETE FROM refeicoes_log WHERE utilizador_id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS refeicoes_chk_values
BEFORE INSERT ON refeicoes
BEGIN
  SELECT
    CASE
      WHEN NEW.pequeno_almoco NOT IN (0,1) THEN RAISE(ABORT,'pequeno_almoco inválido')
      WHEN NEW.lanche NOT IN (0,1) THEN RAISE(ABORT,'lanche inválido')
      WHEN NEW.jantar_sai_unidade NOT IN (0,1) THEN RAISE(ABORT,'jantar_sai_unidade inválido')
      WHEN NEW.almoco_estufa NOT IN (0,1) THEN RAISE(ABORT,'almoco_estufa inválido')
      WHEN NEW.jantar_estufa NOT IN (0,1) THEN RAISE(ABORT,'jantar_estufa inválido')
      WHEN NEW.almoco IS NOT NULL AND NEW.almoco NOT IN ('Normal','Vegetariano','Dieta') THEN RAISE(ABORT,'almoco inválido')
      WHEN NEW.jantar_tipo IS NOT NULL AND NEW.jantar_tipo NOT IN ('Normal','Vegetariano','Dieta') THEN RAISE(ABORT,'jantar_tipo inválido')
    END;
END;
CREATE TRIGGER IF NOT EXISTS refeicoes_chk_values_u
BEFORE UPDATE ON refeicoes
BEGIN
  SELECT
    CASE
      WHEN NEW.pequeno_almoco NOT IN (0,1) THEN RAISE(ABORT,'pequeno_almoco inválido')
      WHEN NEW.lanche NOT IN (0,1) THEN RAISE(ABORT,'lanche inválido')
      WHEN NEW.jantar_sai_unidade NOT IN (0,1) THEN RAISE(ABORT,'jantar_sai_unidade inválido')
      WHEN NEW.almoco_estufa NOT IN (0,1) THEN RAISE(ABORT,'almoco_estufa inválido')
      WHEN NEW.jantar_estufa NOT IN (0,1) THEN RAISE(ABORT,'jantar_estufa inválido')
      WHEN NEW.almoco IS NOT NULL AND NEW.almoco NOT IN ('Normal','Vegetariano','Dieta') THEN RAISE(ABORT,'almoco inválido')
      WHEN NEW.jantar_tipo IS NOT NULL AND NEW.jantar_tipo NOT IN ('Normal','Vegetariano','Dieta') THEN RAISE(ABORT,'jantar_tipo inválido')
    END;
END;

CREATE VIEW IF NOT EXISTS v_ocupacao_dia AS
SELECT
  d.data,
  'Pequeno Almoço' AS refeicao,
  (SELECT COUNT(*) FROM refeicoes r
   JOIN utilizadores u ON u.id=r.utilizador_id AND u.is_active=1
   AND NOT EXISTS (SELECT 1 FROM ausencias a WHERE a.utilizador_id=u.id AND a.ausente_de<=r.data AND a.ausente_ate>=r.data)
   WHERE r.data=d.data AND r.pequeno_almoco=1) AS ocupacao,
  COALESCE((SELECT max_total FROM capacidade_refeicao c WHERE c.data=d.data AND c.refeicao='Pequeno Almoço'), -1) AS capacidade
FROM (SELECT DISTINCT data FROM refeicoes) d
UNION ALL
SELECT
  d.data, 'Lanche',
  (SELECT COUNT(*) FROM refeicoes r
   JOIN utilizadores u ON u.id=r.utilizador_id AND u.is_active=1
   AND NOT EXISTS (SELECT 1 FROM ausencias a WHERE a.utilizador_id=u.id AND a.ausente_de<=r.data AND a.ausente_ate>=r.data)
   WHERE r.data=d.data AND r.lanche=1),
  COALESCE((SELECT max_total FROM capacidade_refeicao c WHERE c.data=d.data AND c.refeicao='Lanche'), -1)
FROM (SELECT DISTINCT data FROM refeicoes) d
UNION ALL
SELECT
  d.data, 'Almoço',
  (SELECT COUNT(*) FROM refeicoes r
   JOIN utilizadores u ON u.id=r.utilizador_id AND u.is_active=1
   AND NOT EXISTS (SELECT 1 FROM ausencias a WHERE a.utilizador_id=u.id AND a.ausente_de<=r.data AND a.ausente_ate>=r.data)
   WHERE r.data=d.data AND r.almoco IS NOT NULL),
  COALESCE((SELECT max_total FROM capacidade_refeicao c WHERE c.data=d.data AND c.refeicao='Almoço'), -1)
FROM (SELECT DISTINCT data FROM refeicoes) d
UNION ALL
SELECT
  d.data, 'Jantar',
  (SELECT COUNT(*) FROM refeicoes r
   JOIN utilizadores u ON u.id=r.utilizador_id AND u.is_active=1
   AND NOT EXISTS (SELECT 1 FROM ausencias a WHERE a.utilizador_id=u.id AND a.ausente_de<=r.data AND a.ausente_ate>=r.data)
   WHERE r.data=d.data AND r.jantar_tipo IS NOT NULL),
  COALESCE((SELECT max_total FROM capacidade_refeicao c WHERE c.data=d.data AND c.refeicao='Jantar'), -1)
FROM (SELECT DISTINCT data FROM refeicoes) d;

CREATE TRIGGER IF NOT EXISTS cap_log_pa
AFTER INSERT ON refeicoes
WHEN NEW.pequeno_almoco=1 AND EXISTS (
  SELECT 1 FROM capacidade_refeicao c WHERE c.data=NEW.data AND c.refeicao='Pequeno Almoço'
) AND (
  (SELECT COUNT(*) FROM refeicoes r WHERE r.data=NEW.data AND r.pequeno_almoco=1) >
  (SELECT max_total FROM capacidade_refeicao c WHERE c.data=NEW.data AND c.refeicao='Pequeno Almoço')
)
BEGIN
  INSERT INTO capacidade_excessos(data,refeicao,ocupacao,capacidade)
  SELECT NEW.data,'Pequeno Almoço',
         (SELECT COUNT(*) FROM refeicoes r WHERE r.data=NEW.data AND r.pequeno_almoco=1),
         (SELECT max_total FROM capacidade_refeicao c WHERE c.data=NEW.data AND c.refeicao='Pequeno Almoço');
END;

CREATE TRIGGER IF NOT EXISTS cap_log_lanche
AFTER INSERT ON refeicoes
WHEN NEW.lanche=1 AND EXISTS (
  SELECT 1 FROM capacidade_refeicao c WHERE c.data=NEW.data AND c.refeicao='Lanche'
) AND (
  (SELECT COUNT(*) FROM refeicoes r WHERE r.data=NEW.data AND r.lanche=1) >
  (SELECT max_total FROM capacidade_refeicao c WHERE c.data=NEW.data AND c.refeicao='Lanche')
)
BEGIN
  INSERT INTO capacidade_excessos(data,refeicao,ocupacao,capacidade)
  SELECT NEW.data,'Lanche',
         (SELECT COUNT(*) FROM refeicoes r WHERE r.data=NEW.data AND r.lanche=1),
         (SELECT max_total FROM capacidade_refeicao c WHERE c.data=NEW.data AND c.refeicao='Lanche');
END;

CREATE TRIGGER IF NOT EXISTS cap_log_almoco
AFTER INSERT ON refeicoes
WHEN NEW.almoco IS NOT NULL AND EXISTS (
  SELECT 1 FROM capacidade_refeicao c WHERE c.data=NEW.data AND c.refeicao='Almoço'
) AND (
  (SELECT COUNT(*) FROM refeicoes r WHERE r.data=NEW.data AND r.almoco IS NOT NULL) >
  (SELECT max_total FROM capacidade_refeicao c WHERE c.data=NEW.data AND c.refeicao='Almoço')
)
BEGIN
  INSERT INTO capacidade_excessos(data,refeicao,ocupacao,capacidade)
  SELECT NEW.data,'Almoço',
         (SELECT COUNT(*) FROM refeicoes r WHERE r.data=NEW.data AND r.almoco IS NOT NULL),
         (SELECT max_total FROM capacidade_refeicao c WHERE c.data=NEW.data AND c.refeicao='Almoço');
END;

CREATE TRIGGER IF NOT EXISTS cap_log_jantar
AFTER INSERT ON refeicoes
WHEN NEW.jantar_tipo IS NOT NULL AND EXISTS (
  SELECT 1 FROM capacidade_refeicao c WHERE c.data=NEW.data AND c.refeicao='Jantar'
) AND (
  (SELECT COUNT(*) FROM refeicoes r WHERE r.data=NEW.data AND r.jantar_tipo IS NOT NULL) >
  (SELECT max_total FROM capacidade_refeicao c WHERE c.data=NEW.data AND c.refeicao='Jantar')
)
BEGIN
  INSERT INTO capacidade_excessos(data,refeicao,ocupacao,capacidade)
  SELECT NEW.data,'Jantar',
         (SELECT COUNT(*) FROM refeicoes r WHERE r.data=NEW.data AND r.jantar_tipo IS NOT NULL),
         (SELECT max_total FROM capacidade_refeicao c WHERE c.data=NEW.data AND c.refeicao='Jantar');
END;
"""
