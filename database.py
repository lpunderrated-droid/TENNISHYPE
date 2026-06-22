"""Datenbank-Handler auf Basis von SQLAlchemy.

Unterstützt zwei Backends mit derselben Code-Basis:
  * Lokal:  SQLite-Datei (Standard) – Daten bleiben auf deinem PC erhalten.
  * Cloud:  dauerhaftes Postgres (z. B. Supabase) über die Umgebungs-/Secret-
            Variable DATABASE_URL – Daten gehen bei Neustarts NICHT verloren.

Alle SQL-Anweisungen verwenden portables `INSERT ... ON CONFLICT`, das sowohl
SQLite (>= 3.24) als auch Postgres beherrschen. Verbindungen werden über den
Context-Manager der Engine geöffnet und garantiert wieder geschlossen.
"""

from contextlib import contextmanager
from datetime import datetime
from typing import Iterator, Optional

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

import config
import combi
from player_utils import normalize_name, names_match

log = config.log

# Engine genau einmal bauen (Connection-Pooling übernimmt SQLAlchemy)
_engine: Optional[Engine] = None


def _build_engine() -> Engine:
    """Erstellt die SQLAlchemy-Engine passend zum konfigurierten Backend."""
    url = config.get_database_url()
    if url.startswith("sqlite"):
        engine = create_engine(url, future=True)

        # Foreign Keys bei SQLite pro Verbindung aktivieren
        @event.listens_for(engine, "connect")
        def _fk_pragma(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON;")
            cur.close()

        return engine
    # Supabase Transaction-Pooler (pgbouncer, Port 6543): kein eigenes
    # Connection-Pooling von SQLAlchemy, sonst drohen "prepared statement"-Fehler.
    if "pooler.supabase.com:6543" in url:
        return create_engine(url, future=True, poolclass=NullPool)
    # Sonstiges Postgres: pool_pre_ping verhindert tote Verbindungen nach Idle
    return create_engine(url, future=True, pool_pre_ping=True)


def get_engine() -> Engine:
    """Gibt die (einmalig erzeugte) Engine zurück."""
    global _engine
    if _engine is None:
        _engine = _build_engine()
        log.info("DB-Engine erstellt: %s", _engine.dialect.name)
    return _engine


@contextmanager
def get_connection() -> Iterator:
    """Liefert eine Transaktions-Verbindung und schließt sie garantiert.

    Commit bei Erfolg, Rollback bei Fehler (über engine.begin()).
    """
    engine = get_engine()
    try:
        with engine.begin() as conn:
            yield conn
    except SQLAlchemyError as exc:
        log.error("Datenbankfehler: %s", exc)
        raise


def _is_sqlite() -> bool:
    """True, wenn das aktive Backend SQLite ist."""
    return get_engine().dialect.name == "sqlite"


def init_db() -> None:
    """Erstellt alle Tabellen, falls sie noch nicht existieren. Gibt None zurück."""
    # Auto-increment-Primärschlüssel ist je Dialekt unterschiedlich
    pk = "INTEGER PRIMARY KEY AUTOINCREMENT" if _is_sqlite() else "BIGSERIAL PRIMARY KEY"
    try:
        with get_connection() as conn:
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS players (
                    id {pk},
                    name TEXT UNIQUE NOT NULL,
                    clay_elo REAL DEFAULT 1500,
                    hard_elo REAL DEFAULT 1500,
                    grass_elo REAL DEFAULT 1500,
                    last_updated TEXT
                );
            """))
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS matches (
                    id {pk},
                    date TEXT NOT NULL,
                    player1 TEXT NOT NULL,
                    player2 TEXT NOT NULL,
                    surface TEXT,
                    tournament TEXT,
                    match_time TEXT,
                    winner TEXT,
                    odds_p1 REAL,
                    odds_p2 REAL,
                    UNIQUE(date, player1, player2)
                );
            """))
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS predictions (
                    id {pk},
                    date TEXT NOT NULL,
                    player1 TEXT NOT NULL,
                    player2 TEXT NOT NULL,
                    tournament TEXT,
                    surface TEXT,
                    match_time TEXT,
                    tip TEXT NOT NULL,
                    betano_odds REAL,
                    prob_calculated REAL,
                    prob_implied REAL,
                    confidence REAL,
                    einsatz REAL DEFAULT 10.0,
                    status TEXT DEFAULT 'offen',
                    gewinn_verlust REAL DEFAULT 0.0,
                    eingetragen_am TEXT,
                    UNIQUE(date, player1, player2)
                );
            """))
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS bankroll_history (
                    id {pk},
                    date TEXT UNIQUE NOT NULL,
                    balance REAL,
                    daily_pnl REAL
                );
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS api_usage (
                    period TEXT NOT NULL,
                    api TEXT NOT NULL,
                    count INTEGER DEFAULT 0,
                    PRIMARY KEY (period, api)
                );
            """))
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS combis (
                    id {pk},
                    date TEXT NOT NULL,
                    einsatz REAL DEFAULT 10.0,
                    combined_odds REAL NOT NULL,
                    status TEXT DEFAULT 'offen',
                    gewinn_verlust REAL DEFAULT 0.0,
                    eingetragen_am TEXT,
                    created_at TEXT
                );
            """))
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS combi_legs (
                    combi_id INTEGER NOT NULL,
                    prediction_id INTEGER NOT NULL,
                    leg_order INTEGER DEFAULT 0,
                    PRIMARY KEY (combi_id, prediction_id)
                );
            """))
        log.info("Datenbank initialisiert (%s).", get_engine().dialect.name)
    except SQLAlchemyError as exc:
        log.error("init_db fehlgeschlagen: %s", exc)
    return None


