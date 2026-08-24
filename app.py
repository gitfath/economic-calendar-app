#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Application Streamlit + Bot Telegram (mode watch)
Basée sur le bot.py fonctionnel.
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
# 1. CHARGEMENT DU .env (comme dans bot.py)
# ============================================================
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ .env chargé")
except ImportError:
    print("ℹ️ python-dotenv non installé, utilisation des variables d'environnement.")

# ============================================================
# 2. LECTURE DES VARIABLES
# ============================================================
PARSEBOT_API_KEY = os.getenv("PARSEBOT_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "VOTRE_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "VOTRE_CHAT_ID")
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
WATCH_INTERVAL = int(os.getenv("WATCH_INTERVAL", "60"))

# Si on est sur Streamlit Cloud, priorité à st.secrets
try:
    import streamlit as st
    if hasattr(st, 'secrets'):
        TELEGRAM_BOT_TOKEN = st.secrets.get("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
        TELEGRAM_CHAT_ID = st.secrets.get("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)
        PARSEBOT_API_KEY = st.secrets.get("PARSEBOT_API_KEY", PARSEBOT_API_KEY)
        FRED_API_KEY = st.secrets.get("FRED_API_KEY", FRED_API_KEY)
        print("🔑 Utilisation de st.secrets")
except Exception:
    pass

# ============================================================
# 3. VÉRIFICATION DES TOKENS
# ============================================================
if TELEGRAM_BOT_TOKEN == "VOTRE_TOKEN" or TELEGRAM_CHAT_ID == "VOTRE_CHAT_ID":
    print("❌ Tokens Telegram non configurés. Arrêt.")
    sys.exit(1)

print("✅ Tokens Telegram configurés.")

# ============================================================
# 4. FONCTIONS DE RÉCUPÉRATION (reprises de bot.py)
# ============================================================
def get_today_utc() -> tuple:
    now_utc = datetime.now(timezone.utc)
    return now_utc.strftime("%Y-%m-%d"), now_utc.strftime("%d/%m/%Y")

TODAY, TODAY_DISPLAY = get_today_utc()

def get_parsebot_events_rest() -> List[Dict]:
    if not PARSEBOT_API_KEY:
        return []
    url = "https://api.parse.bot/scraper/5d47f2a9-c902-4fe9-ac2d-3e00c66f7b7a/get_daily_events"
    headers = {"X-API-Key": PARSEBOT_API_KEY}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
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
    except Exception as e:
        print(f"❌ Parse.bot échec : {e}")
        return []

def get_forexfactory_json() -> List[Dict]:
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"❌ Flux JSON échec : {e}")
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

def get_events() -> tuple:
    events = get_parsebot_events_rest()
    if not events:
        print("🔄 Parse.bot vide, fallback sur ForexFactory...")
        events = get_forexfactory_json()
    fred_data = get_fred_snapshot() if FRED_API_KEY else {}
    return events, fred_data

# ============================================================
# 5. FRED API
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
        except Exception as e:
            print(f"⚠️ FRED {series_id} échec : {e}")
    return result

# ============================================================
# 6. BASE DE CONNAISSANCES (simplifiée mais suffisante)
# ============================================================
INDICATOR_KNOWLEDGE = {
    "FOMC": {
        "category": "Monétaire",
        "description": "Décision de taux de la Fed.",
        "thresholds": {"Hawkish": "Dollar fort", "Dovish": "Dollar faible"},
        "strategy": "Attendre la conférence de presse."
    },
    "CPI": {
        "category": "Inflation",
        "description": "Indice des prix à la consommation.",
        "thresholds": {"Core CPI > 0.4%": "Dollar très haussier", "Core CPI < 0.1%": "Dollar baissier"},
        "strategy": "Regarder le Core CPI."
    },
    "NFP": {
        "category": "Emploi",
        "description": "Créations d'emplois non-agricoles.",
        "thresholds": {"> +200k": "Dollar haussier", "< +100k": "Dollar baissier"},
        "strategy": "Regarder les révisions et les salaires."
    }
    # Ajoutez d'autres indicateurs si nécessaire
}

def classify_event(event_name: str) -> Dict:
    for key, knowledge in INDICATOR_KNOWLEDGE.items():
        if key.lower() in event_name.lower():
            return knowledge
    return {"category": "Autre", "description": "Événement économique", "thresholds": {}, "strategy": "Consulter les détails."}

def get_dollar_impact(event: Dict) -> str:
    actual = event.get("actual", "N/A")
    forecast = event.get("forecast", "N/A")
    if actual == "N/A" or forecast == "N/A":
        return "⏳ Impact non déterminé"
    try:
        def clean(s):
            s = str(s).replace("%", "").replace(",", "").strip()
            return float(s) if s else 0.0
        diff = clean(actual) - clean(forecast)
        if diff > 0:
            return "🟢 HAUSSIER"
        elif diff < 0:
            return "🔴 BAISSIER"
        else:
            return "➖ NEUTRE"
    except:
        return "⚠️ Impact non calculable"

def generate_analysis(event: Dict) -> Dict:
    event_name = event.get("event", "Inconnu")
    knowledge = classify_event(event_name)
    interpretation = "\n".join([f"- {k}: {v}" for k, v in knowledge.get("thresholds", {}).items()])
    return {
        "event": event_name,
        "time": event.get("time", "À déterminer"),
        "country": event.get("country", "US"),
        "impact": event.get("impact", "low").upper(),
        "category": knowledge.get("category", "Autre"),
        "description": knowledge.get("description", ""),
        "forecast": event.get("forecast", "N/A"),
        "previous": event.get("previous", "N/A"),
        "strategy": knowledge.get("strategy", "Surveiller l'annonce."),
        "interpretation": interpretation or "Aucun seuil défini."
    }

# ============================================================
# 7. FONCTIONS D'ENVOI TELEGRAM (comme dans bot.py)
# ============================================================
def send_telegram_message(message: str) -> bool:
    token = TELEGRAM_BOT_TOKEN
    chat_id = TELEGRAM_CHAT_ID
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = [message[i:i+4000] for i in range(0, len(message), 4000)]
    success = True
    for idx, chunk in enumerate(chunks):
        if len(chunks) > 1:
            chunk = f"[{idx+1}/{len(chunks)}]\n{chunk}"
        try:
            resp = requests.post(url, json={"chat_id": chat_id, "text": chunk}, timeout=10)
            resp.raise_for_status()
            print(f"✅ Envoi OK (chunk {idx+1})")
        except Exception as e:
            print(f"❌ Erreur envoi chunk {idx+1}: {e}")
            success = False
    return success

def interpret_event(event: Dict) -> str:
    a = generate_analysis(event)
    lines = []
    lines.append(f"📌 {a['event']} à {a['time']} (impact: {a['impact']})")
    lines.append(f"📖 {a['description']}")
    lines.append(f"📊 Réel: {event.get('actual', 'N/A')} | Prévision: {a['forecast']} | Précédent: {a['previous']}")
    impact_msg = get_dollar_impact(event)
    lines.append(f"💵 Impact Dollar : {impact_msg}")
    if a['interpretation']:
        lines.append(f"🔍 Seuils : {a['interpretation']}")
    lines.append(f"🎯 Stratégie : {a['strategy']}")
    lines.append("🔗 Calendrier : https://www.forexfactory.com/calendar")
    return "\n".join(lines)

# ============================================================
# 8. MODE WATCH (SURVEILLANCE)
# ============================================================
CACHE_FILE = "events_cache.json"
NOTIFIED_FILE = "notified_events.json"

def load_json(filename: str, default: list = None) -> list:
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else []

def save_json(filename: str, data: list):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def watch():
    print(f"🚀 SURVEILLANCE ACTIVE - Annonces du {TODAY}")
    print(f"⏱️ Intervalle : {WATCH_INTERVAL} secondes\n")

    notified_ids = set(load_json(NOTIFIED_FILE, []))
    previous_events = load_json(CACHE_FILE, [])
    prev_map = {}
    for e in previous_events:
        key = e.get("event_id") or f"{e['event']}_{e['time']}"
        prev_map[key] = e

    while True:
        try:
            events, _ = get_events()
            if not events:
                print("⚠️ Aucune annonce US trouvée.")
                time.sleep(WATCH_INTERVAL)
                continue

            curr_map = {}
            for e in events:
                key = e.get("event_id") or f"{e['event']}_{e['time']}"
                curr_map[key] = e

            for key, event in curr_map.items():
                if key in notified_ids:
                    old_actual = prev_map.get(key, {}).get("actual", "N/A")
                    new_actual = event.get("actual", "N/A")
                    if old_actual == "N/A" and new_actual != "N/A":
                        msg = interpret_event(event)
                        if send_telegram_message(msg):
                            notified_ids.add(key)
                            print(f"✅ Mise à jour : {event['event']} -> {new_actual}")
                else:
                    msg = interpret_event(event)
                    if send_telegram_message(msg):
                        notified_ids.add(key)
                        print(f"✅ Nouvelle annonce : {event['event']}")

            save_json(CACHE_FILE, events)
            save_json(NOTIFIED_FILE, list(notified_ids))
            prev_map = curr_map

            time.sleep(WATCH_INTERVAL)

        except KeyboardInterrupt:
            print("⏹️ Arrêt demandé.")
            break
        except Exception as e:
            print(f"❌ Erreur dans la boucle : {e}")
            time.sleep(60)

# ============================================================
# 9. INTERFACE STREAMLIT
# ============================================================
def show_interface():
    import streamlit as st
    import pandas as pd
    st.set_page_config(page_title="Annonces Économiques US", layout="wide")
    st.title("📊 Annonces Économiques US – Jour en cours")
    events, fred_data = get_events()
    if not events:
        st.warning("Aucune annonce économique US prévue aujourd'hui.")
        return
    df = pd.DataFrame(events)
    df = df.rename(columns={
        "event": "Événement",
        "time": "Heure (UTC)",
        "impact": "Impact",
        "actual": "Réel",
        "forecast": "Prévision",
        "previous": "Précédent"
    })
    st.dataframe(df, use_container_width=True)

    if fred_data:
        st.subheader("📈 Snapshot FRED")
        for k, v in fred_data.items():
            st.metric(label=v["name"], value=v["value"], help=f"Date: {v['date']}")

    st.subheader("📖 Détails")
    for _, row in df.iterrows():
        with st.expander(f"{row['Événement']} – {row['Heure (UTC)']} ({row['Impact'].upper()})"):
            a = generate_analysis(row)
            st.markdown(f"**Description :** {a['description']}")
            st.markdown(f"**Catégorie :** {a['category']}")
            st.markdown(f"**Interprétation :**")
            for line in a['interpretation'].split('\n'):
                st.markdown(f"   {line}")
            st.markdown(f"**Stratégie :** {a['strategy']}")

    if st.button("🔄 Forcer le rafraîchissement"):
        st.rerun()

# ============================================================
# 10. ENTRÉE PRINCIPALE
# ============================================================
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--watch":
        watch()
    else:
        try:
            import streamlit as st
            show_interface()
        except ImportError:
            # Batch mode
            events, fred_data = get_events()
            for e in events:
                print(interpret_event(e))
                print("-" * 40)
            print("✅ Rapport généré.")
