"""Kern-Algorithmus: kombiniert vier Schichten zu einer Siegwahrscheinlichkeit
und liefert die besten max. 5 Tipps des Tages.

Schichten und Gewichte (Summe = 100 %):
  1. Elo-Rating          40 %
  2. Form-Adjustment     30 %
  3. Oberflächen-Statistik 20 %
  4. Head-to-Head        10 %

Liefert eine Schicht keine Daten (z. B. zu wenige H2H-Matches), wird ihr
Gewicht proportional auf die übrigen Schichten verteilt, sodass die Summe
weiterhin 100 % beträgt. Ist kein Match konfident genug (> 60 %), werden
bewusst keine Tipps erzeugt – es wird niemals aufgefüllt.
"""

from datetime import datetime, timedelta
from typing import Optional

import config
import data_fetcher
import database
from elo_calculator import expected_score, ranking_to_elo
from player_utils import lookup_ranking, names_match, normalize_name

log = config.log


# --------------------------------------------------------------------------- #
# Schicht 1: Elo
# --------------------------------------------------------------------------- #
def _surface_elo(player: dict | None, surface: str, fallback: float) -> float:
    """Liest die oberflächenspezifische Elo eines Spielers oder gibt fallback zurück."""
    if not player:
        return fallback
    spalte = {"Clay": "clay_elo", "Hard": "hard_elo", "Grass": "grass_elo"}.get(surface, "hard_elo")
    wert = player.get(spalte)
    return float(wert) if wert is not None else fallback


def elo_probability(player1: str, player2: str, surface: str, rankings: dict[str, int]) -> float:
    """Schicht 1: Siegwahrscheinlichkeit von player1 anhand der Oberflächen-Elo."""
    p1 = database.find_player(player1)
    p2 = database.find_player(player2)
    fb1 = ranking_to_elo(lookup_ranking(player1, rankings))
    fb2 = ranking_to_elo(lookup_ranking(player2, rankings))
    elo1 = _surface_elo(p1, surface, fb1)
    elo2 = _surface_elo(p2, surface, fb2)
    return expected_score(elo1, elo2)


# --------------------------------------------------------------------------- #
# Schicht 2: Form
# --------------------------------------------------------------------------- #
def _form_score(matches: list[dict] | None) -> Optional[float]:
    """Berechnet einen Form-Score (0..1) aus den letzten Matches eines Spielers.

    - Exponentieller Decay 0.9^n auf die letzten 10 Matches
    - Müdigkeits-Penalty: -2 % pro Match der letzten 7 Tage (max. -10 %)
    Gibt None zurück, wenn keine Daten vorliegen.
    """
    if not matches:
        return None

    letzte = matches[: config.FORM_LETZTE_N]
    gewicht_summe = 0.0
    sieg_summe = 0.0
    for n, m in enumerate(letzte):
        gewicht = config.FORM_DECAY ** n
        gewicht_summe += gewicht
        if m.get("won"):
            sieg_summe += gewicht
    if gewicht_summe == 0:
        return None
    score = sieg_summe / gewicht_summe

    # Müdigkeit: Anzahl Matches der letzten 7 Tage
    grenze = datetime.now() - timedelta(days=7)
    recent = 0
    for m in letzte:
        try:
            if datetime.fromisoformat(str(m.get("date"))) >= grenze:
                recent += 1
        except (ValueError, TypeError):
            continue
    penalty = min(recent * config.FATIGUE_PRO_MATCH, config.FATIGUE_MAX)
    return max(0.0, score * (1.0 - penalty))


def form_probability(matches_p1: list[dict] | None, matches_p2: list[dict] | None) -> Optional[float]:
    """Schicht 2: relative Form von player1 gegenüber player2 (0..1) oder None."""
    s1 = _form_score(matches_p1)
    s2 = _form_score(matches_p2)
    if s1 is None or s2 is None:
        return None
    if (s1 + s2) == 0:
        return 0.5
    return s1 / (s1 + s2)


# --------------------------------------------------------------------------- #
# Schicht 3: Oberflächen-Statistik
# --------------------------------------------------------------------------- #
def _surface_winrate(matches: list[dict] | None, surface: str) -> Optional[float]:
    """Siegquote eines Spielers auf einer Oberfläche inkl. +5 % Bonus über 60 %.

    Gibt None zurück, wenn keine Matches auf dieser Oberfläche vorliegen.
    """
    if not matches:
        return None
    relevant = [m for m in matches if m.get("surface") == surface]
    if not relevant:
        return None
    siege = sum(1 for m in relevant if m.get("won"))
    quote = siege / len(relevant)
    if quote > config.SURFACE_BONUS_SCHWELLE:
        quote = min(1.0, quote + config.SURFACE_BONUS)
    return quote


