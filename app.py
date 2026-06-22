"""Streamlit-App mit zwei Seiten:
  1. "Heutige Tipps"        – die besten max. 5 Tipps des Tages
  2. "Tracking & Statistiken" – Auswertung, Ergebnis-Eintrag, Charts

Start:  streamlit run app.py   (läuft auf http://localhost:8501)
"""

import html
from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st

import config
import combi
import database
import data_fetcher
import match_analyzer
from player_utils import lookup_ranking

log = config.log

st.set_page_config(page_title="TENNISHYPE Terminal", page_icon="🎾", layout="wide")


def _combi_stake(c: dict) -> float:
    """Einsatz einer Kombiwette (Fallback auf Standard)."""
    return float(c.get("einsatz") or config.COMBI_EINSATZ_DEFAULT)


# --------------------------------------------------------------------------- #
# Design: dunkles "Börsen"-Theme via Custom-CSS
# --------------------------------------------------------------------------- #
TRADING_CSS = """
:root {
  --bg:#0b0e11; --panel:#161a1e; --panel2:#1e2329; --border:#2b3139;
  --text:#eaecef; --muted:#848e9c; --green:#0ecb81; --red:#f6465d;
  --yellow:#f0b90b; --blue:#2f80ed;
}
.stApp { background: var(--bg); color: var(--text); }
#MainMenu, footer, header [data-testid="stToolbar"] { visibility: hidden; }
.block-container { padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1200px; }
section[data-testid="stSidebar"] { background: #0d1116; border-right: 1px solid var(--border); }

/* Kopfzeile */
.tg-header { display:flex; align-items:center; justify-content:space-between;
  padding:14px 18px; background:linear-gradient(90deg,#161a1e,#0f1317);
  border:1px solid var(--border); border-radius:14px; margin-bottom:16px; }
.tg-brand { display:flex; align-items:center; gap:12px; }
.tg-logo { font-size:22px; font-weight:800; letter-spacing:.5px; }
.tg-logo .accent { color: var(--green); }
.tg-sub { color:var(--muted); font-size:12px; margin-top:2px; }
.tg-live { display:flex; align-items:center; gap:7px; color:var(--green);
  font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:.5px; }
.tg-dot { width:8px; height:8px; border-radius:50%; background:var(--green);
  box-shadow:0 0 0 0 rgba(14,203,129,.7); animation:pulse 1.8s infinite; }
@keyframes pulse { 0%{box-shadow:0 0 0 0 rgba(14,203,129,.6);} 70%{box-shadow:0 0 0 8px rgba(14,203,129,0);} 100%{box-shadow:0 0 0 0 rgba(14,203,129,0);} }

/* KPI-Kacheln */
.tg-tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-bottom:18px; }
.tg-tile { background:var(--panel); border:1px solid var(--border); border-radius:12px; padding:14px 16px; }
.tg-tile .lbl { color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.6px; }
.tg-tile .val { font-size:24px; font-weight:800; margin-top:4px; font-variant-numeric:tabular-nums; }
.tg-tile .sub { color:var(--muted); font-size:11px; margin-top:2px; }
.val.green{color:var(--green);} .val.red{color:var(--red);} .val.yellow{color:var(--yellow);}

/* Tipp-Karte (Instrument-Zeile) */
.tcard { background:var(--panel); border:1px solid var(--border); border-radius:14px;
  padding:16px 18px; margin-bottom:14px; transition:.15s border-color, .15s transform; }
.tcard:hover { border-color:#3a434d; transform:translateY(-1px); }
.tcard-top { display:flex; justify-content:space-between; align-items:flex-start; gap:14px; }
.matchup { font-size:18px; font-weight:700; }
.matchup .win { color:var(--green); }
.matchup .player-line { display:block; margin:2px 0; }
.matchup .vs { color:var(--muted); font-weight:500; margin:4px 0; font-size:13px; display:block; }
.pstat { font-size:11px; color:var(--muted); font-weight:600; margin-top:3px; display:flex; gap:6px; flex-wrap:wrap; align-items:center; }
.pstat .rank { color:#c7ccd1; background:var(--panel2); border:1px solid var(--border);
  border-radius:6px; padding:1px 7px; font-size:10px; }
.pstat .form-good { color:var(--green); }
.pstat .form-mid { color:var(--yellow); }
.pstat .form-bad { color:var(--red); }
.form-strip { display:inline-flex; gap:3px; flex-wrap:wrap; }
.form-char { font-size:10px; font-weight:800; width:18px; height:18px; line-height:18px;
  text-align:center; border-radius:4px; display:inline-block; }
.form-w { background:rgba(14,203,129,.15); color:var(--green); border:1px solid rgba(14,203,129,.35); }
.form-l { background:rgba(246,70,57,.12); color:var(--red); border:1px solid rgba(246,70,57,.35); }
.form-empty { color:var(--muted); font-size:10px; }
.matchup .loser { color:var(--text); opacity:.65; }
.meta { color:var(--muted); font-size:12px; margin-top:5px; display:flex; gap:8px; flex-wrap:wrap; }
.chip { background:var(--panel2); border:1px solid var(--border); border-radius:999px;
  padding:2px 10px; font-size:11px; color:#c7ccd1; }
.chip.surface { color:var(--yellow); border-color:#3a3417; }
.pill { padding:4px 12px; border-radius:999px; font-size:11px; font-weight:800;
  text-transform:uppercase; letter-spacing:.5px; }
.pill.open { background:rgba(240,185,11,.12); color:var(--yellow); border:1px solid rgba(240,185,11,.3); }
.pill.win  { background:rgba(14,203,129,.12); color:var(--green); border:1px solid rgba(14,203,129,.3); }
.pill.loss { background:rgba(246,70,57,.12); color:var(--red); border:1px solid rgba(246,70,57,.3); }

.tgrid { display:grid; grid-template-columns:1.4fr .8fr .8fr 1fr; gap:10px; margin-top:14px; }
.cell { background:var(--panel2); border:1px solid var(--border); border-radius:10px; padding:10px 12px; }
.cell .k { color:var(--muted); font-size:10px; text-transform:uppercase; letter-spacing:.6px; }
.cell .v { font-size:18px; font-weight:800; margin-top:3px; font-variant-numeric:tabular-nums; }
.cell .v.tip { font-size:15px; color:var(--green); }
.cell .v.price { color:var(--text); }
.cell .v.green{color:var(--green);} .cell .v.red{color:var(--red);}
.cell .v.warn{color:var(--yellow); font-size:14px;}

.confwrap { margin-top:14px; }
.confhead { display:flex; justify-content:space-between; font-size:11px; color:var(--muted); margin-bottom:6px; }
.confhead b { color:var(--text); }
.confbar { height:8px; background:#0c0f12; border:1px solid var(--border); border-radius:999px; overflow:hidden; }
.conffill { height:100%; background:linear-gradient(90deg,var(--green),#10d18a); border-radius:999px; }

.tg-empty { background:var(--panel); border:1px dashed var(--border); border-radius:14px;
  padding:34px; text-align:center; color:var(--muted); }
.tg-empty .big{ font-size:34px; } .tg-empty .h{ color:var(--text); font-size:17px; font-weight:700; margin:8px 0 4px; }

.tg-section { font-size:13px; color:var(--muted); text-transform:uppercase; letter-spacing:.8px;
  margin:6px 0 10px; font-weight:700; }
.combi-card { background:var(--panel2); border:1px solid #3a434d; border-radius:12px;
  padding:12px 14px; margin-bottom:10px; }
.combi-card .legs { color:var(--muted); font-size:12px; margin-top:6px; line-height:1.5; }
.combi-card .head { display:flex; justify-content:space-between; align-items:center; gap:10px; }
.combi-card .odds { color:var(--yellow); font-weight:800; font-size:16px; }
.combi-builder { background:var(--panel); border:1px solid var(--border); border-radius:12px;
  padding:14px 16px; margin-bottom:16px; }
.stButton>button { background:var(--panel2); color:var(--text); border:1px solid var(--border);
  border-radius:10px; font-weight:600; }
.stButton>button:hover { border-color:var(--green); color:var(--green); }
hr { border-color:var(--border); }

/* Login */
.login-box { max-width:420px; margin:2rem auto 0; padding:24px 22px;
  background:var(--panel); border:1px solid var(--border); border-radius:14px; }

/* ===== Tablet (<=768px) ===== */
@media (max-width:768px) {
  .block-container { padding-top:1rem; padding-left:1rem; padding-right:1rem; max-width:100%; }
  .tg-header { flex-direction:column; align-items:flex-start; gap:10px; padding:12px 14px; }
  .tg-tiles { grid-template-columns:repeat(2,1fr); gap:8px; }
  .tg-tile .val { font-size:20px; }
  .tcard-top { flex-direction:column; gap:8px; }
  .tgrid { grid-template-columns:repeat(2,1fr); gap:8px; }
  .matchup { font-size:16px; line-height:1.35; }
  .confhead { flex-direction:column; align-items:flex-start; gap:4px; }
  div[data-testid="stHorizontalBlock"] { flex-wrap:wrap; gap:0.5rem; }
  [data-testid="stDataFrame"] { overflow-x:auto; -webkit-overflow-scrolling:touch; }
}

/* ===== Smartphone (<=480px) ===== */
@media (max-width:480px) {
  .block-container { padding-top:0.6rem; padding-left:0.65rem; padding-right:0.65rem; padding-bottom:2rem; }
  .tg-logo { font-size:18px; }
  .tg-sub { font-size:11px; }
  .tg-tiles { grid-template-columns:repeat(2,1fr); gap:6px; margin-bottom:12px; }
  .tg-tile { padding:10px 11px; border-radius:10px; }
  .tg-tile .lbl { font-size:9px; letter-spacing:.4px; }
  .tg-tile .val { font-size:17px; }
  .tg-tile .sub { font-size:10px; }
  .tcard { padding:12px; border-radius:12px; margin-bottom:10px; }
  .tcard:hover { transform:none; }
  .matchup { font-size:14px; }
  .matchup .player-line { margin:4px 0; }
  .pstat { font-size:10px; }
  .form-char { width:16px; height:16px; line-height:16px; font-size:9px; }
  .meta { gap:5px; margin-top:4px; }
  .chip { font-size:10px; padding:2px 7px; max-width:100%; overflow:hidden;
    text-overflow:ellipsis; white-space:nowrap; }
  .pill { font-size:10px; padding:3px 10px; align-self:flex-start; }
  .tgrid { grid-template-columns:1fr 1fr; gap:6px; }
  .cell { padding:8px 10px; border-radius:8px; }
  .cell .k { font-size:9px; }
  .cell .v { font-size:15px; }
  .cell .v.tip { font-size:12px; line-height:1.25; word-break:break-word; }
  .cell .v.warn { font-size:12px; }
  .confwrap { margin-top:10px; }
  .confhead { font-size:10px; }
  .confbar { height:7px; }
  .tg-empty { padding:22px 16px; }
  .tg-empty .big { font-size:28px; }
  .tg-empty .h { font-size:15px; }
  .tg-section { font-size:11px; margin-bottom:8px; }
  .login-box { margin-top:1rem; padding:18px 16px; }
  /* Filter-Radios untereinander statt nebeneinander */
  div[role="radiogroup"] { flex-direction:column !important; align-items:flex-start !important; gap:6px !important; }
  div[role="radiogroup"] label { margin-right:0 !important; }
  .stButton>button { width:100%; }
}
"""


