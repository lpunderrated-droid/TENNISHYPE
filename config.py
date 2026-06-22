"""Zentrale Konfiguration: API-Keys, Gewichtungen, Algorithmus-Parameter, Logging.

Alle Konstanten der Anwendung werden hier gebündelt, damit es nur eine
einzige Quelle der Wahrheit gibt. API-Keys werden ausschließlich aus der
.env-Datei gelesen (python-dotenv), niemals hart kodiert.
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# .env laden (liegt im Projekt-Stammverzeichnis)
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def get_secret(name: str, default: str = "") -> str:
    """Liest ein Geheimnis – zuerst aus Streamlit-Secrets (Cloud), dann aus der Umgebung/.env.

    So funktioniert dieselbe Code-Basis lokal (.env) und auf Streamlit Community
    Cloud (st.secrets), ohne Keys hart zu kodieren.
    """
    try:
        import streamlit as st  # nur verfügbar, wenn unter Streamlit ausgeführt

        if hasattr(st, "secrets") and name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        # Kein Streamlit-Kontext / keine secrets.toml -> auf Umgebung zurückfallen
        pass
    return os.getenv(name, default)


# --- API-Keys (aus Streamlit-Secrets oder .env) ---
ODDS_API_KEY = get_secret("ODDS_API_KEY", "")
API_TENNIS_KEY = get_secret("API_TENNIS_KEY", "")

# --- App-Login (gemeinsames Passwort) ---
# Leer = kein Login (z. B. lokal); auf der Cloud sollte ein Passwort gesetzt werden.
APP_PASSWORD = get_secret("APP_PASSWORD", "")

# --- API-Endpunkte ---
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
API_TENNIS_BASE = "https://api.api-tennis.com/tennis/"

# Bevorzugte Buchmacher (Reihenfolge = Priorität). Betano zuerst, sonst Pinnacle.
PREFERRED_BOOKMAKERS = ["betano", "pinnacle"]

# --- Datenbank ---
# DATABASE_URL leer  -> lokale SQLite-Datei (Daten bleiben auf dem PC erhalten)
# DATABASE_URL gesetzt -> dauerhaftes Postgres (z. B. Supabase) für die Cloud
DATABASE_URL = get_secret("DATABASE_URL", "")


def get_database_url() -> str:
    """Liefert die finale SQLAlchemy-Verbindungs-URL.

    - Ohne DATABASE_URL: lokale SQLite-Datei.
    - Mit Postgres-URL: normalisiert auf den psycopg2-Treiber und erzwingt SSL,
      damit z. B. Supabase out-of-the-box funktioniert.
    """
    raw = (DATABASE_URL or "").strip()
    if not raw:
        return f"sqlite:///{DB_PATH}"

    # Treiber vereinheitlichen (Supabase liefert meist "postgresql://...")
    if raw.startswith("postgres://"):
        raw = "postgresql+psycopg2://" + raw[len("postgres://"):]
    elif raw.startswith("postgresql://"):
        raw = "postgresql+psycopg2://" + raw[len("postgresql://"):]

    # SSL für Postgres erzwingen, falls nicht bereits angegeben
    if raw.startswith("postgresql+psycopg2://") and "sslmode=" not in raw:
        raw += ("&" if "?" in raw else "?") + "sslmode=require"
    return raw


# --- Pfade ---
DB_PATH = BASE_DIR / "tennis_analyzer.db"
LOG_PATH = BASE_DIR / "tennis_analyzer.log"
CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)

# --- Wett-Parameter ---
EINSATZ = 10.00  # Fixer Einsatz pro Tipp / Kombi – nicht konfigurierbar
COMBI_MIN_LEGS = 2
COMBI_MAX_LEGS = 5  # max. wie MAX_TIPPS_PRO_TAG
MAX_TIPPS_PRO_TAG = 5
MIN_KONFIDENZ = 0.60  # Nur Tipps mit Konfidenz > 60 %

# --- Algorithmus-Gewichtungen (Summe = 1.0 = 100 %) ---
WEIGHT_ELO = 0.40
WEIGHT_FORM = 0.30
WEIGHT_SURFACE = 0.20
WEIGHT_H2H = 0.10

# Validierung: Gewichte müssen exakt 100 % ergeben
assert abs((WEIGHT_ELO + WEIGHT_FORM + WEIGHT_SURFACE + WEIGHT_H2H) - 1.0) < 1e-9, (
    "Die vier Schicht-Gewichte müssen zusammen 1.0 (100 %) ergeben."
)

# --- Elo-Parameter ---
ELO_K_FAKTOR = 32
ELO_START = 1500.0  # Basis-Elo für unbekannte Spieler

# --- Form-Parameter ---
FORM_DECAY = 0.9          # exponentieller Decay-Faktor (0.9^n)
FORM_LETZTE_N = 10        # letzte 10 Matches betrachten
FATIGUE_PRO_MATCH = 0.02  # -2 % pro Match in den letzten 7 Tagen
FATIGUE_MAX = 0.10        # maximaler Müdigkeits-Penalty -10 %

# --- Oberflächen-Parameter ---
SURFACE_BONUS_SCHWELLE = 0.60  # ab >60 % Siegquote auf der Fläche
SURFACE_BONUS = 0.05           # +5 % Bonus

# --- H2H-Parameter ---
H2H_LETZTE_N = 5      # letzte 5 Direktvergleiche
H2H_MIN_MATCHES = 2   # nur werten ab 2 vorhandenen H2H-Matches
H2H_DECAY = 0.9

# --- Form / Match-Historie ---
FIXTURES_HISTORY_DAYS = 120  # Bulk-Fixtures (ATP/WTA/Challenger) für Form + Oberfläche
FIXTURES_PLAYER_KEY_HISTORY_DAYS = 180  # player_key-Abruf tiefer (mehr Spiele für Top-Spieler)
FIXTURES_CHALLENGER_HISTORY_DAYS = 60  # Challenger in einem Abruf (90+ Tage → HTTP 500)
# API-Tennis event_type_key: ATP/WTA in einem Abruf; Challenger separat (60 Tage)
FIXTURES_EVENT_TYPES_TOUR = ("265", "266")  # Atp Singles, Wta Singles
FIXTURES_EVENT_TYPES_CHALLENGER = ("281", "272")  # Challenger Men/Women Singles
FIXTURES_HISTORY_CACHE_VERSION = "v5"  # Cache-Bust bei Änderungen an der Fixture-Logik

# --- Netzwerk ---
API_TIMEOUT = 15      # Sekunden pro Request
API_MAX_RETRIES = 3   # maximal 3 Retry-Versuche

# --- API-Kontingente (für Anzeige des verbleibenden Monatslimits) ---
# The Odds API meldet das Restkontingent selbst im Header (kein Limit nötig).
# API-Tennis liefert KEINE Quota-Info – daher zählen wir die Aufrufe selbst.
# Das Monatslimit deines API-Tennis-Plans hier (oder per .env/Secret) setzen,
# damit "verbleibend" berechnet werden kann. 0 = unbekannt (zeigt nur genutzt).
try:
    API_TENNIS_MONTHLY_LIMIT = int(get_secret("API_TENNIS_MONTHLY_LIMIT", "0") or "0")
except (TypeError, ValueError):
    API_TENNIS_MONTHLY_LIMIT = 0

# --- Oberflächen-Erkennung anhand Turniernamen ---
GRASS_KEYWORDS = ["wimbledon", "halle", "queen", "eastbourne", "stuttgart", "mallorca", "newport", "s-hertogenbosch"]
CLAY_KEYWORDS = ["roland garros", "french open", "monte", "madrid", "rome", "roma", "barcelona", "hamburg",
                 "kitzbuhel", "gstaad", "umag", "bastad", "estoril", "munich", "houston", "bucharest"]


def setup_logging() -> logging.Logger:
    """Initialisiert das Logging (Datei + Konsole) und gibt den Root-Logger zurück."""
    logger = logging.getLogger("tennis_analyzer")
    if logger.handlers:
        # Bereits konfiguriert – verhindert doppelte Handler bei Streamlit-Reruns
        return logger

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    logger.propagate = False
    return logger


# Logger direkt beim Import bereitstellen
log = setup_logging()
