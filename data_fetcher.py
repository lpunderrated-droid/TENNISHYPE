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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

import config
import database
from player_utils import matches_player_name, normalize_name

# Zuletzt von The Odds API gemeldetes Kontingent (aus Response-Headern).
# Wird bei jedem echten Odds-Aufruf aktualisiert.
_odds_quota: dict[str, Optional[int]] = {"remaining": None, "used": None}

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
                _track_usage(url, resp)
                return resp.json()
            log.warning("GET %s -> Status %s (Versuch %s)", url, resp.status_code, versuch)
        except (requests.RequestException, ValueError) as exc:
            log.warning("GET %s fehlgeschlagen (Versuch %s): %s", url, versuch, exc)
        time.sleep(min(2 ** versuch, 5))  # einfaches Backoff, klar begrenzt
    log.error("GET %s endgültig fehlgeschlagen nach %s Versuchen", url, config.API_MAX_RETRIES)
    return None


def _track_usage(url: str, resp: requests.Response) -> None:
    """Erfasst das API-Kontingent nach einem erfolgreichen Aufruf. Gibt None zurück.

    - The Odds API meldet das Restkontingent in den Headern -> direkt übernehmen.
    - API-Tennis liefert keine Quota -> eigenen Monatszähler in der DB erhöhen.
    """
    try:
        if url.startswith(config.ODDS_API_BASE):
            rem = resp.headers.get("x-requests-remaining")
            used = resp.headers.get("x-requests-used")
            if rem is not None:
                _odds_quota["remaining"] = int(float(rem))
            if used is not None:
                _odds_quota["used"] = int(float(used))
        elif url.startswith(config.API_TENNIS_BASE):
            period = datetime.now().strftime("%Y-%m")
            database.increment_api_usage("api_tennis", period, 1)
    except (ValueError, TypeError) as exc:
        log.warning("Quota-Tracking fehlgeschlagen: %s", exc)
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
def get_api_quota_status() -> dict:
    """Liefert den Kontingent-Status beider APIs für die Anzeige in der UI.

    Returns ein dict:
      {
        "odds":   {"remaining": int|None, "used": int|None, "total": int|None},
        "tennis": {"used": int, "limit": int|None, "remaining": int|None},
      }
    Für The Odds API wird – falls noch nichts erfasst wurde – ein kostenloser
    /sports-Aufruf genutzt (dieser zählt nicht gegen das Kontingent).
    """
    # The Odds API
    if _odds_quota["remaining"] is None and config.ODDS_API_KEY:
        _get(f"{config.ODDS_API_BASE}/sports", {"apiKey": config.ODDS_API_KEY})
    remaining = _odds_quota["remaining"]
    used = _odds_quota["used"]
    total = (remaining + used) if (remaining is not None and used is not None) else None

    # API-Tennis (eigener Zähler)
    period = datetime.now().strftime("%Y-%m")
    tennis_used = database.get_api_usage("api_tennis", period)
    limit = config.API_TENNIS_MONTHLY_LIMIT or None
    tennis_remaining = max(0, limit - tennis_used) if limit else None

    return {
        "odds": {"remaining": remaining, "used": used, "total": total},
        "tennis": {"used": tennis_used, "limit": limit, "remaining": tennis_remaining},
    }


def active_tennis_keys() -> list[str]:
    """Ermittelt die aktiven Tennis-Sport-Keys von The Odds API.

    The Odds API hat keinen festen Key 'tennis' – stattdessen werden alle
    aktiven Turniere der Gruppe 'Tennis' zurückgegeben. Liste evtl. leer.
    """
    sports = _get(f"{config.ODDS_API_BASE}/sports", {"apiKey": config.ODDS_API_KEY})
    if not isinstance(sports, list):
        return []
    return [
        s.get("key") for s in sports
        if s.get("group") == "Tennis" and s.get("active") and s.get("key")
    ]


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

    tennis_keys = active_tennis_keys()
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
            name = normalize_name(row.get("player"))
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
    cache_key = f"h2h_{normalize_name(player1)}_{normalize_name(player2)}".replace(" ", "_")
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


# --------------------------------------------------------------------------- #
# The Odds API – Ergebnisse (für automatische Abrechnung)
# --------------------------------------------------------------------------- #
def _winner_from_scores(event: dict) -> Optional[str]:
    """Ermittelt den Gewinner eines abgeschlossenen Events aus dem scores-Feld.

    Gibt den Spielernamen mit dem höheren Score zurück oder None, wenn die
    Daten unvollständig/uneindeutig sind (z. B. Gleichstand, Aufgabe ohne Score).
    """
    scores = event.get("scores")
    if not isinstance(scores, list) or len(scores) < 2:
        return None
    parsed: list[tuple[str, float]] = []
    for s in scores:
        try:
            parsed.append((s.get("name"), float(s.get("score"))))
        except (TypeError, ValueError):
            return None
    parsed.sort(key=lambda x: x[1], reverse=True)
    if parsed[0][1] == parsed[1][1]:
        return None  # uneindeutig
    return parsed[0][0]