def inject_css() -> None:
    """Bindet das dunkle Trading-Theme ein. Gibt None zurück."""
    st.markdown(f"<style>{TRADING_CSS}</style>", unsafe_allow_html=True)
    return None


def _tiles_html(tiles: list[dict]) -> str:
    """Baut eine Reihe KPI-Kacheln. tiles: [{lbl, val, sub, tone}] -> HTML-String."""
    cells = []
    for t in tiles:
        tone = t.get("tone", "")
        sub = f"<div class='sub'>{html.escape(str(t.get('sub','')))}</div>" if t.get("sub") else ""
        cells.append(
            f"<div class='tg-tile'><div class='lbl'>{html.escape(t['lbl'])}</div>"
            f"<div class='val {tone}'>{html.escape(str(t['val']))}</div>{sub}</div>"
        )
    return f"<div class='tg-tiles'>{''.join(cells)}</div>"


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

    st.markdown(
        "<div class='tg-header'><div class='tg-brand'><div>"
        "<div class='tg-logo'>TENNIS<span class='accent'>HYPE</span></div>"
        "<div class='tg-sub'>Secure Access</div></div></div></div>",
        unsafe_allow_html=True,
    )
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
# Daten-Lade-Hilfen
# --------------------------------------------------------------------------- #
@st.cache_data(ttl=86400, show_spinner=False)
def _ensure_today_tips_generated(_date: str) -> bool:
    """Generiert Tipps für den Tag nur einmal (wenn DB leer). Gibt True zurück."""
    try:
        if database.get_predictions_by_date(_date):
            return True
        match_analyzer.generate_top_tips()
        return True
    except Exception as exc:
        log.error("_ensure_today_tips_generated fehlgeschlagen: %s", exc)
        return False