# --------------------------------------------------------------------------- #
# Spieler
# --------------------------------------------------------------------------- #
def upsert_player(name: str, clay_elo: float, hard_elo: float, grass_elo: float) -> None:
    """Fügt einen Spieler ein oder aktualisiert dessen Elo-Werte. Gibt None zurück."""
    if not name:
        return None
    now = datetime.now().isoformat(timespec="seconds")
    try:
        with get_connection() as conn:
            conn.execute(
                text("""
                    INSERT INTO players (name, clay_elo, hard_elo, grass_elo, last_updated)
                    VALUES (:name, :clay, :hard, :grass, :now)
                    ON CONFLICT(name) DO UPDATE SET
                        clay_elo = excluded.clay_elo,
                        hard_elo = excluded.hard_elo,
                        grass_elo = excluded.grass_elo,
                        last_updated = excluded.last_updated;
                """),
                {"name": name, "clay": clay_elo, "hard": hard_elo, "grass": grass_elo, "now": now},
            )
    except SQLAlchemyError as exc:
        log.error("upsert_player(%s) fehlgeschlagen: %s", name, exc)
    return None


def get_player(name: str) -> Optional[dict]:
    """Liest einen Spieler per exaktem Namen. Gibt ein dict oder None zurück."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                text("SELECT * FROM players WHERE name = :name;"), {"name": name}
            ).mappings().first()
            return dict(row) if row else None
    except SQLAlchemyError as exc:
        log.error("get_player(%s) fehlgeschlagen: %s", name, exc)
        return None


def find_player(name: str) -> Optional[dict]:
    """Findet einen Spieler – zuerst exakt, dann per normalisiertem Namen.

    Nötig, weil Odds-API ('Jović') und gespeicherte DB-Einträge ('Jovic')
    unterschiedlich geschrieben sein können.
    """
    if not name:
        return None
    exact = get_player(name)
    if exact:
        return exact
    try:
        target = normalize_name(name)
        with get_connection() as conn:
            rows = conn.execute(text("SELECT * FROM players;")).mappings().all()
            for row in rows:
                if normalize_name(row["name"]) == target:
                    return dict(row)
                if names_match(row["name"], name):
                    return dict(row)
        return None
    except SQLAlchemyError as exc:
        log.error("find_player(%s) fehlgeschlagen: %s", name, exc)
        return None


# --------------------------------------------------------------------------- #
# Matches
# --------------------------------------------------------------------------- #
def insert_match(match: dict) -> None:
    """Speichert ein Match (idempotent via UNIQUE-Constraint). Gibt None zurück."""
    try:
        with get_connection() as conn:
            conn.execute(
                text("""
                    INSERT INTO matches
                        (date, player1, player2, surface, tournament, match_time, odds_p1, odds_p2)
                    VALUES (:date, :p1, :p2, :surface, :tournament, :mtime, :o1, :o2)
                    ON CONFLICT(date, player1, player2) DO NOTHING;
                """),
                {
                    "date": match.get("date"),
                    "p1": match.get("player1"),
                    "p2": match.get("player2"),
                    "surface": match.get("surface"),
                    "tournament": match.get("tournament"),
                    "mtime": match.get("match_time"),
                    "o1": match.get("odds_p1"),
                    "o2": match.get("odds_p2"),
                },
            )
    except SQLAlchemyError as exc:
        log.error("insert_match fehlgeschlagen: %s", exc)
    return None


# --------------------------------------------------------------------------- #
# Predictions / Tipps
# --------------------------------------------------------------------------- #
def insert_prediction(pred: dict) -> bool:
    """Speichert einen Tipp. Verhindert Duplikate pro (date, player1, player2).

    Returns True bei erfolgreichem Insert, sonst False (z. B. Duplikat/Fehler).
    """
    try:
        with get_connection() as conn:
            result = conn.execute(
                text("""
                    INSERT INTO predictions
                        (date, player1, player2, tournament, surface, match_time, tip,
                         betano_odds, prob_calculated, prob_implied, confidence,
                         einsatz, status, gewinn_verlust)
                    VALUES (:date, :p1, :p2, :tournament, :surface, :mtime, :tip,
                            :odds, :pcalc, :pimpl, :conf, :einsatz, 'offen', 0.0)
                    ON CONFLICT(date, player1, player2) DO NOTHING;
                """),
                {
                    "date": pred.get("date"),
                    "p1": pred.get("player1"),
                    "p2": pred.get("player2"),
                    "tournament": pred.get("tournament"),
                    "surface": pred.get("surface"),
                    "mtime": pred.get("match_time"),
                    "tip": pred.get("tip"),
                    "odds": pred.get("betano_odds"),
                    "pcalc": pred.get("prob_calculated"),
                    "pimpl": pred.get("prob_implied"),
                    "conf": pred.get("confidence"),
                    "einsatz": config.EINSATZ,
                },
            )
            return result.rowcount > 0
    except SQLAlchemyError as exc:
        log.error("insert_prediction fehlgeschlagen: %s", exc)
        return False


def get_predictions_by_date(date_str: str) -> list[dict]:
    """Gibt alle Tipps eines bestimmten Tages zurück (Liste von dicts, evtl. leer)."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                text("SELECT * FROM predictions WHERE date = :date ORDER BY confidence DESC;"),
                {"date": date_str},
            ).mappings().all()
            return [dict(r) for r in rows]
    except SQLAlchemyError as exc:
        log.error("get_predictions_by_date fehlgeschlagen: %s", exc)
        return []


