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

import config

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
    # Postgres & Co.: pool_pre_ping verhindert tote Verbindungen nach Idle
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
    """Liest einen Spieler. Gibt ein dict oder None zurück, wenn nicht gefunden."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                text("SELECT * FROM players WHERE name = :name;"), {"name": name}
            ).mappings().first()
            return dict(row) if row else None
    except SQLAlchemyError as exc:
        log.error("get_player(%s) fehlgeschlagen: %s", name, exc)
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

    Gewinn  = (betano_odds * EINSATZ) - EINSATZ
    Verlust = -EINSATZ
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

            odds = row["betano_odds"] if row["betano_odds"] else 0.0
            if gewonnen:
                status = "gewonnen"
                pnl = round(odds * config.EINSATZ - config.EINSATZ, 2)
            else:
                status = "verloren"
                pnl = -config.EINSATZ

            now = datetime.now().isoformat(timespec="seconds")
            conn.execute(
                text("""
                    UPDATE predictions
                    SET status = :status, gewinn_verlust = :pnl, eingetragen_am = :now
                    WHERE id = :id;
                """),
                {"status": status, "pnl": pnl, "now": now, "id": pred_id},
            )
        log.info("Ergebnis eingetragen: Tipp %s -> %s (%.2f €)", pred_id, status, pnl)
        return True
    except SQLAlchemyError as exc:
        log.error("update_prediction_result fehlgeschlagen: %s", exc)
        return False


# --------------------------------------------------------------------------- #
# Bankroll
# --------------------------------------------------------------------------- #
def update_bankroll_history() -> None:
    """Berechnet die Bankroll-Historie aus abgeschlossenen Tipps neu. Gibt None zurück."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                text("""
                    SELECT date, SUM(gewinn_verlust) AS daily_pnl
                    FROM predictions
                    WHERE status IN ('gewonnen', 'verloren')
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
