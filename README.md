# 🎾 Tennis Tipp-Analyse

Eine Python/Streamlit-Anwendung, die täglich automatisch die besten **max. 5 Tenniswetten des Tages**
auf Basis einer sportlichen Datenanalyse (Elo, Form, Oberfläche, H2H) berechnet – inklusive
Betano-Quoten und vollständigem Tracking mit fixem **10 €**-Einsatz pro Tipp.

## Features

- **4-Schichten-Algorithmus** (Elo 40 % · Form 30 % · Oberfläche 20 % · H2H 10 %) mit
  automatischer Gewichts-Renormierung bei fehlenden Daten.
- **Top-5-Auswahl**: nur Matches mit Konfidenz > 60 %, niemals aufgefüllt.
- **Betano-Quoten** über The Odds API (Fallback: Pinnacle), fehlende Quote wird als Badge angezeigt.
- **Tracking-Seite**: Gesamteinsatz, Gewinn/Verlust, ROI, Trefferquote + 3 Plotly-Charts.
- **SQLite**-Datenbank, tägliches API-Caching, Logging in `tennis_analyzer.log`.

## Setup in 5 Schritten

```bash
# 1. Ins Projektverzeichnis wechseln
cd tennis_analyzer

# 2. Virtuelle Umgebung anlegen & aktivieren
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate

# 3. Abhängigkeiten installieren
pip install -r requirements.txt

# 4. API-Keys eintragen: .env.example nach .env kopieren und Keys einsetzen
copy .env.example .env        # Windows  (macOS/Linux: cp .env.example .env)

# 5. App starten – öffnet http://localhost:8501
streamlit run app.py
```

## Projektstruktur

```
tennis_analyzer/
├── .env                # API-Keys (NICHT eingecheckt)
├── .env.example        # Vorlage für API-Keys
├── config.py           # Konfiguration + Gewichtungen + Logging
├── database.py         # SQLite-Handler (Context-Manager)
├── data_fetcher.py     # API-Abruf (The Odds API + API-Tennis) + Caching
├── elo_calculator.py   # Elo-Rating-Logik
├── match_analyzer.py   # Kern-Algorithmus, liefert Top-5
├── app.py              # Streamlit-App (2 Seiten)
├── requirements.txt
└── README.md
```

## Bedienung

1. **Seite „Heutige Tipps"**: zeigt die heutigen Top-Tipps als Cards (Quote, Konfidenz,
   Wahrscheinlichkeiten, möglicher Gewinn). Über „🔄 Tipps neu laden" wird der Tages-Cache verworfen.
2. **Seite „Tracking & Statistiken"**: Ergebnis je offenem Tipp in der Sidebar eintragen
   (Gewonnen/Verloren). Gewinn = `Quote × 10 − 10`, Verlust = `−10 €`. Tabelle + Charts
   aktualisieren sich automatisch.

## Mit Freunden teilen (Deployment auf Streamlit Community Cloud)

Die App enthält einen **Passwort-Login** (gemeinsames Passwort). So bringst du sie online:

1. **Code auf GitHub pushen** (privates Repo reicht):
   ```bash
   git add .
   git commit -m "Tennis Tipp-Analyse"
   git branch -M main
   git remote add origin https://github.com/<DEIN_USER>/tennis_analyzer.git
   git push -u origin main
   ```
   > Wichtig: `.env` und `.streamlit/secrets.toml` werden durch `.gitignore` **nicht** mitgepusht – deine Keys bleiben geheim.

2. **Auf https://share.streamlit.io** einloggen (mit GitHub) → **„New app"** → dein Repo + Branch `main` + Datei `app.py` wählen.

3. **Secrets eintragen**: in der App unter **„⋮ → Settings → Secrets"** den Inhalt aus
   `.streamlit/secrets.toml.example` einfügen und echte Werte setzen:
   ```toml
   ODDS_API_KEY = "479366c0f530b097bd98d94ea76779fb"
   API_TENNIS_KEY = "aab77147858f960cf56efcaeb8323e992763d2fdd77db0dfe7f9785f45e3be29"
   APP_PASSWORD = "dein-geheimes-passwort"
   DATABASE_URL = "postgresql://postgres:PASSWORT@db.xxxxx.supabase.co:5432/postgres"
   ```

4. **Deploy** klicken → du bekommst eine URL wie `https://<name>.streamlit.app`.

5. **Link + Passwort** an deine Freunde geben. Fertig.

## Dauerhafte Datenbank (Supabase – kostenlos)

Ohne `DATABASE_URL` nutzt die App eine lokale SQLite-Datei. Auf der Streamlit Cloud ist diese
Datei aber **flüchtig** und wird bei jedem Neustart gelöscht. Für dauerhaftes Tracking richtest
du ein kostenloses Postgres bei Supabase ein:

1. Auf https://supabase.com kostenlos registrieren → **„New project"** anlegen
   (Projektname + DB-Passwort wählen, Region möglichst nah).
2. Im Projekt: **Settings → Database → Connection string → URI** kopieren
   (Format: `postgresql://postgres:[PASSWORT]@db.<ref>.supabase.co:5432/postgres`).
   Das `[PASSWORT]` durch dein gewähltes DB-Passwort ersetzen.
3. Diese URL als `DATABASE_URL` in die Streamlit-Secrets (Schritt 3 oben) eintragen.
4. Tabellen werden beim ersten Start **automatisch** angelegt – nichts weiter zu tun.

Danach bleiben alle Tipps, Ergebnisse und Statistiken dauerhaft erhalten – egal wie oft die
App neu startet. Lokal kannst du dieselbe `DATABASE_URL` in deine `.env` setzen, um direkt mit
der Cloud-Datenbank zu arbeiten (sonst bleibt es bei der lokalen SQLite-Datei).

## Datenquellen

| Quelle | Zweck | Endpunkt |
|--------|-------|----------|
| [The Odds API](https://the-odds-api.com) | Matches + Quoten (h2h) | `/v4/sports/{sport_key}/odds` |
| [API-Tennis](https://api-tennis.com) | Rankings (Elo-Proxy), H2H | `get_standings`, `get_H2H` |

> Hinweis: The Odds API besitzt **keinen** festen Sport-Key `tennis`. Die App ermittelt
> aktive Tennis-Turniere dynamisch über `/v4/sports` und fragt diese einzeln ab.

## Bekannte Einschränkungen

- **Form (Schicht 2)** und **Oberflächen-Statistik (Schicht 3)** benötigen eine vollständige
  Match-Historie pro Spieler. Die mitgelieferte Anbindung liefert diese nicht garantiert –
  fehlen die Daten, neutralisieren sich diese Schichten und ihr Gewicht wird auf Elo/H2H
  verteilt. Die Tipps stützen sich dann primär auf das ranking-basierte Elo.
- Spielernamen werden zwischen beiden APIs über Normalisierung abgeglichen; abweichende
  Schreibweisen können einzelne Zuordnungen verhindern.
- Die Elo-Werte starten ranking-basiert und verfeinern sich erst mit eingetragenen Ergebnissen.
- Free-Tier-API-Limits können dazu führen, dass an manchen Tagen keine Quoten verfügbar sind –
  die App zeigt dann eine klare Meldung statt eines Fehlers.

## Disclaimer

Diese Anwendung dient ausschließlich zu Analyse- und Lernzwecken. Sportwetten sind mit
finanziellem Risiko verbunden. Keine Gewähr für Gewinne.