def get_open_predictions() -> list[dict]:
    """Gibt alle offenen Tipps zurück (Liste von dicts, evtl. leer)."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                text("SELECT * FROM predictions WHERE status = 'offen' "
                     "ORDER BY date DESC, confidence DESC;")
            ).mappings().all()
            return [dict(r) for r in rows]
    except SQLAlchemyError as exc:
        log.error("get_open_predictions fehlgeschlagen: %s", exc)
        return []


def get_all_predictions() -> list[dict]:
    """Gibt alle Tipps zurück (neueste zuerst). Liste von dicts, evtl. leer."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                text("SELECT * FROM predictions ORDER BY date DESC, id DESC;")
            ).mappings().all()
            return [dict(r) for r in rows]
    except SQLAlchemyError as exc:
        log.error("get_all_predictions fehlgeschlagen: %s", exc)
        return []


def update_prediction_result(pred_id: int, gewonnen: bool) -> bool:
    """Trägt ein Ergebnis ein und berechnet Gewinn/Verlust.

    Legs in einer Kombiwette erhalten keinen Einzel-PnL (nur Status für die Kombi).
    Returns True bei Erfolg, sonst False.
    """
    try:
        with get_connection() as conn:
            row = conn.execute(
                text("SELECT betano_odds FROM predictions WHERE id = :id;"), {"id": pred_id}
            ).mappings().first()
            if row is None:
                log.warning("update_prediction_result: Tipp %s nicht gefunden", pred_id)
                return False

            in_combi = conn.execute(
                text("SELECT 1 FROM combi_legs WHERE prediction_id = :id LIMIT 1;"),
                {"id": pred_id},
            ).first() is not None

            odds = row["betano_odds"] if row["betano_odds"] else 0.0
            if gewonnen:
                status = "gewonnen"
                pnl = 0.0 if in_combi else round(odds * config.EINSATZ - config.EINSATZ, 2)
            else:
                status = "verloren"
                pnl = 0.0 if in_combi else -config.EINSATZ

            now = datetime.now().isoformat(timespec="seconds")
            conn.execute(
                text("""
                    UPDATE predictions
                    SET status = :status, gewinn_verlust = :pnl, eingetragen_am = :now
                    WHERE id = :id;
                """),
                {"status": status, "pnl": pnl, "now": now, "id": pred_id},
            )

        refresh_combis_for_prediction(pred_id)
        log.info("Ergebnis eingetragen: Tipp %s -> %s (%.2f €)", pred_id, status, pnl)
        return True
    except SQLAlchemyError as exc:
        log.error("update_prediction_result fehlgeschlagen: %s", exc)
        return False


