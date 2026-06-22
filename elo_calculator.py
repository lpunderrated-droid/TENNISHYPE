"""Elo-Rating-Logik (Schicht 1 des Algorithmus).

Enthält die mathematische Elo-Formel sowie das Update nach einem Match und
eine Hilfsfunktion, um aus einer Weltranglisten-Position eine Start-Elo
abzuleiten (für Spieler ohne Match-Historie in der DB).
"""

import config

log = config.log


def expected_score(elo_a: float, elo_b: float) -> float:
    """Erwartete Siegwahrscheinlichkeit von Spieler A gegen B.

    Formel: E_A = 1 / (1 + 10^((Elo_B - Elo_A) / 400))
    Der Rückgabewert liegt garantiert im Intervall (0, 1).
    """
    try:
        return 1.0 / (1.0 + 10 ** ((elo_b - elo_a) / 400.0))
    except (OverflowError, ZeroDivisionError):
        # Bei extremen Differenzen sinnvoll begrenzen
        return 0.0 if elo_a < elo_b else 1.0


def update_elo(elo_winner: float, elo_loser: float, k: int = config.ELO_K_FAKTOR) -> tuple[float, float]:
    """Aktualisiert die Elo beider Spieler nach einem Match.

    Returns (neue_elo_gewinner, neue_elo_verlierer).
    """
    erwartet_gewinner = expected_score(elo_winner, elo_loser)
    neue_winner = elo_winner + k * (1.0 - erwartet_gewinner)
    neue_loser = elo_loser + k * (0.0 - (1.0 - erwartet_gewinner))
    return round(neue_winner, 2), round(neue_loser, 2)


def ranking_to_elo(rank: int | None) -> float:
    """Leitet aus einer ATP/WTA-Position eine Näherungs-Elo ab.

    Ohne historische Matches dient die Weltrangliste als Proxy:
    Rang 1 ~ 2000, je niedriger die Position desto geringer die Elo,
    nach unten begrenzt auf 1300.
    Gibt bei unbekanntem Rang die Standard-Start-Elo zurück.
    """
    if rank is None or rank <= 0:
        return config.ELO_START
    elo = 2000.0 - (rank - 1) * 5.0
    return max(1300.0, elo)