def load_today_tips() -> list[dict]:
    """Lädt heutige Tipps immer frisch aus der DB (kein Stale-Cache der Tipps selbst)."""
    heute = datetime.now().strftime("%Y-%m-%d")
    _ensure_today_tips_generated(heute)
    try:
        return database.get_predictions_by_date(heute)
    except Exception as exc:
        log.error("load_today_tips fehlgeschlagen: %s", exc)
        return []


@st.cache_data(ttl=1800, show_spinner=False)
def run_auto_settle(_cache_key: str) -> int:
    """Rechnet offene Tipps automatisch ab (höchstens alle 30 Min pro Server)."""
    try:
        return match_analyzer.auto_settle()
    except Exception as exc:  # bewusst breit: UI darf nie crashen
        log.error("run_auto_settle fehlgeschlagen: %s", exc)
        return 0


@st.cache_data(ttl=900, show_spinner=False)
def load_api_quota(_cache_key: str) -> dict:
    """Lädt den API-Kontingent-Status (alle 15 Min frisch). Gibt ein dict zurück."""
    try:
        return data_fetcher.get_api_quota_status()
    except Exception as exc:  # bewusst breit: UI darf nie crashen
        log.error("load_api_quota fehlgeschlagen: %s", exc)
        return {}


def _render_api_quota_sidebar() -> None:
    """Zeigt das verbleibende API-Kontingent beider Anbieter in der Sidebar. Gibt None zurück."""
    status = load_api_quota(datetime.now().strftime("%Y-%m-%d-%H-%M")[:15])
    if not status:
        return None

    st.sidebar.divider()
    st.sidebar.markdown("**📡 API-Kontingent**")

    odds = status.get("odds", {})
    rem = odds.get("remaining")
    total = odds.get("total")
    if rem is not None:
        anteil = (rem / total) if total else None
        zusatz = f" / {total:,}".replace(",", ".") if total else ""
        st.sidebar.progress(min(1.0, anteil) if anteil is not None else 1.0,
                            text=f"The Odds API: {rem:,}".replace(",", ".") + zusatz + " übrig")
    else:
        st.sidebar.caption("The Odds API: Kontingent unbekannt")

    tennis = status.get("tennis", {})
    used = tennis.get("used", 0)
    limit = tennis.get("limit")
    t_rem = tennis.get("remaining")
    if limit and t_rem is not None:
        st.sidebar.progress(min(1.0, t_rem / limit),
                            text=f"API-Tennis: {t_rem:,}".replace(",", ".") + f" / {limit:,}".replace(",", ".") + " übrig")
    else:
        st.sidebar.caption(f"API-Tennis: {used:,}".replace(",", ".") + " Aufrufe diesen Monat (Testzugang – kein Limit bekannt)")
    st.sidebar.caption("Zähler aktualisiert sich alle 15 Min.")
    return None


