#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Application Streamlit + Bot Telegram (mode continu optimisé)
Rafraîchissement des données à chaque itération, pas de cache long.
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
REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL", "1800"))

# Si on est sur Streamlit Cloud, on priorise st.secrets
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
# 3. VÉRIFICATION
# ============================================================
print("\n🔍 DIAGNOSTIC :")
print(f"   TELEGRAM_BOT_TOKEN : {'OK' if TELEGRAM_BOT_TOKEN != 'VOTRE_TOKEN' else 'MANQUANT'}")
print(f"   TELEGRAM_CHAT_ID   : {'OK' if TELEGRAM_CHAT_ID != 'VOTRE_CHAT_ID' else 'MANQUANT'}")
print(f"   FRED_API_KEY       : {'OK' if FRED_API_KEY else 'MANQUANT'}")
print(f"   PARSEBOT_API_KEY   : {'OK' if PARSEBOT_API_KEY else 'MANQUANT'}")
if TELEGRAM_BOT_TOKEN != 'VOTRE_TOKEN':
    print(f"   Token (début) : {TELEGRAM_BOT_TOKEN[:5]}...")
if TELEGRAM_CHAT_ID != 'VOTRE_CHAT_ID':
    print(f"   Chat ID : {TELEGRAM_CHAT_ID}")

if TELEGRAM_BOT_TOKEN == "VOTRE_TOKEN" or TELEGRAM_CHAT_ID == "VOTRE_CHAT_ID":
    print("❌ Tokens Telegram non configurés. Arrêt.")
    sys.exit(1)

# ============================================================
# 4. FONCTIONS DE RÉCUPÉRATION (sans cache)
# ============================================================
def get_today_utc() -> tuple:
    now_utc = datetime.now(timezone.utc)
    return now_utc.strftime("%Y-%m-%d"), now_utc.strftime("%d/%m/%Y")

TODAY, TODAY_DISPLAY = get_today_utc()

def get_parsebot_events_rest() -> List[Dict]:
    """Récupère les annonces US depuis Parse.bot (API temps réel)."""
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
    """Récupère les annonces depuis le flux JSON public (fallback)."""
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
    """Récupère les événements du jour (sans cache)."""
    events = get_parsebot_events_rest()
    if not events:
        print("🔄 Parse.bot vide, fallback sur ForexFactory...")
        events = get_forexfactory_json()
    fred_data = get_fred_snapshot() if FRED_API_KEY else {}
    return events, fred_data

FRED_SERIES = {
    "CPIAUCSL": {"name": "CPI (Inflation)"},
    "UNRATE": {"name": "Taux de chômage"},
    "PAYEMS": {"name": "NFP (Emplois)"},
    "DGS10": {"name": "Taux 10 ans"},
    "FEDFUNDS": {"name": "Taux Fed Funds"},
}

def get_fred_snapshot() -> Dict:
    """Récupère les dernières valeurs FRED (si la clé est valide)."""
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
# 5. BASE DE CONNAISSANCES (simplifiée)
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
        "strategy": "Regarder le Core CPI, pas le Headline."
    },
    "NFP": {
        "category": "Emploi",
        "description": "Créations d'emplois non-agricoles.",
        "thresholds": {"> +200k": "Dollar haussier", "< +100k": "Dollar baissier"},
        "strategy": "Regarder les révisions et les salaires."
    },
    # Ajoutez d'autres indicateurs si nécessaire...
}

def classify_event(event_name: str) -> Dict:
    for key, knowledge in INDICATOR_KNOWLEDGE.items():
        if key.lower() in event_name.lower():
            return knowledge
    return {"category": "Autre", "description": "Événement économique", "thresholds": {}, "strategy": "Consulter les détails."}

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
# 6. GÉNÉRATION DU RAPPORT
# ============================================================
def format_fred_snapshot(fred_data: Dict) -> str:
    if not fred_data:
        return ""
    lines = ["📈 SNAPSHOT DES INDICATEURS (valeurs réelles FRED) :"]
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

    fred_text = format_fred_snapshot(fred_data)
    if fred_text:
        lines.append(fred_text)
        lines.append("")

    if not events:
        lines.append("📭 Aucune annonce économique US prévue aujourd'hui.")
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
            lines.append("-" * 80)
            lines.append("")
        sources = set(e.get('source', 'Inconnue') for e in events)
        lines.append(f"📌 Source : {', '.join(sources)}")

    lines.append("")
    lines.append("=" * 80)
    lines.append("💡 RAPPEL : Ne jamais trader les 15 premières minutes après une annonce.")
    lines.append("   Vérifiez toujours les valeurs réelles sur les sources officielles.")
    lines.append("=" * 80)
    return "\n".join(lines)

# ============================================================
# 7. ENVOI TELEGRAM (avec logs)
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
        if len(chunks) > 1:
            chunk = f"[{idx+1}/{len(chunks)}]\n{chunk}"
        try:
            resp = requests.post(url, json={"chat_id": chat_id, "text": chunk}, timeout=10)
            resp.raise_for_status()
            print(f"✅ Chunk {idx+1} envoyé (status {resp.status_code})")
        except Exception as e:
            print(f"❌ Erreur envoi chunk {idx+1} : {e}")
            success = False
    return success

# ============================================================
# 8. MODE CONTINU (raffraîchissement sans cache)
# ============================================================
def run_continuous():
    print(f"🚀 MODE CONTINU - Annonces du {TODAY_DISPLAY}")
    print(f"⏱️ Intervalle : {REFRESH_INTERVAL} secondes")
    # Test d'envoi
    test_msg = f"✅ Bot démarré en mode continu à {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC."
    if send_telegram_message(test_msg):
        print("✅ Message de test envoyé.")
    else:
        print("⚠️ Échec du message de test.")

    while True:
        try:
            # Récupération des données fraîches
            events, fred_data = get_events()
            report = generate_report(events, fred_data)
            print("\n" + report)
            if send_telegram_message(report):
                print("✅ Rapport envoyé.")
            else:
                print("⚠️ Échec de l'envoi du rapport.")
            # Sauvegarde locale pour tracer
            with open(f"logs/rapport_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.txt", "w") as f:
                f.write(report)
            time.sleep(REFRESH_INTERVAL)
        except KeyboardInterrupt:
            print("⏹️ Arrêt demandé.")
            break
        except Exception as e:
            print(f"❌ Erreur : {e}")
            time.sleep(60)

# ============================================================
# 9. INTERFACE STREAMLIT
# ============================================================
def show_interface():
    import streamlit as st
    import pandas as pd
    st.set_page_config(page_title="Annonces Économiques US", layout="wide")
    st.title("📊 Annonces Économiques US – Jour en cours")
    last_refresh = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    st.caption(f"Dernier rafraîchissement : {last_refresh}")

    events, _ = get_events()
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

    st.caption("Données : Parse.bot / ForexFactory | Rafraîchissement auto toutes les 30 min.")

# ============================================================
# 10. ENTRÉE PRINCIPALE
# ============================================================
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--continuous":
        run_continuous()
    else:
        try:
            import streamlit as st
            show_interface()
        except ImportError:
            # Exécution directe sans streamlit
            events, fred_data = get_events()
            report = generate_report(events, fred_data)
            print(report)
            if send_telegram_message(report):
                print("✅ Rapport envoyé.")
            else:
                print("⚠️ Échec de l'envoi.")