def reset_prediction_result(pred_id: int) -> bool:
    """Setzt einen Tipp auf offen zurück (z. B. nach Fehleintrag)."""
    try:
        with get_connection() as conn:
            conn.execute(
                text("""
                    UPDATE predictions
                    SET status = 'offen', gewinn_verlust = 0.0, eingetragen_am = NULL
                    WHERE id = :id;
                """),
                {"id": pred_id},
            )
        refresh_combis_for_prediction(pred_id)
        return True
    except SQLAlchemyError as exc:
        log.error("reset_prediction_result fehlgeschlagen: %s", exc)
        return False


# --------------------------------------------------------------------------- #
# Bankroll
# --------------------------------------------------------------------------- #
def update_bankroll_history() -> None:
    """Berechnet die Bankroll-Historie aus Einzel- und Kombi-Wetten neu."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                text("""
                    SELECT date, SUM(pnl) AS daily_pnl FROM (
                        SELECT p.date, p.gewinn_verlust AS pnl
                        FROM predictions p
                        WHERE p.status IN ('gewonnen', 'verloren')
                          AND NOT EXISTS (
                              SELECT 1 FROM combi_legs cl WHERE cl.prediction_id = p.id
                          )
                        UNION ALL
                        SELECT c.date, c.gewinn_verlust AS pnl
                        FROM combis c
                        WHERE c.status IN ('gewonnen', 'verloren')
                    ) combined
                    GROUP BY date
                    ORDER BY date ASC;
                """)
            ).mappings().all()

            balance = 0.0
            for r in rows:
                daily = r["daily_pnl"] if r["daily_pnl"] is not None else 0.0
                balance += daily
                conn.execute(
                    text("""
                        INSERT INTO bankroll_history (date, balance, daily_pnl)
                        VALUES (:date, :balance, :daily)
                        ON CONFLICT(date) DO UPDATE SET
                            balance = excluded.balance,
                            daily_pnl = excluded.daily_pnl;
                    """),
                    {"date": r["date"], "balance": round(balance, 2), "daily": round(daily, 2)},
                )
    except SQLAlchemyError as exc:
        log.error("update_bankroll_history fehlgeschlagen: %s", exc)
    return None


# --------------------------------------------------------------------------- #
# Kombiwetten
# --------------------------------------------------------------------------- #
def get_prediction_ids_in_combis(date_str: str | None = None) -> set[int]:
    """IDs aller Tipps, die bereits Teil einer Kombi sind (optional nur für ein Datum)."""
    try:
        with get_connection() as conn:
            if date_str:
                rows = conn.execute(
                    text("""
                        SELECT cl.prediction_id
                        FROM combi_legs cl
                        JOIN combis c ON c.id = cl.combi_id
                        WHERE c.date = :date;
                    """),
                    {"date": date_str},
                ).mappings().all()
            else:
                rows = conn.execute(text("SELECT prediction_id FROM combi_legs;")).mappings().all()
            return {int(r["prediction_id"]) for r in rows}
    except SQLAlchemyError as exc:
        log.error("get_prediction_ids_in_combis fehlgeschlagen: %s", exc)
        return set()


def _fetch_combi_legs(conn, combi_id: int) -> list[dict]:
    """Lädt Legs einer Kombi mit Prediction-Details."""
    rows = conn.execute(
        text("""
            SELECT p.*
            FROM combi_legs cl
            JOIN predictions p ON p.id = cl.prediction_id
            WHERE cl.combi_id = :cid
            ORDER BY cl.leg_order ASC, cl.prediction_id ASC;
        """),
        {"cid": combi_id},
    ).mappings().all()
    return [dict(r) for r in rows]


def refresh_combis_for_prediction(pred_id: int) -> None:
    """Aktualisiert alle Kombis, die diesen Tipp enthalten."""
    try:
        with get_connection() as conn:
            combi_ids = conn.execute(
                text("SELECT combi_id FROM combi_legs WHERE prediction_id = :pid;"),
                {"pid": pred_id},
            ).mappings().all()
            for row in combi_ids:
                _refresh_combi(conn, int(row["combi_id"]))
    except SQLAlchemyError as exc:
        log.error("refresh_combis_for_prediction fehlgeschlagen: %s", exc)


def _refresh_combi(conn, combi_id: int) -> None:
    """Berechnet Status/PnL einer Kombi neu aus ihren Legs."""
    combi_row = conn.execute(
        text("SELECT * FROM combis WHERE id = :id;"), {"id": combi_id}
    ).mappings().first()
    if not combi_row:
        return

    legs = _fetch_combi_legs(conn, combi_id)
    status, pnl = combi.evaluate_combi(legs, float(combi_row["combined_odds"]))
    eingetragen = datetime.now().isoformat(timespec="seconds") if status != "offen" else None
    conn.execute(
        text("""
            UPDATE combis
            SET status = :status, gewinn_verlust = :pnl, eingetragen_am = :ts
            WHERE id = :id;
        """),
        {"status": status, "pnl": pnl, "ts": eingetragen, "id": combi_id},
    )


def create_combi(prediction_ids: list[int], date_str: str) -> tuple[bool, str]:
    """Legt eine Kombiwette an. Returns (ok, message)."""
    ids = list(dict.fromkeys(prediction_ids))
    if len(ids) < config.COMBI_MIN_LEGS:
        return False, f"Mindestens {config.COMBI_MIN_LEGS} Legs nötig."
    if len(ids) > config.COMBI_MAX_LEGS:
        return False, f"Maximal {config.COMBI_MAX_LEGS} Legs pro Kombi."

    try:
        with get_connection() as conn:
            placeholders = ", ".join(f":id{i}" for i in range(len(ids)))
            params = {f"id{i}": pid for i, pid in enumerate(ids)}
            preds = conn.execute(
                text(f"SELECT * FROM predictions WHERE id IN ({placeholders});"),
                params,
            ).mappings().all()
            if len(preds) != len(ids):
                return False, "Ein oder mehrere Tipps wurden nicht gefunden."

            pred_map = {int(p["id"]): dict(p) for p in preds}
            for pid in ids:
                p = pred_map[pid]
                if p.get("date") != date_str:
                    return False, "Alle Legs müssen vom selben Tag sein."
                taken = conn.execute(
                    text("SELECT 1 FROM combi_legs WHERE prediction_id = :pid LIMIT 1;"),
                    {"pid": pid},
                ).first()
                if taken:
                    return False, f"{p.get('tip')} ist bereits in einer Kombi."

            legs = [pred_map[pid] for pid in ids]
            combined = combi.combined_odds(legs)
            if combined <= 1.0:
                return False, "Kombi-Quote konnte nicht berechnet werden (fehlende Quoten)."

            now = datetime.now().isoformat(timespec="seconds")
            if _is_sqlite():
                result = conn.execute(
                    text("""
                        INSERT INTO combis (date, einsatz, combined_odds, status, gewinn_verlust, created_at)
                        VALUES (:date, :einsatz, :odds, 'offen', 0.0, :now);
                    """),
                    {"date": date_str, "einsatz": config.EINSATZ, "odds": combined, "now": now},
                )
                combi_id = result.lastrowid
            else:
                row = conn.execute(
                    text("""
                        INSERT INTO combis (date, einsatz, combined_odds, status, gewinn_verlust, created_at)
                        VALUES (:date, :einsatz, :odds, 'offen', 0.0, :now)
                        RETURNING id;
                    """),
                    {"date": date_str, "einsatz": config.EINSATZ, "odds": combined, "now": now},
                ).mappings().first()
                combi_id = int(row["id"])

            for order, pid in enumerate(ids):
                conn.execute(
                    text("""
                        INSERT INTO combi_legs (combi_id, prediction_id, leg_order)
                        VALUES (:cid, :pid, :ord);
                    """),
                    {"cid": combi_id, "pid": pid, "ord": order},
                )

            _refresh_combi(conn, combi_id)
        log.info("Kombi #%s angelegt (%s Legs, Quote %.2f).", combi_id, len(ids), combined)
        return True, f"Kombi gespeichert ({len(ids)} Legs · Quote {combined:.2f})."
    except SQLAlchemyError as exc:
        log.error("create_combi fehlgeschlagen: %s", exc)
        return False, "Speichern fehlgeschlagen – siehe Log."


def get_combis_by_date(date_str: str) -> list[dict]:
    """Alle Kombis eines Tages inkl. Legs."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                text("SELECT * FROM combis WHERE date = :date ORDER BY id DESC;"),
                {"date": date_str},
            ).mappings().all()
            result = []
            for row in rows:
                item = dict(row)
                item["legs"] = _fetch_combi_legs(conn, int(row["id"]))
                result.append(item)
            return result
    except SQLAlchemyError as exc:
        log.error("get_combis_by_date fehlgeschlagen: %s", exc)
        return []


