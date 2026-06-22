"""Streamlit-App mit zwei Seiten:
  1. "Heutige Tipps"        – die besten max. 5 Tipps des Tages
  2. "Tracking & Statistiken" – Auswertung, Ergebnis-Eintrag, Charts

Start:  streamlit run app.py   (läuft auf http://localhost:8501)
"""

from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st

import config
import database
import match_analyzer

log = config.log

st.set_page_config(page_title="Tennis Tipp-Analyse", page_icon="🎾", layout="wide")


# --------------------------------------------------------------------------- #
# Login (gemeinsames Passwort)
# --------------------------------------------------------------------------- #
def check_login() -> bool:
    """Prüft das gemeinsame Passwort. Gibt True zurück, wenn der Zugang frei ist.

    Ist kein Passwort konfiguriert (APP_PASSWORD leer), wird der Zugang ohne
    Login gewährt – praktisch für die lokale Entwicklung.
    """
    if not config.APP_PASSWORD:
        return True
    if st.session_state.get("auth_ok"):
        return True

    st.title("🎾 Tennis Tipp-Analyse")
    st.markdown("#### 🔒 Bitte anmelden")
    pw = st.text_input("Passwort", type="password", key="login_pw")
    if st.button("Anmelden"):
        if pw == config.APP_PASSWORD:
            st.session_state["auth_ok"] = True
            st.rerun()
        else:
            st.error("Falsches Passwort.")
    st.caption("Zugang nur für eingeladene Freunde.")
    return False


# --------------------------------------------------------------------------- #
# Daten-Lade-Hilfen (täglich gecacht, kein Re-Fetch pro Refresh)
# --------------------------------------------------------------------------- #
@st.cache_data(ttl=3600, show_spinner=False)
def load_today_tips(_cache_key: str) -> list[dict]:
    """Lädt/berechnet die heutigen Tipps. _cache_key (Datum) steuert den Cache."""
    try:
        return match_analyzer.generate_top_tips()
    except Exception as exc:  # bewusst breit: UI darf nie crashen
        log.error("load_today_tips fehlgeschlagen: %s", exc)
        return []


def status_badge(status: str) -> str:
    """Gibt das passende Status-Emoji-Label zurück."""
    return {"gewonnen": "✅ Gewonnen", "verloren": "❌ Verloren"}.get(status, "🟡 Offen")


# --------------------------------------------------------------------------- #
# SEITE 1: Heutige Tipps
# --------------------------------------------------------------------------- #
def render_tips_page() -> None:
    """Rendert die Seite mit den heutigen Tipp-Cards. Gibt None zurück."""
    heute = datetime.now().strftime("%d.%m.%Y")
    st.title(f"🎾 Tennis Tipp-Analyse – {heute}")

    col_btn, _ = st.columns([1, 4])
    with col_btn:
        if st.button("🔄 Tipps neu laden"):
            load_today_tips.clear()

    with st.spinner("Analysiere heutige Matches …"):
        tips = load_today_tips(datetime.now().strftime("%Y-%m-%d"))

    # Metric-Row
    gesamt_einsatz = len(tips) * config.EINSATZ
    offene = sum(1 for t in tips if t["status"] == "offen")
    eingetragen = sum(1 for t in tips if t["status"] != "offen")
    m1, m2, m3 = st.columns(3)
    m1.metric("Heutiger Einsatz gesamt", f"{gesamt_einsatz:.0f} €")
    m2.metric("Offene Tipps", offene)
    m3.metric("Bereits eingetragen", eingetragen)

    st.divider()

    if not tips:
        st.info(
            "ℹ️ Aktuell keine Tipps verfügbar. Entweder gibt es heute keine Tennis-Matches "
            "mit Quoten, oder kein Match erreicht die Mindest-Konfidenz von "
            f"{config.MIN_KONFIDENZ:.0%}. Es wird bewusst nicht aufgefüllt."
        )
        return None

    for t in tips:
        _render_tip_card(t)
    return None


