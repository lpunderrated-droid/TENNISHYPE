"""API-Abruf + tägliches Caching.

Zwei Datenquellen:
1. The Odds API  -> Matches des Tages inkl. Betano-Quoten (Markt h2h)
2. API-Tennis    -> Weltranglisten (Elo-Proxy), Form, H2H

Alle Netzwerk-Aufrufe sind mit Timeout, bis zu 3 Retries und try/except
abgesichert. Antworten werden pro Tag in JSON-Dateien gecacht, damit ein
Streamlit-Refresh die APIs nicht erneut belastet.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

import config

log = config.log


# --------------------------------------------------------------------------- #
# Caching
# --------------------------------------------------------------------------- #
def _cache_path(key: str) -> Path:
    """Pfad zur heutigen Cache-Datei für einen logischen Schlüssel."""
    heute = datetime.now().strftime("%Y-%m-%d")
    return config.CACHE_DIR / f"{key}_{heute}.json"


def _read_cache(key: str) -> Optional[object]:
    """Liest die heutige Cache-Datei. Gibt deren Inhalt oder None zurück."""
    pfad = _cache_path(key)
    if not pfad.exists():
        return None
    try:
        with open(pfad, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Cache lesen fehlgeschlagen (%s): %s", key, exc)
        return None


def _write_cache(key: str, data: object) -> None:
    """Schreibt Daten in die heutige Cache-Datei. Gibt None zurück."""
    try:
        with open(_cache_path(key), "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
    except OSError as exc:
        log.warning("Cache schreiben fehlgeschlagen (%s): %s", key, exc)
    return None


# --------------------------------------------------------------------------- #
# HTTP mit Retry
# --------------------------------------------------------------------------- #
def _get(url: str, params: dict) -> Optional[object]:
    """Führt einen GET-Request mit bis zu 3 Versuchen aus.

    Gibt das geparste JSON zurück oder None, wenn alle Versuche scheitern.
    """
    for versuch in range(1, config.API_MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=config.API_TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
            log.warning("GET %s -> Status %s (Versuch %s)", url, resp.status_code, versuch)
        except (requests.RequestException, ValueError) as exc:
            log.warning("GET %s fehlgeschlagen (Versuch %s): %s", url, versuch, exc)
        time.sleep(min(2 ** versuch, 5))  # einfaches Backoff, klar begrenzt
    log.error("GET %s endgültig fehlgeschlagen nach %s Versuchen", url, config.API_MAX_RETRIES)
    return None


# --------------------------------------------------------------------------- #
# Oberflächen-Erkennung
# --------------------------------------------------------------------------- #
def infer_surface(tournament: str | None) -> str:
    """Leitet die Oberfläche aus dem Turniernamen ab. Default: 'Hard'."""
    if not tournament:
        return "Hard"
    name = tournament.lower()
    if any(k in name for k in config.GRASS_KEYWORDS):
        return "Grass"
    if any(k in name for k in config.CLAY_KEYWORDS):
        return "Clay"
    return "Hard"


# --------------------------------------------------------------------------- #
# The Odds API – Matches + Quoten
# --------------------------------------------------------------------------- #
def _pick_bookmaker_odds(event: dict) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """Wählt aus einem Event die Quoten des bevorzugten Buchmachers (h2h).

    Returns (odds_home, odds_away, bookmaker_title). Werte sind None, wenn der
    bevorzugte Buchmacher keine h2h-Quoten liefert.
    """
    home = event.get("home_team")
    away = event.get("away_team")
    bookmakers = {b.get("key"): b for b in event.get("bookmakers", [])}

    gewaehlt = None
    for key in config.PREFERRED_BOOKMAKERS:
        if key in bookmakers:
            gewaehlt = bookmakers[key]
            break
    if gewaehlt is None:
        return None, None, None

    odds_home = odds_away = None
    for markt in gewaehlt.get("markets", []):
        if markt.get("key") != "h2h":
            continue
        for outcome in markt.get("outcomes", []):
            if outcome.get("name") == home:
                odds_home = outcome.get("price")
            elif outcome.get("name") == away:
                odds_away = outcome.get("price")
    return odds_home, odds_away, gewaehlt.get("title")


def fetch_odds() -> list[dict]:
    """Holt heutige Tennis-Matches inkl. Quoten von The Odds API.

    Gibt eine Liste von Match-dicts zurück (evtl. leer bei Fehler/keine Daten).
    Pro Match: player1/player2, odds_p1/odds_p2, tournament, surface,
    match_time, date, bookmaker.
    """
    if not config.ODDS_API_KEY:
        log.error("ODDS_API_KEY fehlt – Quoten können nicht geladen werden.")
        return []

    cache = _read_cache("odds")
    if cache is not None:
        log.info("Quoten aus Cache geladen (%s Matches).", len(cache))
        return cache

    # 1) Aktive Tennis-Sportarten ermitteln (The Odds API hat keinen festen Key 'tennis')
    sports = _get(f"{config.ODDS_API_BASE}/sports", {"apiKey": config.ODDS_API_KEY})
    if not isinstance(sports, list):
        return []
    tennis_keys = [
        s.get("key") for s in sports
        if s.get("group") == "Tennis" and s.get("active") and s.get("key")
    ]
    if not tennis_keys:
        log.info("Aktuell keine aktiven Tennis-Sportarten bei The Odds API.")
        return []

    heute = datetime.now().strftime("%Y-%m-%d")
    matches: list[dict] = []
    for key in tennis_keys:
        events = _get(
            f"{config.ODDS_API_BASE}/sports/{key}/odds",
            {
                "apiKey": config.ODDS_API_KEY,
                "regions": "eu",
                "markets": "h2h",
                "oddsFormat": "decimal",
            },
        )
        if not isinstance(events, list):
            continue
        for event in events:
            odds_home, odds_away, bm = _pick_bookmaker_odds(event)
            if odds_home is None or odds_away is None:
                continue  # ohne Quoten kein Tipp
            commence = event.get("commence_time", "")
            # commence_time ist ISO-8601 (UTC); nur heutige Matches berücksichtigen
            match_time = ""
            match_date = heute
            try:
                dt = datetime.fromisoformat(commence.replace("Z", "+00:00")).astimezone()
                match_time = dt.strftime("%H:%M")
                match_date = dt.strftime("%Y-%m-%d")
            except (ValueError, AttributeError):
                pass
            tournament = event.get("sport_title") or key
            matches.append(
                {
                    "date": match_date,
                    "player1": event.get("home_team"),
                    "player2": event.get("away_team"),
                    "tournament": tournament,
                    "surface": infer_surface(tournament),
                    "match_time": match_time,
                    "odds_p1": odds_home,
                    "odds_p2": odds_away,
                    "bookmaker": bm,
                }
            )

    # Nur heutige Matches behalten
    matches = [m for m in matches if m["date"] == heute]
    _write_cache("odds", matches)
    log.info("Quoten geladen: %s heutige Matches.", len(matches))
    return matches


# --------------------------------------------------------------------------- #
# API-Tennis – Rankings (Elo-Proxy)
# --------------------------------------------------------------------------- #
def _normalize_name(name: str | None) -> str:
    """Vereinheitlicht Spielernamen für den Abgleich zwischen den APIs."""
    return (name or "").strip().lower()


def fetch_rankings() -> dict[str, int]:
    """Lädt ATP- und WTA-Weltranglisten von API-Tennis.

    Returns ein dict {normalisierter_name: rang}. Bei Fehler leeres dict.
    """
    if not config.API_TENNIS_KEY:
        log.error("API_TENNIS_KEY fehlt – Rankings können nicht geladen werden.")
        return {}

    cache = _read_cache("rankings")
    if cache is not None:
        log.info("Rankings aus Cache geladen (%s Spieler).", len(cache))
        return cache

    rankings: dict[str, int] = {}
    for event_type in ("ATP", "WTA"):
        data = _get(
            config.API_TENNIS_BASE,
            {
                "method": "get_standings",
                "event_type": event_type,
                "APIkey": config.API_TENNIS_KEY,
            },
        )
        if not isinstance(data, dict):
            continue
        result = data.get("result", [])
        if not isinstance(result, list):
            continue
        for row in result:
            name = _normalize_name(row.get("player"))
            try:
                rank = int(row.get("place"))
            except (TypeError, ValueError):
                continue
            if name:
                rankings[name] = rank

    _write_cache("rankings", rankings)
    log.info("Rankings geladen: %s Spieler.", len(rankings))
    return rankings


# --------------------------------------------------------------------------- #
# API-Tennis – H2H + Form (best effort)
# --------------------------------------------------------------------------- #
def fetch_h2h(player1: str, player2: str) -> Optional[list[dict]]:
    """Holt die H2H-Historie zweier Spieler (best effort).

    Returns eine Liste vergangener Direktvergleiche (dicts mit 'winner') oder
    None, wenn keine Daten verfügbar sind / der Abruf scheitert.
    """
    if not config.API_TENNIS_KEY:
        return None
    cache_key = f"h2h_{_normalize_name(player1)}_{_normalize_name(player2)}".replace(" ", "_")
    cache = _read_cache(cache_key)
    if cache is not None:
        return cache

    data = _get(
        config.API_TENNIS_BASE,
        {
            "method": "get_H2H",
            "first_player_key": player1,
            "second_player_key": player2,
            "APIkey": config.API_TENNIS_KEY,
        },
    )
    if not isinstance(data, dict):
        return None
    result = data.get("result", {})
    h2h = result.get("H2H", []) if isinstance(result, dict) else []
    if not isinstance(h2h, list):
        return None
    _write_cache(cache_key, h2h)
    return h2h
