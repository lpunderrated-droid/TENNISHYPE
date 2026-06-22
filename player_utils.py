"""Spieler-Namen vereinheitlichen und Rankings zuordnen.

Tennis-Namen kommen je nach API unterschiedlich an:
  - Akzente:   'Jović' vs 'Jovic'
  - Bindestrich: 'Chun-Hsin' vs 'Chun Hsin'
  - Reihenfolge: 'Bu Yunchaokete' vs 'Yunchaokete Bu'

Dieses Modul ist die einzige Quelle für Namens-Normalisierung im gesamten Projekt.
"""

import re
import unicodedata
from typing import Optional


def normalize_name(name: str | None) -> str:
    """Vereinheitlicht einen Spielernamen für Vergleiche zwischen APIs.

    Schritte: trimmen, lowercase, Akzente entfernen, Satzzeichen/Bindestriche
    zu Leerzeichen, mehrfache Leerzeichen zusammenfassen.
    """
    if not name:
        return ""
    text = name.strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[''`´]", " ", text)       # Apostrophe
    text = re.sub(r"[-./]", " ", text)        # Bindestrich & Trennzeichen
    text = re.sub(r"[^a-z0-9\s]", " ", text)  # übrige Sonderzeichen
    return re.sub(r"\s+", " ", text).strip()


def name_tokens(name: str | None) -> frozenset[str]:
    """Token-Menge eines Namens (Reihenfolge-unabhängig)."""
    return frozenset(normalize_name(name).split())


def names_match(a: str | None, b: str | None) -> bool:
    """True, wenn zwei Namen nach Normalisierung identisch sind (inkl. Token-Reihenfolge)."""
    if not a or not b:
        return False
    if normalize_name(a) == normalize_name(b):
        return True
    return name_tokens(a) == name_tokens(b)


def matches_player_name(a: str | None, b: str | None) -> bool:
    """True, wenn zwei Namen derselbe Spieler sind (voller Name, Abkürzung, Akzente).

    Deckt z. B. 'Francisco Comesana' vs 'F. Comesana' ab (Nachname + Anfangsbuchstabe).
    """
    if not a or not b:
        return False
    if names_match(a, b):
        return True
    ta = normalize_name(a).split()
    tb = normalize_name(b).split()
    if not ta or not tb:
        return False
    if ta[-1] != tb[-1]:
        return False
    return ta[0][0] == tb[0][0]


def _build_ranking_index(rankings: dict[str, int]) -> tuple[dict[str, int], dict[frozenset[str], int]]:
    """Baut Hilfs-Indizes für exakte und token-basierte Rankings-Suche."""
    exact = dict(rankings)
    by_tokens: dict[frozenset[str], int] = {}
    for rank_name, rank in rankings.items():
        tokens = name_tokens(rank_name)
        if tokens:
            # Bei Kollision gewinnt der bessere (niedrigere) Rang
            by_tokens[tokens] = min(by_tokens.get(tokens, rank), rank)
    return exact, by_tokens


def lookup_ranking(name: str | None, rankings: dict[str, int]) -> Optional[int]:
    """Findet den Weltranglisten-Platz zu einem Spielernamen (best effort).

    Strategie (in dieser Reihenfolge):
      1. Exakter normalisierter Name
      2. Gleiche Token-Menge (Reihenfolge egal)
      3. Teiltreffer: alle Tokens des Ranking-Namens sind im Spielernamen
         enthalten – gewählt wird der Treffer mit den meisten Tokens
         (z. B. 'Felipe Meligeni Alves' in 'Felipe Meligeni Rodrigues Alves')
    """
    if not name or not rankings:
        return None

    exact, by_tokens = _build_ranking_index(rankings)

    key = normalize_name(name)
    if key in exact:
        return exact[key]

    tokens = name_tokens(name)
    if not tokens:
        return None

    if tokens in by_tokens:
        return by_tokens[tokens]

    # Teiltreffer: Ranking-Name ist Teilmenge der Spieler-Tokens
    best_rank: Optional[int] = None
    best_len = 0
    for rank_tokens, rank in by_tokens.items():
        if rank_tokens <= tokens and len(rank_tokens) > best_len:
            best_len = len(rank_tokens)
            best_rank = rank
    # Mindestens 2 Tokens, damit 'Alves' nicht alles matcht
    if best_len >= 2:
        return best_rank
    return None


def lookup_player_key(name: str | None, player_keys: dict[str, str]) -> Optional[str]:
    """Findet den API-Tennis player_key zu einem Spielernamen (best effort).

    Nutzt dieselbe Strategie wie lookup_ranking (exakt, Token-Menge, Teiltreffer).
    """
    if not name or not player_keys:
        return None

    exact = dict(player_keys)
    by_tokens: dict[frozenset[str], str] = {}
    for key_name, pk in player_keys.items():
        tokens = name_tokens(key_name)
        if tokens and tokens not in by_tokens:
            by_tokens[tokens] = pk

    key = normalize_name(name)
    if key in exact:
        return exact[key]

    tokens = name_tokens(name)
    if not tokens:
        return None

    if tokens in by_tokens:
        return by_tokens[tokens]

    best_key: Optional[str] = None
    best_len = 0
    for rank_tokens, pk in by_tokens.items():
        if rank_tokens <= tokens and len(rank_tokens) > best_len:
            best_len = len(rank_tokens)
            best_key = pk
    if best_len >= 2:
        return best_key
    return None