def _render_tip_card(t: dict) -> None:
    """Rendert eine einzelne Tipp-Card. Gibt None zurück."""
    with st.container(border=True):
        kopf, quote = st.columns([3, 1])
        with kopf:
            st.subheader(f"{t['player1']}  vs  {t['player2']}")
            badge = f"🏟️ {t.get('tournament') or 'Turnier unbekannt'}  ·  🎾 {t.get('surface') or '?'}"
            if t.get("match_time"):
                badge += f"  ·  🕒 {t['match_time']}"
            st.caption(badge)
            st.markdown(f"**Unser Tipp:** **:blue[{t['tip']}]**")
        with quote:
            if t.get("betano_odds"):
                st.metric("Betano-Quote", f"{t['betano_odds']:.2f}")
            else:
                st.markdown("### ⚠️ Quote fehlt")

        konfidenz = float(t.get("confidence") or 0.0)
        st.progress(min(1.0, konfidenz), text=f"Konfidenz: {konfidenz:.0%}")

        c1, c2, c3 = st.columns(3)
        prob_calc = t.get("prob_calculated")
        prob_impl = t.get("prob_implied")
        c1.markdown(
            f"**Unsere Wahrscheinlichkeit:** {prob_calc:.0%}" if prob_calc is not None else "—"
        )
        c2.markdown(
            f"**Buchmacher (implizit):** {prob_impl:.0%}" if prob_impl is not None else "**Buchmacher:** —"
        )
        if t.get("betano_odds"):
            gewinn = t["betano_odds"] * config.EINSATZ - config.EINSATZ
            c3.markdown(f"**Einsatz:** 10 €  ·  **Möglicher Gewinn:** {gewinn:.2f} €")
        else:
            c3.markdown("**Einsatz:** 10 €")

        st.markdown(f"**Status:** {status_badge(t['status'])}")
    return None


# --------------------------------------------------------------------------- #
# SEITE 2: Tracking & Statistiken
# --------------------------------------------------------------------------- #
def render_tracking_page() -> None:
    """Rendert die Tracking-Seite inkl. Metrics, Tabelle und Charts. Gibt None zurück."""
    st.title("📊 Tracking & Statistiken")

    alle = database.get_all_predictions()
    settled = [p for p in alle if p["status"] in ("gewonnen", "verloren")]
    gewonnen = [p for p in settled if p["status"] == "gewonnen"]

    gesamt_einsatz = len(alle) * config.EINSATZ
    gesamt_pnl = sum(p["gewinn_verlust"] for p in settled)
    settled_einsatz = len(settled) * config.EINSATZ
    roi = (gesamt_pnl / settled_einsatz * 100) if settled_einsatz > 0 else 0.0
    trefferquote = (len(gewonnen) / len(settled) * 100) if settled else 0.0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Gesamteinsatz", f"{gesamt_einsatz:.0f} €")
    m2.metric("Gewinn/Verlust", f"{gesamt_pnl:+.2f} €")
    m3.metric("ROI", f"{roi:+.1f} %")
    m4.metric("Trefferquote", f"{trefferquote:.1f} %")

    _render_result_sidebar()

    st.divider()
    _render_table(alle)
    st.divider()
    _render_charts(settled)
    return None


def _render_result_sidebar() -> None:
    """Sidebar-Formular zum Eintragen von Ergebnissen. Gibt None zurück."""
    st.sidebar.divider()
    st.sidebar.subheader("✍️ Ergebnis eintragen")

    offene = database.get_open_predictions()
    if not offene:
        st.sidebar.info("Keine offenen Tipps.")
        return None

    optionen = {
        f"{p['date']} | {p['tip']} ({p['player1']} vs {p['player2']})": p["id"]
        for p in offene
    }
    auswahl = st.sidebar.selectbox("Offenen Tipp wählen", list(optionen.keys()))
    ergebnis = st.sidebar.radio("Ergebnis", ["Gewonnen", "Verloren"], horizontal=True)

    if st.sidebar.button("💾 Ergebnis speichern"):
        pred_id = optionen[auswahl]
        ok = database.update_prediction_result(pred_id, gewonnen=(ergebnis == "Gewonnen"))
        if ok:
            database.update_bankroll_history()
            st.sidebar.success("Ergebnis gespeichert.")
            st.rerun()
        else:
            st.sidebar.error("Speichern fehlgeschlagen – siehe Log.")
    return None