def get_all_combis() -> list[dict]:
    """Alle Kombis (neueste zuerst) inkl. Legs."""
    try:
        with get_connection() as conn:
            rows = conn.execute(text("SELECT * FROM combis ORDER BY date DESC, id DESC;")).mappings().all()
            result = []
            for row in rows:
                item = dict(row)
                item["legs"] = _fetch_combi_legs(conn, int(row["id"]))
                result.append(item)
            return result
    except SQLAlchemyError as exc:
        log.error("get_all_combis fehlgeschlagen: %s", exc)
        return []


def get_single_predictions_for_tracking() -> list[dict]:
    """Tipps, die nicht Teil einer Kombi sind (für Einzel-Tracking)."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                text("""
                    SELECT p.*
                    FROM predictions p
                    WHERE NOT EXISTS (
                        SELECT 1 FROM combi_legs cl WHERE cl.prediction_id = p.id
                    )
                    ORDER BY p.date DESC, p.id DESC;
                """)
            ).mappings().all()
            return [dict(r) for r in rows]
    except SQLAlchemyError as exc:
        log.error("get_single_predictions_for_tracking fehlgeschlagen: %s", exc)
        return []


# --------------------------------------------------------------------------- #
# API-Nutzung (eigener Monatszähler, v. a. für API-Tennis)
# --------------------------------------------------------------------------- #
def increment_api_usage(api: str, period: str, n: int = 1) -> None:
    """Erhöht den Aufruf-Zähler für eine API im angegebenen Monat. Gibt None zurück."""
    if not api or not period:
        return None
    try:
        with get_connection() as conn:
            conn.execute(
                text("""
                    INSERT INTO api_usage (period, api, count)
                    VALUES (:period, :api, :n)
                    ON CONFLICT(period, api) DO UPDATE SET
                        count = api_usage.count + :n;
                """),
                {"period": period, "api": api, "n": n},
            )
    except SQLAlchemyError as exc:
        log.error("increment_api_usage(%s) fehlgeschlagen: %s", api, exc)
    return None


def get_api_usage(api: str, period: str) -> int:
    """Liest den Aufruf-Zähler einer API für einen Monat. Gibt 0 bei Fehler/leer."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                text("SELECT count FROM api_usage WHERE period = :period AND api = :api;"),
                {"period": period, "api": api},
            ).mappings().first()
            return int(row["count"]) if row else 0
    except SQLAlchemyError as exc:
        log.error("get_api_usage(%s) fehlgeschlagen: %s", api, exc)
        return 0