def status_badge(status: str) -> str:
    """Gibt das passende Status-Emoji-Label zurück."""
    return {"gewonnen": "✅ Gewonnen", "verloren": "❌ Verloren"}.get(status, "🟡 Offen")


@st.cache_data(ttl=3600, show_spinner=False)
def load_player_context(_cache_key: str, player_names: tuple[str, ...]) -> dict:
    """Lädt Rankings + Match-Historie für die Anzeige auf den Tipp-Karten."""
    try:
        rankings = data_fetcher.fetch_rankings()
        histories = data_fetcher.build_match_histories_with_supplement(set(player_names))
        return {"rankings": rankings, "histories": histories}
    except Exception as exc:
        log.error("load_player_context fehlgeschlagen: %s", exc)
        return {"rankings": {}, "histories": {}}


def _form_strip_html(hist: list[dict]) -> str:
    """Letzte 10 Spiele als W/L-Kette (neuestes Ergebnis links)."""
    letzte = hist[: config.FORM_LETZTE_N]
    if not letzte:
        return "<span class='form-empty'>Keine Form-Daten</span>"
    chars = []
    for m in letzte:
        cls = "form-w" if m.get("won") else "form-l"
        letter = "W" if m.get("won") else "L"
        chars.append(f"<span class='form-char {cls}'>{letter}</span>")
    return f"<span class='form-strip'>{''.join(chars)}</span>"


def _player_stats_html(name: str, rankings: dict, histories: dict) -> str:
    """Baut Rank + W/L-Form der letzten 10 Spiele unter dem Spielernamen."""
    rank = lookup_ranking(name, rankings)
    rank_html = f"<span class='rank'>#{rank}</span>" if rank else "<span class='rank'>#—</span>"
    hist = histories.get(name, [])
    form_html = _form_strip_html(hist)
    return f"<div class='pstat'>{rank_html}<span>·</span>{form_html}</div>"


# --------------------------------------------------------------------------- #
# SEITE 1: Heutige Tipps
# --------------------------------------------------------------------------- #
def render_tips_page() -> None:
    """Rendert die Seite mit den heutigen Tipp-Cards. Gibt None zurück."""
    heute = datetime.now().strftime("%d.%m.%Y")
    st.markdown(
        "<div class='tg-header'><div class='tg-brand'><div>"
        "<div class='tg-logo'>TENNIS<span class='accent'>HYPE</span></div>"
        f"<div class='tg-sub'>Daily Value Picks · {heute}</div></div></div>"
        "<div class='tg-live'><span class='tg-dot'></span>Live</div></div>",
        unsafe_allow_html=True,
    )

    col_btn, col_sp = st.columns([1, 3])
    with col_btn:
        if st.button("🔄 Tipps neu laden", use_container_width=True):
            _ensure_today_tips_generated.clear()
            load_player_context.clear()
            st.rerun()

    with st.spinner("Lade heutige Tipps …"):
        tips = load_today_tips()

    gesamt_einsatz = len(tips) * config.EINSATZ
    offene = sum(1 for t in tips if t["status"] == "offen")
    eingetragen = sum(1 for t in tips if t["status"] != "offen")
    moegl_gewinn = sum(
        (t["betano_odds"] * config.EINSATZ - config.EINSATZ)
        for t in tips if t.get("betano_odds")
    )
    st.markdown(
        _tiles_html([
            {"lbl": "Aktive Picks", "val": len(tips), "tone": "green"},
            {"lbl": "Einsatz gesamt", "val": f"{gesamt_einsatz:.0f} €"},
            {"lbl": "Möglicher Gewinn", "val": f"+{moegl_gewinn:.2f} €", "tone": "green"},
            {"lbl": "Offen", "val": offene, "tone": "yellow"},
            {"lbl": "Eingetragen", "val": eingetragen},
        ]),
        unsafe_allow_html=True,
    )

    if not tips:
        st.markdown(
            "<div class='tg-empty'><div class='big'>📉</div>"
            "<div class='h'>Keine Picks über der Schwelle</div>"
            f"<div>Aktuell erreicht kein Match die Mindest-Konfidenz von {config.MIN_KONFIDENZ:.0%}. "
            "Es wird bewusst nicht aufgefüllt – Qualität vor Quantität.</div></div>",
            unsafe_allow_html=True,
        )
        return None

    st.markdown("<div class='tg-section'>Heutige Positionen</div>", unsafe_allow_html=True)

    date_str = datetime.now().strftime("%Y-%m-%d")
    in_combis = database.get_prediction_ids_in_combis(date_str)

    spieler = tuple(sorted({p for t in tips for p in (t["player1"], t["player2"])}))
    ctx = load_player_context(
        f"{date_str}-{config.FIXTURES_HISTORY_CACHE_VERSION}",
        spieler,
    )
    rankings = ctx.get("rankings", {})
    histories = ctx.get("histories", {})

    _render_combi_section(tips, date_str, in_combis)

    for t in tips:
        pid = t.get("id")
        col_pick, col_card = st.columns([0.35, 12], gap="small")
        with col_pick:
            if pid in in_combis:
                st.markdown("<div style='padding-top:18px;font-size:18px' title='In Kombi'>🔗</div>",
                            unsafe_allow_html=True)
            else:
                st.checkbox("Kombi", key=f"combi_pick_{pid}", label_visibility="collapsed")
        with col_card:
            badge = " <span class='chip' style='color:var(--yellow)'>Kombi-Leg</span>" if pid in in_combis else ""
            st.markdown(_tip_card_html(t, rankings, histories, combi_badge=badge), unsafe_allow_html=True)
    return None


