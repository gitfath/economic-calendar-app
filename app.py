#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Application Streamlit + Bot Telegram (modes batch, continu)
Annonces économiques US du jour, cache, notifications, rapport complet toutes les 30 min.
"""

import os
import sys
import json
import time
import requests
from datetime import datetime, timezone
from typing import List, Dict
from pathlib import Path

# ============================================================
# CHARGEMENT SIMPLE DU .env (comme dans bot.py)
# ============================================================
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ .env chargé (load_dotenv)")
except ImportError:
    print("ℹ️ dotenv non installé, utilisation des variables d'environnement")

# Lire les variables
PARSEBOT_API_KEY = os.getenv("PARSEBOT_API_KEY", "")
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "VOTRE_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "VOTRE_CHAT_ID")
REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL", "1800"))

# Vérification
if TELEGRAM_BOT_TOKEN == "VOTRE_TOKEN" or TELEGRAM_CHAT_ID == "VOTRE_CHAT_ID":
    print("⚠️ Tokens Telegram non configurés.")
else:
    print("✅ Tokens Telegram configurés.")

# --- Import conditionnel de streamlit et pandas ---
if "--batch" not in sys.argv and "--continuous" not in sys.argv and "action" not in os.environ.get("QUERY_STRING", ""):
    import streamlit as st
    import pandas as pd
else:
    class DummySt:
        def cache_data(self, *args, **kwargs): return lambda f: f
        def error(self, msg): print(msg)
        def warning(self, msg): print(msg)
        def title(self, msg): print(msg)
        def caption(self, msg): print(msg)
        def multiselect(self, *args, **kwargs): return []
        def dataframe(self, *args, **kwargs): pass
        def subheader(self, msg): print(msg)
        def expander(self, msg): return self
        def columns(self, n): return [self, self]
        def markdown(self, msg): print(msg)
        def metric(self, *args, **kwargs): pass
        def info(self, msg): print(msg)
        def button(self, msg): return False
        def stop(self): sys.exit(0)
        def set_page_config(self, *args, **kwargs): pass
        def rerun(self): pass
        def cache_data_clear(self): pass
        def query_params(self): return {}
        @property
        def secrets(self):
            class Secrets:
                def get(self, key, default=None):
                    return os.getenv(key, default)
                def __getitem__(self, key):
                    return os.getenv(key, "")
            return Secrets()
    st = DummySt()
    pd = None

# ============================================================
# Constantes et fonctions (inchangées)
# ============================================================
CACHE_FILE = "events_cache.json"
NOTIFIED_FILE = "notified_events.json"

def get_today_utc() -> tuple:
    now_utc = datetime.now(timezone.utc)
    return now_utc.strftime("%Y-%m-%d"), now_utc.strftime("%d/%m/%Y")

TODAY, TODAY_DISPLAY = get_today_utc()

def load_json(filename: str, default: List = None) -> List:
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else []

def save_json(filename: str, data: List):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ============================================================
# Récupération des événements (Parse.bot + fallback)
# ============================================================
def get_parsebot_events_rest() -> List[Dict]:
    if not PARSEBOT_API_KEY:
        return []
    url = "https://api.parse.bot/scraper/5d47f2a9-c902-4fe9-ac2d-3e00c66f7b7a/get_daily_events"
    headers = {"X-API-Key": PARSEBOT_API_KEY}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"❌ Parse.bot API REST échec : {e}")
        return []
    if data.get("status") != "success":
        return []
    events_data = data.get("data", {}).get("events", [])
    us_events = []
    exclude_keywords = ["ECB", "German", "Lagarde", "Trump"]
    for item in events_data:
        currency = item.get("currency", "").upper()
        country = item.get("country", "").lower()
        if currency not in ["USD", "US"] and "united states" not in country:
            continue
        event_name = item.get("event", item.get("event_long", "Unknown"))
        if any(kw in event_name for kw in exclude_keywords):
            continue
        time_raw = item.get("time", "N/A")
        if "T" in time_raw:
            try:
                dt = datetime.fromisoformat(time_raw.replace("Z", "+00:00"))
                time_display = dt.strftime("%H:%M UTC")
            except:
                time_display = time_raw
        else:
            time_display = time_raw
        us_events.append({
            "event_id": item.get("event_id"),
            "time": time_display,
            "event": event_name,
            "country": "US",
            "impact": item.get("importance", "low").lower(),
            "actual": item.get("actual", "N/A"),
            "forecast": item.get("forecast", "N/A"),
            "previous": item.get("previous", "N/A"),
            "source": "Parse.bot"
        })
    return us_events

def get_forexfactory_json() -> List[Dict]:
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"❌ Erreur flux JSON : {e}")
        return []
    events = []
    today_utc = datetime.now(timezone.utc).date()
    exclude_keywords = ["ECB", "German", "Lagarde", "Trump"]
    for item in data:
        date_str = item.get("date", "")
        if not date_str:
            continue
        try:
            if "T" in date_str:
                event_date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
            else:
                event_date = datetime.fromisoformat(date_str).date()
        except:
            continue
        if event_date != today_utc:
            continue
        currency = item.get("currency", "").upper()
        if currency not in ["USD", "US"]:
            continue
        title = item.get("title", "")
        if any(kw in title for kw in exclude_keywords):
            continue
        events.append({
            "event_id": None,
            "time": item.get("time", "N/A"),
            "event": title,
            "country": "US",
            "impact": item.get("impact", "low").lower(),
            "actual": item.get("actual", "N/A"),
            "forecast": item.get("forecast", "N/A"),
            "previous": item.get("previous", "N/A"),
            "source": "ForexFactory"
        })
    return events

def get_events() -> List[Dict]:
    events = get_parsebot_events_rest()
    ff_events = get_forexfactory_json()
    existing_keys = {(e["event"], e["time"]) for e in events}
    for ev in ff_events:
        if (ev["event"], ev["time"]) not in existing_keys:
            events.append(ev)
    return events

# ============================================================
# FRED API
# ============================================================
FRED_SERIES = {
    "CPIAUCSL": {"name": "CPI (Inflation)"},
    "UNRATE": {"name": "Taux de chômage"},
    "PAYEMS": {"name": "NFP (Emplois)"},
    "DGS10": {"name": "Taux 10 ans"},
    "FEDFUNDS": {"name": "Taux Fed Funds"},
}

def get_fred_snapshot() -> Dict:
    if not FRED_API_KEY:
        print("ℹ️ FRED_API_KEY non définie. Snapshot FRED désactivé.")
        return {}
    result = {}
    for series_id, info in FRED_SERIES.items():
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": series_id,
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 1
        }
        try:
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("observations"):
                obs = data["observations"][0]
                result[series_id] = {
                    "name": info["name"],
                    "value": obs.get("value", "N/A"),
                    "date": obs.get("date", "N/A")
                }
            else:
                print(f"⚠️ FRED {series_id}: aucune observation trouvée")
        except Exception as e:
            print(f"❌ FRED {series_id} échec : {e}")
    return result

# ============================================================
# Base de connaissances (INDICATOR_KNOWLEDGE) - version réduite pour l'exemple
# ============================================================
INDICATOR_KNOWLEDGE = {
    "FOMC": {
        "category": "Monétaire",
        "description": "Décision de taux de la Fed. Le plus important pour le Dollar.",
        "thresholds": {
            "Hawkish": "Dollar très haussier",
            "Dovish": "Dollar très baissier"
        },
        "strategy": "Attendre la conférence de presse."
    },
    "CPI": {
        "category": "Inflation",
        "description": "Indice des prix à la consommation.",
        "thresholds": {
            "Core CPI > 0.4%": "Dollar très haussier",
            "Core CPI < 0.1%": "Dollar baissier"
        },
        "strategy": "Regarder le Core CPI."
    },
    # ... (ajoutez tous vos indicateurs ici, le dictionnaire est long mais vous l'avez déjà)
}

# ============================================================
# Fonctions d'analyse (inchangées)
# ============================================================
def classify_event(event_name: str) -> Dict:
    for key, knowledge in INDICATOR_KNOWLEDGE.items():
        if key.lower() in event_name.lower():
            return knowledge
    return {"category": "Autre", "description": "", "thresholds": {}, "strategy": ""}

def generate_analysis(event: Dict) -> Dict:
    event_name = event.get("event", "")
    knowledge = classify_event(event_name)
    interpretation = "\n".join([f"- {k}: {v}" for k, v in knowledge.get("thresholds", {}).items()])
    return {
        "event": event_name,
        "time": event.get("time", ""),
        "country": event.get("country", "US"),
        "impact": event.get("impact", "low").upper(),
        "category": knowledge.get("category", ""),
        "description": knowledge.get("description", ""),
        "forecast": event.get("forecast", "N/A"),
        "previous": event.get("previous", "N/A"),
        "strategy": knowledge.get("strategy", ""),
        "interpretation": interpretation
    }

def get_dollar_impact(event: Dict) -> str:
    # Simplifié pour l'exemple
    return "🔍 Impact non déterminé"

# ============================================================
# Notifications Telegram (simplifiée)
# ============================================================
def send_telegram_message(message: str) -> bool:
    token = TELEGRAM_BOT_TOKEN
    chat_id = TELEGRAM_CHAT_ID
    if token == "VOTRE_TOKEN" or chat_id == "VOTRE_CHAT_ID":
        print("⚠️ Token ou Chat ID non configurés.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = [message[i:i+4000] for i in range(0, len(message), 4000)]
    success = True
    for idx, chunk in enumerate(chunks):
        try:
            resp = requests.post(url, json={"chat_id": chat_id, "text": chunk}, timeout=10)
            resp.raise_for_status()
            print(f"✅ Chunk {idx+1}/{len(chunks)} envoyé")
        except Exception as e:
            print(f"❌ Erreur envoi chunk {idx+1}: {e}")
            success = False
    return success

def check_and_notify_updates(new_events: List[Dict], send_immediate: bool = True):
    # À implémenter selon vos besoins
    pass

# ============================================================
# Génération du rapport
# ============================================================
def format_fred_snapshot(fred_data: Dict) -> str:
    if not fred_data:
        return "   📭 Données FRED non disponibles"
    lines = []
    for series_id, info in fred_data.items():
        value = info.get("value", "N/A")
        name = info.get("name", series_id)
        date = info.get("date", "N/A")
        lines.append(f"   • {name}: {value} (au {date})")
    return "\n".join(lines)

def generate_report(events: List[Dict], fred_data: Dict) -> str:
    lines = []
    lines.append("=" * 80)
    lines.append(f"📊 ANNONCES ÉCONOMIQUES DU {TODAY_DISPLAY} (UTC)")
    lines.append("=" * 80)
    lines.append("")

    if fred_data:
        lines.append("📈 SNAPSHOT DES INDICATEURS (valeurs réelles FRED) :")
        lines.append(format_fred_snapshot(fred_data))
        lines.append("")
    else:
        lines.append("⚠️ Snapshot FRED indisponible.")
        lines.append("")

    if not events:
        lines.append("📭 Aucune annonce économique US prévue aujourd'hui.")
        lines.append("")
        lines.append("💡 Conseil : Consultez https://www.investing.com/economic-calendar/ pour vérifier.")
    else:
        lines.append("📋 ANNONCES DU JOUR (USD) :")
        lines.append("")
        impact_order = {"high": 0, "medium": 1, "low": 2}
        events_sorted = sorted(events, key=lambda e: impact_order.get(e.get("impact", "low"), 3))

        for event in events_sorted:
            a = generate_analysis(event)
            lines.append(f"🔹 {a['event']} ({a['country']})")
            lines.append(f"   ⏰ {a['time']}  |  Impact: {a['impact']}  |  Catégorie: {a['category']}")
            lines.append(f"   📖 {a['description']}")
            lines.append(f"   📊 Prévision: {a['forecast']}  |  Précédent: {a['previous']}")
            lines.append(f"   🔍 INTERPRÉTATION :")
            for line in a['interpretation'].split('\n'):
                lines.append(f"      {line}")
            lines.append(f"   🎯 STRATÉGIE : {a['strategy']}")
            lines.append(f"   🔗 Calendrier Investing.com : https://www.investing.com/economic-calendar/")
            lines.append("-" * 80)
            lines.append("")

        sources = set(e.get('source', 'Inconnue') for e in events)
        lines.append(f"📌 Source : {', '.join(sources)}")
        lines.append("")

    lines.append("")
    lines.append("=" * 80)
    lines.append("💡 RAPPEL : Ne jamais trader les 15 premières minutes après une annonce.")
    lines.append("   Le Core CPI et le Core PCE sont plus importants que le Headline.")
    lines.append("   Les salaires (AHE) sont plus importants que le NFP pour l'inflation.")
    lines.append("   Vérifiez toujours les valeurs réelles sur les sources officielles avant de trader.")
    lines.append("=" * 80)

    return "\n".join(lines)

# ============================================================
# Modes d'exécution
# ============================================================
def run_batch_once():
    print(f"🚀 BATCH UTC - Annonces du {TODAY_DISPLAY}")
    events = get_events()
    fred_data = get_fred_snapshot()
    if not events:
        print("❌ Aucune annonce US trouvée.")
        return
    report = generate_report(events, fred_data)
    print("\n" + report)
    if send_telegram_message(report):
        print("✅ Rapport envoyé.")
    else:
        print("⚠️ Échec envoi.")
    with open(f"rapport_annonces_{TODAY}.txt", "w", encoding="utf-8") as f:
        f.write(report)
    print(f"📁 Rapport sauvegardé.")

def run_batch_continuous():
    print(f"🚀 CONTINU - Annonces du {TODAY_DISPLAY}")
    print(f"⏱️ Intervalle : {REFRESH_INTERVAL} secondes")
    print(f"🔑 Token Telegram : {TELEGRAM_BOT_TOKEN[:5]}...")
    # Envoi d'un message de test
    test_msg = f"✅ Bot démarré le {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
    send_telegram_message(test_msg)

    while True:
        try:
            events = get_events()
            fred_data = get_fred_snapshot()
            if events:
                report = generate_report(events, fred_data)
                print("\n" + report)
                if send_telegram_message(report):
                    print("✅ Rapport complet envoyé.")
                else:
                    print("⚠️ ÉCHEC envoi.")
            else:
                print("❌ Aucune annonce US trouvée.")
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            os.makedirs("logs", exist_ok=True)
            with open(f"logs/rapport_continu_{timestamp}.txt", "w", encoding="utf-8") as f:
                f.write(report if events else "Aucune annonce")
            print(f"📁 Rapport sauvegardé.")
            time.sleep(REFRESH_INTERVAL)
        except KeyboardInterrupt:
            print("⏹️ Arrêt demandé.")
            break
        except Exception as e:
            print(f"❌ Erreur : {e}")
            time.sleep(60)

# ============================================================
# Interface Streamlit
# ============================================================
def show_streamlit_interface():
    st.set_page_config(page_title="Annonces Économiques US", layout="wide")
    st.title("📊 Annonces Économiques US – Jour en cours")
    st.caption("Rafraîchissement automatique toutes les 30 min")

    @st.cache_data(ttl=1800)
    def get_cached_events():
        return get_events()

    events = get_cached_events()
    if not events:
        st.warning("Aucune annonce prévue aujourd'hui.")
        return

    df = pd.DataFrame(events)
    st.dataframe(df)

# ============================================================
# Entrée principale
# ============================================================
if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "--batch":
            run_batch_once()
            sys.exit(0)
        elif sys.argv[1] == "--continuous":
            run_batch_continuous()
            sys.exit(0)
    else:
        try:
            query_params = st.query_params
            if query_params.get("action") == "batch":
                run_batch_once()
                st.write("✅ Batch terminé.")
                sys.exit(0)
            elif query_params.get("action") == "continuous":
                run_batch_continuous()
                st.write("✅ Continu terminé.")
                sys.exit(0)
        except:
            pass
        show_streamlit_interface()