def surface_probability(
    matches_p1: list[dict] | None, matches_p2: list[dict] | None, surface: str
) -> Optional[float]:
    """Schicht 3: relative Oberflächen-Stärke von player1 (0..1) oder None."""
    w1 = _surface_winrate(matches_p1, surface)
    w2 = _surface_winrate(matches_p2, surface)
    if w1 is None or w2 is None:
        return None
    if (w1 + w2) == 0:
        return 0.5
    return w1 / (w1 + w2)


# --------------------------------------------------------------------------- #
# Schicht 4: Head-to-Head
# --------------------------------------------------------------------------- #
def h2h_probability(h2h: list[dict] | None, player1: str) -> Optional[float]:
    """Schicht 4: gewichteter H2H-Vorteil von player1 (0..1).

    Nur gültig ab mindestens 2 Direktvergleichen; sonst None. Die letzten 5
    Begegnungen werden mit Decay 0.9 gewichtet.
    """
    if not h2h or len(h2h) < config.H2H_MIN_MATCHES:
        return None
    letzte = h2h[: config.H2H_LETZTE_N]
    name1 = normalize_name(player1)
    gewicht_summe = 0.0
    p1_summe = 0.0
    for n, m in enumerate(letzte):
        raw_winner = str(m.get("event_winner", m.get("winner", "")))
        gewicht = config.H2H_DECAY ** n
        gewicht_summe += gewicht
        # event_winner ist bei API-Tennis oft "First Player" / "Second Player"
        if raw_winner.strip().lower() == "first player" or names_match(raw_winner, player1):
            p1_summe += gewicht
    if gewicht_summe == 0:
        return None
    return p1_summe / gewicht_summe


# --------------------------------------------------------------------------- #
# Kombination der Schichten
# --------------------------------------------------------------------------- #
def combine_layers(
    p_elo: float,
    p_form: Optional[float],
    p_surface: Optional[float],
    p_h2h: Optional[float],
) -> float:
    """Kombiniert die Schicht-Wahrscheinlichkeiten zu einer Gesamtwahrscheinlichkeit.

    Fehlende Schichten (None) werden ausgelassen und ihr Gewicht proportional
    auf die vorhandenen Schichten verteilt (Renormierung auf Summe 1).
    Der Rückgabewert liegt garantiert in [0, 1].
    """
    schichten = [
        (config.WEIGHT_ELO, p_elo),
        (config.WEIGHT_FORM, p_form),
        (config.WEIGHT_SURFACE, p_surface),
        (config.WEIGHT_H2H, p_h2h),
    ]
    aktiv = [(w, p) for w, p in schichten if p is not None]
    gewicht_summe = sum(w for w, _ in aktiv)
    if gewicht_summe == 0:
        return p_elo
    kombiniert = sum(w * p for w, p in aktiv) / gewicht_summe
    return min(1.0, max(0.0, kombiniert))


# --------------------------------------------------------------------------- #
# Spieler-Stammdaten pflegen
# --------------------------------------------------------------------------- #
def _ensure_player(name: str, rankings: dict[str, int]) -> None:
    """Legt einen Spieler mit ranking-basierter Start-Elo an, falls unbekannt."""
    if not name:
        return None
    if database.find_player(name) is None:
        elo = ranking_to_elo(lookup_ranking(name, rankings))
        database.upsert_player(name, clay_elo=elo, hard_elo=elo, grass_elo=elo)
    return None


# --------------------------------------------------------------------------- #
# Haupt-Einstieg: Top-5 berechnen
# --------------------------------------------------------------------------- #
def analyze_match(match: dict, rankings: dict[str, int], use_h2h: bool = True) -> Optional[dict]:
    """Analysiert ein einzelnes Match und liefert ein Tipp-dict oder None.

    None, wenn die Konfidenz die Mindestschwelle nicht erreicht oder Quoten
    fehlen. Mit use_h2h=False wird die (langsame) H2H-Abfrage übersprungen –
    nützlich für einen schnellen Vorab-Filter über alle Matches.
    """
    player1 = match.get("player1")
    player2 = match.get("player2")
    surface = match.get("surface", "Hard")
    odds_p1 = match.get("odds_p1")
    odds_p2 = match.get("odds_p2")
    if not player1 or not player2 or odds_p1 is None or odds_p2 is None:
        return None

    _ensure_player(player1, rankings)
    _ensure_player(player2, rankings)

    # Schicht 1: Elo (immer vorhanden)
    p_elo = elo_probability(player1, player2, surface, rankings)

    # Schichten 2 & 3 benötigen Match-Historie (best effort; sonst None = neutral)
    p_form = form_probability(None, None)
    p_surface = surface_probability(None, None, surface)

    # Schicht 4: H2H (best effort) – nur wenn gewünscht, da langsam
    if use_h2h:
        h2h = data_fetcher.fetch_h2h(player1, player2)
        p_h2h = h2h_probability(h2h, player1)
    else:
        p_h2h = None

    prob_p1 = combine_layers(p_elo, p_form, p_surface, p_h2h)

    # Tipp auf den Spieler mit der höheren berechneten Wahrscheinlichkeit
    if prob_p1 >= 0.5:
        tip, prob_calc, odds_tip = player1, prob_p1, odds_p1
    else:
        tip, prob_calc, odds_tip = player2, 1.0 - prob_p1, odds_p2

    confidence = prob_calc  # Konfidenz = berechnete Siegwahrscheinlichkeit des Tipps
    if confidence <= config.MIN_KONFIDENZ:
        return None

    prob_implied = 1.0 / odds_tip if odds_tip and odds_tip > 0 else None

    return {
        "date": match.get("date"),
        "player1": player1,
        "player2": player2,
        "tournament": match.get("tournament"),
        "surface": surface,
        "match_time": match.get("match_time"),
        "tip": tip,
        "betano_odds": odds_tip,
        "prob_calculated": round(prob_calc, 4),
        "prob_implied": round(prob_implied, 4) if prob_implied is not None else None,
        "confidence": round(confidence, 4),
        "bookmaker": match.get("bookmaker"),
    }