def _render_combi_section(tips: list[dict], date_str: str, in_combis: set[int]) -> None:
    """Zeigt bestehende Kombis und Builder zum Anlegen neuer Kombiwetten."""
    combis = database.get_combis_by_date(date_str)
    selectable = [t for t in tips if t.get("id") not in in_combis]

    st.markdown("<div class='tg-section'>Kombiwetten</div>", unsafe_allow_html=True)

    if combis:
        for c in combis:
            st.markdown(_combi_card_html(c), unsafe_allow_html=True)
    else:
        st.caption("Noch keine Kombi für heute.")

    selected = [
        t for t in selectable
        if st.session_state.get(f"combi_pick_{t.get('id')}", False)
    ]

    combi_stake = st.number_input(
        "Einsatz Kombi (€)",
        min_value=1.0,
        max_value=10000.0,
        value=float(config.COMBI_EINSATZ_DEFAULT),
        step=1.0,
        key="combi_stake_input",
    )

    if selected:
        combined = combi.combined_odds(selected)
        payout = combi.combi_payout(combined, combi_stake)
        legs_txt = " · ".join(html.escape(t.get("tip") or "?") for t in selected)
        st.markdown(
            f"<div class='combi-builder'>"
            f"<b>{len(selected)} Legs ausgewählt</b> · Kombi-Quote <span class='odds'>{combined:.2f}</span> "
            f"· Einsatz <b>{combi_stake:.2f} €</b> · Gewinn: <span style='color:var(--green)'>+{payout:.2f} €</span>"
            f"<div class='legs'>{legs_txt}</div></div>",
            unsafe_allow_html=True,
        )

    col_a, col_b = st.columns([1, 3])
    with col_a:
        if st.button("🎰 Kombi spielen", use_container_width=True, disabled=len(selected) < config.COMBI_MIN_LEGS):
            ok, msg = database.create_combi([int(t["id"]) for t in selected], date_str, einsatz=combi_stake)
            if ok:
                database.update_bankroll_history()
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)
    with col_b:
        if selected:
            st.caption(
                f"Mindestens {config.COMBI_MIN_LEGS} Legs · Einsatz frei wählbar · "
                "Legs in Kombis zählen nicht als Einzelwette im Tracking."
            )
    return None


def _combi_option_label(c: dict) -> str:
    """Anzeigetext für Kombi-Auswahl in Dropdowns."""
    legs = c.get("legs") or []
    tips = " + ".join(leg.get("tip") or "?" for leg in legs)
    return f"{c.get('date')} | {c.get('einsatz', config.COMBI_EINSATZ_DEFAULT):.0f} € · {len(legs)} Legs @ {c.get('combined_odds', 0):.2f} ({tips})"


def _render_combi_manual_fallback(combis: list[dict] | None = None) -> None:
    """Manuelles Eintragen oder Zurücksetzen offener Kombiwetten."""
    offene = [c for c in (combis or database.get_open_combis()) if c.get("status") == "offen"]
    if not offene:
        return None

    with st.expander(f"🎰 Kombi-Ergebnis manuell · {len(offene)} offen"):
        st.caption(
            "Setzt die ganze Kombiwette auf Gewonnen oder Verloren und synchronisiert alle Legs. "
            "Alternativ kannst du auch einzelne Legs im Einzel-Fallback unten abrechnen."
        )
        optionen = {_combi_option_label(c): int(c["id"]) for c in offene}
        auswahl = st.selectbox("Offene Kombi wählen", list(optionen.keys()), key="combi_manual_pick")
        ergebnis = st.radio("Ergebnis", ["Gewonnen", "Verloren"], horizontal=True, key="combi_manual_result")
        col_save, col_reset = st.columns(2)
        with col_save:
            if st.button("💾 Kombi speichern", use_container_width=True):
                cid = optionen[auswahl]
                ok = database.update_combi_result(cid, gewonnen=(ergebnis == "Gewonnen"))
                if ok:
                    database.update_bankroll_history()
                    st.success("Kombi-Ergebnis gespeichert.")
                    st.rerun()
                else:
                    st.error("Speichern fehlgeschlagen – siehe Log.")
        with col_reset:
            if st.button("↩️ Kombi zurücksetzen", use_container_width=True):
                cid = optionen[auswahl]
                if database.reset_combi_result(cid):
                    database.update_bankroll_history()
                    st.success("Kombi zurückgesetzt (alle Legs wieder offen).")
                    st.rerun()
                else:
                    st.error("Zurücksetzen fehlgeschlagen.")
    return None


