"""
OSMA - Memoria cerebral del harness RPG
=============================================
SQLite + FTS5. HIPOCAMPO del arnes: guarda recuerdos (observations),
misiones (quests), sesiones, agentes y relaciones (edges, FASE 2).

Uso (desde Python):
    from osma_brain import OsmaBrain
    brain = OsmaBrain(db_path)
    brain.save_observation(agent="vivi", topic_key="vivi/ui-patterns",
                           type="pattern", content="...")

Uso (desde CLI PowerShell): ver osma-memory.ps1
100% local. CERO dependencias externas. Solo Python 3.14 + SQLite nativo.
"""

import atexit
import json
import math
import os
import re
import sqlite3
import sys
import datetime


def _fts_escape(query):
    """Sanitiza el texto para MATCH de FTS5: solo palabras alfanumericas (con acentos)."""
    words = re.findall(r"[\wÁÉÍÓÚÑÜáéíóúñü]+", query or "")
    return " ".join(words) or '""'


# Palabras vacias para extraccion de entidades OSMA V4 (determinista, sin libs externas)
_OSMA_STOPWORDS = frozenset("""
de la el los las del para con por que una un and the en es se su al lo como mas pero ya no si
me mi te le les sus esta este esto ese eso uno dos sin sobre entre porque cuando donde tambien
todo toda todos todas nuestra nuestro ellos ellas quien cual quienes como cuanto cuando donde
ademas durante mediante hasta hacia tras via bien muy poco mucho muchos muchas
""".split())

# ---- V6: Multidimensional Memory (cues, salience, anchors) ------------------
# Tecnologias conocidas para extraccion de cues de tipo 'technology'
# (match por substring sobre el texto de la experiencia en minusculas).
_TECH_LIST = ["supabase", "firebase", "nextjs", "next.js", "react", "python", "sqlite",
              "postgres", "postgresql", "tailwind", "typescript", "javascript", "docker",
              "vercel", "prisma", "zod", "rls", "fts5", "supabase-cli", "node", "express",
              "git", "github", "playwright", "vitest", "jest", "fastapi", "django",
              "flask", "redis", "graphql", "rest"]

# Alias estaticos para generacion de anclas de recuperacion:
# si una experiencia tiene un cue con el valor clave, sus alias se agregan como anchors.
_ALIAS_TABLE = {
    "permission denied": ["access denied", "acceso denegado", "authorization error",
                          "auth problem", "403 forbidden"],
    "rls": ["row level security", "politicas rls", "rls policies"],
    "login": ["autenticacion", "auth", "sign in", "inicio de sesion", "acceso"],
    "supabase": ["base de datos", "db postgres"],
    "middleware": ["interceptor", "request pipeline"],
}

# Saliencia base por tipo de experiencia (0..1). Default 0.3.
_SALIENCE_BASE = {"decision": 0.7, "bugfix": 0.6, "verdict": 0.5, "discovery": 0.4,
                  "recommendation": 0.35, "preference": 0.3, "pattern": 0.5,
                  "action": 0.2, "session_summary": 0.2}

# GAMMA de convergencia no-lineal multi-cue (episode_activation_score).
_GAMMA = 0.1

# ---- V7 audit (Tywin): competition_between_memories + fts5_integration ----
# Pesos del competition_score: tie-breaker compuesto que ordena SOLO entre
# resultados con episode_activation_score igual/cercano (el score primario
# sigue dominando el ranking). Tunables: ajustar recencia/importancia/proyecto/
# coherencia SIN tocar el factor primario episode_activation_score.
_W_RECENCY = 0.05
_W_IMPORTANCE = 0.05
_W_PROJECT = 0.03
_W_COHERENCE = 0.02
# FIX 3 (Tywin): fallback FTS5 para cues SIN match estructurado. Flag para
# desactivar el fallback sin tocar el path primario (LIKE sobre experience_cues).
_FTS5_FALLBACK = True
_FTS5_MAX_UNMATCHED = 2      # cues sin hit estructurado procesados por busqueda
_FTS5_RECALL_LIMIT = 3       # observaciones FTS5 por cue no matcheado

# Palabras de problema/error para el cue 'problem' (situacion).
_PROBLEM_WORDS = ("error", "falla", "denied", "bug", "no puede", "permiso")

# Frases de error explicitas para el cue 'error'.
_ERROR_PHRASES = ("permission denied", "access denied")

# Regex de frases de error: 'error <texto>'.
_ERROR_RE = re.compile(r"error\s+[a-z0-9 ]{3,60}", re.IGNORECASE)


SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    class        TEXT,
    role         TEXT,
    model        TEXT,
    trust_score  REAL DEFAULT 0.5,
    xp           INTEGER DEFAULT 0,
    level        INTEGER DEFAULT 1,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS observations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent       TEXT NOT NULL,
    topic_key   TEXT NOT NULL,
    type        TEXT NOT NULL,          -- bugfix | decision | pattern | discovery | preference | verdict | recommendation | action | session_summary
    content     TEXT NOT NULL,
    quest_id    TEXT,
    score       INTEGER DEFAULT 0,      -- importancia 1-5
    tags        TEXT DEFAULT '',
    archived    INTEGER DEFAULT 0,
    memory_kind TEXT DEFAULT 'episodic',    -- working | episodic | semantic | procedural
    confidence  REAL DEFAULT 0.5,           -- que tan cierto es (0-1)
    storage_strength REAL DEFAULT 0.4,      -- que tan consolidado (0-1)
    retrieval_strength REAL DEFAULT 0.6,    -- que tan accesible ahora (0-1)
    last_retrieved_at TEXT,
    volatility  TEXT DEFAULT 'stable',      -- immutable | stable | slow | dynamic | ephemeral
    state       TEXT DEFAULT 'active',      -- active | dormant | archived | contested | superseded
    evidence    TEXT DEFAULT '',            -- JSON: fuentes independientes
    source      TEXT DEFAULT '',
    supersedes  INTEGER,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS quests (
    id          TEXT PRIMARY KEY,        -- Q-001
    description TEXT,
    quest_type  TEXT,                    -- frontend | backend | fix | architecture | research | devops | boss
    party       TEXT,                    -- JSON: ["vivi","eiko"]
    result      TEXT,                    -- PASS | FAIL_PARTIAL | FAIL_TOTAL
    tokens_used INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now')),
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT DEFAULT (datetime('now')),
    ended_at    TEXT,
    summary     TEXT
);

CREATE TABLE IF NOT EXISTS edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_a      TEXT NOT NULL,           -- "login-form"
    node_b      TEXT NOT NULL,           -- "zod"
    relation    TEXT NOT NULL,           -- "uses" | "created_by" | "depends_on" | "touched_by"
    agent       TEXT,
    quest_id    TEXT,
    weight      REAL DEFAULT 0.5,            -- fuerza de la asociacion (0-1)
    success_count  INTEGER DEFAULT 0,
    failure_count  INTEGER DEFAULT 0,
    coactivation_count INTEGER DEFAULT 0,
    last_activated_at TEXT,
    last_success_at  TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

-- V3: dominio de skills por proyecto (aprendizaje procedural)
CREATE TABLE IF NOT EXISTS skill_mastery (
    skill_id        TEXT PRIMARY KEY,
    skill_version   TEXT,
    state           TEXT DEFAULT 'new',      -- new | learning | reliable | mastered | needs_review | stale | quarantined
    mastery         REAL DEFAULT 0.0,
    confidence      REAL DEFAULT 0.0,
    success_count   INTEGER DEFAULT 0,
    failure_count   INTEGER DEFAULT 0,
    consecutive_successes INTEGER DEFAULT 0,
    trigger_patterns    TEXT DEFAULT '[]',
    anti_trigger_patterns TEXT DEFAULT '[]',
    contexts        TEXT DEFAULT '[]',
    failure_patterns    TEXT DEFAULT '[]',
    avg_tokens      REAL DEFAULT 0,
    avg_cost        REAL DEFAULT 0,
    last_used_at    TEXT,
    last_success_at TEXT,
    last_failure_at TEXT,
    last_verified_at TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT
);

-- V3: registro de cada ejecucion de skill (que funciona de verdad)
CREATE TABLE IF NOT EXISTS skill_executions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id      TEXT,
    skill_version TEXT,
    agent         TEXT,
    quest_id      TEXT,
    trigger       TEXT,
    context_summary TEXT,
    model         TEXT,
    provider      TEXT,
    started_at    TEXT,
    finished_at   TEXT,
    result        TEXT,
    verdict       TEXT,
    success       INTEGER DEFAULT 0,
    evidence      TEXT DEFAULT '',
    error         TEXT,
    tokens_in     INTEGER DEFAULT 0,
    tokens_out    INTEGER DEFAULT 0,
    tool_calls    INTEGER DEFAULT 0,
    created_at    TEXT DEFAULT (datetime('now'))
);

-- V3: vinculos memoria <-> skill
CREATE TABLE IF NOT EXISTS skill_memory_links (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id     INTEGER,
    skill_id      TEXT,
    relation      TEXT DEFAULT 'supports',   -- supports | trigger | precondition | warning | counterexample | result
    weight        REAL DEFAULT 0.5,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    last_used     TEXT
);

-- V3: revision programada (spaced review)
CREATE TABLE IF NOT EXISTS memory_reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id       INTEGER,
    next_review_at  TEXT,
    importance      INTEGER DEFAULT 0,
    volatility      TEXT,
    confidence      REAL DEFAULT 0,
    last_reviewed_at TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- V3: version de esquema (migraciones idempotentes)
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- V3: COGNITIVE CHECKPOINTS (estado operativo para reconstruir la mente de trabajo)
CREATE TABLE IF NOT EXISTS cognitive_checkpoints (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id         TEXT,
    quest_id           TEXT,
    agent              TEXT,
    reason             TEXT,
    created_at         TEXT DEFAULT (datetime('now')),
    goal               TEXT,
    phase              TEXT,
    completed_tasks    TEXT DEFAULT '[]',
    pending_tasks      TEXT DEFAULT '[]',
    active_files       TEXT DEFAULT '[]',
    modified_files     TEXT DEFAULT '[]',
    active_decisions   TEXT DEFAULT '[]',
    critical_memory_ids TEXT DEFAULT '[]',
    active_skill       TEXT,
    skill_state        TEXT DEFAULT '{}',
    blockers           TEXT DEFAULT '[]',
    errors             TEXT DEFAULT '[]',
    test_state         TEXT,
    build_state        TEXT,
    git_state          TEXT,
    next_action        TEXT,
    working_memory_ids TEXT DEFAULT '[]',
    tokens_used        INTEGER DEFAULT 0,
    context_usage      REAL DEFAULT 0,
    metadata           TEXT DEFAULT '{}',
    recovery_capsule   TEXT,
    continuity_score   REAL DEFAULT 0
);

-- V3: AUTONOMOUS PARTY (task graph de quests autonomas)
CREATE TABLE IF NOT EXISTS autonomous_quests (
    id             TEXT PRIMARY KEY,       -- school-platform-001
    description    TEXT,
    status         TEXT DEFAULT 'active',  -- active | paused | done | blocked
    party          TEXT DEFAULT '[]',      -- JSON: agentes elegidos por Atlas
    progress       TEXT DEFAULT '{}',
    mode           TEXT DEFAULT 'balanced',
    created_at     TEXT DEFAULT (datetime('now')),
    updated_at     TEXT
);

