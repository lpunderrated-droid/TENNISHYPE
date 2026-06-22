"""Kombiwetten: Quote berechnen und Status aus Leg-Ergebnissen ableiten."""

import config


def combined_odds(legs: list[dict]) -> float:
    """Multipliziert Leg-Quoten zu einer Kombi-Quote."""
    total = 1.0
    for leg in legs:
        odds = leg.get("betano_odds")
        if not odds or odds <= 1.0:
            return 0.0
        total *= float(odds)
    return round(total, 4)


def evaluate_combi(
    legs: list[dict],
    combined: float | None = None,
    stake: float | None = None,
) -> tuple[str, float]:
    """Leitet Kombi-Status und PnL aus den Leg-Status ab.

    Returns (status, gewinn_verlust) mit status in offen/gewonnen/verloren.
    """
    if not legs:
        return "offen", 0.0

    einsatz = float(stake if stake is not None else config.COMBI_EINSATZ_DEFAULT)
    statuses = [leg.get("status") or "offen" for leg in legs]

    if any(s == "offen" for s in statuses):
        return "offen", 0.0
    if any(s == "verloren" for s in statuses):
        return "verloren", -einsatz

    odds = combined if combined else combined_odds(legs)
    if odds <= 1.0:
        return "offen", 0.0
    return "gewonnen", round(odds * einsatz - einsatz, 2)


def combi_payout(combined: float, stake: float) -> float:
    """Möglicher Netto-Gewinn einer Kombi bei Sieg."""
    if combined <= 1.0 or stake <= 0:
        return 0.0
    return round(combined * stake - stake, 2)


def format_legs_summary(legs: list[dict]) -> str:
    """Kurzbeschreibung für Tracking-Tabelle."""
    return " · ".join(leg.get("tip") or "?" for leg in legs)