def fetch_scores() -> list[dict]:
    """Holt abgeschlossene Tennis-Ergebnisse der letzten Tage von The Odds API.

    Returns eine Liste {player1, player2, winner} (evtl. leer). Nutzt einen
    kurzlebigen, stündlichen Cache, damit Ergebnisse zeitnah aktualisiert
    werden, ohne die API bei jedem Refresh zu belasten.
    """
    if not config.ODDS_API_KEY:
        return []

    cache_key = "scores_" + datetime.now().strftime("%H")  # stündlich frisch
    cache = _read_cache(cache_key)
    if cache is not None:
        return cache

    results: list[dict] = []
    for key in active_tennis_keys():
        events = _get(
            f"{config.ODDS_API_BASE}/sports/{key}/scores",
            {"apiKey": config.ODDS_API_KEY, "daysFrom": 3},
        )
        if not isinstance(events, list):
            continue
        for event in events:
            if not event.get("completed"):
                continue
            winner = _winner_from_scores(event)
            if winner:
                results.append(
                    {
                        "player1": event.get("home_team"),
                        "player2": event.get("away_team"),
                        "winner": winner,
                    }
                )

    _write_cache(cache_key, results)
    log.info("Ergebnisse geladen: %s abgeschlossene Matches.", len(results))
    return results


# --------------------------------------------------------------------------- #
# API-Tennis – Match-Historie (Form + Oberfläche)
# --------------------------------------------------------------------------- #
def _normalize_surface(raw: str | None, tournament: str | None) -> str:
    """Mappt API-Oberflächenbezeichnung auf Clay/Hard/Grass."""
    if raw:
        s = raw.strip().lower()
        if "clay" in s:
            return "Clay"
        if "grass" in s:
            return "Grass"
        if "hard" in s:
            return "Hard"
    return infer_surface(tournament)


def fetch_finished_fixtures() -> list[dict]:
    """Lädt abgeschlossene Einzel-Matches der letzten N Tage (täglich gecacht).

    Returns normalisierte dicts mit date, first_player, second_player, winner,
    surface, tournament – sortiert nach Datum absteigend (neueste zuerst).
    """
    if not config.API_TENNIS_KEY:
        return []

    cache = _read_cache("fixtures_finished")
    if cache is not None:
        log.info("Match-Historie aus Cache (%s Fixtures).", len(cache))
        return cache

    start = (datetime.now() - timedelta(days=config.FIXTURES_HISTORY_DAYS)).strftime("%Y-%m-%d")
    end = datetime.now().strftime("%Y-%m-%d")
    rohe: list[dict] = []

    for event_type_key in ("265", "266"):  # ATP / WTA Singles
        data = _get(
            config.API_TENNIS_BASE,
            {
                "method": "get_fixtures",
                "APIkey": config.API_TENNIS_KEY,
                "date_start": start,
                "date_stop": end,
                "event_type_key": event_type_key,
            },
        )
        if not isinstance(data, dict):
            continue
        result = data.get("result", [])
        if not isinstance(result, list):
            continue
        for ev in result:
            if ev.get("event_status") != "Finished":
                continue
            typ = str(ev.get("event_type_type", "")).lower()
            if "single" not in typ:
                continue
            rohe.append(
                {
                    "date": ev.get("event_date"),
                    "first_player": ev.get("event_first_player"),
                    "second_player": ev.get("event_second_player"),
                    "winner": ev.get("event_winner"),
                    "surface": _normalize_surface(ev.get("tournament_surface"), ev.get("tournament_name")),
                    "tournament": ev.get("tournament_name"),
                }
            )

    rohe.sort(key=lambda x: str(x.get("date") or ""), reverse=True)
    _write_cache("fixtures_finished", rohe)
    log.info("Match-Historie geladen: %s abgeschlossene Fixtures.", len(rohe))
    return rohe


def build_match_histories(fixtures: list[dict], player_names: set[str]) -> dict[str, list[dict]]:
    """Baut pro Spieler die Historie {date, won, surface} für Form/Oberfläche.

    player_names: volle Namen aus der Odds-API (z. B. 'Francisco Comesana').
    Returns dict {spielername: [matches...]} – nur Spieler mit mindestens 1 Match.
    """
    histories: dict[str, list[dict]] = {name: [] for name in player_names}
    if not fixtures or not player_names:
        return histories

    for ev in fixtures:
        fp, sp = ev.get("first_player"), ev.get("second_player")
        winner = ev.get("winner")
        if not fp or not sp or not winner:
            continue
        for name in player_names:
            side = None
            if matches_player_name(name, fp):
                side = "first"
            elif matches_player_name(name, sp):
                side = "second"
            if side is None:
                continue
            won = (winner == "First Player") if side == "first" else (winner == "Second Player")
            histories[name].append(
                {
                    "date": ev.get("date"),
                    "won": won,
                    "surface": ev.get("surface") or "Hard",
                }
            )

    for name in histories:
        histories[name].sort(key=lambda m: str(m.get("date") or ""), reverse=True)
    return histories