CREATE TABLE IF NOT EXISTS autonomous_tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    quest_id        TEXT,
    task_id         TEXT,                  -- AUTH-03
    description     TEXT,
    agent           TEXT,
    status          TEXT DEFAULT 'pending',-- pending | ready | running | pass | fail | blocked
    dependencies    TEXT DEFAULT '[]',     -- JSON: [task_id]
    acceptance      TEXT DEFAULT '',
    summary         TEXT DEFAULT '',
    evidence        TEXT DEFAULT '',
    files_changed   TEXT DEFAULT '[]',
    tests           TEXT DEFAULT '',
    blockers        TEXT DEFAULT '[]',
    attempts        INTEGER DEFAULT 0,
    model           TEXT DEFAULT '',
    escalated_model TEXT DEFAULT '',
    tokens_used     INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts USING fts5(
    content,
    content_rowid='id',
    content='observations',
    tokenize='unicode61'
);
"""

TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS observations_ai AFTER INSERT ON observations BEGIN
    INSERT INTO observations_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS observations_ad AFTER DELETE ON observations BEGIN
    INSERT INTO observations_fts(observations_fts, rowid, content)
    VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS observations_au AFTER UPDATE ON observations BEGIN
    INSERT INTO observations_fts(observations_fts, rowid, content)
    VALUES ('delete', old.id, old.content);
    INSERT INTO observations_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


class OsmaBrain:
    """Cerebro del arnes: memoria persistente con SQLite + FTS5."""

    def __init__(self, db_path):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.executescript(TRIGGERS)
        self._migrate()
        self._conn.commit()
        # Ultima red de seguridad: cierra la conexion al salir del proceso
        # aunque main() nunca llegue al finally (p.ej. sys.exit en un guard).
        atexit.register(self.close)

    def _migrate(self):
        """Migracion V3 idempotente: agrega columnas/tablas sin romper datos existentes."""
        def _has_column(table, col):
            return any(r["name"] == col for r in self._conn.execute("PRAGMA table_info(%s)" % table))

        # observaciones: columnas cognitivas V3
        for col, ddl in [
            ("score", "ALTER TABLE observations ADD COLUMN score INTEGER DEFAULT 0"),
            ("tags", "ALTER TABLE observations ADD COLUMN tags TEXT DEFAULT ''"),
            ("archived", "ALTER TABLE observations ADD COLUMN archived INTEGER DEFAULT 0"),
            ("memory_kind", "ALTER TABLE observations ADD COLUMN memory_kind TEXT DEFAULT 'episodic'"),
            ("confidence", "ALTER TABLE observations ADD COLUMN confidence REAL DEFAULT 0.5"),
            ("storage_strength", "ALTER TABLE observations ADD COLUMN storage_strength REAL DEFAULT 0.4"),
            ("retrieval_strength", "ALTER TABLE observations ADD COLUMN retrieval_strength REAL DEFAULT 0.6"),
            ("last_retrieved_at", "ALTER TABLE observations ADD COLUMN last_retrieved_at TEXT"),
            ("volatility", "ALTER TABLE observations ADD COLUMN volatility TEXT DEFAULT 'stable'"),
            ("state", "ALTER TABLE observations ADD COLUMN state TEXT DEFAULT 'active'"),
            ("evidence", "ALTER TABLE observations ADD COLUMN evidence TEXT DEFAULT ''"),
            ("source", "ALTER TABLE observations ADD COLUMN source TEXT DEFAULT ''"),
            ("supersedes", "ALTER TABLE observations ADD COLUMN supersedes INTEGER"),
        ]:
            if not _has_column("observations", col):
                self._conn.execute(ddl)

        # edges: pesos de asociacion
        for col, ddl in [
            ("weight", "ALTER TABLE edges ADD COLUMN weight REAL DEFAULT 0.5"),
            ("success_count", "ALTER TABLE edges ADD COLUMN success_count INTEGER DEFAULT 0"),
            ("failure_count", "ALTER TABLE edges ADD COLUMN failure_count INTEGER DEFAULT 0"),
            ("coactivation_count", "ALTER TABLE edges ADD COLUMN coactivation_count INTEGER DEFAULT 0"),
            ("last_activated_at", "ALTER TABLE edges ADD COLUMN last_activated_at TEXT"),
            ("last_success_at", "ALTER TABLE edges ADD COLUMN last_success_at TEXT"),
        ]:
            if not _has_column("edges", col):
                try:
                    self._conn.execute(ddl)
                except Exception:
                    pass

        # tablas V3 (CREATE IF NOT EXISTS cubre frescas; para viejas las crea)
        self._conn.execute("""CREATE TABLE IF NOT EXISTS skill_mastery (
            skill_id TEXT PRIMARY KEY, skill_version TEXT,
            state TEXT DEFAULT 'new', mastery REAL DEFAULT 0.0, confidence REAL DEFAULT 0.0,
            success_count INTEGER DEFAULT 0, failure_count INTEGER DEFAULT 0,
            consecutive_successes INTEGER DEFAULT 0,
            trigger_patterns TEXT DEFAULT '[]', anti_trigger_patterns TEXT DEFAULT '[]',
            contexts TEXT DEFAULT '[]', failure_patterns TEXT DEFAULT '[]',
            avg_tokens REAL DEFAULT 0, avg_cost REAL DEFAULT 0,
            last_used_at TEXT, last_success_at TEXT, last_failure_at TEXT, last_verified_at TEXT,
            created_at TEXT DEFAULT (datetime('now')), updated_at TEXT)""")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS skill_executions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, skill_id TEXT, skill_version TEXT, agent TEXT,
            quest_id TEXT, trigger TEXT, context_summary TEXT, model TEXT, provider TEXT,
            started_at TEXT, finished_at TEXT, result TEXT, verdict TEXT, success INTEGER DEFAULT 0,
            evidence TEXT DEFAULT '', error TEXT, tokens_in INTEGER DEFAULT 0,
            tokens_out INTEGER DEFAULT 0, tool_calls INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')))""")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS skill_memory_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id INTEGER, skill_id TEXT,
            relation TEXT DEFAULT 'supports', weight REAL DEFAULT 0.5,
            success_count INTEGER DEFAULT 0, failure_count INTEGER DEFAULT 0, last_used TEXT)""")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS memory_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id INTEGER, next_review_at TEXT,
            importance INTEGER DEFAULT 0, volatility TEXT, confidence REAL DEFAULT 0,
            last_reviewed_at TEXT, created_at TEXT DEFAULT (datetime('now')))""")
        self._conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        self._conn.execute("CREATE TABLE IF NOT EXISTS observation_revisions ("
                           "id INTEGER PRIMARY KEY AUTOINCREMENT, observation_id INTEGER NOT NULL,"
                           "content TEXT, type TEXT, created_at TEXT DEFAULT (datetime('now')))")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS autonomous_quests (
            id TEXT PRIMARY KEY, description TEXT, status TEXT DEFAULT 'active',
            party TEXT DEFAULT '[]', progress TEXT DEFAULT '{}', mode TEXT DEFAULT 'balanced',
            created_at TEXT DEFAULT (datetime('now')), updated_at TEXT)""")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS autonomous_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, quest_id TEXT, task_id TEXT, description TEXT,
            agent TEXT, status TEXT DEFAULT 'pending', dependencies TEXT DEFAULT '[]',
            acceptance TEXT DEFAULT '', summary TEXT DEFAULT '', evidence TEXT DEFAULT '',
            files_changed TEXT DEFAULT '[]', tests TEXT DEFAULT '', blockers TEXT DEFAULT '[]',
            attempts INTEGER DEFAULT 0, model TEXT DEFAULT '', escalated_model TEXT DEFAULT '',
            tokens_used INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now')), updated_at TEXT)""")

        # backfill: defaults cognitivos para observaciones existentes
        kind_map = {"decision": "semantic", "pattern": "semantic", "preference": "semantic",
                    "recommendation": "semantic", "bugfix": "semantic"}
        for type_name, kind in kind_map.items():
            self._conn.execute(
                "UPDATE observations SET memory_kind=? WHERE memory_kind='episodic' AND type=?",
                (kind, type_name))
        conf_defaults = {"decision": 0.7, "verdict": 0.85, "pattern": 0.6, "discovery": 0.45,
                         "preference": 0.6, "recommendation": 0.6, "action": 0.5, "bugfix": 0.65,
                         "session_summary": 0.75}
        for type_name, c in conf_defaults.items():
            self._conn.execute(
                "UPDATE observations SET confidence=? WHERE confidence=0.5 AND type=?",
                (c, type_name))
        # ---- V4: OSMA associativo (co-activacion, contradicciones, consolidacion) ----
        for col, ddl in [
            ("activation_level", "ALTER TABLE observations ADD COLUMN activation_level REAL DEFAULT 0"),
            ("last_decay_at", "ALTER TABLE observations ADD COLUMN last_decay_at TEXT"),
            ("successful_retrievals", "ALTER TABLE observations ADD COLUMN successful_retrievals INTEGER DEFAULT 0"),
            ("decay_base", "ALTER TABLE observations ADD COLUMN decay_base REAL"),
        ]:
            if not _has_column("observations", col):
                self._conn.execute(ddl)

        # V4: decay_base = pico de retrieval_strength (nunca decae); backfill con el valor actual
        self._conn.execute(
            "UPDATE observations SET decay_base = retrieval_strength WHERE decay_base IS NULL")

        self._conn.execute("""CREATE TABLE IF NOT EXISTS observation_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            obs_a_id INTEGER NOT NULL,
            obs_b_id INTEGER NOT NULL,
            weight REAL DEFAULT 0.1,
            coactivation_count INTEGER DEFAULT 0,
            successful_coactivations INTEGER DEFAULT 0,
            failed_coactivations INTEGER DEFAULT 0,
            last_coactivated_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(obs_a_id, obs_b_id))""")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS contradictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            obs_a_id INTEGER NOT NULL,
            obs_b_id INTEGER NOT NULL,
            conflict_type TEXT,
            status TEXT DEFAULT 'open',
            detected_at TEXT,
            detected_by TEXT,
            resolution TEXT,
            resolved_at TEXT,
            superseded_id INTEGER)""")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS consolidations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            kind TEXT,
            summary TEXT,
            source_ids TEXT,
            importance REAL DEFAULT 0.5,
            confidence REAL DEFAULT 0.6,
            project TEXT,
            topic_key TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now')))""")
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', '4')")
        self._conn.commit()

        # ---- V5: Experience Memory (experiencias validadas -> patrones -> reuso) ----
        self._conn.execute("""CREATE TABLE IF NOT EXISTS experiences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            situation TEXT NOT NULL, reasoning TEXT, conclusion TEXT, action TEXT, outcome TEXT,
            reward_signal REAL DEFAULT 0.0,
            validation_status TEXT DEFAULT 'unverified',   -- unverified|attempted|partial|verified|failed
            confidence REAL DEFAULT 0.4, importance REAL DEFAULT 0.3,
            successful_retrievals INTEGER DEFAULT 0, failed_retrievals INTEGER DEFAULT 0,
            agent TEXT, project TEXT, topic_key TEXT,
            created_at TEXT DEFAULT (datetime('now')), last_used_at TEXT)""")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT, description TEXT, check_procedure TEXT,
            confidence REAL DEFAULT 0.5, importance REAL DEFAULT 0.4,
            source_experience_ids TEXT,                    -- JSON array
            created_at TEXT DEFAULT (datetime('now')))""")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS experience_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exp_a_id INTEGER NOT NULL, exp_b_id INTEGER NOT NULL,
            weight REAL DEFAULT 0.1, coactivation_count INTEGER DEFAULT 0,
            last_coactivated_at TEXT, created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(exp_a_id, exp_b_id))""")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS experience_observation_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            experience_id INTEGER NOT NULL, observation_id INTEGER NOT NULL,
            weight REAL DEFAULT 0.1, coactivation_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(experience_id, observation_id))""")
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', '5')")
        self._conn.commit()

        # ---- V6: Multidimensional Memory (cues descompuestos, salience, anchors) ----
        # Columnas nuevas en experiences (guarded ALTER via _has_column).
        for col, ddl in [
            ("salience", "ALTER TABLE experiences ADD COLUMN salience REAL DEFAULT 0.0"),
            ("summary", "ALTER TABLE experiences ADD COLUMN summary TEXT"),
            ("entities", "ALTER TABLE experiences ADD COLUMN entities TEXT"),
            ("concepts", "ALTER TABLE experiences ADD COLUMN concepts TEXT"),
            ("files", "ALTER TABLE experiences ADD COLUMN files TEXT"),
            ("temporal_context", "ALTER TABLE experiences ADD COLUMN temporal_context TEXT"),
            ("session_id", "ALTER TABLE experiences ADD COLUMN session_id TEXT"),
            ("quest_id", "ALTER TABLE experiences ADD COLUMN quest_id TEXT"),
            ("retrieval_anchors", "ALTER TABLE experiences ADD COLUMN retrieval_anchors TEXT"),
            # FIX 3 (Tywin): dimensiones independientes — cada una evoluciona por su senal,
            # NUNCA atadas a confidence (association_strength = avg de pesos de experience_links;
            # retrieval_strength = reuso exitoso/fallido; frequency = veces reusada).
            ("association_strength", "ALTER TABLE experiences ADD COLUMN association_strength REAL DEFAULT 0.5"),
            ("retrieval_strength", "ALTER TABLE experiences ADD COLUMN retrieval_strength REAL DEFAULT 0.5"),
            ("frequency", "ALTER TABLE experiences ADD COLUMN frequency INTEGER DEFAULT 0"),
        ]:
            if not _has_column("experiences", col):
                self._conn.execute(ddl)
        self._conn.execute("""CREATE TABLE IF NOT EXISTS experience_cues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            experience_id INTEGER NOT NULL,
            component_type TEXT NOT NULL,   -- project|agent|concept|technology|problem|error|action|reasoning|solution|result|validation|temporal|file|quest|session|entity|pattern|anchor
            value TEXT NOT NULL,
            cue_quality REAL DEFAULT 0.5,
            source TEXT DEFAULT 'extracted', -- extracted|generated|manual
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(experience_id, component_type, value))""")
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', '6')")
        self._conn.commit()

        # ---- V7: Episode Pattern Completion (reactivacion post-retrieval) ----
        # Columnas de reactivacion en experience_cues (guarded ALTER via _has_column).
        # 'recordar tambien modifica la memoria': un cue que participa en una
        # recuperacion exitosa acumula coactivation_count y last_coactivated_at.
        for col, ddl in [
            ("coactivation_count", "ALTER TABLE experience_cues ADD COLUMN coactivation_count INTEGER DEFAULT 0"),
            ("last_coactivated_at", "ALTER TABLE experience_cues ADD COLUMN last_coactivated_at TEXT"),
        ]:
            if not _has_column("experience_cues", col):
                self._conn.execute(ddl)
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', '7')")
        self._conn.commit()

    # ---------- AGENTS ----------
    def upsert_agent(self, agent_id, name=None, cls=None, role=None, model=None):
        existing = self._conn.execute(
            "SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        if existing:
            self._conn.execute(
                "UPDATE agents SET name=COALESCE(?,name), class=COALESCE(?,class), "
                "role=COALESCE(?,role), model=COALESCE(?,model) WHERE id=?",
                (name, cls, role, model, agent_id))
        else:
            self._conn.execute(
                "INSERT INTO agents (id, name, class, role, model) VALUES (?,?,?,?,?)",
                (agent_id, name or agent_id, cls, role, model))
        self._conn.commit()
        return agent_id

    def list_agents(self):
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM agents ORDER BY id")]

    def get_agent(self, agent_id):
        return self._conn.execute(
            "SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()

    def add_xp(self, agent_id, xp_gain):
        self._conn.execute(
            "UPDATE agents SET xp=xp+?, level=1+(xp+?)/100 WHERE id=?",
            (xp_gain, xp_gain, agent_id))
        self._conn.commit()

    # ---------- OBSERVATIONS ----------
    @staticmethod
    def _default_confidence(type_name):
        return {"decision": 0.7, "verdict": 0.85, "pattern": 0.6, "discovery": 0.45,
                "preference": 0.6, "recommendation": 0.6, "action": 0.5, "bugfix": 0.65,
                "session_summary": 0.75}.get(type_name, 0.5)

    @staticmethod
    def _default_kind(type_name):
        return ("semantic" if type_name in ("decision", "pattern", "preference",
                                            "recommendation", "bugfix") else "episodic")

    def save_observation(self, agent, topic_key, type, content, quest_id=None, score=0,
                         tags=None, memory_kind=None, confidence=None, volatility="stable",
                         evidence=None, source=None):
        kind = memory_kind or self._default_kind(type)
        conf = confidence if confidence is not None else self._default_confidence(type)
        tags_json = json.dumps(tags, ensure_ascii=False) if tags else ""
        ev_json = json.dumps(evidence, ensure_ascii=False) if evidence else ""
        cur = self._conn.execute(
            "INSERT INTO observations (agent, topic_key, type, content, quest_id, score, tags, "
            "memory_kind, confidence, storage_strength, retrieval_strength, decay_base, volatility, state, "
            "evidence, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (agent, topic_key, type, content, quest_id, int(score), tags_json, kind, conf,
             0.4 if kind != "episodic" else 0.35, 0.6, 0.6, volatility, "active", ev_json, source or ""))
        obs_id = cur.lastrowid
        self._conn.commit()
        self._schedule_review(obs_id, importance=int(score), volatility=volatility, confidence=conf)
        return obs_id

    def save_observation_upsert(self, agent, topic_key, type, content, quest_id=None, score=0,
                                tags=None, memory_kind=None, confidence=None, volatility="stable",
                                evidence=None, source=None):
        """Upsert: si el topico existe, guarda la revision previa y actualiza (V3)."""
        row = self._conn.execute(
            "SELECT id, content, type, confidence, storage_strength, evidence FROM observations "
            "WHERE agent=? AND topic_key=? ORDER BY id DESC LIMIT 1",
            (agent, topic_key)).fetchone()
        if row:
            self._conn.execute(
                "INSERT INTO observation_revisions (observation_id, content, type) VALUES (?,?,?)",
                (row["id"], row["content"], row["type"]))
            kind = memory_kind or self._default_kind(type)
            conf = confidence if confidence is not None else self._default_confidence(type)
            tags_json = json.dumps(tags, ensure_ascii=False) if tags else ""
            ev_json = json.dumps(evidence, ensure_ascii=False) if evidence else ""
            self._conn.execute(
                "UPDATE observations SET content=?, type=?, quest_id=?, score=?, tags=?, "
                "memory_kind=?, confidence=?, volatility=?, evidence=?, source=? WHERE id=?",
                (content, type, quest_id, int(score), tags_json, kind, conf, volatility,
                 ev_json, source or "", row["id"]))
            self._conn.commit()
            return row["id"]
        return self.save_observation(agent, topic_key, type, content, quest_id, score, tags,
                                     memory_kind, confidence, volatility, evidence, source)

    def _schedule_review(self, obs_id, importance=0, volatility="stable", confidence=0.5):
        """Spaced review: cuan seguido revalidar segun volatilidad e importancia."""
        days = {"immutable": 365, "stable": 90, "slow": 45, "dynamic": 14, "ephemeral": 5}.get(volatility, 90)
        if importance >= 4:
            days = max(1, days // 2)
        next_at = (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                   + datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        self._conn.execute(
            "INSERT INTO memory_reviews (memory_id, next_review_at, importance, volatility, confidence) "
            "VALUES (?,?,?,?,?)", (obs_id, next_at, importance, volatility, confidence))
        self._conn.commit()

    def _effective_retrieval(self, row, now=None):
        """Retrieval strength efectiva con decay por volatilidad (olvido adaptativo, no borrado)."""
        lambdas = {"immutable": 365.0, "stable": 90.0, "slow": 45.0, "dynamic": 14.0, "ephemeral": 5.0}
        lam = lambdas.get(row.get("volatility", "stable"), 90.0)
        last = row.get("last_retrieved_at")
        base = float(row.get("retrieval_strength", 0.6))
        if not last:
            return base
        try:
            last_dt = datetime.datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
            now_dt = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            days = max(0.0, (now_dt - last_dt).total_seconds() / 86400.0)
            return base * (2.0 ** (-days / lam))
        except Exception:
            return base

    def recall(self, query, agent=None, limit=5, tag=None):
        """Recall V3: BM25 + retrieval efectivo + confianza + importancia. Practica de recuperacion."""
        raw = self.search(query, agent=agent, limit=limit * 3, tag=tag)
        scored = []
        for r in raw:
            rs = self._effective_retrieval(r)
            bm25 = -float(r.get("rank", 0.0))
            # ranking ponderado: BM25 .5 + retrieval .2 + confianza .15 + importancia .15
            total = bm25 * 0.5 + rs * 0.2 + float(r.get("confidence", 0.5)) * 0.15 + (float(r.get("score", 0)) / 5.0) * 0.15
            r["effective_retrieval"] = round(rs, 3)
            r["_recall_score"] = round(total, 4)
            scored.append(r)
        scored.sort(key=lambda x: x["_recall_score"], reverse=True)
        top = scored[:limit]
        # practica de recuperacion: sube retrieval_strength del usado (y su pico decay_base)
        for r in top:
            new_rs = min(1.0, float(r.get("retrieval_strength", 0.6)) + 0.05)
            old_base = float(r["decay_base"]) if r.get("decay_base") is not None else float(r.get("retrieval_strength", 0.6))
            base = max(old_base, new_rs)
            self._conn.execute(
                "UPDATE observations SET retrieval_strength=?, decay_base=?, "
                "last_retrieved_at=datetime('now') WHERE id=?",
                (new_rs, base, r["id"]))
        self._conn.commit()
        return top

    def reinforce(self, obs_id, evidence=None, success=True):
        """Fortalecer: sube storage/confianza solo con evidencia real (no por repeticion)."""
        row = self._conn.execute("SELECT * FROM observations WHERE id=?", (obs_id,)).fetchone()
        if not row:
            return False
        delta = 0.06 if evidence else 0.02   # sin evidencia: refuerzo minimo (evita 5x repeticion = verdad)
        if success:
            conf = min(0.99, float(row["confidence"]) + delta)
            storage = min(0.99, float(row["storage_strength"]) + (0.08 if evidence else 0.03))
        else:
            conf = max(0.05, float(row["confidence"]) - delta * 2)
            storage = max(0.05, float(row["storage_strength"]) - delta)
        self._conn.execute(
            "UPDATE observations SET confidence=?, storage_strength=? WHERE id=?",
            (conf, storage, obs_id))
        self._conn.commit()
        return True

    def verify(self, obs_id, verdict, evidence=None):
        """Tywin: PASS sube confianza con evidencia; FAIL baja y puede marcar contested."""
        row = self._conn.execute("SELECT * FROM observations WHERE id=?", (obs_id,)).fetchone()
        if not row:
            return False
        if verdict == "PASS":
            conf = min(0.99, float(row["confidence"]) + 0.10)
            storage = min(0.99, float(row["storage_strength"]) + 0.10)
            state = "active"
        elif verdict == "FAIL":
            conf = max(0.05, float(row["confidence"]) - 0.15)
            storage = max(0.05, float(row["storage_strength"]) - 0.10)
            state = "contested" if float(row["confidence"]) >= 0.6 else row["state"]
        else:
            return False
        self._conn.execute(
            "UPDATE observations SET confidence=?, storage_strength=?, state=? WHERE id=?",
            (conf, storage, state, obs_id))
        self._conn.commit()
        return True

    def reconsolidate(self, obs_id, new_content, evidence=None):
        """Reconsolidacion: revision + actualizacion con evidencia (nunca sobrescribir en silencio)."""
        row = self._conn.execute("SELECT * FROM observations WHERE id=?", (obs_id,)).fetchone()
        if not row:
            return False
        self._conn.execute(
            "INSERT INTO observation_revisions (observation_id, content, type) VALUES (?,?,?)",
            (obs_id, row["content"], row["type"]))
        ev_json = json.dumps(evidence, ensure_ascii=False) if evidence else row["evidence"]
        base = float(row["decay_base"]) if row["decay_base"] is not None else float(row["retrieval_strength"])
        self._conn.execute(
            "UPDATE observations SET content=?, evidence=?, decay_base=?, "
            "storage_strength=?, state='active' WHERE id=?",
            (new_content, ev_json, base, min(0.99, float(row["storage_strength"]) + 0.05), obs_id))
        self._conn.commit()
        return True

    def search(self, query, agent=None, limit=20, tag=None):
        """Busqueda FTS5 con ranking BM25 (relevancia real). Fallback: por importancia
        si las keywords no coinciden (sinonimos, preguntas parafraseadas)."""
        words = _fts_escape(query).split()
        # intentos progresivos: todas las palabras (AND) -> primeras 2 -> primera (prefix)
        attempts = []
        if words:
            attempts.append(words)
            if len(words) > 2:
                attempts.append(words[:2])
            attempts.append(words[:1])

        for attempt in attempts:
            q = " ".join(w + "*" for w in attempt)
            params = []
            sql = ("SELECT o.*, bm25(observations_fts) AS rank FROM observations o "
                   "JOIN observations_fts f ON o.id=f.rowid "
                   "WHERE observations_fts MATCH ? AND o.archived=0 AND o.state != 'archived'")
            params.append(q)
            if agent:
                sql += " AND o.agent=?"
                params.append(agent)
            if tag:
                sql += " AND o.tags LIKE ?"
                params.append("%" + tag + "%")
            sql += " ORDER BY rank LIMIT ?"
            params.append(limit)
            rows = [dict(r) for r in self._conn.execute(sql, params)]
            if rows:
                return rows

        # fallback por importancia: memorias activas top-score (recuperar senal correcta)
        params = []
        sql = ("SELECT * FROM observations WHERE archived=0 AND state != 'archived' "
               "AND score >= 4 ORDER BY score DESC, created_at DESC LIMIT ?")
        params.append(limit)
        if agent:
            sql = sql.replace("WHERE archived=0 AND state != 'archived' ",
                              "WHERE archived=0 AND state != 'archived' AND agent=? ")
            params.insert(0, agent)
        if tag:
            sql = sql.replace(" AND score >= 4", " AND tags LIKE ? AND score >= 4")
            params.insert(-1, "%" + tag + "%")
        return [dict(r) for r in self._conn.execute(sql, params)]

    def agent_memory(self, agent, limit=50):
        """Memoria completa de un agente - namespace privado (relevantes primero)."""
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM observations WHERE agent=? AND archived=0 "
            "ORDER BY score DESC, created_at DESC LIMIT ?",
            (agent, limit))]

    def recent_context(self, limit=30):
        """Contexto reciente del harness - para arranque de sesion (importantes primero)."""
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM observations WHERE archived=0 "
            "ORDER BY score DESC, created_at DESC LIMIT ?",
            (limit,))]

    def list_revisions(self, obs_id, limit=50):
        """Historial de revisiones de una observacion (la memoria de la memoria)."""
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM observation_revisions WHERE observation_id=? ORDER BY id DESC LIMIT ?",
            (obs_id, limit))]

    def compact(self, older_than_days=30):
        """El sueno: resume observaciones antiguas en un digest por agente y las archiva."""
        cutoff = (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                  - datetime.timedelta(days=older_than_days)).strftime("%Y-%m-%d %H:%M:%S")
        rows = [dict(r) for r in self._conn.execute(
            "SELECT * FROM observations WHERE archived=0 AND created_at < ? ORDER BY agent, created_at",
            (cutoff,))]
        by_agent = {}
        for r in rows:
            by_agent.setdefault(r["agent"], []).append(r)
        digests = 0
        for agent, obs in by_agent.items():
            digest = "\n".join("[{0}] {1}".format(o["topic_key"], o["content"]) for o in obs)
            if len(digest) > 12000:
                digest = digest[:12000] + "..."
            self._conn.execute(
                "INSERT INTO observations (agent, topic_key, type, content, score, tags) VALUES (?,?,?,?,?,?)",
                (agent, "{0}/compact-{1}".format(agent, datetime.date.today().isoformat()),
                 "session_summary", digest, 5, '["compact"]'))
            ids = [o["id"] for o in obs]
            placeholders = ",".join("?" * len(ids))
            self._conn.execute(
                "UPDATE observations SET archived=1, state='archived' WHERE id IN ({0})".format(placeholders), ids)
            digests += 1
        self._conn.commit()
        return {"compacted": len(rows), "digests": digests, "cutoff": cutoff}

    # ---------- V3: SKILL MASTERY (aprendizaje procedural por proyecto) ----------
    def skill_register(self, skill_id, version="1.0", triggers=None, anti_triggers=None):
        row = self._conn.execute("SELECT * FROM skill_mastery WHERE skill_id=?", (skill_id,)).fetchone()
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        if row:
            version_changed = row["skill_version"] != version
            self._conn.execute(
                "UPDATE skill_mastery SET skill_version=?, trigger_patterns=?, anti_trigger_patterns=?, "
                "state=?, mastery=?, updated_at=? WHERE skill_id=?",
                (version,
                 json.dumps(triggers or [], ensure_ascii=False),
                 json.dumps(anti_triggers or [], ensure_ascii=False),
                 "needs_review" if version_changed else row["state"],
                 float(row["mastery"]) * 0.5 if version_changed else row["mastery"],
                 now, skill_id))
        else:
            self._conn.execute(
                "INSERT INTO skill_mastery (skill_id, skill_version, state, mastery, confidence, "
                "trigger_patterns, anti_trigger_patterns, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (skill_id, version, "new", 0.0, 0.0,
                 json.dumps(triggers or [], ensure_ascii=False),
                 json.dumps(anti_triggers or [], ensure_ascii=False), now))
        self._conn.commit()
        return skill_id

    def skill_record_execution(self, skill_id, version="1.0", agent=None, quest_id=None, trigger=None,
                               success=True, verdict=None, evidence=None, error=None,
                               tokens_in=0, tokens_out=0, tool_calls=0, model=None, provider=None):
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        self._conn.execute(
            "INSERT INTO skill_executions (skill_id, skill_version, agent, quest_id, trigger, "
            "success, verdict, evidence, error, tokens_in, tokens_out, tool_calls, model, provider, "
            "started_at, finished_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (skill_id, version, agent, quest_id, trigger, 1 if success else 0, verdict,
             evidence or "", error or "", tokens_in, tokens_out, tool_calls, model, provider,
             now, now, now))
        # actualizar mastery
        sm = self._conn.execute("SELECT * FROM skill_mastery WHERE skill_id=?", (skill_id,)).fetchone()
        if not sm:
            self.skill_register(skill_id, version)
            sm = self._conn.execute("SELECT * FROM skill_mastery WHERE skill_id=?", (skill_id,)).fetchone()
        success_count = int(sm["success_count"]) + (1 if success else 0)
        failure_count = int(sm["failure_count"]) + (0 if success else 1)
        consec = (int(sm["consecutive_successes"]) + 1) if success else 0
        total = max(1, success_count + failure_count)
        mastery = round((success_count / total) * (0.6 + 0.4 * min(1.0, total / 10.0)), 3)
        # transiciones de estado
        state = sm["state"]
        if success and mastery >= 0.8 and total >= 8 and consec >= 3:
            state = "mastered"
        elif success and mastery >= 0.6 and total >= 3:
            state = "reliable" if state in ("new", "learning") else state
        elif success and state == "new":
            state = "learning"
        elif not success and state == "mastered":
            state = "needs_review"
        elif not success and failure_count >= 5 and success_count == 0:
            state = "quarantined"
        avg_tokens = ((float(sm["avg_tokens"]) * (total - 1)) + (tokens_in + tokens_out)) / total if total > 1 else (tokens_in + tokens_out)
        self._conn.execute(
            "UPDATE skill_mastery SET state=?, mastery=?, success_count=?, failure_count=?, "
            "consecutive_successes=?, avg_tokens=?, last_used_at=?, "
            "last_success_at=?, last_failure_at=?, confidence=?, updated_at=? WHERE skill_id=?",
            (state, mastery, success_count, failure_count, consec, round(avg_tokens, 1),
             now, now if success else sm["last_success_at"], now if not success else sm["last_failure_at"],
             round(min(0.95, mastery * 0.9 + 0.05), 3), now, skill_id))
        self._conn.commit()
        return {"skill": skill_id, "state": state, "mastery": mastery,
                "success": success, "total": total}

    def skill_status(self, skill_id=None):
        if skill_id:
            row = self._conn.execute("SELECT * FROM skill_mastery WHERE skill_id=?", (skill_id,)).fetchone()
            return dict(row) if row else None
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM skill_mastery ORDER BY mastery DESC")]

    def skill_links(self, memory_id, skill_id, relation="supports", success=True):
        """Refuerza/debilita el vinculo memoria<->skill con resultados verificados."""
        row = self._conn.execute(
            "SELECT * FROM skill_memory_links WHERE memory_id=? AND skill_id=?",
            (memory_id, skill_id)).fetchone()
        if row:
            sc = int(row["success_count"]) + (1 if success else 0)
            fc = int(row["failure_count"]) + (0 if success else 1)
            total = max(1, sc + fc)
            weight = round(sc / total, 3)
            self._conn.execute(
                "UPDATE skill_memory_links SET weight=?, success_count=?, failure_count=?, "
                "last_used=datetime('now') WHERE id=?", (weight, sc, fc, row["id"]))
        else:
            self._conn.execute(
                "INSERT INTO skill_memory_links (memory_id, skill_id, relation, weight, "
                "success_count, failure_count, last_used) VALUES (?,?,?,?,?,?,datetime('now'))",
                (memory_id, skill_id, relation, 0.5, 1 if success else 0, 0 if success else 1))
        self._conn.commit()

    # ---------- V3: COGNITIVE EFFORT ROUTER (FAST | RECALL | SKILL | DELIBERATE | DEEP) ----------
    def route(self, query, risk_text=None):
        """Decide cuanta cognicion gastar. NO confundir recuperacion facil con verdad."""
        risk_markers = ["drop", "delete from", "truncate", "production", "deploy", "migra",
                        "security", "secret", "password", "token", "rls", "permisos", "database"]
        risk = sum(1 for m in risk_markers if m in (query + " " + (risk_text or "")).lower())
        hits = self.recall(query, limit=3)
        top = hits[0] if hits else None
        if top:
            conf = float(top.get("confidence", 0.5))
            state = top.get("state", "active")
            vol = top.get("volatility", "stable")
            rs = float(top.get("effective_retrieval", 0.6))
            if conf >= 0.9 and state == "active" and vol != "ephemeral" and risk == 0:
                return {"path": "FAST", "reason": "hecho estable con confianza %.2f" % conf,
                        "memory": top.get("id")}
            if conf >= 0.7 and rs >= 0.3:
                return {"path": "RECALL", "reason": "memoria recuperable (conf %.2f, rs %.2f)" % (conf, rs),
                        "memory": top.get("id")}
        # skill matching
        skills = self._conn.execute(
            "SELECT * FROM skill_mastery WHERE mastery >= 0.8 AND state IN ('reliable','mastered')").fetchall()
        for s in skills:
            triggers = json.loads(s["trigger_patterns"] or "[]")
            if any(t.lower() in query.lower() for t in triggers):
                return {"path": "SKILL", "reason": "patron conocido -> %s (mastery %.2f)" % (s["skill_id"], s["mastery"]),
                        "skill": s["skill_id"]}
        if risk >= 2 or (top and float(top.get("confidence", 0)) < 0.35):
            return {"path": "DEEP", "reason": "riesgo %d o confianza baja -> orquestacion completa" % risk}
        return {"path": "DELIBERATE", "reason": "contexto nuevo o confianza media -> razonar con herramientas"}

    def list_due_reviews(self, limit=20):
        """Spaced review: memorias que toca revalidar."""
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        return [dict(r) for r in self._conn.execute(
            "SELECT r.*, o.topic_key, o.content FROM memory_reviews r "
            "JOIN observations o ON o.id=r.memory_id "
            "WHERE r.next_review_at <= ? ORDER BY r.next_review_at LIMIT ?", (now, limit))]

    # ---------- V3: AUTONOMOUS PARTY (task graph) ----------
    def quest_create(self, quest_id, description, mode="balanced"):
        self._conn.execute(
            "INSERT OR REPLACE INTO autonomous_quests (id, description, status, mode) "
            "VALUES (?,?,?,?)", (quest_id, description, "active", mode))
        self._conn.commit()
        return quest_id

    def quest_update(self, quest_id, **fields):
        sets = []
        params = []
        for k, v in fields.items():
            if v is None:
                continue
            sets.append("{0}=?".format(k))
            params.append(json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
        if not sets:
            return False
        params.append(quest_id)
        self._conn.execute("UPDATE autonomous_quests SET {0}, updated_at=datetime('now') WHERE id=?".format(", ".join(sets)), params)
        self._conn.commit()
        return True

    def quest_get(self, quest_id):
        row = self._conn.execute("SELECT * FROM autonomous_quests WHERE id=?", (quest_id,)).fetchone()
        if not row:
            return None
        q = dict(row)
        for k in ("party", "progress"):
            try:
                q[k] = json.loads(q.get(k) or "{}" if k == "progress" else q.get(k) or "[]")
            except Exception:
                q[k] = [] if k == "party" else {}
        return q

    def quest_list(self, limit=10):
        return [dict(r) for r in self._conn.execute(
            "SELECT id, description, status, mode, created_at FROM autonomous_quests "
            "ORDER BY created_at DESC LIMIT ?", (limit,))]

    def task_create(self, quest_id, task_id, description, agent, dependencies=None, acceptance=""):
        cur = self._conn.execute(
            "INSERT INTO autonomous_tasks (quest_id, task_id, description, agent, dependencies, acceptance) "
            "VALUES (?,?,?,?,?,?)",
            (quest_id, task_id, description, agent,
             json.dumps(dependencies or []), acceptance))
        self._conn.commit()
        return cur.lastrowid

    def task_list(self, quest_id=None, status=None):
        sql = "SELECT * FROM autonomous_tasks"
        params = []
        if quest_id:
            sql += " WHERE quest_id=?"
            params.append(quest_id)
        if status:
            sql += (" AND status=?" if quest_id else " WHERE status=?")
            params.append(status)
        sql += " ORDER BY id"
        return [dict(r) for r in self._conn.execute(sql, params)]

    def task_update(self, task_id, **fields):
        sets = []
        params = []
        for k, v in fields.items():
            if v is None:
                continue
            sets.append("{0}=?".format(k))
            params.append(json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
        if not sets:
            return False
        params.append(task_id)
        self._conn.execute(
            "UPDATE autonomous_tasks SET {0}, updated_at=datetime('now') WHERE id=?".format(", ".join(sets)), params)
        self._conn.commit()
        return True

    def task_ready(self, quest_id):
        """Tareas listas para ejecutar: pendientes cuyas dependencias estan PASS."""
        tasks = self.task_list(quest_id)
        by_id = {t["task_id"]: t for t in tasks}
        ready = []
        for t in tasks:
            if t["status"] != "pending":
                continue
            deps = json.loads(t.get("dependencies") or "[]")
            if all(by_id.get(d, {}).get("status") == "pass" for d in deps):
                ready.append(t)
        return ready

    def quest_progress(self, quest_id):
        tasks = self.task_list(quest_id)
        counts = {}
        for t in tasks:
            counts[t["status"]] = counts.get(t["status"], 0) + 1
        total = max(1, len(tasks))
        done = counts.get("pass", 0)
        return {"total": len(tasks), "pass": counts.get("pass", 0),
                "running": counts.get("running", 0), "ready": counts.get("ready", 0),
                "blocked": counts.get("blocked", 0), "fail": counts.get("fail", 0),
                "pct": round(done / total * 100)}

    # ---------- V3: COGNITIVE COMPACTION (checkpoints + recuperacion) ----------
    _CP_COLS = ["session_id", "quest_id", "agent", "reason", "goal", "phase",
                "completed_tasks", "pending_tasks", "active_files", "modified_files",
                "active_decisions", "critical_memory_ids", "active_skill", "skill_state",
                "blockers", "errors", "test_state", "build_state", "git_state",
                "next_action", "working_memory_ids", "tokens_used", "context_usage", "metadata"]

    def create_checkpoint(self, data):
        """Checkpoint cognitivo estructurado (JSON en columnas, no markdown gigante)."""
        vals = {k: data.get(k) for k in self._CP_COLS}
        for k in ["completed_tasks", "pending_tasks", "active_files", "modified_files",
                  "active_decisions", "critical_memory_ids", "blockers", "errors",
                  "working_memory_ids"]:
            v = vals[k]
            vals[k] = json.dumps(v, ensure_ascii=False) if v is not None else "[]"
        for k in ["skill_state", "metadata"]:
            v = vals[k]
            vals[k] = json.dumps(v, ensure_ascii=False) if v is not None else "{}"
        cols = ",".join(self._CP_COLS)
        ph = ",".join("?" * len(self._CP_COLS))
        cur = self._conn.execute(
            "INSERT INTO cognitive_checkpoints ({0}) VALUES ({1})".format(cols, ph),
            [vals[k] for k in self._CP_COLS])
        cp_id = cur.lastrowid
        cp = dict(self._conn.execute("SELECT * FROM cognitive_checkpoints WHERE id=?", (cp_id,)).fetchone())
        score = self.continuity_score(cp)
        capsule = self.build_recovery_capsule(cp)
        self._conn.execute(
            "UPDATE cognitive_checkpoints SET continuity_score=?, recovery_capsule=? WHERE id=?",
            (score, capsule, cp_id))
        self._conn.commit()
        return {"id": cp_id, "continuity_score": score}

    def get_checkpoint(self, cp_id):
        row = self._conn.execute("SELECT * FROM cognitive_checkpoints WHERE id=?", (cp_id,)).fetchone()
        if not row:
            return None
        cp = dict(row)
        for k in ["completed_tasks", "pending_tasks", "active_files", "modified_files",
                  "active_decisions", "critical_memory_ids", "blockers", "errors",
                  "working_memory_ids", "skill_state", "metadata"]:
            try:
                cp[k] = json.loads(cp.get(k) or "[]" if k in ("skill_state", "metadata") else cp.get(k) or "[]")
            except Exception:
                cp[k] = []
        return cp

    def list_checkpoints(self, limit=10):
        return [dict(r) for r in self._conn.execute(
            "SELECT id, quest_id, agent, goal, next_action, continuity_score, created_at "
            "FROM cognitive_checkpoints ORDER BY id DESC LIMIT ?", (limit,))]

    def build_recovery_capsule(self, cp):
        """Capsula de recuperacion determinista (500-1500 tkns): estado minimo para continuar."""
        def _loads(x):
            if x is None:
                return []
            if isinstance(x, (list, dict)):
                return x
            try:
                return json.loads(x) if x else []
            except Exception:
                return []
        lines = ["[ARGOS RECOVERY CAPSULE]"]
        proj = os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(self.db_path))))
        lines.append("Project: " + proj)
        if cp.get("quest_id"):
            lines.append("Quest: " + cp["quest_id"])
        if cp.get("agent"):
            lines.append("Agent: " + cp["agent"])
        if cp.get("goal"):
            lines.append("Goal: " + cp["goal"])
        comp = _loads(cp.get("completed_tasks"))
        if comp:
            lines.append("Completed:")
            lines += ["- " + str(t) for t in comp[:6]]
        pend = _loads(cp.get("pending_tasks"))
        if pend:
            lines.append("Pending:")
            lines += ["- " + str(t) for t in pend[:6]]
        decs = _loads(cp.get("critical_memory_ids"))
        if decs:
            lines.append("Critical decisions: " + " ".join("#" + str(d) for d in decs[:6]))
        skill = cp.get("active_skill")
        if skill:
            ss = cp.get("skill_state") or {}
            stage = ss.get("stage", "") if isinstance(ss, dict) else ""
            lines.append("Active procedure: " + skill + ((" (stage: " + stage + ")") if stage else ""))
        blocks = _loads(cp.get("blockers"))
        if blocks:
            lines.append("Current blocker: " + str(blocks[0]))
        if cp.get("next_action"):
            lines.append("NEXT ACTION: " + cp["next_action"])
        capsule = "\n".join(lines)
        if len(capsule) > 1500:
            capsule = capsule[:1500] + "\n..."
        return capsule

    def continuity_score(self, cp):
        """% de campos criticos restaurados (goal/quest/agent/next/blockers/decisions/skill/pending)."""
        fields = {
            "goal": cp.get("goal"),
            "quest": cp.get("quest_id"),
            "agent": cp.get("agent"),
            "next_action": cp.get("next_action"),
            "blockers": cp.get("blockers"),
            "decisions": cp.get("critical_memory_ids"),
            "skill": cp.get("active_skill"),
            "pending": cp.get("pending_tasks"),
        }
        ok = 0
        for k, v in fields.items():
            if isinstance(v, (list, dict)):
                if v:
                    ok += 1
            elif v not in (None, ""):
                ok += 1
        return round(ok / len(fields), 2)

    def salience(self, content, type_name, score, tags, evidence):
        """Senal determinista de consolidacion: que merece guardarse (no confundir con verdad)."""
        s = 0.0
        s += {"decision": 2.0, "verdict": 3.0, "bugfix": 2.0, "pattern": 1.5,
              "preference": 1.0, "recommendation": 1.5, "action": 1.0}.get(type_name, 0.5)
        s += min(2.0, float(score or 0) / 5.0 * 2.0)
        if evidence:
            s += 2.0
        low = ["voy a revisar", "vamos a", "puedes", "podrias", "hola", "ok", "gracias",
               "dime", "revisa esto", "prueba esto", "dale", "ya quedo"]
        c = (content or "").lower()
        if any(w in c for w in low):
            s -= 1.5
        tags_s = " ".join(tags or []).lower()
        for k in ["schema", "arquitectura", "architecture", "seguridad", "security",
                  "constraint", "decision", "bug"]:
            if k in tags_s:
                s += 1.0
        return max(0.0, min(8.0, s))

    def consolidate_recent(self, hours=24):
        """PRE-COMPACTION: clasifica la experiencia reciente (working/episodic/semantic/procedural/noise)."""
        cutoff = (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                  - datetime.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        rows = [dict(r) for r in self._conn.execute(
            "SELECT * FROM observations WHERE archived=0 AND created_at >= ?", (cutoff,))]
        cls = {"working": 0, "episodic": 0, "semantic": 0, "procedural": 0, "noise": 0, "classified": len(rows)}
        for r in rows:
            tags = json.loads(r.get("tags") or "[]")
            sal = self.salience(r.get("content", ""), r.get("type", "discovery"),
                                r.get("score", 0), tags, r.get("evidence"))
            kind = r.get("memory_kind", "episodic")
            if sal >= 4.0:
                new_kind = "procedural" if (kind == "procedural" or sal >= 5.5) else "semantic"
                self._conn.execute(
                    "UPDATE observations SET memory_kind=?, "
                    "storage_strength=min(0.9, storage_strength+0.1) WHERE id=?",
                    (new_kind, r["id"]))
                cls[new_kind] += 1
            elif sal <= 1.0:
                self._conn.execute(
                    "UPDATE observations SET archived=1, state='archived' WHERE id=?", (r["id"],))
                cls["noise"] += 1
            elif kind == "episodic":
                cls["episodic"] += 1
            else:
                cls["working"] += 1
        self._conn.commit()
        return cls

    def get_observation(self, obs_id):
        return self._conn.execute(
            "SELECT * FROM observations WHERE id=?", (obs_id,)).fetchone()

    def update_observation(self, obs_id, content=None, topic_key=None):
        if content is not None:
            self._conn.execute("UPDATE observations SET content=? WHERE id=?",
                               (content, obs_id))
        if topic_key is not None:
            self._conn.execute("UPDATE observations SET topic_key=? WHERE id=?",
                               (topic_key, obs_id))
        self._conn.commit()
        return True

    def delete_observation(self, obs_id):
        self._conn.execute("DELETE FROM observations WHERE id=?", (obs_id,))
        self._conn.commit()

    def recent_context(self, limit=30):
        """Contexto reciente del harness - para arranque de sesion."""
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM observations ORDER BY created_at DESC LIMIT ?", (limit,))]

    def count_observations(self):
        return self._conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]

    # ---------- QUESTS ----------
    def save_quest(self, quest_id, description, quest_type, party, result, tokens_used=0):
        self._conn.execute(
            "INSERT INTO quests (id, description, quest_type, party, result, tokens_used, completed_at) "
            "VALUES (?,?,?,?,?,?,datetime('now')) "
            "ON CONFLICT(id) DO UPDATE SET result=excluded.result, "
            "tokens_used=excluded.tokens_used, completed_at=excluded.completed_at",
            (quest_id, description, quest_type, json.dumps(party), result, tokens_used))
        self._conn.commit()
        return quest_id

    def quest_history(self, limit=50):
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM quests ORDER BY created_at DESC LIMIT ?", (limit,))]

    def next_quest_id(self):
        row = self._conn.execute("SELECT MAX(id) FROM quests").fetchone()
        if not row or not row[0]:
            return "Q-001"
        num = int(row[0].replace("Q-", "")) + 1
        return f"Q-{num:03d}"

    # ---------- SESSIONS ----------
    def start_session(self):
        cur = self._conn.execute("INSERT INTO sessions DEFAULT VALUES")
        self._conn.commit()
        return cur.lastrowid

    def end_session(self, session_id, summary=None):
        self._conn.execute(
            "UPDATE sessions SET ended_at=datetime('now'), summary=? WHERE id=?",
            (summary, session_id))
        self._conn.commit()

    # ---------- EDGES (FASE 2) ----------
    def add_edge(self, node_a, node_b, relation, agent=None, quest_id=None):
        cur = self._conn.execute(
            "INSERT INTO edges (node_a, node_b, relation, agent, quest_id) VALUES (?,?,?,?,?)",
            (node_a, node_b, relation, agent, quest_id))
        self._conn.commit()
        return cur.lastrowid

    def query_edges(self, node=None, relation=None, agent=None):
        sql = "SELECT * FROM edges WHERE 1=1"
        params = []
        if node:
            sql += " AND (node_a=? OR node_b=?)"
            params += [node, node]
        if relation:
            sql += " AND relation=?"
            params.append(relation)
        if agent:
            sql += " AND agent=?"
            params.append(agent)
        sql += " ORDER BY created_at DESC LIMIT 100"
        return [dict(r) for r in self._conn.execute(sql, params)]

    def neighbors(self, node, max_depth=1, relation=None):
        """Vecinos de un nodo hasta profundidad max_depth (recorrido de relaciones)."""
        if max_depth < 1:
            return []
        visited = {node: 0}
        queue = [node]
        result = []
        while queue:
            current = queue.pop(0)
            depth = visited[current]
            if depth >= max_depth:
                continue
            sql = "SELECT * FROM edges WHERE node_a=? OR node_b=?"
            params = [current, current]
            if relation:
                sql += " AND relation=?"
                params.append(relation)
            for e in self._conn.execute(sql, params):
                other = e["node_b"] if e["node_a"] == current else e["node_a"]
                if other not in visited:
                    visited[other] = depth + 1
                    queue.append(other)
                    result.append({
                        "from": current, "to": other,
                        "relation": e["relation"], "agent": e["agent"],
                        "depth": depth + 1
                    })
        return result

    def path(self, start, end, max_depth=6):
        """BFS path-finding: encuentra el camino mas corto entre dos nodos."""
        if start == end:
            return [{"from": start, "to": end, "relation": "self"}]
        visited = {start}
        queue = [(start, [])]
        while queue:
            current, path = queue.pop(0)
            if len(path) >= max_depth:
                continue
            for e in self._conn.execute(
                    "SELECT * FROM edges WHERE node_a=? OR node_b=?", (current, current)):
                other = e["node_b"] if e["node_a"] == current else e["node_a"]
                edge_info = {"from": current, "to": other, "relation": e["relation"]}
                if other == end:
                    return path + [edge_info]
                if other not in visited:
                    visited.add(other)
                    queue.append((other, path + [edge_info]))
        return []

    def graph_stats(self):
        """Estadisticas del grafo: nodos, edges, relaciones mas comunes."""
        edges = [dict(r) for r in self._conn.execute("SELECT * FROM edges")]
        nodes = set()
        for e in edges:
            nodes.add(e["node_a"])
            nodes.add(e["node_b"])
        relations = {}
        for e in edges:
            relations[e["relation"]] = relations.get(e["relation"], 0) + 1
        agents = {}
        for e in edges:
            if e["agent"]:
                agents[e["agent"]] = agents.get(e["agent"], 0) + 1
        return {
            "nodes": len(nodes),
            "edges": len(edges),
            "relations": relations,
            "agents_active": agents,
        }

    # ---------- OSMA V4: MEMORIA ASOCIATIVA (co-activacion, activacion, consolidacion) ----------
    _VOL_LAMBDAS = {"immutable": 365.0, "stable": 90.0, "slow": 45.0, "dynamic": 14.0, "ephemeral": 5.0}

    @staticmethod
    def _parse_dt(s):
        """Parsea 'YYYY-MM-DD HH:MM:SS' o 'YYYY-MM-DD' (tolerando 'T' separador). None si falla."""
        if not s:
            return None
        s = str(s).strip().replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None

    # OSMA V4 API - 15 métodos: 11 públicos osma_* + 4 helpers privados _*
    #   públicos:  osma_migrate, osma_link, osma_recall, osma_reinforce, osma_context,
    #              osma_contradictions, osma_contradiction_resolve, osma_sleep,
    #              osma_consolidations, osma_consolidation_finalize, osma_stats
    #   privados:  _entity_tokens, _shared_entity_count, _upsert_link, _spreading_activation
    @staticmethod
    def _entity_tokens(content, tags, topic_key):
        """Tokens-entidad deterministas: minusculas, >=3 chars, sin stopwords. Sin libs externas."""
        tag_str = ""
        if tags:
            if isinstance(tags, str):
                try:
                    parsed = json.loads(tags)
                    tag_str = " ".join(parsed) if isinstance(parsed, list) else tags
                except Exception:
                    tag_str = tags
            elif isinstance(tags, (list, tuple)):
                tag_str = " ".join(str(t) for t in tags)
        text = " ".join([content or "", tag_str, topic_key or ""]).lower()
        tokens = re.findall(r"[a-z\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1\u00fc0-9]+", text)
        return sorted({t for t in tokens if len(t) >= 3 and t not in _OSMA_STOPWORDS})

    def _shared_entity_count(self, row_a, row_b):
        ea = set(self._entity_tokens(row_a.get("content"), row_a.get("tags"), row_a.get("topic_key")))
        eb = set(self._entity_tokens(row_b.get("content"), row_b.get("tags"), row_b.get("topic_key")))
        return len(ea & eb)

    def _project_name(self):
        try:
            return os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(self.db_path))))
        except Exception:
            return ""

    @staticmethod
    def _has_conflict_signal(text):
        """Senales de contradiccion: stems de cambio + palabras de negacion (con frontera)."""
        t = (text or "").lower()
        stems = ["migr", "reemplaz", "deprecad", "sustitu", "ya no", "no usar", "no funciona",
                 "no aplica", "deja de", "dejo de", "eliminar", "quitar", "evitar", "rompe",
                 "ya no se usa", "no se usa", "cancelad", "revertir", "invalida", "contradic"]
        for s in stems:
            if s in t:
                return True
        for w in ("no", "nunca", "jamas", "falso", "incorrecto", "imposible"):
            if re.search(r"\b" + re.escape(w) + r"\b", t):
                return True
        return False

    def _upsert_link(self, a_id, b_id, delta=0.1, coact=0, succ=0, fail=0, now=None):
        """Crea o fortalece un link associativo (undirected; UNIQUE obs_a_id < obs_b_id)."""
        a_id, b_id = int(a_id), int(b_id)
        lo, hi = (a_id, b_id) if a_id < b_id else (b_id, a_id)
        row = self._conn.execute(
            "SELECT * FROM observation_links WHERE obs_a_id=? AND obs_b_id=?", (lo, hi)).fetchone()
        ts = now or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        if row:
            new_w = max(0.0, min(1.0, float(row["weight"]) + delta))
            self._conn.execute(
                "UPDATE observation_links SET weight=?, coactivation_count=coactivation_count+?, "
                "successful_coactivations=successful_coactivations+?, "
                "failed_coactivations=failed_coactivations+?, last_coactivated_at=? WHERE id=?",
                (new_w, coact, succ, fail, ts, row["id"]))
            return row["id"]
        self._conn.execute(
            "INSERT INTO observation_links (obs_a_id, obs_b_id, weight, coactivation_count, "
            "successful_coactivations, failed_coactivations, last_coactivated_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (lo, hi, max(0.0, min(1.0, delta)), coact, succ, fail, ts, ts))
        return self._conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def _link_on_write(self, obs_id, content, tags, topic_key):
        """Post-save: enlaza la observacion nueva con activas que compartan >=2 entidades."""
        try:
            new_entities = set(self._entity_tokens(content, tags, topic_key))
        except Exception:
            return 0
        if not new_entities:
            return 0
        candidates = [dict(r) for r in self._conn.execute(
            "SELECT id, content, tags, topic_key FROM observations "
            "WHERE archived=0 AND state NOT IN ('archived','superseded') AND id != ? ORDER BY id",
            (obs_id,))]
        linked = 0
        for c in candidates:
            c_entities = set(self._entity_tokens(c["content"], c["tags"], c["topic_key"]))
            if len(new_entities & c_entities) >= 2:
                self._upsert_link(obs_id, c["id"], delta=0.1, coact=1)
                linked += 1
        if linked:
            self._conn.commit()
        return linked

    def osma_migrate(self):
        """Backfill V4+V5 idempotente: observation_links (>=2 entidades) + experience_links (>=2 entidades).
        El return conserva schema_version '4' byte-identico (compat con tests V4); la meta del
        cerebro queda en '5' (los nuevos campos V5 se reportan aparte)."""
        rows = [dict(r) for r in self._conn.execute(
            "SELECT id, content, tags, topic_key FROM observations "
            "WHERE archived=0 AND state NOT IN ('archived','superseded') ORDER BY id")]
        linked = 0
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                if self._shared_entity_count(rows[i], rows[j]) >= 2:
                    self._upsert_link(rows[i]["id"], rows[j]["id"], delta=0.1)
                    linked += 1
        # V5: enlazar experiencias existentes que compartan >=2 entidades
        exps = [dict(r) for r in self._conn.execute(
            "SELECT * FROM experiences ORDER BY id")]
        exp_linked = 0
        for i in range(len(exps)):
            for j in range(i + 1, len(exps)):
                if self._shared_entity_count(self._exp_as_obs_row(exps[i]),
                                             self._exp_as_obs_row(exps[j])) >= 2:
                    self._upsert_experience_link(exps[i]["id"], exps[j]["id"], delta=0.1)
                    exp_linked += 1
        # V6: descomponer las experiencias existentes en cues (idempotente:
        #     INSERT OR IGNORE por UNIQUE(experience_id, component_type, value)).
        self._conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', '6')")
        analyze = self.osma_experience_analyze()
        # V7: columnas de reactivacion en experience_cues (guarded; idempotente sobre la
        #     migracion hecha por _migrate al abrir la db).
        def _has_column(table, col):
            return any(r["name"] == col for r in self._conn.execute("PRAGMA table_info(%s)" % table))
        for col, ddl in [
            ("coactivation_count", "ALTER TABLE experience_cues ADD COLUMN coactivation_count INTEGER DEFAULT 0"),
            ("last_coactivated_at", "ALTER TABLE experience_cues ADD COLUMN last_coactivated_at TEXT"),
        ]:
            if not _has_column("experience_cues", col):
                self._conn.execute(ddl)
        self._conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', '7')")
        self._conn.commit()
        return {"schema_version": "4", "links_backfilled": linked, "observations_scanned": len(rows),
                "experience_links_backfilled": exp_linked, "experiences_scanned": len(exps),
                "experiences_analyzed": analyze["experiences_analyzed"],
                "cues_created": analyze["cues_created"],
                "cues_updated": analyze["cues_updated"],
                "anchors_created": analyze["anchors_created"],
                "cues_reactivation_ready": True}

    def osma_link(self, new_id, recalled_ids, signal="coactivation", quest_id=None, agent=None):
        """Co-activacion: fortalece links entre la memoria nueva y las recuperadas."""
        deltas = {"coactivation": 0.1, "success": 0.15, "correction": 0.05, "same_quest": 0.1}
        delta = deltas.get(signal, 0.1)
        coact = 1 if signal in ("coactivation", "same_quest") else 0
        succ = 1 if signal == "success" else 0
        fail = 1 if signal == "correction" else 0
        try:
            new_id = int(new_id)
        except (TypeError, ValueError):
            return {"linked": 0}
        if not self._conn.execute("SELECT id FROM observations WHERE id=?", (new_id,)).fetchone():
            return {"linked": 0}
        n = 0
        for rid in (recalled_ids or []):
            try:
                rid = int(rid)
            except (TypeError, ValueError):
                continue
            if rid == new_id:
                continue
            self._upsert_link(new_id, rid, delta=delta, coact=coact, succ=succ, fail=fail)
            n += 1
        self._conn.commit()
        return {"linked": n}

    def _spreading_activation(self, seed_ids, budget=12):
        """BFS por waves sobre observation_links (undirected): act_child = act_parent * 0.8 * weight.
        Para en 0.25. No atraviesa archivadas/superseded. Budget = max resultados a coleccionar."""
        activation = {}
        queue = []
        for sid in seed_ids:
            if activation.get(sid, 0.0) < 1.0:
                activation[sid] = 1.0
                queue.append(sid)
        blocked = ("archived", "superseded")
        expanded = 0
        max_expansions = max(200, budget * 40)
        while queue and expanded < max_expansions:
            current = queue.pop(0)
            expanded += 1
            act = activation.get(current, 0.0)
            if act < 0.25:
                continue
            for link in self._conn.execute(
                    "SELECT obs_a_id, obs_b_id, weight FROM observation_links "
                    "WHERE obs_a_id=? OR obs_b_id=?", (current, current)):
                other = link["obs_b_id"] if link["obs_a_id"] == current else link["obs_a_id"]
                row = self._conn.execute(
                    "SELECT archived, state FROM observations WHERE id=?", (other,)).fetchone()
                if not row or row["archived"] == 1 or row["state"] in blocked:
                    continue
                child = act * 0.8 * float(link["weight"])
                if child < 0.25:
                    continue
                if child > activation.get(other, 0.0):
                    activation[other] = child
                    queue.append(other)
        return activation

    def osma_recall(self, query, agent=None, limit=5, tag=None):
        """Recall + activacion propagada: misma forma que recall() mas campo activation (0..1)."""
        results = self.recall(query, agent=agent, limit=limit, tag=tag)
        seed_ids = [int(r["id"]) for r in results]
        budget = max(12, limit)
        activation = self._spreading_activation(seed_ids, budget=budget) if seed_ids else {}
        seen = set(seed_ids)
        out = []
        for r in results:
            r["activation"] = round(activation.get(r["id"], 1.0), 4)
            out.append(r)
        for nid, act_val in sorted(activation.items(), key=lambda kv: kv[1], reverse=True):
            if nid in seen or len(out) >= budget:
                continue
            row = self._conn.execute("SELECT * FROM observations WHERE id=?", (nid,)).fetchone()
            if not row:
                continue
            d = dict(row)
            d["activation"] = round(act_val, 4)
            out.append(d)
            seen.add(nid)
        return out

    def osma_reinforce(self, obs_id, success=True):
        """Refuerzo de utilidad: exito sube recuperaciones/importancia/retrieval; fallo baja confianza."""
        row = self._conn.execute("SELECT * FROM observations WHERE id=?", (obs_id,)).fetchone()
        if not row:
            return {"ok": False, "id": obs_id, "state": "not_found"}
        if success:
            succ = int(row["successful_retrievals"]) + 1
            score = min(5, int(row["score"]) + 1)
            new_rs = min(1.0, float(row["retrieval_strength"]) + 0.05)
            old_base = float(row["decay_base"]) if row["decay_base"] is not None else float(row["retrieval_strength"])
            base = max(old_base, new_rs)
            state = row["state"]
            self._conn.execute(
                "UPDATE observations SET successful_retrievals=?, score=?, retrieval_strength=?, decay_base=? "
                "WHERE id=?",
                (succ, score, new_rs, base, obs_id))
        else:
            conf = max(0.05, float(row["confidence"]) - 0.05)
            state = "contested" if float(row["confidence"]) >= 0.6 else row["state"]
            self._conn.execute(
                "UPDATE observations SET confidence=?, state=? WHERE id=?",
                (conf, state, obs_id))
        self._conn.commit()
        return {"ok": True, "id": obs_id, "state": state}

    def osma_context(self, query, project=None, agent=None, max_tokens=6000):
        """Paquete de recall contextual (lo que ARGOS inyecta al prompt), bajo max_tokens."""
        def _tok(x):
            return len(json.dumps(x, ensure_ascii=False)) // 4

        package = {"project": project, "direct": [], "associations": [], "decisions": [],
                   "errors_solutions": [], "agents": [], "contradictions": []}
        pool = self.recall(query, agent=agent, limit=10)
        direct = pool[:5]
        package["direct"] = direct
        seed_ids = [int(r["id"]) for r in direct]
        act = self._spreading_activation(seed_ids, budget=8) if seed_ids else {}
        for nid, act_val in sorted(act.items(), key=lambda kv: kv[1], reverse=True):
            if nid in seed_ids or len(package["associations"]) >= 8:
                continue
            row = self._conn.execute("SELECT * FROM observations WHERE id=?", (nid,)).fetchone()
            if row:
                d = dict(row)
                d["activation"] = round(act_val, 4)
                package["associations"].append(d)
        package["decisions"] = [r for r in pool if r.get("type") in ("decision", "verdict")][:3]
        package["errors_solutions"] = [r for r in pool if r.get("type") in ("bugfix", "discovery", "action")][:3]
        package["agents"] = [dict(r) for r in self._conn.execute(
            "SELECT id, class, role, trust_score, xp, level FROM agents "
            "ORDER BY trust_score DESC LIMIT 5")]
        package["contradictions"] = [dict(r) for r in self._conn.execute(
            "SELECT c.id, c.conflict_type, c.status, c.detected_at, "
            "a.topic_key AS topic_a, a.content AS content_a, a.agent AS agent_a, "
            "b.topic_key AS topic_b, b.content AS content_b, b.agent AS agent_b "
            "FROM contradictions c LEFT JOIN observations a ON a.id=c.obs_a_id "
            "LEFT JOIN observations b ON b.id=c.obs_b_id "
            "WHERE c.status='open' LIMIT 3")]
        trim_order = ["associations", "errors_solutions", "agents", "contradictions",
                      "decisions", "direct"]
        while _tok(package) > max_tokens:
            trimmed = False
            for k in trim_order:
                if package[k]:
                    package[k].pop()
                    trimmed = True
                    break
            if not trimmed:
                break
        return package

    def osma_contradictions(self, status="open"):
        sql = ("SELECT c.*, a.topic_key AS topic_a, a.content AS content_a, a.agent AS agent_a, "
               "a.confidence AS conf_a, b.topic_key AS topic_b, b.content AS content_b, "
               "b.agent AS agent_b, b.confidence AS conf_b "
               "FROM contradictions c "
               "LEFT JOIN observations a ON a.id=c.obs_a_id "
               "LEFT JOIN observations b ON b.id=c.obs_b_id")
        params = []
        if status:
            sql += " WHERE c.status=?"
            params.append(status)
        sql += " ORDER BY c.id DESC LIMIT 50"
        return [dict(r) for r in self._conn.execute(sql, params)]

    def osma_contradiction_resolve(self, cid, winner_id, evidence=None):
        row = self._conn.execute("SELECT * FROM contradictions WHERE id=?", (cid,)).fetchone()
        if not row:
            return {"ok": False, "error": "contradiction no existe"}
        winner_id = int(winner_id)
        if row["obs_a_id"] != winner_id and row["obs_b_id"] != winner_id:
            return {"ok": False, "error": "winner_id no es parte de la contradiccion"}
        loser_id = row["obs_b_id"] if row["obs_a_id"] == winner_id else row["obs_a_id"]
        loser = self._conn.execute("SELECT * FROM observations WHERE id=?", (loser_id,)).fetchone()
        winner = self._conn.execute("SELECT * FROM observations WHERE id=?", (winner_id,)).fetchone()
        if loser:
            self._conn.execute(
                "UPDATE observations SET state='superseded', supersedes=? WHERE id=?",
                (winner_id, loser_id))
        if loser and winner:
            self.add_edge(loser["topic_key"], winner["topic_key"], "replaced_by", agent="osma")
        self._conn.execute(
            "UPDATE contradictions SET status='resolved', resolution=?, resolved_at=datetime('now'), "
            "superseded_id=? WHERE id=?",
            (evidence or "", loser_id if loser else None, cid))
        self._conn.commit()
        return {"ok": True, "superseded": loser_id if loser else None}

    def osma_sleep(self, hours=24, now=None):
        """Consolidacion completa (el sueno): decay, transiciones, dedup, contradicciones, links debiles."""
        now_dt = self._parse_dt(now) or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        now_iso = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        stats = {"decayed": 0, "transitioned": 0, "deduped": 0, "consolidated": 0,
                 "contradictions_detected": 0, "links_weakened": 0}

        # (a) decay persistido de retrieval_strength (misma formula que _effective_retrieval).
        #     Base estable: decay_base (pico, nunca decae) -> sleeps repetidos no componen decay.
        for r in [dict(x) for x in self._conn.execute("SELECT * FROM observations")]:
            lam = self._VOL_LAMBDAS.get(r.get("volatility", "stable"), 90.0)
            last = r.get("last_retrieved_at")
            base = float(r["decay_base"]) if r.get("decay_base") is not None else float(r.get("retrieval_strength", 0.6))
            eff = base
            if last:
                last_dt = self._parse_dt(last)
                if last_dt:
                    days = max(0.0, (now_dt - last_dt).total_seconds() / 86400.0)
                    eff = base * (2.0 ** (-days / lam))
            self._conn.execute(
                "UPDATE observations SET retrieval_strength=?, last_decay_at=? WHERE id=?",
                (round(eff, 4), now_iso, r["id"]))
            stats["decayed"] += 1

        # (b) transiciones: active -> dormant (inactividad segun volatilidad); dormant -> archived si baja importancia
        inactivity_days = {"immutable": 365, "stable": 90, "slow": 45, "dynamic": 14, "ephemeral": 5}
        for r in [dict(x) for x in self._conn.execute("SELECT * FROM observations")]:
            if r["state"] == "active" and r["archived"] == 0:
                ref = self._parse_dt(r.get("last_retrieved_at") or r.get("created_at"))
                if not ref:
                    continue
                if (now_dt - ref).total_seconds() / 86400.0 > inactivity_days.get(r.get("volatility", "stable"), 90):
                    self._conn.execute("UPDATE observations SET state='dormant' WHERE id=?", (r["id"],))
                    stats["transitioned"] += 1
            elif r["state"] == "dormant" and r["archived"] == 0 and int(r.get("score", 0)) <= 2:
                self._conn.execute("UPDATE observations SET state='archived', archived=1 WHERE id=?", (r["id"],))
                stats["transitioned"] += 1

        # (c) dedup de observaciones casi identicas (mismo topic_key + >=3 entidades) -> consolidations
        active_rows = [dict(x) for x in self._conn.execute(
            "SELECT * FROM observations WHERE archived=0 AND state NOT IN ('archived','superseded') ORDER BY id")]
        by_topic = {}
        for r in active_rows:
            by_topic.setdefault(r["topic_key"], []).append(r)
        for topic_key, group in by_topic.items():
            if len(group) < 2:
                continue
            consumed = set()
            for i in range(len(group)):
                if i in consumed:
                    continue
                cluster = [group[i]]
                for j in range(i + 1, len(group)):
                    if j in consumed:
                        continue
                    a, b = group[i], group[j]
                    if self._has_conflict_signal(a.get("content", "")) or self._has_conflict_signal(b.get("content", "")):
                        continue  # pares con senal de contradiccion no son duplicados
                    if self._shared_entity_count(a, b) >= 3:
                        cluster.append(b)
                        consumed.add(j)
                if len(cluster) < 2:
                    continue
                consumed.add(i)
                parts = [(s.get("content") or "")[:200] for s in cluster[:5]]
                summary = "{0}: {1}".format(topic_key, " · ".join(parts))
                imp = round(sum(float(s.get("score", 0)) for s in cluster) / len(cluster), 2)
                conf = round(sum(float(s.get("confidence", 0.5)) for s in cluster) / len(cluster), 2)
                self._conn.execute(
                    "INSERT INTO consolidations (title, kind, summary, source_ids, importance, confidence, "
                    "project, topic_key, status) VALUES (?,?,?,?,?,?,?,?,'pending')",
                    (topic_key, cluster[0].get("type", "episodic"), summary,
                     json.dumps([s["id"] for s in cluster], ensure_ascii=False), imp, conf,
                     self._project_name(), topic_key))
                for s in cluster:
                    self._conn.execute("UPDATE observations SET archived=1, state='archived' WHERE id=?", (s["id"],))
                stats["deduped"] += 1
                stats["consolidated"] += len(cluster)

        # (d) deteccion de contradicciones (mismo topic_key o >=2 entidades; conf >= 0.6; senales de conflicto)
        pair_pool = [dict(x) for x in self._conn.execute(
            "SELECT * FROM observations WHERE archived=0 AND state NOT IN ('archived','superseded') ORDER BY id")]
        for i in range(len(pair_pool)):
            for j in range(i + 1, len(pair_pool)):
                a, b = pair_pool[i], pair_pool[j]
                if float(a.get("confidence", 0)) < 0.6 or float(b.get("confidence", 0)) < 0.6:
                    continue
                same_topic = a["topic_key"] == b["topic_key"]
                if not (same_topic or self._shared_entity_count(a, b) >= 2):
                    continue
                if not (self._has_conflict_signal(a.get("content", "")) or self._has_conflict_signal(b.get("content", ""))):
                    continue
                dup = self._conn.execute(
                    "SELECT id FROM contradictions WHERE status='open' AND "
                    "((obs_a_id=? AND obs_b_id=?) OR (obs_a_id=? AND obs_b_id=?))",
                    (a["id"], b["id"], b["id"], a["id"])).fetchone()
                if dup:
                    continue
                self._conn.execute(
                    "INSERT INTO contradictions (obs_a_id, obs_b_id, conflict_type, status, detected_at, detected_by) "
                    "VALUES (?,?,?,'open',?,'osma-sleep')",
                    (a["id"], b["id"], "same_topic" if same_topic else "shared_entities", now_iso))
                stats["contradictions_detected"] += 1

        # (e) debilitar links estancos: weight *= 0.995 por periodo inactivo (hours).
        #     periods = segundos / (hours*3600): con hours=24, 1 dia inactivo = 1 periodo exacto.
        for l in [dict(x) for x in self._conn.execute("SELECT * FROM observation_links")]:
            last = self._parse_dt(l.get("last_coactivated_at"))
            if not last:
                continue
            periods = int((now_dt - last).total_seconds() / (max(1.0, float(hours)) * 3600.0))
            if periods >= 1:
                new_w = max(0.01, float(l["weight"]) * (0.995 ** periods))
                self._conn.execute("UPDATE observation_links SET weight=? WHERE id=?", (round(new_w, 4), l["id"]))
                stats["links_weakened"] += 1

        self._conn.commit()
        return stats

    def osma_consolidations(self, status=None):
        sql = "SELECT * FROM consolidations"
        params = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT 50"
        return [dict(r) for r in self._conn.execute(sql, params)]

    def osma_consolidation_finalize(self, cid, summary):
        row = self._conn.execute("SELECT * FROM consolidations WHERE id=?", (cid,)).fetchone()
        if not row:
            return {"ok": False, "error": "no existe"}
        self._conn.execute("UPDATE consolidations SET summary=?, status='done' WHERE id=?", (summary, cid))
        self._conn.commit()
        return {"ok": True, "id": cid, "status": "done"}

    def osma_stats(self):
        def _c(sql, *params):
            return self._conn.execute(sql, params).fetchone()[0]
        links = [dict(r) for r in self._conn.execute("SELECT weight FROM observation_links")]
        avg = round(sum(float(l["weight"]) for l in links) / len(links), 4) if links else 0.0
        return {
            "links": len(links),
            "avg_link_weight": avg,
            "active": _c("SELECT COUNT(*) FROM observations WHERE state='active' AND archived=0"),
            "warm": _c("SELECT COUNT(*) FROM observations WHERE state='dormant'"),
            "cold": 0,
            "archived": _c("SELECT COUNT(*) FROM observations WHERE archived=1 OR state='archived'"),
            "contradictions_open": _c("SELECT COUNT(*) FROM contradictions WHERE status='open'"),
            "consolidations_pending": _c("SELECT COUNT(*) FROM consolidations WHERE status='pending'"),
        }

    # OSMA V5 API - Experience Memory (experiencias validadas -> patrones -> reuso)
    #   publicos: osma_experience_record, osma_experience_validate, osma_experience_search,
    #             osma_pattern_detect, osma_experience_reuse, osma_experience_stats
    #   privados: _clamp, _experience_text, _experience_entities, _exp_as_obs_row,
    #             _upsert_experience_link, _patterns_for_experience, _superseded_by_newer,
    #             _experience_applicability
    @staticmethod
    def _clamp(v, lo=0.0, hi=1.0):
        """Acota un valor numerico a [lo, hi]."""
        return max(lo, min(hi, float(v)))

    @staticmethod
    def _status_from_reward(reward):
        """Taxonomia V5 completa (6 estados) desde reward:
        proposal (0.0) | hypothesis (0<r<0.4) | attempted (-0.6<=r<0)
        | partial (0.4<=r<0.9) | verified (r>=0.9) | failed (r<-0.6)."""
        if reward < -0.6:
            return "failed"
        if reward < 0:
            return "attempted"
        if reward == 0:
            return "proposal"
        if reward < 0.4:
            return "hypothesis"
        if reward < 0.9:
            return "partial"
        return "verified"

    @staticmethod
    def _dt_ts(s):
        """Timestamp unix de 'YYYY-MM-DD HH:MM:SS' para ranking por recencia (0.0 si no parsea)."""
        dt = OsmaBrain._parse_dt(s)
        return dt.timestamp() if dt else 0.0

    @staticmethod
    def _days_since(created_at, now=None):
        """Dias transcurridos desde created_at ('YYYY-MM-DD HH:MM:SS'). None si no parsea."""
        dt = OsmaBrain._parse_dt(created_at)
        if dt is None:
            return None
        now_dt = now or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        return max(0.0, (now_dt - dt).total_seconds() / 86400.0)

    @staticmethod
    def _competition_score(exp, k, project, now=None):
        """Tie-breaker compuesto (V7 audit Tywin — competition_between_memories).
        episode_activation_score es el factor PRIMARIO del ranking; este score solo
        ordena resultados con activacion igual/cercana. Componentes:
          recency_norm      = 1/(1+days) desde created_at (reciente = mayor)
          importance        = importance de la experiencia (0..1)
          project_match     = 1.0 si query.project coincide con el proyecto, 0.5
                              neutro si el query no trae project, 0.0 si difiere
          episode_coherence = 1.0 si k>=3 cues matcheados, si no k/3 (episodios
                              multi-cue coherentes rankean mas alto)
        Pesos W_* documentados como tunables en el modulo."""
        days = OsmaBrain._days_since(exp.get("created_at"), now)
        recency_norm = 1.0 / (1.0 + days) if days is not None else 0.5
        importance = float(exp.get("importance") or 0.0)
        if project:
            project_match = 1.0 if exp.get("project") == project else 0.0
        else:
            project_match = 0.5
        coherence = 1.0 if k >= 3 else (k / 3.0)
        return (recency_norm * _W_RECENCY + importance * _W_IMPORTANCE
                + project_match * _W_PROJECT + coherence * _W_COHERENCE)

    @staticmethod
    def _experience_text(situation, reasoning="", conclusion="", action="", outcome="", topic_key=""):
        """Texto plano de la experiencia: fuente de extraccion de entidades (mismo tokenizer V4)."""
        return " ".join([situation or "", reasoning or "", conclusion or "",
                         action or "", outcome or "", topic_key or ""])

    def _experience_entities(self, situation, reasoning="", conclusion="", action="",
                             outcome="", topic_key=""):
        """Entidades de una experiencia via _entity_tokens (misma logica de stopwords V4)."""
        text = self._experience_text(situation, reasoning, conclusion, action, outcome, topic_key)
        return set(self._entity_tokens(text, None, topic_key))

    def _exp_as_obs_row(self, exp):
        """Pseudo-fila de observacion para reusar _shared_entity_count (solapamiento >=2)."""
        return {"content": self._experience_text(exp.get("situation"), exp.get("reasoning"),
                                                 exp.get("conclusion"), exp.get("action"),
                                                 exp.get("outcome")),
                "tags": None, "topic_key": exp.get("topic_key")}

    def _upsert_experience_link(self, a_id, b_id, delta=0.1, coact=0, now=None):
        """Crea o fortalece un link experiencia-experiencia (undirected; UNIQUE exp_a_id < exp_b_id)."""
        a_id, b_id = int(a_id), int(b_id)
        lo, hi = (a_id, b_id) if a_id < b_id else (b_id, a_id)
        row = self._conn.execute(
            "SELECT * FROM experience_links WHERE exp_a_id=? AND exp_b_id=?", (lo, hi)).fetchone()
        ts = now or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        if row:
            new_w = max(0.0, min(1.0, float(row["weight"]) + delta))
            self._conn.execute(
                "UPDATE experience_links SET weight=?, coactivation_count=coactivation_count+?, "
                "last_coactivated_at=? WHERE id=?",
                (new_w, coact, ts, row["id"]))
            return row["id"]
        self._conn.execute(
            "INSERT INTO experience_links (exp_a_id, exp_b_id, weight, coactivation_count, "
            "last_coactivated_at, created_at) VALUES (?,?,?,?,?,?)",
            (lo, hi, max(0.0, min(1.0, delta)), coact, ts, ts))
        return self._conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def _patterns_for_experience(self, exp_id, patterns=None):
        """Patrones cuyo source_experience_ids contiene a exp_id (solo ids JSON validos)."""
        out = []
        for p in (patterns if patterns is not None else
                  [dict(r) for r in self._conn.execute("SELECT * FROM patterns ORDER BY id")]):
            try:
                ids = json.loads(p.get("source_experience_ids") or "[]")
            except Exception:
                continue
            if exp_id in ids:
                out.append(p)
        return out

    def _superseded_by_newer(self, exp, all_experiences, patterns):
        """Contradiccion contextual: una experiencia 'verified' MAS NUEVA (reward>0) obsoleta a la
        actual SOLO si comparte el mismo patron (id) O (mismo proyecto Y >=2 entidades compartidas).
        Conocimiento no relacionado dentro del mismo proyecto NO se marca obsoleto."""
        exp_entities = self._experience_entities(
            exp.get("situation"), exp.get("reasoning"), exp.get("conclusion"),
            exp.get("action"), exp.get("outcome"), exp.get("topic_key"))
        exp_pattern_ids = {p["id"] for p in self._patterns_for_experience(exp["id"], patterns)}
        for other in all_experiences:
            if other["id"] <= exp["id"]:
                continue
            if other.get("validation_status") != "verified":
                continue
            if float(other.get("reward_signal", 0.0)) <= 0:
                continue
            other_pattern_ids = {p["id"] for p in self._patterns_for_experience(other["id"], patterns)}
            same_pattern = bool(exp_pattern_ids & other_pattern_ids)
            same_project = bool(exp.get("project")) and exp.get("project") == other.get("project")
            other_entities = self._experience_entities(
                other.get("situation"), other.get("reasoning"), other.get("conclusion"),
                other.get("action"), other.get("outcome"), other.get("topic_key"))
            shared = len(exp_entities & other_entities) >= 2
            if same_pattern or (same_project and shared):
                return True
        return False

    def _experience_applicability(self, exp, exp_entities, query_entities, project,
                                  all_experiences, patterns):
        """'apply' (verified+reward>0+project match, sin contradiccion nueva)
        | 'caution' (partial/attempted) | 'obsolete' (failed o superseded)
        | 'context_mismatch' (proyecto y topico difieren del query)."""
        status = exp.get("validation_status")
        if status == "failed":
            return "obsolete"
        if self._superseded_by_newer(exp, all_experiences, patterns):
            return "obsolete"
        project_matches = (not project) or (exp.get("project") == project)
        topic_matches = len(query_entities & exp_entities) >= 2
        if not project_matches and not topic_matches:
            return "context_mismatch"
        if status == "verified" and float(exp.get("reward_signal", 0.0)) > 0 and project_matches:
            return "apply"
        return "caution"

    def osma_experience_record(self, data):
        """Registra una experiencia (situation->reasoning->conclusion->action->outcome->reward) y
        la auto-enlaza con experiencias y observaciones que comparten >=2 entidades.
        V6: acepta session_id/quest_id/files (JSON array string); tras el insert descompone
        la experiencia en cues COMPLETOS (base + reasoning/action/concept/pattern/problem/error/
        solution/result/temporal — FIX 2), computa la saliencia inicial, inicializa
        association_strength (avg de pesos de experience_links, FIX 3) y refresca cue_quality
        (IDF) de los cues compartidos con experiencias previas. La descomposicion NUNCA falla
        el record."""
        situation = (data.get("situation") or "").strip()
        if not situation:
            return {"error": "situation es obligatoria"}
        reasoning = data.get("reasoning") or ""
        conclusion = data.get("conclusion") or ""
        action = data.get("action") or ""
        outcome = data.get("outcome") or ""
        reward = self._clamp(float(data.get("reward", 0.0) or 0.0), -1.0, 1.0)
        agent = data.get("agent")
        project = data.get("project")
        topic_key = data.get("topic_key")
        # Si el caller no especifica validation_status, derivarlo del reward (misma logica que
        # osma_experience_validate, helper compartido _status_from_reward).
        if data.get("validation_status"):
            validation_status = data["validation_status"]
        else:
            validation_status = self._status_from_reward(reward)
        cur = self._conn.execute(
            "INSERT INTO experiences (situation, reasoning, conclusion, action, outcome, "
            "reward_signal, validation_status, agent, project, topic_key) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (situation, reasoning, conclusion, action, outcome, round(reward, 4),
             validation_status, agent, project, topic_key))
        exp_id = cur.lastrowid
        new_entities = self._experience_entities(
            situation, reasoning, conclusion, action, outcome, topic_key)
        linked_experiences = 0
        linked_observations = 0
        if new_entities:
            for other in [dict(r) for r in self._conn.execute(
                    "SELECT * FROM experiences WHERE id != ? ORDER BY id", (exp_id,))]:
                other_entities = self._experience_entities(
                    other.get("situation"), other.get("reasoning"), other.get("conclusion"),
                    other.get("action"), other.get("outcome"), other.get("topic_key"))
                if len(new_entities & other_entities) >= 2:
                    self._upsert_experience_link(exp_id, other["id"], delta=0.1, coact=1)
                    linked_experiences += 1
            for obs in [dict(r) for r in self._conn.execute(
                    "SELECT id, content, tags, topic_key FROM observations "
                    "WHERE archived=0 AND state NOT IN ('archived','superseded') ORDER BY id")]:
                obs_entities = set(self._entity_tokens(obs.get("content"), obs.get("tags"),
                                                       obs.get("topic_key")))
                if len(new_entities & obs_entities) < 2:
                    continue
                existing = self._conn.execute(
                    "SELECT id FROM experience_observation_links "
                    "WHERE experience_id=? AND observation_id=?",
                    (exp_id, obs["id"])).fetchone()
                if existing:
                    self._conn.execute(
                        "UPDATE experience_observation_links SET weight=min(1.0, weight+0.1), "
                        "coactivation_count=coactivation_count+1 WHERE id=?",
                        (existing["id"],))
                else:
                    self._conn.execute(
                        "INSERT INTO experience_observation_links "
                        "(experience_id, observation_id, weight, coactivation_count) VALUES (?,?,?,?)",
                        (exp_id, obs["id"], 0.1, 1))
                linked_observations += 1
        self._conn.commit()
        # ---- V6: saliencia + descomposicion COMPLETA en cues (nunca falla el record) ----
        # FIX 2 (Tywin): record corre la misma descomposicion completa que analyze
        # (base + reasoning/action/concept/pattern/problem/error/solution/result/temporal).
        cues_created = 0
        salience = 0.3
        try:
            salience = self._salience_for(data, validation_status, topic_key)
            exp = self._conn.execute("SELECT * FROM experiences WHERE id=?", (exp_id,)).fetchone()
            exp = dict(exp)
            exp["session_id"] = data.get("session_id")
            exp["quest_id"] = data.get("quest_id")
            exp["files"] = data.get("files")
            for ctype, value, source in self._extract_full_cues(exp):
                if self._insert_cue(exp_id, ctype, value, source):
                    cues_created += 1
            # refrescar cue_quality (IDF) para todos los cues que comparten valor con la nueva
            self._refresh_cue_qualities(exp_id)
            self._update_v6_metadata(exp_id)
            # FIX 3 (Tywin): association_strength = avg de pesos de experience_links (0.5 default).
            association_strength = self._avg_link_weight(exp_id)
            self._conn.execute(
                "UPDATE experiences SET salience=?, association_strength=?, session_id=?, "
                "quest_id=? WHERE id=?",
                (round(salience, 4), round(association_strength, 4),
                 data.get("session_id"), data.get("quest_id"), exp_id))
            self._conn.commit()
        except Exception:
            # la descomposicion JAMAS rompe el record: se registra sin cues
            try:
                self._conn.commit()
            except Exception:
                pass
        return {"id": exp_id, "linked_experiences": linked_experiences,
                "linked_observations": linked_observations,
                "cues_created": cues_created, "salience": round(salience, 4)}

    def osma_experience_validate(self, data):
        """Valida con reward real: clamp -1..1, mapa de 6 estados (helper compartido),
        ajuste de confianza/importancia. reward>=0.4 (partial/verified) sube
        successful_retrievals (usuario confirma / test pasa / otro agente verifica);
        reward < 0 sube failed_retrievals.
        FIX 4 (Tywin): saliencia sube SOLO con senales funcionales verificables — correccion
        fuerte (reward < -0.6) +0.10, verificacion (reward >= 0.9) +0.05. Saliencia =
        significancia funcional, NO emocion simulada."""
        exp_id = int(data.get("id", 0))
        row = self._conn.execute("SELECT * FROM experiences WHERE id=?", (exp_id,)).fetchone()
        if not row:
            return {"ok": False, "id": exp_id, "error": "no existe"}
        reward = self._clamp(float(data.get("reward", 0.0) or 0.0), -1.0, 1.0)
        status = self._status_from_reward(reward)
        conf = self._clamp(float(row["confidence"]) + reward * 0.15, 0.05, 0.95)
        imp = self._clamp(float(row["importance"]) + reward * 0.05, 0.05, 0.95)
        failed_retrievals = int(row["failed_retrievals"]) + (1 if reward < 0 else 0)
        successful_retrievals = int(row["successful_retrievals"]) + (1 if reward >= 0.4 else 0)
        sal_delta = 0.10 if reward < -0.6 else (0.05 if reward >= 0.9 else 0.0)
        sal = self._clamp(float(row["salience"] or 0.0) + sal_delta, 0.0, 1.0)
        self._conn.execute(
            "UPDATE experiences SET reward_signal=?, validation_status=?, confidence=?, importance=?, "
            "failed_retrievals=?, successful_retrievals=?, salience=? WHERE id=?",
            (round(reward, 4), status, round(conf, 4), round(imp, 4), failed_retrievals,
             successful_retrievals, round(sal, 4), exp_id))
        self._conn.commit()
        return {"ok": True, "id": exp_id, "reward_signal": round(reward, 4),
                "validation_status": status, "confidence": round(conf, 4),
                "importance": round(imp, 4),
                "successful_retrievals": successful_retrievals,
                "failed_retrievals": failed_retrievals,
                "salience": round(sal, 4)}

    def osma_experience_search(self, query, project=None, agent=None, limit=5):
        """Recuperacion por experiencias: entidades del query + patrones que matchean + aplicabilidad.
        Contradiccion contextual: una 'verified' mas nueva obsoleta a la vieja SOLO si comparte
        patron (id) o (proyecto y >=2 entidades); conocimiento no relacionado no se marca obsoleto."""
        query_entities = set(self._entity_tokens(query, None, None))
        all_experiences = [dict(r) for r in self._conn.execute("SELECT * FROM experiences ORDER BY id")]
        patterns = [dict(r) for r in self._conn.execute("SELECT * FROM patterns ORDER BY id")]

        # (2) patrones cuyo texto (title+description+check_procedure) comparte >=1 entidad con el query
        matched_patterns = []
        for p in patterns:
            p_text = " ".join([p.get("title") or "", p.get("description") or "",
                               p.get("check_procedure") or ""])
            p_entities = set(self._entity_tokens(p_text, None, None))
            if query_entities and p_entities and query_entities & p_entities:
                matched_patterns.append(p)
        pattern_sources = {}
        for p in matched_patterns:
            try:
                ids = json.loads(p.get("source_experience_ids") or "[]")
            except Exception:
                ids = []
            for eid in ids:
                pattern_sources.setdefault(eid, []).append(p)

        # (3) coleccion: match directo de entidades (>=2) o via patron
        pool = []
        for e in all_experiences:
            if agent and e.get("agent") != agent:
                continue
            e_entities = self._experience_entities(
                e.get("situation"), e.get("reasoning"), e.get("conclusion"),
                e.get("action"), e.get("outcome"), e.get("topic_key"))
            direct = len(query_entities & e_entities) >= 2
            via_pattern = e["id"] in pattern_sources
            if not (direct or via_pattern):
                continue
            pats = pattern_sources.get(e["id"], [])
            source_pattern = pats[0]["title"] if pats else None
            derived = []
            for p in pats:
                try:
                    derived.extend(json.loads(p.get("source_experience_ids") or "[]"))
                except Exception:
                    pass
            derived_from = sorted(set(derived))
            applicability = self._experience_applicability(
                e, e_entities, query_entities, project, all_experiences, patterns)
            pool.append({"id": e["id"], "situation": e.get("situation"),
                         "conclusion": e.get("conclusion"), "action": e.get("action"),
                         "outcome": e.get("outcome"), "reward_signal": e.get("reward_signal"),
                         "validation_status": e.get("validation_status"),
                         "confidence": e.get("confidence"),
                         "successful_retrievals": e.get("successful_retrievals"),
                         "failed_retrievals": e.get("failed_retrievals"),
                         "agent": e.get("agent"), "project": e.get("project"),
                         "topic_key": e.get("topic_key"), "applicability": applicability,
                         "salience": float(e.get("salience") or 0.0),
                         "retrieval_routes": self._retrieval_routes(e["id"]),
                         "source_pattern": source_pattern, "derived_from": derived_from,
                         "_ts": self._dt_ts(e.get("created_at"))})

        # (5) ranking: apply-verified primero, luego confidence desc, luego recencia desc
        def _rank_key(r):
            prio = 0 if (r["validation_status"] == "verified" and float(r["reward_signal"]) > 0
                         and r["applicability"] == "apply") else 1
            return (prio, -float(r["confidence"]), -r["_ts"])

        pool.sort(key=_rank_key)
        for r in pool:
            r.pop("_ts", None)
        return pool[:max(0, int(limit))]

    def osma_pattern_detect(self):
        """Clusteriza experiencias verified/partial con reward>0 por solapamiento de entidades
        (>=2, union-find sobre pares). Cada cluster >=2 genera o actualiza un patron:
        title = top-2 entidades compartidas, check_procedure = action de la de mayor confianza."""
        candidates = [dict(r) for r in self._conn.execute(
            "SELECT * FROM experiences "
            "WHERE validation_status IN ('verified','partial') AND reward_signal > 0 ORDER BY id")]
        n = len(candidates)
        parent = list(range(n))

        def _find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def _union(x, y):
            rx, ry = _find(x), _find(y)
            if rx != ry:
                parent[ry] = rx

        for i in range(n):
            for j in range(i + 1, n):
                if self._shared_entity_count(self._exp_as_obs_row(candidates[i]),
                                             self._exp_as_obs_row(candidates[j])) >= 2:
                    _union(i, j)
        clusters = {}
        for i in range(n):
            clusters.setdefault(_find(i), []).append(candidates[i])

        created = 0
        updated = 0
        experiences_covered = 0
        for members in clusters.values():
            if len(members) < 2:
                continue
            experiences_covered += len(members)
            freq = {}
            for m in members:
                for ent in self._experience_entities(
                        m.get("situation"), m.get("reasoning"), m.get("conclusion"),
                        m.get("action"), m.get("outcome"), m.get("topic_key")):
                    freq[ent] = freq.get(ent, 0) + 1
            top = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:2]
            title = " ".join(t for t, _ in top) if top else (members[0].get("topic_key") or "patron")
            description = " · ".join((m.get("situation") or "")[:200] for m in members[:5])
            best = max(members, key=lambda m: float(m.get("confidence", 0.0)))
            check_procedure = best.get("action") or ""
            source_ids = sorted(m["id"] for m in members)
            source_json = json.dumps(source_ids, ensure_ascii=False)
            existing = self._conn.execute(
                "SELECT id FROM patterns WHERE source_experience_ids=?", (source_json,)).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE patterns SET title=?, description=?, check_procedure=? WHERE id=?",
                    (title, description, check_procedure, existing["id"]))
                updated += 1
            else:
                self._conn.execute(
                    "INSERT INTO patterns (title, description, check_procedure, source_experience_ids) "
                    "VALUES (?,?,?,?)",
                    (title, description, check_procedure, source_json))
                created += 1
        self._conn.commit()
        return {"patterns_created": created, "patterns_updated": updated,
                "experiences_covered": experiences_covered}

    def osma_patterns(self):
        """Provenance semantica: lista patrones con derived_from (source_experience_ids parseado)
        y la situation de cada experiencia fuente (truncada a 120 chars)."""
        out = []
        for p in [dict(r) for r in self._conn.execute("SELECT * FROM patterns ORDER BY id")]:
            try:
                ids = json.loads(p.get("source_experience_ids") or "[]")
            except Exception:
                ids = []
            if not isinstance(ids, list):
                ids = []
            sources = []
            for eid in ids:
                row = self._conn.execute(
                    "SELECT id, situation FROM experiences WHERE id=?", (int(eid),)).fetchone()
                if row:
                    sit = row["situation"] or ""
                    sources.append({"id": row["id"], "situation": sit[:120]})
            out.append({
                "id": p["id"],
                "title": p.get("title"),
                "check_procedure": p.get("check_procedure"),
                "confidence": p.get("confidence"),
                "derived_from": ids,
                "sources": sources,
            })
        return out

    def osma_experience_reuse(self, data):
        """Marca un reuso: success -> successful_retrievals+1, reward_signal>=0.5, confidence+0.05;
        fail -> failed_retrievals+1, confidence-0.10 y 'verified' degrada a 'attempted'.
        FIX 3 (Tywin): dimensiones independientes — retrieval_strength +0.05 (success, cap 1.0)
        / -0.05 (fail, floor 0.0) y frequency+1 en ambos casos (frequency = veces reusada),
        NUNCA atadas a confidence. FIX 4: salience +0.03 en ambos casos (exito repetido Y
        fallo repetido son funcionalmente significativos para ARGOS), cap 1.0."""
        exp_id = int(data.get("id", 0))
        success = bool(data.get("success", True))
        row = self._conn.execute("SELECT * FROM experiences WHERE id=?", (exp_id,)).fetchone()
        if not row:
            return {"ok": False, "id": exp_id, "error": "no existe"}
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        if success:
            succ = int(row["successful_retrievals"]) + 1
            reward = max(float(row["reward_signal"]), 0.5)
            conf = self._clamp(float(row["confidence"]) + 0.05, 0.05, 0.95)
            status = row["validation_status"]
            retr = self._clamp(float(row["retrieval_strength"] or 0.5) + 0.05, 0.0, 1.0)
            sal = self._clamp(float(row["salience"] or 0.0) + 0.03, 0.0, 1.0)
            self._conn.execute(
                "UPDATE experiences SET successful_retrievals=?, reward_signal=?, confidence=?, "
                "retrieval_strength=?, salience=?, frequency=frequency+1, last_used_at=? WHERE id=?",
                (succ, round(reward, 4), round(conf, 4), round(retr, 4), round(sal, 4), now, exp_id))
        else:
            fail = int(row["failed_retrievals"]) + 1
            conf = self._clamp(float(row["confidence"]) - 0.10, 0.05, 0.95)
            status = "attempted" if row["validation_status"] == "verified" else row["validation_status"]
            retr = self._clamp(float(row["retrieval_strength"] or 0.5) - 0.05, 0.0, 1.0)
            sal = self._clamp(float(row["salience"] or 0.0) + 0.03, 0.0, 1.0)
            self._conn.execute(
                "UPDATE experiences SET failed_retrievals=?, confidence=?, validation_status=?, "
                "retrieval_strength=?, salience=?, frequency=frequency+1, last_used_at=? WHERE id=?",
                (fail, round(conf, 4), status, round(retr, 4), round(sal, 4), now, exp_id))
        updated = self._conn.execute("SELECT * FROM experiences WHERE id=?", (exp_id,)).fetchone()
        self._conn.commit()
        return {"ok": True, "id": exp_id,
                "successful_retrievals": updated["successful_retrievals"],
                "failed_retrievals": updated["failed_retrievals"],
                "confidence": updated["confidence"],
                "retrieval_strength": round(float(updated["retrieval_strength"] or 0.5), 4),
                "frequency": int(updated["frequency"] or 0),
                "salience": round(float(updated["salience"] or 0.0), 4)}

    def osma_experience_stats(self):
        def _c(sql, *params):
            return self._conn.execute(sql, params).fetchone()[0]
        avg = _c("SELECT AVG(reward_signal) FROM experiences")
        # V6: promedio de rutas de recuperacion incluyendo experiencias sin cues (0)
        total_exps = _c("SELECT COUNT(*) FROM experiences")
        route_rows = [dict(r) for r in self._conn.execute(
            "SELECT COUNT(*) AS n FROM experience_cues WHERE cue_quality >= 0.3 "
            "GROUP BY experience_id")]
        total_routes = sum(int(r["n"]) for r in route_rows)
        avg_sal = _c("SELECT AVG(salience) FROM experiences")
        avg_assoc = _c("SELECT AVG(association_strength) FROM experiences")
        avg_retr = _c("SELECT AVG(retrieval_strength) FROM experiences")
        tot_freq = _c("SELECT COALESCE(SUM(frequency),0) FROM experiences")
        return {
            "experiences": total_exps,
            "patterns": _c("SELECT COUNT(*) FROM patterns"),
            "verified": _c("SELECT COUNT(*) FROM experiences WHERE validation_status='verified'"),
            "failed": _c("SELECT COUNT(*) FROM experiences WHERE validation_status='failed'"),
            # 'unverified' se mantiene por compat legacy (filas pre-V5 que puedan existir)
            "unverified": _c("SELECT COUNT(*) FROM experiences WHERE validation_status='unverified'"),
            "proposal": _c("SELECT COUNT(*) FROM experiences WHERE validation_status='proposal'"),
            "hypothesis": _c("SELECT COUNT(*) FROM experiences WHERE validation_status='hypothesis'"),
            "partial": _c("SELECT COUNT(*) FROM experiences WHERE validation_status='partial'"),
            "attempted": _c("SELECT COUNT(*) FROM experiences WHERE validation_status='attempted'"),
            "avg_reward": round(float(avg), 4) if avg is not None else 0.0,
            "reused_successfully": _c("SELECT COALESCE(SUM(successful_retrievals),0) FROM experiences"),
            "reused_failed": _c("SELECT COALESCE(SUM(failed_retrievals),0) FROM experiences"),
            # V6: multidimensional memory metrics
            "total_cues": _c("SELECT COUNT(*) FROM experience_cues"),
            "total_anchors": _c("SELECT COUNT(*) FROM experience_cues WHERE component_type='anchor'"),
            "avg_salience": round(float(avg_sal), 4) if avg_sal is not None else 0.0,
            "avg_retrieval_routes": round(total_routes / total_exps, 4) if total_exps else 0.0,
            # FIX 3 (Tywin): dimensiones independientes expuestas en stats
            "avg_association_strength": round(float(avg_assoc), 4) if avg_assoc is not None else 0.0,
            "avg_retrieval_strength": round(float(avg_retr), 4) if avg_retr is not None else 0.0,
            "total_frequency": int(tot_freq),
        }

    # ---------- OSMA V6 API - Multidimensional Memory (cues, salience, anchors) ----------
    #   publicos: osma_experience_analyze, osma_cues, osma_cue_search, osma_anchor_add, osma_routes
    #   privados: _loads_list, _cue_usage_count, _cue_quality, _insert_cue, _refresh_cue_quality,
    #             _refresh_cue_qualities, _retrieval_routes, _salience_for, _extract_base_cues,
    #             _extract_analyze_cues, _extract_full_cues, _avg_link_weight,
    #             _generate_anchors, _update_v6_metadata
    @staticmethod
    def _loads_list(raw):
        """Parsea JSON array (string o lista) a lista de strings. Tolera texto plano."""
        if raw is None:
            return []
        if isinstance(raw, (list, tuple)):
            return [str(x) for x in raw]
        if isinstance(raw, str):
            s = raw.strip()
            if not s:
                return []
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed]
            except Exception:
                pass
            return [s]
        return [str(raw)]

    def _cue_usage_count(self, value):
        """n = numero de experiencias distintas que comparten exactamente ese cue value (IDF)."""
        row = self._conn.execute(
            "SELECT COUNT(DISTINCT experience_id) AS n FROM experience_cues WHERE value=?",
            (value,)).fetchone()
        return int(row["n"]) if row else 0

    def _cue_quality(self, value):
        """Calidad del cue (IDF inverso): q = clamp(0.1, 1.0, 1/(1+log(1+n))). Raro = mejor."""
        n = self._cue_usage_count(value)
        q = 1.0 / (1.0 + math.log(1.0 + n))
        return self._clamp(q, 0.1, 1.0)

    def _insert_cue(self, experience_id, component_type, value, source="extracted"):
        """Inserta un cue si no existe (UNIQUE experience_id/component_type/value) y le
        calcula cue_quality via IDF. Devuelve True si creo, False si ya existia."""
        value = str(value)[:512]
        if not value:
            return False
        exists = self._conn.execute(
            "SELECT id FROM experience_cues WHERE experience_id=? AND component_type=? AND value=?",
            (experience_id, component_type, value)).fetchone()
        if exists:
            self._refresh_cue_quality(exists["id"])
            return False
        q = self._cue_quality(value)
        self._conn.execute(
            "INSERT INTO experience_cues (experience_id, component_type, value, cue_quality, source) "
            "VALUES (?,?,?,?,?)",
            (experience_id, component_type, value, round(q, 4), source))
        return True

    def _refresh_cue_quality(self, cue_id):
        """Recomputa cue_quality de UN cue por su valor (IDF actual)."""
        row = self._conn.execute("SELECT value FROM experience_cues WHERE id=?", (cue_id,)).fetchone()
        if not row:
            return 0.0
        q = self._cue_quality(row["value"])
        self._conn.execute("UPDATE experience_cues SET cue_quality=? WHERE id=?",
                           (round(q, 4), cue_id))
        return round(q, 4)

    def _refresh_cue_qualities(self, exp_id):
        """UPDATE loop: refresca cue_quality de todos los cues que comparten valor con los
        cues de la experiencia dada (tras insertar, el conteo IDF incluye la nueva)."""
        values = [r["value"] for r in self._conn.execute(
            "SELECT DISTINCT value FROM experience_cues WHERE experience_id=?", (exp_id,))]
        updated = 0
        for v in values:
            q = self._cue_quality(v)
            cur = self._conn.execute(
                "UPDATE experience_cues SET cue_quality=? WHERE value=?", (round(q, 4), v))
            updated += cur.rowcount if cur.rowcount is not None else 0
        return updated

    def _retrieval_routes(self, experience_id):
        """rutas de recuperacion = COUNT de cues con cue_quality >= 0.3."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM experience_cues "
            "WHERE experience_id=? AND cue_quality >= 0.3",
            (experience_id,)).fetchone()
        return int(row["n"]) if row else 0

    def _salience_for(self, data, validation_status, topic_key):
        """Saliencia V6 inicial: base por tipo + ajuste por validacion + arch-decision. Clamp 0..1.
        La saliencia es SIGNIFICANCIA FUNCIONAL (que tan relevante es el episodio para decidir
        en ARGOS), NO emocion simulada: sube solo con senales verificables — validacion
        (osma_experience_validate: correccion fuerte +0.10, verificacion +0.05) y reuso
        (osma_experience_reuse: exito/fallo repetido +0.03)."""
        type_name = data.get("type") or ""
        s = _SALIENCE_BASE.get(type_name, 0.3)
        if not type_name or type_name not in _SALIENCE_BASE:
            tk = str(topic_key or "").lower()
            for k, v in _SALIENCE_BASE.items():
                if k in tk:
                    s = v
                    break
        if validation_status == "verified":
            s += 0.15
        elif validation_status == "failed":
            s += 0.05
        if topic_key and "arch-decision" in str(topic_key):
            s += 0.2
        return self._clamp(s, 0.0, 1.0)

    def _extract_base_cues(self, exp):
        """Cues base V6 de una experiencia: project, agent, entities, technologies,
        validation, quest, session, file. Lista de (component_type, value, source)."""
        text = self._experience_text(exp.get("situation"), exp.get("reasoning"),
                                     exp.get("conclusion"), exp.get("action"),
                                     exp.get("outcome"), exp.get("topic_key")).lower()
        cues = []
        project = exp.get("project")
        if not project and exp.get("topic_key"):
            project = str(exp["topic_key"]).split("/")[0]
        if project:
            cues.append(("project", str(project), "extracted"))
        if exp.get("agent"):
            cues.append(("agent", str(exp["agent"]), "extracted"))
        for ent in sorted(self._experience_entities(
                exp.get("situation"), exp.get("reasoning"), exp.get("conclusion"),
                exp.get("action"), exp.get("outcome"), exp.get("topic_key"))):
            cues.append(("entity", ent, "extracted"))
        for tech in _TECH_LIST:
            if tech in text:
                cues.append(("technology", tech, "extracted"))
        if exp.get("validation_status"):
            cues.append(("validation", str(exp["validation_status"]), "extracted"))
        if exp.get("quest_id"):
            cues.append(("quest", str(exp["quest_id"]), "extracted"))
        if exp.get("session_id"):
            cues.append(("session", str(exp["session_id"]), "extracted"))
        for f in self._loads_list(exp.get("files")):
            cues.append(("file", str(f), "extracted"))
        return cues

    def _extract_analyze_cues(self, exp):
        """Descomposicion completa V6 (17 tipos de cue): base + problem/error/solution/result/
        temporal + reasoning/action/concept/pattern. Usada por analyze y por record via
        _extract_full_cues (FIX 1/FIX 2)."""
        cues = self._extract_base_cues(exp)
        situation = (exp.get("situation") or "").lower()
        text = self._experience_text(exp.get("situation"), exp.get("reasoning"),
                                     exp.get("conclusion"), exp.get("action"),
                                     exp.get("outcome"), exp.get("topic_key")).lower()
        for w in _PROBLEM_WORDS:
            if w in situation:
                cues.append(("problem", w, "extracted"))
        for m in _ERROR_RE.findall(text):
            cues.append(("error", m.strip(), "extracted"))
        for phrase in _ERROR_PHRASES:
            if phrase in text:
                cues.append(("error", phrase, "extracted"))
        solution = (exp.get("action") or exp.get("conclusion") or "").strip()
        if solution:
            cues.append(("solution", solution[:512], "extracted"))
        if exp.get("outcome"):
            cues.append(("result", str(exp["outcome"])[:512], "extracted"))
        created = exp.get("created_at")
        if created:
            cues.append(("temporal", str(created)[:10], "extracted"))
        # ---- FIX 1 (Tywin): reasoning / action / concept / pattern ----
        reasoning_text = (exp.get("reasoning") or "").strip()
        if reasoning_text:
            cues.append(("reasoning", reasoning_text[:200], "extracted"))
        action_text = (exp.get("action") or "").strip()
        if action_text:
            cues.append(("action", action_text[:512], "extracted"))
        # concept: tokens-entidad largos (>=5) y conceptuales, fuera de _TECH_LIST.
        # Los tokens cortos/distintivos quedan como 'entity' (base). El overlap entity+concept
        # es 'genuinamente distinto' (tipo de cue distinto: token lexico vs termino conceptual).
        for ent in sorted(self._experience_entities(
                exp.get("situation"), exp.get("reasoning"), exp.get("conclusion"),
                exp.get("action"), exp.get("outcome"), exp.get("topic_key"))):
            if len(ent) >= 5 and ent not in _TECH_LIST:
                cues.append(("concept", ent, "extracted"))
        # pattern: patrones cuyo source_experience_ids contiene a esta experiencia, O cuyo
        # title (tokens >=3 chars) aparece como substring en el texto de la experiencia.
        pattern_rows = [dict(r) for r in self._conn.execute(
            "SELECT id, title, source_experience_ids FROM patterns ORDER BY id")]
        if pattern_rows:
            exp_id = exp.get("id")
            for p in pattern_rows:
                title = (p.get("title") or "").strip()
                if not title:
                    continue
                belongs = False
                try:
                    pids = json.loads(p.get("source_experience_ids") or "[]")
                except Exception:
                    pids = []
                if exp_id is not None and exp_id in pids:
                    belongs = True
                if not belongs:
                    title_tokens = [t for t in re.findall(
                        r"[a-z\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1\u00fc0-9]+", title.lower())
                        if len(t) >= 3]
                    if not title_tokens or not any(t in text for t in title_tokens):
                        continue
                cues.append(("pattern", title[:512], "extracted"))
        return cues

    def _extract_full_cues(self, exp):
        """Descomposicion COMPLETA (base + extendidos) usada por record (FIX 2) y por analyze:
        mismo resultado que _extract_analyze_cues. Idempotente: _insert_cue no duplica."""
        return self._extract_analyze_cues(exp)

    def _avg_link_weight(self, experience_id):
        """FIX 3: association_strength = promedio de pesos de experience_links de la experiencia
        (0.5 default si no tiene links). Independiente de confidence."""
        rows = self._conn.execute(
            "SELECT weight FROM experience_links WHERE exp_a_id=? OR exp_b_id=?",
            (experience_id, experience_id)).fetchall()
        if not rows:
            return 0.5
        return sum(float(r["weight"]) for r in rows) / len(rows)

    def _generate_anchors(self, exp):
        """Anclas de recuperacion: aliases estaticos de cues conocidos + tokens distintivos
        (entity/technology con IDF n <= 3, raros en la DB)."""
        anchors = []
        known = set()
        for row in self._conn.execute(
                "SELECT value FROM experience_cues WHERE experience_id=?", (exp["id"],)):
            known.add(str(row["value"]).lower())
        for cue_value, aliases in _ALIAS_TABLE.items():
            if cue_value in known:
                for a in aliases:
                    if a not in anchors:
                        anchors.append(a)
        for row in self._conn.execute(
                "SELECT value FROM experience_cues WHERE experience_id=? "
                "AND component_type IN ('entity','technology')", (exp["id"],)):
            v = str(row["value"])
            if self._cue_usage_count(v) <= 3:
                if v not in anchors:
                    anchors.append(v)
        return anchors

    def _update_v6_metadata(self, exp_id):
        """Refresca columnas V6 (entities/concepts/files/temporal_context/summary) desde cues."""
        exp = self._conn.execute("SELECT * FROM experiences WHERE id=?", (exp_id,)).fetchone()
        if not exp:
            return
        exp = dict(exp)
        entities = [r["value"] for r in self._conn.execute(
            "SELECT value FROM experience_cues WHERE experience_id=? AND component_type='entity' "
            "ORDER BY value", (exp_id,))]
        concepts = [r["value"] for r in self._conn.execute(
            "SELECT value FROM experience_cues WHERE experience_id=? AND component_type='technology' "
            "ORDER BY value", (exp_id,))]
        files = [r["value"] for r in self._conn.execute(
            "SELECT value FROM experience_cues WHERE experience_id=? AND component_type='file' "
            "ORDER BY value", (exp_id,))]
        temporal = str(exp["created_at"] or "")[:10] or None
        summary = exp.get("summary") or (exp.get("situation") or "")[:300]
        self._conn.execute(
            "UPDATE experiences SET entities=?, concepts=?, files=?, temporal_context=?, summary=? "
            "WHERE id=?",
            (json.dumps(entities, ensure_ascii=False), json.dumps(concepts, ensure_ascii=False),
             json.dumps(files, ensure_ascii=False), temporal, summary, exp_id))

    def osma_experience_analyze(self, experience_id=None):
        """Descomposicion V6 completa + generacion de anclas:
        (a) agrega todos los cues faltantes de cada experiencia;
        (b) recomputa cue_quality (IDF) de todos los cues;
        (c) para experiencias con retrieval_routes < 3 y (importance >= 0.5 o salience >= 0.6)
            genera anclas de recuperacion (aliases estaticos + tokens distintivos)."""
        if experience_id is not None:
            rows = [dict(r) for r in self._conn.execute(
                "SELECT * FROM experiences WHERE id=?", (int(experience_id),))]
        else:
            rows = [dict(r) for r in self._conn.execute("SELECT * FROM experiences ORDER BY id")]
        cues_created = 0
        cues_updated = 0
        anchors_created = 0
        routes_now = []
        for exp in rows:
            for ctype, value, source in self._extract_full_cues(exp):
                if self._insert_cue(exp["id"], ctype, value, source):
                    cues_created += 1
            values = [r["value"] for r in self._conn.execute(
                "SELECT DISTINCT value FROM experience_cues WHERE experience_id=?", (exp["id"],))]
            for v in values:
                q = self._cue_quality(v)
                cur = self._conn.execute(
                    "UPDATE experience_cues SET cue_quality=? WHERE value=?", (round(q, 4), v))
                cues_updated += cur.rowcount if cur.rowcount is not None else 0
            self._update_v6_metadata(exp["id"])
            routes = self._retrieval_routes(exp["id"])
            if (routes < 3 and (float(exp.get("importance") or 0.0) >= 0.5
                                or float(exp.get("salience") or 0.0) >= 0.6)):
                anchor_values = self._loads_list(exp.get("retrieval_anchors"))
                for a in self._generate_anchors(exp):
                    if self._insert_cue(exp["id"], "anchor", a, "generated"):
                        anchors_created += 1
                        anchor_values.append(a)
                self._conn.execute(
                    "UPDATE experiences SET retrieval_anchors=? WHERE id=?",
                    (json.dumps(anchor_values, ensure_ascii=False), exp["id"]))
            # backfill saliencia V6 para experiencias que no la tienen (0.0 default)
            if float(exp.get("salience") or 0.0) == 0.0:
                sal = self._salience_for({"type": ""}, exp.get("validation_status"),
                                         exp.get("topic_key"))
                self._conn.execute("UPDATE experiences SET salience=? WHERE id=?",
                                   (round(sal, 4), exp["id"]))
                exp["salience"] = sal
            routes_now.append({"experience_id": exp["id"],
                               "retrieval_routes": self._retrieval_routes(exp["id"])})
        self._conn.commit()
        return {"experiences_analyzed": len(rows), "cues_created": cues_created,
                "cues_updated": cues_updated, "anchors_created": anchors_created,
                "routes_now": routes_now}

    def osma_cues(self, experience_id):
        """Lista los cues de una experiencia ordenados por cue_quality desc."""
        exp_id = int(experience_id)
        cues = [dict(r) for r in self._conn.execute(
            "SELECT id, component_type, value, cue_quality, source, created_at, "
            "coactivation_count, last_coactivated_at "
            "FROM experience_cues WHERE experience_id=? ORDER BY cue_quality DESC, id",
            (exp_id,))]
        return {"experience_id": exp_id, "retrieval_routes": self._retrieval_routes(exp_id),
                "cues": cues}

    def osma_cue_search(self, data):
        """Convergencia multi-cue + pattern completion (FIX 5, Tywin): CUE -> activar nodos ->
        PROPAGACION (1 hop via experience_links) -> convergencia -> competencia -> ganador.
        Cada query cue matchea cues por substring (minusculas); cada experiencia acumula sus
        cues matcheados con cue_quality. episode_activation_score = sum(q_i) + GAMMA * k^2 *
        avg(q_i), clamp 0..10. Propagacion: experiencias enlazadas (weight >= 0.1) a una
        directamente activada se suman al pool con score = activacion_directa * 0.5 * link_weight
        (decaimiento por distancia y peso) y via_association: true — participan en el ranking
        como contexto de apoyo, pero SOLO las directas pueden ser winner. total_activated =
        directas + propagadas.
        Ranking competitivo (FIX 2, Tywin): episode_activation_score sigue siendo el factor
        PRIMARIO; se agrega competition_score (recencia + importancia + proyecto + coherencia,
        pesos W_* tunables) como tie-breaker SOLO entre scores iguales/cercanos.
        Integracion FTS5 (FIX 3, Tywin): los cues estructurados (experience_cues) son la
        taxonomia exacta del episodio y se matchean DETERMINISTICAMENTE con LIKE — FTS5 no
        esta disenado para matching exacto de valores de cue, por eso este es el path primario.
        Cuando un cue del query NO encuentra ningun hit estructurado, se reusa la capa FTS5
        existente (recall/observations_fts) para hallar observaciones relacionadas y, via
        experience_observation_links, experiencias candidatas que entran al pool como
        via_fts: true (contexto semantico, NUNCA winner por no tener cue directo). Flag
        _FTS5_FALLBACK permite desactivar el fallback sin tocar el path primario."""
        query_cues = [str(c).strip().lower() for c in (data.get("cues") or []) if str(c).strip()]
        if not query_cues:
            return {"error": "cues es obligatorio (array de strings)"}
        limit = int(data.get("limit") or 10)
        project = data.get("project")
        agent = data.get("agent")
        all_experiences = [dict(r) for r in self._conn.execute("SELECT * FROM experiences ORDER BY id")]
        patterns = [dict(r) for r in self._conn.execute("SELECT * FROM patterns ORDER BY id")]
        query_entities = set(self._entity_tokens(" ".join(query_cues), None, None))

        matched_by_exp = {}
        structured_hits = set()   # FIX 3: cues con al menos un hit estructurado (LIKE)
        for c in query_cues:
            cue_hit = False
            for row in self._conn.execute(
                    "SELECT experience_id, component_type, value, cue_quality FROM experience_cues "
                    "WHERE lower(value) LIKE ?", ("%" + c + "%",)):
                cue_hit = True
                eid = row["experience_id"]
                if project or agent:
                    exp_row = self._conn.execute(
                        "SELECT project, agent FROM experiences WHERE id=?", (eid,)).fetchone()
                    if not exp_row:
                        continue
                    if project and exp_row["project"] != project:
                        continue
                    if agent and exp_row["agent"] != agent:
                        continue
                matched_by_exp.setdefault(eid, []).append({
                    "component_type": row["component_type"],
                    "value": row["value"],
                    "cue_quality": round(float(row["cue_quality"]), 4),
                })
            if cue_hit:
                structured_hits.add(c)

        # ---- activacion directa: convergencia multi-cue por experiencia ----
        # referencia temporal unica para competition_score (determinismo intra-query)
        now_dt = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        activated = {}
        direct_scores = {}
        for eid, matched in matched_by_exp.items():
            exp = next((e for e in all_experiences if e["id"] == eid), None)
            if not exp:
                continue
            k = len(matched)
            qualities = [float(m["cue_quality"]) for m in matched]
            avg_q = sum(qualities) / k if k else 0.0
            score = sum(qualities) + _GAMMA * (k ** 2) * avg_q
            score = self._clamp(score, 0.0, 10.0)
            e_entities = self._experience_entities(
                exp.get("situation"), exp.get("reasoning"), exp.get("conclusion"),
                exp.get("action"), exp.get("outcome"), exp.get("topic_key"))
            applicability = self._experience_applicability(
                exp, e_entities, query_entities, project, all_experiences, patterns)
            direct_scores[eid] = score
            activated[eid] = {
                "experience_id": eid,
                "episode_activation_score": round(score, 4),
                "matched_cues": matched,
                "k": k,
                "salience": round(float(exp.get("salience") or 0.0), 4),
                "validation_status": exp.get("validation_status"),
                "reward_signal": exp.get("reward_signal"),
                "applicability": applicability,
                "summary": exp.get("summary") or (exp.get("situation") or ""),
                "solution": exp.get("action") or exp.get("conclusion") or "",
                "outcome": exp.get("outcome") or "",
                "project": exp.get("project"),
                "agent": exp.get("agent"),
                "via_association": False,
                "_confidence": float(exp.get("confidence") or 0.0),
                "competition_score": round(self._competition_score(exp, k, project, now_dt), 4),
            }

        # ---- PROPAGACION asociativa (1 hop): experiencia enlazada -> contexto de apoyo ----
        if direct_scores:
            direct_ids = set(direct_scores.keys())
            placeholders = ",".join("?" * len(direct_ids))
            link_rows = self._conn.execute(
                "SELECT exp_a_id, exp_b_id, weight FROM experience_links "
                "WHERE weight >= 0.1 AND (exp_a_id IN (%s) OR exp_b_id IN (%s))"
                % (placeholders, placeholders),
                tuple(direct_ids) + tuple(direct_ids)).fetchall()
            propagated_pool = {}
            for lr in link_rows:
                a, b = lr["exp_a_id"], lr["exp_b_id"]
                w = float(lr["weight"])
                if a in direct_ids and b not in direct_ids:
                    neigh, dscore = b, direct_scores[a]
                elif b in direct_ids and a not in direct_ids:
                    neigh, dscore = a, direct_scores[b]
                else:
                    continue  # ambos directos (o ninguno): sin propagar
                # decaimiento por distancia (1 hop *0.5) y por peso del link
                prop_score = dscore * 0.5 * w
                if prop_score > propagated_pool.get(neigh, 0.0):
                    propagated_pool[neigh] = prop_score
            for neigh, pscore in propagated_pool.items():
                exp = next((e for e in all_experiences if e["id"] == neigh), None)
                if not exp:
                    continue
                if project and exp.get("project") != project:
                    continue
                if agent and exp.get("agent") != agent:
                    continue
                e_entities = self._experience_entities(
                    exp.get("situation"), exp.get("reasoning"), exp.get("conclusion"),
                    exp.get("action"), exp.get("outcome"), exp.get("topic_key"))
                applicability = self._experience_applicability(
                    exp, e_entities, query_entities, project, all_experiences, patterns)
                activated[neigh] = {
                    "experience_id": neigh,
                    "episode_activation_score": round(pscore, 4),
                    "matched_cues": [],
                    "k": 0,
                    "salience": round(float(exp.get("salience") or 0.0), 4),
                    "validation_status": exp.get("validation_status"),
                    "reward_signal": exp.get("reward_signal"),
                    "applicability": applicability,
                    "summary": exp.get("summary") or (exp.get("situation") or ""),
                    "solution": exp.get("action") or exp.get("conclusion") or "",
                    "outcome": exp.get("outcome") or "",
                    "project": exp.get("project"),
                    "agent": exp.get("agent"),
                    "via_association": True,
                    "_confidence": float(exp.get("confidence") or 0.0),
                    "competition_score": round(self._competition_score(exp, 0, project, now_dt), 4),
                }

        # ---- FALLBACK FTS5 (FIX 3, Tywin - fts5_integration): cues SIN match estructurado ----
        # Diseno deliberado: los cues estructurados (experience_cues) son la taxonomia exacta
        # del episodio y se matchean DETERMINISTICAMENTE con LIKE (path primario — FTS5 no esta
        # disenado para matching exacto de valores de cue). Cuando un cue del query NO encuentra
        # ningun hit estructurado, se reusa la capa FTS5 existente (recall/observations_fts)
        # para hallar observaciones relacionadas y, via experience_observation_links, experiencias
        # candidatas que entran al pool como via_fts: true (contexto semantico, participan en el
        # ranking pero NUNCA son winner por no tener cue directo). Flag _FTS5_FALLBACK permite
        # desactivar el fallback; try/except lo hace no-fatal.
        fts_added = 0
        if _FTS5_FALLBACK:
            unmatched = [c for c in query_cues if c not in structured_hits]
            for cue in unmatched[:_FTS5_MAX_UNMATCHED]:
                try:
                    obs_rows = self.recall(cue, agent=agent, limit=_FTS5_RECALL_LIMIT)
                except Exception:
                    continue  # FTS5 nunca rompe la busqueda
                for obs in obs_rows:
                    obs_id = int(obs["id"])
                    linked = self._conn.execute(
                        "SELECT experience_id FROM experience_observation_links "
                        "WHERE observation_id=?", (obs_id,)).fetchall()
                    for lr in linked:
                        eid = int(lr["experience_id"])
                        if eid in activated:
                            continue  # ya tiene match directo: no degradar
                        exp = next((e for e in all_experiences if e["id"] == eid), None)
                        if not exp:
                            continue
                        if project and exp.get("project") != project:
                            continue
                        if agent and exp.get("agent") != agent:
                            continue
                        e_entities = self._experience_entities(
                            exp.get("situation"), exp.get("reasoning"), exp.get("conclusion"),
                            exp.get("action"), exp.get("outcome"), exp.get("topic_key"))
                        applicability = self._experience_applicability(
                            exp, e_entities, query_entities, project, all_experiences, patterns)
                        # score modesto (< 1.0, bajo el umbral de winner): contexto semantico
                        fts_score = self._clamp(
                            0.3 + float(obs.get("confidence") or 0.5) * 0.4, 0.0, 0.99)
                        activated[eid] = {
                            "experience_id": eid,
                            "episode_activation_score": round(fts_score, 4),
                            "matched_cues": [],
                            "k": 0,
                            "salience": round(float(exp.get("salience") or 0.0), 4),
                            "validation_status": exp.get("validation_status"),
                            "reward_signal": exp.get("reward_signal"),
                            "applicability": applicability,
                            "summary": exp.get("summary") or (exp.get("situation") or ""),
                            "solution": exp.get("action") or exp.get("conclusion") or "",
                            "outcome": exp.get("outcome") or "",
                            "project": exp.get("project"),
                            "agent": exp.get("agent"),
                            "via_association": False,
                            "via_fts": True,
                            "_confidence": float(exp.get("confidence") or 0.0),
                            "competition_score": round(
                                self._competition_score(exp, 0, project, now_dt), 4),
                        }
                        fts_added += 1

        results = list(activated.values())
        results.sort(key=lambda r: (-r["episode_activation_score"], -r["competition_score"],
                                    -r["salience"], -r["_confidence"]))
        results = results[:limit]
        winner = None
        # solo las experiencias con match DIRECTO pueden ser winner
        # (propagadas via_association y FTS via_fts = contexto, nunca winner).
        for top in results:
            if top.get("via_association") or top.get("via_fts"):
                continue
            if (top["episode_activation_score"] >= 1.0
                    and top["validation_status"] != "failed"
                    and top["applicability"] != "obsolete"):
                winner = {
                    "experience_id": top["experience_id"],
                    "episode_activation_score": top["episode_activation_score"],
                    "confidence": top["_confidence"],
                    "reconstruction": {
                        "summary": top["summary"],
                        "solution": top["solution"],
                        "outcome": top["outcome"],
                        "validation": top["validation_status"],
                    },
                }
                break
        for r in results:
            r.pop("_confidence", None)
            r["episode_id"] = self._episode_id(r["experience_id"])
        # ---- V7: reactivacion post-retrieval (recordar modifica la memoria) ----
        reactivation = None
        if winner is not None:
            winner["episode_id"] = self._episode_id(winner["experience_id"])
            try:
                reactivation = self._reactivate(winner["experience_id"], results, activated)
            except Exception:
                # V7: un fallo de escritura NUNCA rompe la busqueda — se responde sin refuerzo
                reactivation = None
        return {"winner": winner, "total_activated": len(activated), "results": results,
                "reactivation": reactivation}

    def osma_anchor_add(self, data):
        """Ancla manual: inserta cue (component_type='anchor', source='manual') y computa
        cue_quality via IDF. Actualiza experiences.retrieval_anchors (JSON)."""
        exp_id = int(data.get("experience_id", 0))
        anchor = str(data.get("anchor") or "").strip()
        if not anchor:
            return {"ok": False, "error": "anchor es obligatorio"}
        row = self._conn.execute("SELECT * FROM experiences WHERE id=?", (exp_id,)).fetchone()
        if not row:
            return {"ok": False, "error": "experiencia no existe"}
        ctype = str(data.get("anchor_type") or "anchor")
        if self._insert_cue(exp_id, ctype, anchor, "manual"):
            self._refresh_cue_qualities(exp_id)
            anchors = self._loads_list(row["retrieval_anchors"])
            if anchor not in anchors:
                anchors.append(anchor)
            self._conn.execute(
                "UPDATE experiences SET retrieval_anchors=? WHERE id=?",
                (json.dumps(anchors, ensure_ascii=False), exp_id))
        self._conn.commit()
        cue = self._conn.execute(
            "SELECT id FROM experience_cues WHERE experience_id=? AND component_type=? AND value=?",
            (exp_id, ctype, anchor)).fetchone()
        return {"ok": True, "cue_id": cue["id"] if cue else None,
                "cue_quality": round(self._cue_quality(anchor), 4)}

    def osma_routes(self, experience_id):
        """Rutas de recuperacion de una experiencia + conteo por tipo + calidad promedio."""
        exp_id = int(experience_id)
        rows = [dict(r) for r in self._conn.execute(
            "SELECT component_type, cue_quality FROM experience_cues WHERE experience_id=?",
            (exp_id,))]
        by_type = {}
        total_q = 0.0
        for r in rows:
            by_type[r["component_type"]] = by_type.get(r["component_type"], 0) + 1
            total_q += float(r["cue_quality"])
        avg_q = round(total_q / len(rows), 4) if rows else 0.0
        return {"experience_id": exp_id, "retrieval_routes": self._retrieval_routes(exp_id),
                "route_count_by_type": by_type, "avg_cue_quality": avg_q}

    # ---------- OSMA V7 API - Episode Pattern Completion (reactivacion + reconstruccion) ----------
    #   publicos: osma_episode
    #   privados: _episode_id, _reactivate
    @staticmethod
    def _episode_id(eid):
        """ID visible de episodio: EPISODE_XXXX (zero-padded 4 digitos). Si el id supera
        9999, se muestra el numero tal cual (el formato :04d no agrega padding de mas)."""
        return f"EPISODE_{int(eid):04d}"

    def _reactivate(self, winner_id, results, activated):
        """V7 reactivacion post-retrieval: reforzar la memoria que se acaba de recuperar.
        1) winner: frequency+1 y retrieval_strength+0.03 (cap 1.0) — recuperar con exito
           hace el episodio MAS accesible.
        2) cues del winner que participaron en la recuperacion (matched_cues):
           coactivation_count+1 y last_coactivated_at=now.
        3) experience_links winner <-> OTRAS experiencias co-activadas del mismo pool de
           resultados (directas o via_association, excluyendo al winner): delta +0.05 (cap 1.0)
           y coactivation_count+1 con last_coactivated_at=now (FIX 1, Tywin: reforzar el link
           TAMBIEN incrementa su coactivacion — coact=1 en _upsert_experience_link).
        El caller (osma_cue_search) lo envuelve en try/except: un fallo de escritura nunca
        debe romper la busqueda."""
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        winner_id = int(winner_id)
        # 1) winner: frequency+1, retrieval_strength+0.03 (cap 1.0)
        row = self._conn.execute(
            "SELECT retrieval_strength FROM experiences WHERE id=?", (winner_id,)).fetchone()
        if row:
            new_rs = self._clamp(float(row["retrieval_strength"] or 0.5) + 0.03, 0.0, 1.0)
            self._conn.execute(
                "UPDATE experiences SET frequency=frequency+1, retrieval_strength=? WHERE id=?",
                (round(new_rs, 4), winner_id))
        # 2) cues matcheados del winner: coactivation_count+1, last_coactivated_at=now
        cues_reinforced = 0
        win_entry = activated.get(winner_id)
        for m in (win_entry.get("matched_cues", []) if isinstance(win_entry, dict) else []):
            cur = self._conn.execute(
                "UPDATE experience_cues SET coactivation_count=coactivation_count+1, "
                "last_coactivated_at=? WHERE experience_id=? AND component_type=? AND value=?",
                (now, winner_id, m.get("component_type"), m.get("value")))
            if cur.rowcount:
                cues_reinforced += 1
        # 3) links winner <-> otras co-activadas del pool (delta 0.05 cap 1.0, coact +1)
        reinforced_links = 0
        for other in results:
            other_id = int(other.get("experience_id"))
            if other_id == winner_id:
                continue
            self._upsert_experience_link(winner_id, other_id, delta=0.05, coact=1)
            reinforced_links += 1
        self._conn.commit()
        return {"episode_id": self._episode_id(winner_id),
                "reinforced_links": reinforced_links,
                "link_coactivation_delta": 1 if reinforced_links else 0,
                "cues_reinforced": cues_reinforced,
                "retrieval_strength_delta": 0.03,
                "frequency_delta": 1}

    def osma_episode(self, experience_id):
        """V7 reconstruccion completa del episodio (EPISODE_XXXX): todos los campos de la
        experiencia + cues + rutas + experiencias relacionadas + observaciones relacionadas
        + patrones. Todo derivado de tablas existentes (no crea nada nuevo)."""
        exp_id = int(experience_id)
        row = self._conn.execute("SELECT * FROM experiences WHERE id=?", (exp_id,)).fetchone()
        if not row:
            return {"error": f"experiencia {exp_id} no existe"}
        exp = dict(row)
        cues = [dict(r) for r in self._conn.execute(
            "SELECT id, component_type, value, cue_quality, source, coactivation_count, "
            "last_coactivated_at FROM experience_cues WHERE experience_id=? "
            "ORDER BY cue_quality DESC, id", (exp_id,))]
        routes = self.osma_routes(exp_id)
        link_rows = [dict(r) for r in self._conn.execute(
            "SELECT * FROM experience_links WHERE exp_a_id=? OR exp_b_id=? "
            "ORDER BY weight DESC", (exp_id, exp_id))]
        related_experiences = []
        for lr in link_rows:
            other_id = lr["exp_a_id"] if lr["exp_b_id"] == exp_id else lr["exp_b_id"]
            related_experiences.append({
                "experience_id": int(other_id),
                "episode_id": self._episode_id(other_id),
                "weight": round(float(lr["weight"]), 4),
                "coactivation_count": int(lr["coactivation_count"] or 0),
            })
        related_observations = [{
            "observation_id": int(o["observation_id"]),
            "weight": round(float(o["weight"]), 4),
        } for o in self._conn.execute(
            "SELECT observation_id, weight FROM experience_observation_links "
            "WHERE experience_id=? ORDER BY weight DESC", (exp_id,))]
        patterns = [{"id": p["id"], "title": p.get("title"),
                     "check_procedure": p.get("check_procedure")}
                    for p in self._patterns_for_experience(exp_id)]
        return {
            "episode_id": self._episode_id(exp_id),
            "experience_id": exp_id,
            "summary": exp.get("summary"),
            "situation": exp.get("situation"),
            "reasoning": exp.get("reasoning"),
            "conclusion": exp.get("conclusion"),
            "action": exp.get("action"),
            "outcome": exp.get("outcome"),
            "validation_status": exp.get("validation_status"),
            "reward_signal": exp.get("reward_signal"),
            "confidence": exp.get("confidence"),
            "importance": exp.get("importance"),
            "salience": exp.get("salience"),
            "retrieval_strength": exp.get("retrieval_strength"),
            "frequency": exp.get("frequency"),
            "association_strength": exp.get("association_strength"),
            "project": exp.get("project"),
            "agent": exp.get("agent"),
            "topic_key": exp.get("topic_key"),
            "quest_id": exp.get("quest_id"),
            "session_id": exp.get("session_id"),
            "files": self._loads_list(exp.get("files")),
            "temporal_context": exp.get("temporal_context"),
            "cues": cues,
            "routes": routes,
            "related_experiences": related_experiences,
            "related_observations": related_observations,
            "patterns": patterns,
        }

    # ---------- OSMA V4-V7 BRAIN SUMMARY (osma-stats) ----------
    # Resumen del cerebro OSMA completo: metricas legacy V4 (observations/links)
    # + experiences (V5) + cues (V6) + episodes (V7). Defensivo por grupo: cada
    # bloque va en try/except y hace default a ceros/arrays vacios si una tabla
    # no existe, esta vacia o esta incompleta. NUNCA crashea (requisito: empty
    # DB y missing table tolerados — FIX Tywin: el dispatch ya NO llama a
    # osma_stats() sin guarda; todo el output sale de este unico metodo).
    def _osma_stats(self):
        def _count(sql, *params):
            try:
                return int(self._conn.execute(sql, params).fetchone()[0])
            except Exception:
                return 0

        def _avg(sql, *params):
            try:
                val = self._conn.execute(sql, params).fetchone()[0]
                return round(float(val), 4) if val is not None else 0.0
            except Exception:
                return 0.0

        # ---- V4 legacy (antes osma_stats): metricas de observations/links ----
        # Defensivas: observation_links puede faltar o estar incompleta (sin la
        # columna weight) en DBs legacy; si la SELECT falla, se reportan ceros.
        legacy_links = []
        try:
            legacy_links = [dict(r) for r in self._conn.execute(
                "SELECT weight FROM observation_links")]
        except Exception:
            legacy_links = []
        legacy = {
            "links": len(legacy_links),
            "avg_link_weight": round(
                sum(float(l["weight"]) for l in legacy_links) / len(legacy_links), 4
            ) if legacy_links else 0.0,
            "active": _count("SELECT COUNT(*) FROM observations WHERE state='active' AND archived=0"),
            "warm": _count("SELECT COUNT(*) FROM observations WHERE state='dormant'"),
            "cold": 0,
            "archived": _count("SELECT COUNT(*) FROM observations WHERE archived=1 OR state='archived'"),
            "contradictions_open": _count("SELECT COUNT(*) FROM contradictions WHERE status='open'"),
            "consolidations_pending": _count("SELECT COUNT(*) FROM consolidations WHERE status='pending'"),
        }

        total_experiences = _count("SELECT COUNT(*) FROM experiences")
        total_cues = _count("SELECT COUNT(*) FROM experience_cues")
        # links del brain: experience_links (V5) + experience_observation_links (V5)
        # + observation_links (V4) — la red asociativa completa V4-V7.
        total_links = (_count("SELECT COUNT(*) FROM experience_links")
                       + _count("SELECT COUNT(*) FROM experience_observation_links")
                       + _count("SELECT COUNT(*) FROM observation_links"))
        total_episodes = total_experiences

        # ids visibles de episodio (EPISODE_XXXX) reusando el helper V7.
        episode_ids = []
        try:
            for r in self._conn.execute("SELECT id FROM experiences ORDER BY id"):
                episode_ids.append(self._episode_id(r["id"]))
        except Exception:
            episode_ids = []

        # distribucion por validation_status (taxonomia V5; 'recorded' inicial
        # corresponde a 'proposal' — el estado default al registrar sin reward).
        status_distribution = {
            "proposal": 0, "hypothesis": 0, "attempted": 0, "partial": 0,
            "verified": 0, "failed": 0, "unverified": 0,
        }
        try:
            for r in self._conn.execute(
                    "SELECT validation_status, COUNT(*) AS n FROM experiences "
                    "GROUP BY validation_status"):
                st = r["validation_status"] or "unverified"
                status_distribution[st] = int(r["n"])
        except Exception:
            pass

        avg_cues_per_experience = round(total_cues / total_experiences, 4) if total_experiences else 0.0

        result = dict(legacy)
        result.update({
            "total_experiences": total_experiences,
            "total_cues": total_cues,
            "total_links": total_links,
            "total_episodes": total_episodes,
            "episodes": {"count": len(episode_ids), "ids": episode_ids},
            "status_distribution": status_distribution,
            "avg_confidence": _avg("SELECT AVG(confidence) FROM experiences"),
            "avg_importance": _avg("SELECT AVG(importance) FROM experiences"),
            "avg_retrieval_strength": _avg("SELECT AVG(retrieval_strength) FROM experiences"),
            "avg_cues_per_experience": avg_cues_per_experience,
        })
        return result

    # ---------- EXPORT / IMPORT ----------
    def export_jsonl(self, out_dir):
        """Snapshot portable para git/backup."""
        os.makedirs(out_dir, exist_ok=True)
        files = {}
        for row in self._conn.execute(
                "SELECT * FROM observations ORDER BY id"):
            agent = row["agent"]
            path = os.path.join(out_dir, f"{agent}-memory.jsonl")
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
            files[path] = files.get(path, 0) + 1
        return files

    def import_jsonl(self, in_dir):
        """Recupera memoria desde JSONL si no hay db."""
        count = 0
        for fname in os.listdir(in_dir):
            if not fname.endswith(".jsonl"):
                continue
            path = os.path.join(in_dir, fname)
            agent = fname.replace("-memory.jsonl", "").replace(".jsonl", "")
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        self.save_observation(
                            agent=data.get("agent", agent),
                            topic_key=data.get("topic_key", "imported"),
                            type=data.get("type", "discovery"),
                            content=data.get("content", ""),
                            quest_id=data.get("quest_id"))
                        count += 1
                    except json.JSONDecodeError:
                        continue
        return count

    # ---------- STATS ----------
    def stats(self):
        return {
            "agents": self._conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0],
            "observations": self._conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
            "quests": self._conn.execute("SELECT COUNT(*) FROM quests").fetchone()[0],
            "sessions": self._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
            "edges": self._conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0],
            "db_size_bytes": os.path.getsize(self.db_path),
        }

    def close(self):
        """Cierra la conexion SQLite. Idempotente: puede llamarse varias veces sin error."""
        conn = getattr(self, "_conn", None)
        if conn is not None:
            self._conn = None
            try:
                conn.close()
            except Exception:
                pass

    def __del__(self):
        """Ultima red de seguridad: cierra la conexion si el objeto se recolecta sin close()."""
        try:
            self.close()
        except Exception:
            pass