def generate_top_tips() -> list[dict]:
    """Erzeugt die Top-5-Tipps des Tages, speichert sie und gibt sie zurück.

    Ablauf: Quoten + Rankings laden -> Matches speichern -> jedes Match
    analysieren -> nach Konfidenz sortieren -> max. 5 mit Konfidenz > 60 %
    in der DB ablegen. Gibt die heutigen Tipps (aus der DB) zurück.
    """
    heute = datetime.now().strftime("%Y-%m-%d")

    # Bereits berechnet? Dann vorhandene Tipps zurückgeben (tägliches Caching).
    bestehend = database.get_predictions_by_date(heute)
    if bestehend:
        log.info("Heutige Tipps bereits vorhanden (%s).", len(bestehend))
        return bestehend

    matches = data_fetcher.fetch_odds()
    rankings = data_fetcher.fetch_rankings()
    if not matches:
        log.info("Keine Matches/Quoten für heute verfügbar.")
        return []

    # Durchlauf 1 (schnell, ohne H2H): grobe Vorauswahl über alle Matches
    vorauswahl: list[tuple[dict, dict]] = []
    for match in matches:
        database.insert_match(match)
        tipp = analyze_match(match, rankings, use_h2h=False)
        if tipp is not None:
            vorauswahl.append((match, tipp))

    # Nur die besten Kandidaten weiterverfolgen (begrenzt die H2H-API-Aufrufe)
    vorauswahl.sort(key=lambda paar: paar[1]["confidence"], reverse=True)
    engere_wahl = vorauswahl[: config.MAX_TIPPS_PRO_TAG * 2]

    # Durchlauf 2 (genau, mit H2H): nur für die engere Auswahl
    kandidaten: list[dict] = []
    for match, _ in engere_wahl:
        tipp = analyze_match(match, rankings, use_h2h=True)
        if tipp is not None:
            kandidaten.append(tipp)

    # Nach Konfidenz absteigend, max. 5 – niemals auffüllen
    kandidaten.sort(key=lambda t: t["confidence"], reverse=True)
    top = kandidaten[: config.MAX_TIPPS_PRO_TAG]

    for tipp in top:
        database.insert_prediction(tipp)

    log.info("%s konfidente Tipps für heute gespeichert.", len(top))
    return database.get_predictions_by_date(heute)


def auto_settle() -> int:
    """Rechnet offene Tipps automatisch ab, sobald Ergebnisse vorliegen.

    Holt abgeschlossene Matches von The Odds API (gleicher Namensraum wie die
    gespeicherten Quoten) und setzt passende offene Tipps auf gewonnen/verloren.
    Gibt die Anzahl der automatisch abgerechneten Tipps zurück (0 bei keinen).
    """
    offene = database.get_open_predictions()
    if not offene:
        return 0

    ergebnisse = data_fetcher.fetch_scores()
    if not ergebnisse:
        return 0

    def norm(name: str | None) -> str:
        return normalize_name(name)

    # Nachschlage-Index: Spielerpaar -> Gewinner (Originalname aus API)
    lookup: dict[frozenset, str] = {}
    for r in ergebnisse:
        paar = frozenset({norm(r.get("player1")), norm(r.get("player2"))})
        lookup[paar] = r.get("winner") or ""

    abgerechnet = 0
    for p in offene:
        paar = frozenset({norm(p.get("player1")), norm(p.get("player2"))})
        gewinner = lookup.get(paar)
        if not gewinner:
            continue
        gewonnen = names_match(p.get("tip"), gewinner)
        if database.update_prediction_result(p["id"], gewonnen):
            abgerechnet += 1

    if abgerechnet:
        database.update_bankroll_history()
        log.info("auto_settle: %s Tipps automatisch abgerechnet.", abgerechnet)
    return abgerechnet