def _render_table(alle: list[dict]) -> None:
    """Rendert die filter- und sortierbare Tabelle aller Tipps. Gibt None zurück."""
    st.subheader("Alle Tipps")
    if not alle:
        st.info("Noch keine Tipps vorhanden.")
        return None

    filter_wahl = st.radio(
        "Filter", ["Alle", "Nur Gewonnen", "Nur Verloren", "Nur Offen"], horizontal=True
    )
    status_map = {"Nur Gewonnen": "gewonnen", "Nur Verloren": "verloren", "Nur Offen": "offen"}
    daten = alle if filter_wahl == "Alle" else [p for p in alle if p["status"] == status_map[filter_wahl]]

    if not daten:
        st.info("Keine Tipps für diesen Filter.")
        return None

    df = pd.DataFrame(
        [
            {
                "Datum": p["date"],
                "Match": f"{p['player1']} vs {p['player2']}",
                "Turnier": p.get("tournament") or "—",
                "Tipp": p["tip"],
                "Quote": p.get("betano_odds"),
                "Konfidenz %": round((p.get("confidence") or 0) * 100, 1),
                "Einsatz €": p.get("einsatz") or config.EINSATZ,
                "G/V €": p.get("gewinn_verlust") or 0.0,
                "Status": status_badge(p["status"]),
            }
            for p in daten
        ]
    )
    df = df.sort_values("Datum", ascending=False)

    def _farbe(val: float) -> str:
        """Färbt Gewinn grün, Verlust rot."""
        if val > 0:
            return "color: #16a34a; font-weight: 600;"
        if val < 0:
            return "color: #dc2626; font-weight: 600;"
        return ""

    styler = df.style.map(_farbe, subset=["G/V €"]).format(
        {"Quote": "{:.2f}", "Einsatz €": "{:.0f}", "G/V €": "{:+.2f}"}, na_rep="—"
    )
    st.dataframe(styler, use_container_width=True, hide_index=True)
    return None


def _render_charts(settled: list[dict]) -> None:
    """Rendert die drei Plotly-Charts mit Fallbacks. Gibt None zurück."""
    st.subheader("Auswertung")
    if not settled:
        st.info("Sobald Ergebnisse eingetragen sind, erscheinen hier Charts.")
        return None

    df = pd.DataFrame(settled)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")

    c1, c2 = st.columns(2)

    # Chart 1: Kumulativer Gewinn/Verlust
    with c1:
        st.markdown("**Kumulativer Gewinn/Verlust**")
        if df.empty:
            st.info("Keine Daten.")
        else:
            tmp = df.copy()
            tmp["kumuliert"] = tmp["gewinn_verlust"].cumsum()
            fig = px.line(tmp, x="date", y="kumuliert", markers=True,
                          labels={"date": "Datum", "kumuliert": "Kumuliert €"})
            fig.update_layout(margin=dict(l=10, r=10, t=10, b=10), height=320)
            st.plotly_chart(fig, use_container_width=True)

    # Chart 2: Trefferquote pro Monat
    with c2:
        st.markdown("**Trefferquote pro Monat**")
        if df.empty:
            st.info("Keine Daten.")
        else:
            tmp = df.copy()
            tmp["monat"] = tmp["date"].dt.strftime("%Y-%m")
            tmp["gewonnen"] = (tmp["status"] == "gewonnen").astype(int)
            grp = tmp.groupby("monat").agg(quote=("gewonnen", "mean")).reset_index()
            grp["quote"] = grp["quote"] * 100
            fig = px.bar(grp, x="monat", y="quote",
                         labels={"monat": "Monat", "quote": "Trefferquote %"})
            fig.update_layout(margin=dict(l=10, r=10, t=10, b=10), height=320, yaxis_range=[0, 100])
            st.plotly_chart(fig, use_container_width=True)

    # Chart 3: Durchschnittliche Konfidenz Gewonnen vs Verloren
    st.markdown("**Durchschnittliche Konfidenz: Gewonnen vs Verloren**")
    tmp = df.copy()
    tmp["confidence"] = pd.to_numeric(tmp["confidence"], errors="coerce") * 100
    grp = tmp.groupby("status").agg(konfidenz=("confidence", "mean")).reset_index()
    grp["status"] = grp["status"].map({"gewonnen": "Gewonnen", "verloren": "Verloren"})
    fig = px.bar(grp, x="status", y="konfidenz", color="status",
                 color_discrete_map={"Gewonnen": "#16a34a", "Verloren": "#dc2626"},
                 labels={"status": "", "konfidenz": "Ø Konfidenz %"})
    fig.update_layout(margin=dict(l=10, r=10, t=10, b=10), height=320, showlegend=False)
    st.plotly_chart(fig, use_container_width=True)
    return None


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    """Einstiegspunkt: initialisiert DB und steuert die Seiten-Navigation."""
    if not check_login():
        return None

    database.init_db()

    st.sidebar.title("🎾 Navigation")
    seite = st.sidebar.radio("Seite", ["Heutige Tipps", "Tracking & Statistiken"])

    if not config.ODDS_API_KEY or not config.API_TENNIS_KEY:
        st.sidebar.warning("⚠️ API-Keys fehlen in der .env-Datei.")

    if config.APP_PASSWORD and st.session_state.get("auth_ok"):
        if st.sidebar.button("🚪 Abmelden"):
            st.session_state["auth_ok"] = False
            st.rerun()

    if seite == "Heutige Tipps":
        render_tips_page()
    else:
        render_tracking_page()


if __name__ == "__main__":
    main()