def _combi_card_html(c: dict) -> str:
    """HTML für eine gespeicherte Kombiwette."""
    legs = c.get("legs") or []
    status = c.get("status") or "offen"
    pill = {"gewonnen": ("win", "Gewonnen"), "verloren": ("loss", "Verloren")}.get(
        status, ("open", "Offen")
    )
    legs_html = "<br>".join(
        f"{'✅' if l.get('status') == 'gewonnen' else '❌' if l.get('status') == 'verloren' else '⏳'} "
        f"{html.escape(l.get('tip') or '?')} "
        f"({html.escape(l.get('player1') or '')} vs {html.escape(l.get('player2') or '')}) "
        f"@ {l.get('betano_odds') or '—'}"
        for l in legs
    )
    pnl = c.get("gewinn_verlust") or 0
    pnl_html = ""
    if status != "offen":
        cls = "green" if pnl > 0 else "red"
        pnl_html = f" · <span style='color:var(--{cls})'>{pnl:+.2f} €</span>"

    return (
        f"<div class='combi-card'><div class='head'>"
        f"<span><b>Kombi</b> · {len(legs)} Legs · {float(c.get('einsatz') or config.COMBI_EINSATZ_DEFAULT):.2f} € · "
        f"Quote <span class='odds'>{c.get('combined_odds', 0):.2f}</span>"
        f"{pnl_html}</span>"
        f"<span class='pill {pill[0]}'>{pill[1]}</span></div>"
        f"<div class='legs'>{legs_html}</div></div>"
    )


def _tip_card_html(t: dict, rankings: dict, histories: dict, combi_badge: str = "") -> str:
    """Baut eine Tipp-Karte im Trading-Look. Gibt einen HTML-String zurück."""
    tip = t.get("tip", "")
    p1, p2 = t.get("player1", ""), t.get("player2", "")
    p1_cls = "win" if tip == p1 else "loser"
    p2_cls = "win" if tip == p2 else "loser"

    meta = [f"<span class='chip surface'>🎾 {html.escape(str(t.get('surface') or '—'))}</span>",
            f"<span class='chip'>🏟️ {html.escape(str(t.get('tournament') or '—'))}</span>"]
    if t.get("match_time"):
        meta.append(f"<span class='chip'>🕒 {html.escape(str(t['match_time']))}</span>")

    odds = t.get("betano_odds")
    if odds:
        quote_cell = f"<div class='cell'><div class='k'>Quote</div><div class='v price'>{odds:.2f}</div></div>"
        payout = odds * config.EINSATZ - config.EINSATZ
        payout_cell = f"<div class='cell'><div class='k'>Payout (10€)</div><div class='v green'>+{payout:.2f} €</div></div>"
    else:
        quote_cell = "<div class='cell'><div class='k'>Quote</div><div class='v warn'>⚠️ fehlt</div></div>"
        payout_cell = "<div class='cell'><div class='k'>Payout (10€)</div><div class='v'>—</div></div>"

    prob_calc = t.get("prob_calculated")
    prob_impl = t.get("prob_implied")
    if prob_calc is not None and prob_impl is not None:
        edge = (prob_calc - prob_impl) * 100
        edge_cls = "green" if edge >= 0 else "red"
        edge_cell = f"<div class='cell'><div class='k'>Edge</div><div class='v {edge_cls}'>{edge:+.0f}%</div></div>"
        conf_sub = f"Modell <b>{prob_calc:.0%}</b> · Buchmacher <b>{prob_impl:.0%}</b>"
    else:
        edge_cell = "<div class='cell'><div class='k'>Edge</div><div class='v'>—</div></div>"
        conf_sub = ""

    pill = {"gewonnen": ("win", "Gewonnen"), "verloren": ("loss", "Verloren")}.get(
        t.get("status"), ("open", "Offen")
    )
    konf = float(t.get("confidence") or 0.0)

    return (
        "<div class='tcard'>"
        "<div class='tcard-top'><div>"
        "<div class='matchup'>"
        f"<div class='player-line'><span class='{p1_cls}'>{html.escape(p1)}</span>"
        f"{_player_stats_html(p1, rankings, histories)}</div>"
        f"<span class='vs'>vs</span>"
        f"<div class='player-line'><span class='{p2_cls}'>{html.escape(p2)}</span>"
        f"{_player_stats_html(p2, rankings, histories)}</div>"
        "</div>"
        f"<div class='meta'>{''.join(meta)}{combi_badge}</div></div>"
        f"<span class='pill {pill[0]}'>{pill[1]}</span></div>"
        "<div class='tgrid'>"
        f"<div class='cell'><div class='k'>Tipp</div><div class='v tip'>{html.escape(tip)}</div></div>"
        f"{quote_cell}{edge_cell}{payout_cell}</div>"
        "<div class='confwrap'>"
        f"<div class='confhead'><span>Konfidenz <b>{konf:.0%}</b></span><span>{conf_sub}</span></div>"
        f"<div class='confbar'><div class='conffill' style='width:{min(100, konf*100):.0f}%'></div></div>"
        "</div></div>"
    )