def _read_json_arg(raw):
    """Lee JSON desde argumento o desde stdin (si raw == '-' o vacio)."""
    if raw and raw != "-":
        return json.loads(raw)
    # Leer de stdin (pipe desde PowerShell evita problemas de escaping)
    data = sys.stdin.read().strip()
    if not data:
        return {}
    return json.loads(data)


def main():
    """CLI: python osma_brain.py <db> <command> [args]"""
    if len(sys.argv) < 3:
        print(json.dumps({"error": "uso: osma_brain.py <db> <command> [args]"}))
        sys.exit(1)

    db_path, command = sys.argv[1], sys.argv[2]
    brain = None
    try:
        brain = OsmaBrain(db_path)
        _dispatch(brain, command)
    finally:
        # Garantiza el cierre de la conexion SQLite en TODOS los code paths
        # (excepciones, sys.exit, comandos desconocidos, final normal).
        if brain is not None:
            brain.close()


def _dispatch(brain, command):
    """Ejecuta el comando pedido sobre el cerebro (separado de main para que el
    cierre de la conexion quede garantizado en un finally)."""
    if command == "init":
        agents = _read_json_arg(sys.argv[3]) if len(sys.argv) > 3 else []
        for a in agents:
            brain.upsert_agent(a["id"], a.get("name"), a.get("class"),
                               a.get("role"), a.get("model"))
        print(json.dumps(brain.stats()))

    elif command == "save":
        data = _read_json_arg(sys.argv[3] if len(sys.argv) > 3 else "-")
        if data.get("upsert"):
            obs_id = brain.save_observation_upsert(
                data.get("agent", "atlas"), data.get("topic_key", "atlas/general"),
                data.get("type", "discovery"), data.get("content", ""), data.get("quest_id"),
                data.get("score", 0), data.get("tags"), data.get("memory_kind"),
                data.get("confidence"), data.get("volatility"), data.get("evidence"), data.get("source"))
        else:
            obs_id = brain.save_observation(
                data.get("agent", "atlas"), data.get("topic_key", "atlas/general"),
                data.get("type", "discovery"), data.get("content", ""), data.get("quest_id"),
                data.get("score", 0), data.get("tags"), data.get("memory_kind"),
                data.get("confidence"), data.get("volatility"), data.get("evidence"), data.get("source"))
        brain._link_on_write(obs_id, data.get("content", ""), data.get("tags"),
                             data.get("topic_key", "atlas/general"))
        print(json.dumps({"id": obs_id, "status": "saved"}))

    elif command == "recall":
        query = sys.argv[3]
        agent = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] != "-" else None
        limit = int(sys.argv[5]) if len(sys.argv) > 5 else 5
        tag = sys.argv[6] if len(sys.argv) > 6 and sys.argv[6] != "-" else None
        print(json.dumps(brain.recall(query, agent=agent, limit=limit, tag=tag), ensure_ascii=False))

    elif command == "reinforce":
        data = _read_json_arg(sys.argv[3] if len(sys.argv) > 3 else "-")
        ok = brain.reinforce(int(data.get("id", 0)), evidence=data.get("evidence"),
                             success=bool(data.get("success", True)))
        print(json.dumps({"ok": ok, "id": data.get("id")}))

    elif command == "verify":
        data = _read_json_arg(sys.argv[3] if len(sys.argv) > 3 else "-")
        ok = brain.verify(int(data.get("id", 0)), data.get("verdict", "PASS"),
                          evidence=data.get("evidence"))
        print(json.dumps({"ok": ok, "id": data.get("id"), "verdict": data.get("verdict")}))

    elif command == "reconsolidate":
        data = _read_json_arg(sys.argv[3] if len(sys.argv) > 3 else "-")
        ok = brain.reconsolidate(int(data.get("id", 0)), data.get("content", ""),
                                 evidence=data.get("evidence"))
        print(json.dumps({"ok": ok, "id": data.get("id")}))

    elif command == "skill":
        action = sys.argv[3]
        if action == "register":
            data = _read_json_arg(sys.argv[4] if len(sys.argv) > 4 else "-")
            brain.skill_register(data.get("skill_id", ""), data.get("version", "1.0"),
                                 data.get("triggers"), data.get("anti_triggers"))
            print(json.dumps({"ok": True, "skill": data.get("skill_id")}))
        elif action == "exec":
            data = _read_json_arg(sys.argv[4] if len(sys.argv) > 4 else "-")
            res = brain.skill_record_execution(
                data.get("skill_id", ""), data.get("version", "1.0"), data.get("agent"),
                data.get("quest_id"), data.get("trigger"), bool(data.get("success", True)),
                data.get("verdict"), data.get("evidence"), data.get("error"),
                data.get("tokens_in", 0), data.get("tokens_out", 0),
                data.get("tool_calls", 0), data.get("model"), data.get("provider"))
            print(json.dumps(res, ensure_ascii=False))
        elif action == "link":
            data = _read_json_arg(sys.argv[4] if len(sys.argv) > 4 else "-")
            brain.skill_links(int(data.get("memory_id", 0)), data.get("skill_id", ""),
                              data.get("relation", "supports"), bool(data.get("success", True)))
            print(json.dumps({"ok": True}))
        elif action == "status":
            skill_id = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] != "-" else None
            print(json.dumps(brain.skill_status(skill_id), ensure_ascii=False))
        elif action == "executions":
            rows = [dict(r) for r in brain._conn.execute(
                "SELECT * FROM skill_executions ORDER BY id DESC LIMIT 20")]
            print(json.dumps(rows, ensure_ascii=False))
        else:
            print(json.dumps({"error": "skill action desconocida"}))

    elif command == "route":
        query = sys.argv[3]
        risk = sys.argv[4] if len(sys.argv) > 4 else None
        print(json.dumps(brain.route(query, risk), ensure_ascii=False))

    elif command == "reviews":
        rows = brain.list_due_reviews(int(sys.argv[3]) if len(sys.argv) > 3 else 20)
        print(json.dumps(rows, ensure_ascii=False))

    elif command == "checkpoint":
        action = sys.argv[3]
        if action == "create":
            data = _read_json_arg(sys.argv[4] if len(sys.argv) > 4 else "-")
            print(json.dumps(brain.create_checkpoint(data), ensure_ascii=False))
        elif action == "get":
            cp = brain.get_checkpoint(int(sys.argv[4]))
            print(json.dumps(cp, ensure_ascii=False))
        elif action == "list":
            lim = int(sys.argv[4]) if len(sys.argv) > 4 else 10
            print(json.dumps(brain.list_checkpoints(lim), ensure_ascii=False))
        else:
            print(json.dumps({"error": "checkpoint action desconocida"}))

    elif command == "capsule":
        cp_id = int(sys.argv[3])
        cp = brain.get_checkpoint(cp_id)
        if not cp:
            print(json.dumps({"error": "checkpoint no existe"}))
        else:
            print(json.dumps({"id": cp_id, "capsule": brain.build_recovery_capsule(cp),
                              "continuity_score": brain.continuity_score(cp)}, ensure_ascii=False))

    elif command == "consolidate-recent":
        hours = int(sys.argv[3]) if len(sys.argv) > 3 else 24
        print(json.dumps(brain.consolidate_recent(hours), ensure_ascii=False))

    elif command == "continuity":
        cp_id = int(sys.argv[3])
        cp = brain.get_checkpoint(cp_id)
        if not cp:
            print(json.dumps({"error": "checkpoint no existe"}))
        else:
            print(json.dumps({"id": cp_id, "continuity_score": brain.continuity_score(cp)},
                             ensure_ascii=False))

    elif command == "aquest":
        action = sys.argv[3]
        if action == "create":
            data = _read_json_arg(sys.argv[4] if len(sys.argv) > 4 else "-")
            print(json.dumps({"id": brain.quest_create(data.get("id", "quest"), data.get("description", ""), data.get("mode", "balanced"))}))
        elif action == "get":
            print(json.dumps(brain.quest_get(sys.argv[4]), ensure_ascii=False))
        elif action == "list":
            print(json.dumps(brain.quest_list(int(sys.argv[4]) if len(sys.argv) > 4 else 10), ensure_ascii=False))
        elif action == "progress":
            print(json.dumps(brain.quest_progress(sys.argv[4]), ensure_ascii=False))
        elif action == "update":
            data = _read_json_arg(sys.argv[4] if len(sys.argv) > 4 else "-")
            qid = data.pop("id", None)
            if qid:
                brain.quest_update(qid, **data)
                print(json.dumps({"ok": True, "id": qid}))
            else:
                print(json.dumps({"error": "falta id"}))
        else:
            print(json.dumps({"error": "quest action desconocida"}))

    elif command == "atask":
        action = sys.argv[3]
        if action == "create":
            data = _read_json_arg(sys.argv[4] if len(sys.argv) > 4 else "-")
            tid = brain.task_create(data.get("quest_id", ""), data.get("task_id", "T"),
                                    data.get("description", ""), data.get("agent", "atlas"),
                                    data.get("dependencies"), data.get("acceptance", ""))
            print(json.dumps({"id": tid}))
        elif action == "list":
            qid = sys.argv[4] if len(sys.argv) > 4 else None
            st = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] != "-" else None
            print(json.dumps(brain.task_list(qid, st), ensure_ascii=False))
        elif action == "ready":
            print(json.dumps(brain.task_ready(sys.argv[4]), ensure_ascii=False))
        elif action == "update":
            data = _read_json_arg(sys.argv[4] if len(sys.argv) > 4 else "-")
            tid = int(data.pop("id", 0))
            if tid:
                brain.task_update(tid, **data)
                print(json.dumps({"ok": True, "id": tid}))
            else:
                print(json.dumps({"error": "falta id"}))
        elif action == "progress":
            print(json.dumps(brain.quest_progress(sys.argv[4]), ensure_ascii=False))
        else:
            print(json.dumps({"error": "task action desconocida"}))

    elif command == "get":
        obs_id = int(sys.argv[3])
        row = brain.get_observation(obs_id)
        print(json.dumps(dict(row) if row else {"error": "no encontrada"}, ensure_ascii=False))

    elif command == "update":
        data = _read_json_arg(sys.argv[3] if len(sys.argv) > 3 else "-")
        obs_id = int(data.get("id", 0))
        updated = brain.update_observation(
            obs_id, content=data.get("content"), topic_key=data.get("topic_key"))
        print(json.dumps({"id": obs_id, "updated": updated}, ensure_ascii=False))

    elif command == "revisions":
        obs_id = int(sys.argv[3])
        revs = brain.list_revisions(obs_id)
        print(json.dumps(revs, ensure_ascii=False))

    elif command == "compact":
        days = int(sys.argv[3]) if len(sys.argv) > 3 else 30
        print(json.dumps(brain.compact(days), ensure_ascii=False))

    elif command == "search":
        query = sys.argv[3]
        agent = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] != "-" else None
        limit = int(sys.argv[5]) if len(sys.argv) > 5 else 20
        tag = sys.argv[6] if len(sys.argv) > 6 and sys.argv[6] != "-" else None
        print(json.dumps(brain.search(query, agent=agent, limit=limit, tag=tag), ensure_ascii=False))

    elif command == "context":
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 30
        print(json.dumps(brain.recent_context(limit), ensure_ascii=False))

    elif command == "agent":
        agent = sys.argv[3]
        limit = int(sys.argv[4]) if len(sys.argv) > 4 else 50
        print(json.dumps(brain.agent_memory(agent, limit), ensure_ascii=False))

    elif command == "export":
        out = sys.argv[3]
        files = brain.export_jsonl(out)
        print(json.dumps({"exported": files, "count": len(files)}))

    elif command == "import":
        indir = sys.argv[3]
        count = brain.import_jsonl(indir)
        print(json.dumps({"imported": count}))

    elif command == "stats":
        print(json.dumps(brain.stats()))

    elif command == "quest":
        data = _read_json_arg(sys.argv[3] if len(sys.argv) > 3 else "-")
        qid = brain.save_quest(
            data.get("id", brain.next_quest_id()),
            data.get("description", ""),
            data.get("quest_type", "general"),
            data.get("party", []),
            data.get("result", "PASS"),
            data.get("tokens_used", 0))
        print(json.dumps({"quest_id": qid, "status": "saved"}))

    elif command == "quests":
        print(json.dumps(brain.quest_history(), ensure_ascii=False))

    elif command == "edge":
        data = _read_json_arg(sys.argv[3] if len(sys.argv) > 3 else "-")
        eid = brain.add_edge(
            data.get("node_a", ""), data.get("node_b", ""),
            data.get("relation", "related"),
            data.get("agent"), data.get("quest_id"))
        print(json.dumps({"edge_id": eid, "status": "saved"}))

    elif command == "edges":
        node = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] != "-" else None
        print(json.dumps(brain.query_edges(node=node), ensure_ascii=False))

    elif command == "neighbors":
        node = sys.argv[3]
        depth = int(sys.argv[4]) if len(sys.argv) > 4 else 1
        print(json.dumps(brain.neighbors(node, max_depth=depth), ensure_ascii=False))

    elif command == "path":
        start = sys.argv[3]
        end = sys.argv[4]
        max_depth = int(sys.argv[5]) if len(sys.argv) > 5 else 6
        print(json.dumps(brain.path(start, end, max_depth), ensure_ascii=False))

    elif command == "graph-stats":
        print(json.dumps(brain.graph_stats(), ensure_ascii=False))

    elif command == "osma-migrate":
        print(json.dumps(brain.osma_migrate(), ensure_ascii=False))

    elif command == "osma-link":
        data = _read_json_arg(sys.argv[3] if len(sys.argv) > 3 else "-")
        print(json.dumps(brain.osma_link(int(data.get("new_id", 0)), data.get("recalled_ids", []),
                                         signal=data.get("signal", "coactivation"),
                                         quest_id=data.get("quest_id"), agent=data.get("agent")),
                         ensure_ascii=False))

    elif command == "osma-recall":
        query = sys.argv[3]
        agent = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] != "-" else None
        limit = int(sys.argv[5]) if len(sys.argv) > 5 else 5
        tag = sys.argv[6] if len(sys.argv) > 6 and sys.argv[6] != "-" else None
        print(json.dumps(brain.osma_recall(query, agent=agent, limit=limit, tag=tag), ensure_ascii=False))

    elif command == "osma-reinforce":
        data = _read_json_arg(sys.argv[3] if len(sys.argv) > 3 else "-")
        print(json.dumps(brain.osma_reinforce(int(data.get("id", 0)), bool(data.get("success", True))),
                         ensure_ascii=False))

    elif command == "osma-context":
        query = sys.argv[3]
        project = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] != "-" else None
        agent = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] != "-" else None
        max_tokens = int(sys.argv[6]) if len(sys.argv) > 6 else 6000
        print(json.dumps(brain.osma_context(query, project=project, agent=agent, max_tokens=max_tokens),
                         ensure_ascii=False))

    elif command == "osma-contradictions":
        st = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] != "-" else "open"
        print(json.dumps(brain.osma_contradictions(status=st), ensure_ascii=False))

    elif command == "osma-contradiction-resolve":
        data = _read_json_arg(sys.argv[3] if len(sys.argv) > 3 else "-")
        print(json.dumps(brain.osma_contradiction_resolve(int(data.get("id", 0)), int(data.get("winner_id", 0)),
                                                          evidence=data.get("evidence")), ensure_ascii=False))

    elif command == "osma-sleep":
        hours = int(sys.argv[3]) if len(sys.argv) > 3 else 24
        now = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] != "-" else None
        print(json.dumps(brain.osma_sleep(hours=hours, now=now), ensure_ascii=False))

    elif command == "osma-consolidations":
        st = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] != "-" else None
        print(json.dumps(brain.osma_consolidations(status=st), ensure_ascii=False))

    elif command == "osma-consolidation-finalize":
        data = _read_json_arg(sys.argv[3] if len(sys.argv) > 3 else "-")
        print(json.dumps(brain.osma_consolidation_finalize(int(data.get("id", 0)), data.get("summary", "")),
                         ensure_ascii=False))

    elif command == "osma-stats":
        # Summary V4-V7 del cerebro OSMA construido por UN metodo defensivo:
        # _osma_stats() protege CADA grupo (legacy V4 + V5-V7) con try/except.
        # Una tabla legacy faltante/incompleta devuelve defaults vacios en vez
        # de crashear (FIX Tywin: ya NO se llama a osma_stats() sin guarda).
        print(json.dumps(brain._osma_stats(), ensure_ascii=False))

    elif command == "osma-experience-record":
        data = _read_json_arg(sys.argv[3] if len(sys.argv) > 3 else "-")
        print(json.dumps(brain.osma_experience_record(data), ensure_ascii=False))

    elif command == "osma-experience-validate":
        data = _read_json_arg(sys.argv[3] if len(sys.argv) > 3 else "-")
        print(json.dumps(brain.osma_experience_validate(data), ensure_ascii=False))

    elif command == "osma-experience-search":
        query = sys.argv[3]
        project = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] != "-" else None
        agent = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] != "-" else None
        limit = int(sys.argv[6]) if len(sys.argv) > 6 else 5
        print(json.dumps(brain.osma_experience_search(query, project=project, agent=agent,
                                                      limit=limit), ensure_ascii=False))

    elif command == "osma-pattern-detect":
        print(json.dumps(brain.osma_pattern_detect(), ensure_ascii=False))

    elif command == "osma-patterns":
        print(json.dumps(brain.osma_patterns(), ensure_ascii=False))

    elif command == "osma-experience-reuse":
        data = _read_json_arg(sys.argv[3] if len(sys.argv) > 3 else "-")
        print(json.dumps(brain.osma_experience_reuse(data), ensure_ascii=False))

    elif command == "osma-experience-stats":
        print(json.dumps(brain.osma_experience_stats(), ensure_ascii=False))

    elif command == "osma-experience-analyze":
        exp_id = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] != "-" else None
        print(json.dumps(brain.osma_experience_analyze(exp_id), ensure_ascii=False))

    elif command == "osma-cues":
        exp_id = int(sys.argv[3])
        print(json.dumps(brain.osma_cues(exp_id), ensure_ascii=False))

    elif command == "osma-cue-search":
        data = _read_json_arg(sys.argv[3] if len(sys.argv) > 3 else "-")
        print(json.dumps(brain.osma_cue_search(data), ensure_ascii=False))

    elif command == "osma-anchor-add":
        data = _read_json_arg(sys.argv[3] if len(sys.argv) > 3 else "-")
        print(json.dumps(brain.osma_anchor_add(data), ensure_ascii=False))

    elif command == "osma-routes":
        exp_id = int(sys.argv[3])
        print(json.dumps(brain.osma_routes(exp_id), ensure_ascii=False))

    elif command == "osma-episode":
        data = _read_json_arg(sys.argv[3] if len(sys.argv) > 3 else "-")
        exp_id = int(data.get("experience_id", 0)) if isinstance(data, dict) else int(data)
        print(json.dumps(brain.osma_episode(exp_id), ensure_ascii=False))

    else:
        print(json.dumps({"error": f"comando desconocido: {command}"}))


if __name__ == "__main__":
    main()