# --------------------------------------------------------------------------- #
# SEITE 2: Tracking & Statistiken
# --------------------------------------------------------------------------- #
def render_tracking_page() -> None:
    """Rendert die Tracking-Seite inkl. Metrics, Tabelle und Charts. Gibt None zurück."""
    st.markdown(
        "<div class='tg-header'><div class='tg-brand'><div>"
        "<div class='tg-logo'>PORT<span class='accent'>FOLIO</span></div>"
        "<div class='tg-sub'>Tracking & Performance</div></div></div>"
        "<div class='tg-live'><span class='tg-dot'></span>Sync</div></div>",
        unsafe_allow_html=True,
    )

    alle_combis = database.get_all_combis()
    singles = database.get_single_predictions_for_tracking()
    settled_singles = [p for p in singles if p["status"] in ("gewonnen", "verloren")]
    settled_combis = [c for c in alle_combis if c["status"] in ("gewonnen", "verloren")]
    gewonnen_s = [p for p in settled_singles if p["status"] == "gewonnen"]
    gewonnen_c = [c for c in settled_combis if c["status"] == "gewonnen"]

    gesamt_einsatz = sum(float(p.get("einsatz") or config.EINSATZ) for p in singles) + sum(
        _combi_stake(c) for c in alle_combis
    )
    gesamt_pnl = sum(p["gewinn_verlust"] for p in settled_singles) + sum(
        c["gewinn_verlust"] for c in settled_combis
    )
    settled_einsatz = sum(float(p.get("einsatz") or config.EINSATZ) for p in settled_singles) + sum(
        _combi_stake(c) for c in settled_combis
    )
    settled_count = len(settled_singles) + len(settled_combis)
    gewonnen_count = len(gewonnen_s) + len(gewonnen_c)
    roi = (gesamt_pnl / settled_einsatz * 100) if settled_einsatz > 0 else 0.0
    trefferquote = (gewonnen_count / settled_count * 100) if settled_count else 0.0

    pnl_tone = "green" if gesamt_pnl > 0 else ("red" if gesamt_pnl < 0 else "")
    roi_tone = "green" if roi > 0 else ("red" if roi < 0 else "")
    st.markdown(
        _tiles_html([
            {"lbl": "Gesamteinsatz", "val": f"{gesamt_einsatz:.0f} €",
             "sub": f"{len(singles)} Einzel · {len(alle_combis)} Kombis"},
            {"lbl": "Gewinn / Verlust", "val": f"{gesamt_pnl:+.2f} €", "tone": pnl_tone},
            {"lbl": "ROI", "val": f"{roi:+.1f} %", "tone": roi_tone},
            {"lbl": "Trefferquote", "val": f"{trefferquote:.1f} %",
             "sub": f"{gewonnen_count}/{settled_count} settled"},
        ]),
        unsafe_allow_html=True,
    )

    _render_manual_fallback()
    _render_combi_manual_fallback(None)

    st.divider()
    _render_combi_table(alle_combis)
    st.divider()
    _render_table(singles)
    st.divider()
    settled_for_charts = settled_singles + [
        {"date": c["date"], "gewinn_verlust": c["gewinn_verlust"], "status": c["status"],
         "confidence": None, "tip": combi.format_legs_summary(c.get("legs") or [])}
        for c in settled_combis
    ]
    _render_charts(settled_for_charts)
    return None


def _render_combi_table(combis: list[dict]) -> None:
    """Tabelle aller Kombiwetten."""
    st.markdown("<div class='tg-section'>Kombiwetten</div>", unsafe_allow_html=True)
    if not combis:
        st.info("Noch keine Kombiwetten angelegt.")
        return None

    rows = []
    for c in combis:
        legs = c.get("legs") or []
        rows.append({
            "Datum": c.get("date"),
            "Legs": len(legs),
            "Spiele": combi.format_legs_summary(legs),
            "Quote": c.get("combined_odds"),
            "Einsatz €": _combi_stake(c),
            "G/V €": c.get("gewinn_verlust") or 0.0,
            "Status": status_badge(c.get("status") or "offen"),
        })
    df = pd.DataFrame(rows).sort_values("Datum", ascending=False)

    def _farbe(val: float) -> str:
        if val > 0:
            return "color: #0ecb81; font-weight: 700;"
        if val < 0:
            return "color: #f6465d; font-weight: 700;"
        return ""

    styler = df.style.map(_farbe, subset=["G/V €"]).format(
        {"Quote": "{:.2f}", "Einsatz €": "{:.0f}", "G/V €": "{:+.2f}"}, na_rep="—"
    )
    st.dataframe(styler, use_container_width=True, hide_index=True)
    return None


def _render_manual_fallback() -> None:
    """Eingeklappter Fallback, falls ein Ergebnis nicht automatisch abgerechnet wurde.

    Normalerweise erledigt die automatische Abrechnung alles; dies fängt nur
    seltene Fälle ab (z. B. abweichende Spieler-Schreibweise). Gibt None zurück.
    """
    offene = database.get_open_predictions()
    if not offene:
        return None

    with st.expander(f"🛠️ Ergebnis manuell eintragen (Fallback) · {len(offene)} offen"):
        st.caption(
            "Wird normalerweise automatisch erledigt. Nutze dies nur, wenn ein Tipp "
            "trotz beendetem Match offen bleibt (z. B. abweichende Schreibweise)."
        )
        optionen = {
            f"{p['date']} | {p['tip']} ({p['player1']} vs {p['player2']})": p["id"]
            for p in offene
        }
        auswahl = st.selectbox("Offenen Tipp wählen", list(optionen.keys()))
        ergebnis = st.radio("Ergebnis", ["Gewonnen", "Verloren"], horizontal=True)
        if st.button("💾 Ergebnis speichern"):
            pred_id = optionen[auswahl]
            ok = database.update_prediction_result(pred_id, gewonnen=(ergebnis == "Gewonnen"))
            if ok:
                database.update_bankroll_history()
                st.success("Ergebnis gespeichert.")
                st.rerun()
            else:
                st.error("Speichern fehlgeschlagen – siehe Log.")
    return None


def _render_table(alle: list[dict]) -> None:
    """Rendert die filter- und sortierbare Tabelle aller Tipps. Gibt None zurück."""
    st.markdown("<div class='tg-section'>Einzelwetten</div>", unsafe_allow_html=True)
    if not alle:
        st.info("Noch keine Tipps vorhanden.")
        return None

    st.markdown("<div class='tg-section' style='margin-bottom:4px'>Filter</div>", unsafe_allow_html=True)
    filter_wahl = st.selectbox(
        "Filter",
        ["Alle", "Nur Gewonnen", "Nur Verloren", "Nur Offen"],
        label_visibility="collapsed",
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
            return "color: #0ecb81; font-weight: 700;"
        if val < 0:
            return "color: #f6465d; font-weight: 700;"
        return ""

    styler = df.style.map(_farbe, subset=["G/V €"]).format(
        {"Quote": "{:.2f}", "Einsatz €": "{:.0f}", "G/V €": "{:+.2f}"}, na_rep="—"
    )
    st.dataframe(styler, use_container_width=True, hide_index=True)
    return None


def _render_charts(settled: list[dict]) -> None:
    """Rendert die drei Plotly-Charts mit Fallbacks. Gibt None zurück."""
    st.markdown("<div class='tg-section'>Auswertung</div>", unsafe_allow_html=True)
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
            fig.update_traces(line_color="#0ecb81", marker_color="#0ecb81")
            _style_fig(fig)
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
            fig.update_traces(marker_color="#0ecb81")
            _style_fig(fig)
            fig.update_layout(yaxis_range=[0, 100])
            st.plotly_chart(fig, use_container_width=True)

    # Chart 3: Durchschnittliche Konfidenz Gewonnen vs Verloren (nur Einzelwetten)
    st.markdown("**Durchschnittliche Konfidenz: Gewonnen vs Verloren (Einzelwetten)**")
    tmp = df.copy()
    tmp["confidence"] = pd.to_numeric(tmp["confidence"], errors="coerce") * 100
    tmp = tmp.dropna(subset=["confidence"])
    if tmp.empty:
        st.info("Noch keine abgerechneten Einzelwetten mit Konfidenz.")
    else:
        grp = tmp.groupby("status").agg(konfidenz=("confidence", "mean")).reset_index()
        grp["status"] = grp["status"].map({"gewonnen": "Gewonnen", "verloren": "Verloren"})
        fig = px.bar(grp, x="status", y="konfidenz", color="status",
                     color_discrete_map={"Gewonnen": "#0ecb81", "Verloren": "#f6465d"},
                     labels={"status": "", "konfidenz": "Ø Konfidenz %"})
        _style_fig(fig)
        fig.update_layout(showlegend=False)
        st.plotly_chart(fig, use_container_width=True)
    return None


def _style_fig(fig) -> None:
    """Wendet das dunkle Theme auf eine Plotly-Figur an. Gibt None zurück."""
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#eaecef",
        margin=dict(l=10, r=10, t=10, b=10),
        height=320,
    )
    fig.update_xaxes(gridcolor="#2b3139", zerolinecolor="#2b3139")
    fig.update_yaxes(gridcolor="#2b3139", zerolinecolor="#2b3139")
    return None


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    """Einstiegspunkt: initialisiert DB und steuert die Seiten-Navigation."""
    inject_css()
    if not check_login():
        return None

    database.init_db()

    # Ergebnisse automatisch abrechnen (gedrosselt auf alle 30 Minuten)
    neu_abgerechnet = run_auto_settle(datetime.now().strftime("%Y-%m-%d-%H"))
    if neu_abgerechnet:
        st.sidebar.success(f"✅ {neu_abgerechnet} Ergebnis(se) automatisch eingetragen")

    st.sidebar.title("🎾 Navigation")
    seite = st.sidebar.radio("Seite", ["Heutige Tipps", "Tracking & Statistiken"])

    if not config.ODDS_API_KEY or not config.API_TENNIS_KEY:
        st.sidebar.warning("⚠️ API-Keys fehlen in der .env-Datei.")

    _render_api_quota_sidebar()

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
