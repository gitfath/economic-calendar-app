#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Application Streamlit + Bot Telegram (modes batch, continuous, watch)
Annonces économiques US du jour, cache, notifications, rapport complet.
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
# CHARGEMENT DU .env
# ============================================================
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ .env chargé")
except ImportError:
    print("ℹ️ dotenv non installé, utilisation des variables d'environnement")

PARSEBOT_API_KEY = os.getenv("PARSEBOT_API_KEY", "")
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "VOTRE_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "VOTRE_CHAT_ID")
REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL", "1800"))
WATCH_INTERVAL = int(os.getenv("WATCH_INTERVAL", "60"))

# Priorité st.secrets pour Streamlit Cloud
try:
    import streamlit as st
    if hasattr(st, 'secrets'):
        TELEGRAM_BOT_TOKEN = st.secrets.get("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
        TELEGRAM_CHAT_ID = st.secrets.get("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)
        PARSEBOT_API_KEY = st.secrets.get("PARSEBOT_API_KEY", PARSEBOT_API_KEY)
        FRED_API_KEY = st.secrets.get("FRED_API_KEY", FRED_API_KEY)
except Exception:
    pass

# Vérification des tokens
if TELEGRAM_BOT_TOKEN == "VOTRE_TOKEN" or TELEGRAM_CHAT_ID == "VOTRE_CHAT_ID":
    print("⚠️ Tokens Telegram non configurés. Le bot ne pourra pas envoyer de messages.")
else:
    print("✅ Tokens Telegram configurés.")

# --- Import conditionnel de streamlit et pandas pour l'interface ---
if "--batch" not in sys.argv and "--continuous" not in sys.argv and "--watch" not in sys.argv and "action" not in os.environ.get("QUERY_STRING", ""):
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
# CONSTANTES ET FICHIERS DE CACHE
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
# RÉCUPÉRATION DES ÉVÉNEMENTS (Parse.bot + fallback)
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
# BASE DE CONNAISSANCES (version complète)
# ============================================================
INDICATOR_KNOWLEDGE = {
    "FOMC": {
        "category": "Monétaire",
        "description": "Décision de taux de la Fed. Le plus important pour le Dollar.",
        "thresholds": {
            "Hawkish (hausse de taux)": "Dollar très haussier",
            "Dovish (baisse de taux)": "Dollar très baissier"
        },
        "strategy": "Attendre la conférence de presse. Ne pas trader les 15 premières minutes."
    },
    "FOMC Minutes": {
        "category": "Monétaire",
        "description": "Compte‑rendu détaillé de la réunion du FOMC.",
        "thresholds": {
            "Hawkish (préoccupation sur l'inflation)": "Dollar haussier",
            "Dovish (préoccupation sur l'emploi)": "Dollar baissier"
        },
        "strategy": "Comparer le ton avec le communiqué précédent."
    },
    "CPI": {
        "category": "Inflation",
        "description": "Indice des prix à la consommation. Mesure l'inflation.",
        "thresholds": {
            "Core CPI (mensuel) > 0.4%": "Dollar très haussier",
            "Core CPI (mensuel) < 0.1%": "Dollar baissier"
        },
        "strategy": "Regarder le Core CPI, pas le Headline. Attendre 15 minutes."
    },
    "Core CPI": {
        "category": "Inflation",
        "description": "Inflation sous‑jacente (hors alimentation et énergie).",
        "thresholds": {
            "> 0.4% (mensuel)": "Dollar très haussier",
            "< 0.1% (mensuel)": "Dollar baissier"
        },
        "strategy": "Le Core est plus important que le Headline."
    },
    "PPI": {
        "category": "Inflation",
        "description": "Indice des prix à la production. Indicateur avancé de l'inflation.",
        "thresholds": {
            "> 0.5% (mensuel)": "Dollar haussier",
            "< 0.1% (mensuel)": "Dollar baissier"
        },
        "strategy": "Anticipe le CPI dans 2 à 6 semaines."
    },
    "Core PCE": {
        "category": "Inflation",
        "description": "Indicateur d'inflation officiel de la Fed.",
        "thresholds": {
            "> 0.3% (mensuel)": "Dollar très haussier",
            "< 0.2% (mensuel)": "Dollar baissier"
        },
        "strategy": "L'indicateur roi. Une surprise fait bouger le Dollar violemment."
    },
    "NFP": {
        "category": "Emploi",
        "description": "Créations d'emplois non‑agricoles (Non‑Farm Payrolls).",
        "thresholds": {
            "> +200k": "Dollar haussier",
            "+100k à +200k": "Neutre",
            "< +100k": "Dollar baissier",
            "Négatif": "Dollar très baissier"
        },
        "strategy": "Regarder les révisions et les salaires (AHE)."
    },
    "Unemployment Rate": {
        "category": "Emploi",
        "description": "Taux de chômage officiel.",
        "thresholds": {
            "En baisse": "Dollar haussier",
            "En hausse > 0.2%": "Dollar baissier (alerte Sahm)"
        },
        "strategy": "Vérifier le taux de participation."
    },
    "AHE": {
        "category": "Emploi",
        "description": "Salaires horaires moyens (Average Hourly Earnings).",
        "thresholds": {
            "> 0.4% (mensuel)": "Dollar très haussier",
            "< 0.2% (mensuel)": "Dollar baissier"
        },
        "strategy": "Un AHE élevé est plus important qu'un NFP élevé."
    },
    "Jobless Claims": {
        "category": "Emploi",
        "description": "Inscriptions hebdomadaires au chômage.",
        "thresholds": {
            "MA4 < 200k": "Dollar haussier",
            "MA4 > 220k": "Dollar baissier"
        },
        "strategy": "Suivre la moyenne mobile sur 4 semaines."
    },
    "JOLTS": {
        "category": "Emploi",
        "description": "Offres d'emploi (Job Openings and Labor Turnover Survey).",
        "thresholds": {
            "Ratio offres/chômeurs > 1.5": "Dollar haussier",
            "Ratio < 1.0": "Dollar baissier"
        },
        "strategy": "Regarder le taux de démission (quit rate)."
    },
    "ADP": {
        "category": "Emploi",
        "description": "Rapport privé sur l'emploi (publié par ADP).",
        "thresholds": {},
        "strategy": "⚠️ Ne pas trader sur l'ADP seul. Servir de pré‑indicateur pour le NFP."
    },
    "Retail Sales": {
        "category": "Consommation",
        "description": "Ventes au détail. Mesure la consommation des ménages.",
        "thresholds": {
            "Control Group > 0.5%": "Dollar haussier",
            "Control Group < 0.1%": "Dollar baissier"
        },
        "strategy": "Regarder le 'Control Group' (exclut auto, essence, matériaux de construction)."
    },
    "GDP": {
        "category": "Croissance",
        "description": "Produit intérieur brut. Taux de croissance annualisé.",
        "thresholds": {
            "> 3.0%": "Dollar haussier",
            "2.0% - 3.0%": "Neutre",
            "< 1.5%": "Dollar baissier"
        },
        "strategy": "Regarder la composante consommation et les investissements."
    },
    "ISM Manufacturing": {
        "category": "Croissance",
        "description": "PMI manufacturier (Institute for Supply Management).",
        "thresholds": {
            "> 50": "Dollar haussier",
            "< 50": "Dollar baissier"
        },
        "strategy": "Regarder la composante 'Prix Payés'."
    },
    "ISM Services": {
        "category": "Croissance",
        "description": "PMI des services.",
        "thresholds": {
            "> 55": "Dollar haussier",
            "< 50": "Dollar baissier"
        },
        "strategy": "Regarder 'Prices Paid' > 65 → signal inflationniste → dollar haussier."
    },
    "Trade Balance": {
        "category": "Commerce extérieur",
        "description": "Balance commerciale (exportations – importations).",
        "thresholds": {
            "Déficit moins important que prévu": "Dollar haussier",
            "Déficit plus important que prévu": "Dollar baissier"
        },
        "strategy": "Surveiller l'évolution sur 3 mois."
    },
    "EIA": {
        "category": "Énergie",
        "description": "Rapport hebdomadaire de l'Energy Information Administration sur les stocks de pétrole.",
        "thresholds": {
            "Stocks en baisse inattendue": "Dollar haussier (demande forte)",
            "Stocks en hausse inattendue": "Dollar baissier"
        },
        "strategy": "Regarder la variation des stocks de Cushing."
    }
}

def classify_event(event_name: str) -> Dict:
    for key, knowledge in INDICATOR_KNOWLEDGE.items():
        if key.lower() in event_name.lower():
            return knowledge
    return {"category": "Autre", "description": "Événement économique à surveiller.", "thresholds": {}, "strategy": "Consulter les détails de l'annonce."}

# ============================================================
# ANALYSE ET IMPACT SUR LE DOLLAR
# ============================================================
def clean_number(s):
    if not s or s == "N/A":
        return 0.0
    try:
        s = str(s).replace("%", "").replace("K", "").replace("M", "").replace("B", "").replace("T", "").replace(",", "").strip()
        return float(s) if s else 0.0
    except:
        return 0.0

def get_dollar_impact(event: Dict) -> str:
    """Calcule l'impact directionnel sur le Dollar en fonction de l'écart réel/prévision."""
    actual = event.get("actual", "N/A")
    forecast = event.get("forecast", "N/A")
    event_name = event.get("event", "")
    if actual == "N/A" or forecast == "N/A":
        return "⏳ Impact non déterminé (données manquantes)"

    try:
        diff = clean_number(actual) - clean_number(forecast)
    except:
        return "⚠️ Impossible d'évaluer l'impact (vérifier les unités)"

    # Indicateurs inverses : si la valeur réelle est plus basse que prévu, c'est haussier
    inverse_indicators = ["unemployment rate", "jobless claims", "initial claims", "continuing claims",
                          "crude oil inventories", "eia crude oil", "trade balance", "current account"]
    is_inverse = any(kw in event_name.lower() for kw in inverse_indicators)
    # Exceptions : Core CPI, NFP, AHE, ISM sont normaux
    if any(kw in event_name.lower() for kw in ["core cpi", "nfp", "non-farm", "ahe", "average hourly", "ism", "pmi"]):
        is_inverse = False

    if abs(diff) < 0.01:
        return "➖ Impact NEUTRE sur le Dollar (conforme aux attentes)"

    if is_inverse:
        if diff < 0:
            return "🟢 Impact HAUSSIER sur le Dollar (meilleur que prévu : valeur plus faible)"
        else:
            return "🔴 Impact BAISSIER sur le Dollar (pire que prévu : valeur plus élevée)"
    else:
        if diff > 0:
            return "🟢 Impact HAUSSIER sur le Dollar (meilleur que prévu : valeur plus élevée)"
        else:
            return "🔴 Impact BAISSIER sur le Dollar (pire que prévu : valeur plus faible)"

def generate_analysis(event: Dict) -> Dict:
    event_name = event.get("event", "Inconnu")
    knowledge = classify_event(event_name)
    interpretation = "\n".join([f"- {k}: {v}" for k, v in knowledge.get("thresholds", {}).items()])
    if not interpretation:
        interpretation = "Aucun seuil défini."
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
        "interpretation": interpretation,
        "dollar_impact": get_dollar_impact(event)
    }

# ============================================================
# NOTIFICATIONS TELEGRAM (avec déduplication)
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
            print(f"✅ Chunk {idx+1}/{len(chunks)} envoyé")
        except Exception as e:
            print(f"❌ Erreur envoi chunk {idx+1}: {e}")
            success = False
    return success

def interpret_event_for_telegram(event: Dict) -> str:
    a = generate_analysis(event)
    lines = []
    lines.append(f"📌 {a['event']} à {a['time']} (impact: {a['impact']})")
    lines.append(f"📖 {a['description']}")
    lines.append(f"📊 Réel: {event.get('actual', 'N/A')} | Prévision: {a['forecast']} | Précédent: {a['previous']}")
    lines.append(f"💵 {a['dollar_impact']}")
    if a['interpretation']:
        lines.append(f"🔍 Seuils : {a['interpretation']}")
    if a['strategy']:
        lines.append(f"🎯 Stratégie : {a['strategy']}")
    lines.append("🔗 Calendrier : https://www.investing.com/economic-calendar/")
    return "\n".join(lines)

def check_and_notify_updates(new_events: List[Dict], send_immediate: bool = True):
    """Vérifie les nouvelles annonces et mises à jour des valeurs réelles, envoie des notifications."""
    old_events = load_json(CACHE_FILE, [])
    notified_ids = load_json(NOTIFIED_FILE, [])
    notified_set = set(notified_ids)

    updates = []
    old_map = {}
    for e in old_events:
        key = e.get("event_id") or f"{e['event']}_{e['time']}"
        old_map[key] = e

    for ne in new_events:
        key = ne.get("event_id") or f"{ne['event']}_{ne['time']}"
        if key not in old_map:
            # Nouvel événement
            updates.append(ne)
        else:
            old_actual = old_map[key].get("actual", "N/A")
            new_actual = ne.get("actual", "N/A")
            if old_actual == "N/A" and new_actual != "N/A" and key not in notified_set:
                # Mise à jour de la valeur réelle
                updates.append(ne)

    if updates and send_immediate:
        lines = [f"📊 **Nouvelles données économiques – {TODAY}**", ""]
        for ev in updates:
            lines.append("─" * 40)
            lines.append(interpret_event_for_telegram(ev))
            lines.append("")
        lines.append("")
        lines.append("🔗 [Voir le calendrier complet sur Investing.com](https://www.investing.com/economic-calendar/)")
        msg = "\n".join(lines)
        if send_telegram_message(msg):
            for ev in updates:
                key = ev.get("event_id") or f"{ev['event']}_{ev['time']}"
                notified_set.add(key)
            save_json(NOTIFIED_FILE, list(notified_set))
            print(f"✅ Notification enrichie envoyée pour {len(updates)} événement(s).")
        else:
            print("⚠️ Échec de l'envoi de la notification.")

    save_json(CACHE_FILE, new_events)

# ============================================================
# GÉNÉRATION DU RAPPORT COMPLET
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

def generate_report(events: List[Dict], fred_data: Dict, include_message: str = "") -> str:
    lines = []
    if include_message:
        lines.append(include_message)
        lines.append("")
    lines.append("=" * 80)
    lines.append(f"📊 ANNONCES ÉCONOMIQUES DU {TODAY_DISPLAY} (UTC)")
    lines.append("=" * 80)
    lines.append("")

    fred_text = format_fred_snapshot(fred_data)
    if fred_text:
        lines.append(fred_text)
        lines.append("")
    else:
        if FRED_API_KEY:
            lines.append("⚠️ Snapshot FRED indisponible (vérifiez la clé).")
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
            lines.append(f"   💵 {a['dollar_impact']}")
            lines.append(f"   🎯 STRATÉGIE : {a['strategy']}")
            lines.append(f"   🔗 Calendrier : https://www.investing.com/economic-calendar/")
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
# MODES D'EXÉCUTION
# ============================================================
def run_batch_once():
    print(f"🚀 BATCH UTC - Annonces du {TODAY_DISPLAY}")
    events = get_events()
    fred_data = get_fred_snapshot() if FRED_API_KEY else {}
    # Envoi d'un message de démarrage
    start_msg = f"🟢 BOT D'ANALYSE ÉCONOMIQUE DÉMARRÉ (batch) – {TODAY_DISPLAY}"
    send_telegram_message(start_msg)
    if not events:
        # Envoyer un message même sans annonce
        report = generate_report([], fred_data, "⚠️ Aucune annonce US prévue aujourd'hui.")
        if send_telegram_message(report):
            print("✅ Rapport envoyé (sans annonce).")
        else:
            print("⚠️ Échec de l'envoi du rapport.")
        return

    # Notifier les mises à jour immédiates (si des valeurs réelles sont déjà disponibles)
    check_and_notify_updates(events, send_immediate=True)

    report = generate_report(events, fred_data, "📊 RAPPORT COMPLET DU JOUR")
    if send_telegram_message(report):
        print("✅ Rapport complet envoyé.")
    else:
        print("⚠️ Échec de l'envoi du rapport complet.")

    with open(f"rapport_annonces_{TODAY}.txt", "w", encoding="utf-8") as f:
        f.write(report)
    print(f"📁 Rapport sauvegardé.")

def run_batch_continuous():
    print(f"🚀 CONTINU - Annonces du {TODAY_DISPLAY}")
    print(f"⏱️ Intervalle : {REFRESH_INTERVAL} secondes")
    # Message de test au démarrage
    test_msg = f"🟢 BOT D'ANALYSE ÉCONOMIQUE DÉMARRÉ (continu) – {TODAY_DISPLAY}\n📡 Intervalle : {REFRESH_INTERVAL} secondes."
    send_telegram_message(test_msg)

    while True:
        try:
            events = get_events()
            fred_data = get_fred_snapshot() if FRED_API_KEY else {}
            if not events:
                # Envoyer un message de statut même sans annonce
                report = generate_report([], fred_data, "⚠️ Aucune annonce US prévue pour le moment.")
                if send_telegram_message(report):
                    print("✅ Rapport de statut envoyé (aucune annonce).")
                else:
                    print("⚠️ Échec de l'envoi du rapport de statut.")
            else:
                # Détecter et notifier les nouvelles annonces ou mises à jour
                check_and_notify_updates(events, send_immediate=True)
                # Envoyer un rapport complet périodique
                report = generate_report(events, fred_data, "📊 MISE À JOUR PÉRIODIQUE")
                if send_telegram_message(report):
                    print("✅ Rapport complet envoyé.")
                else:
                    print("⚠️ Échec de l'envoi du rapport complet.")

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

def run_batch_watch():
    """Mode watch : surveille en temps réel et notifie uniquement les nouvelles annonces/mises à jour."""
    print(f"🚀 WATCH - Surveillance des annonces du {TODAY_DISPLAY}")
    print(f"⏱️ Intervalle : {WATCH_INTERVAL} secondes")
    # Message de test
    test_msg = f"🟢 BOT DE SURVEILLANCE DÉMARRÉ – {TODAY_DISPLAY}\n📡 Intervalle : {WATCH_INTERVAL} secondes."
    send_telegram_message(test_msg)

    # Charger l'état précédent
    notified_ids = set(load_json(NOTIFIED_FILE, []))
    previous_events = load_json(CACHE_FILE, [])

    while True:
        try:
            current_events = get_events()
            if not current_events:
                print("⚠️ Aucune annonce US trouvée.")
                time.sleep(WATCH_INTERVAL)
                continue

            # Détecter les changements
            old_map = {}
            for e in previous_events:
                key = e.get("event_id") or f"{e['event']}_{e['time']}"
                old_map[key] = e

            updates = []
            for ce in current_events:
                key = ce.get("event_id") or f"{ce['event']}_{ce['time']}"
                if key not in old_map:
                    updates.append(ce)
                else:
                    old_actual = old_map[key].get("actual", "N/A")
                    new_actual = ce.get("actual", "N/A")
                    if old_actual == "N/A" and new_actual != "N/A" and key not in notified_ids:
                        updates.append(ce)

            if updates:
                lines = [f"📊 **Nouvelles données économiques – {TODAY}**", ""]
                for ev in updates:
                    lines.append("─" * 40)
                    lines.append(interpret_event_for_telegram(ev))
                    lines.append("")
                lines.append("")
                lines.append("🔗 [Voir le calendrier complet sur Investing.com](https://www.investing.com/economic-calendar/)")
                msg = "\n".join(lines)
                if send_telegram_message(msg):
                    for ev in updates:
                        key = ev.get("event_id") or f"{ev['event']}_{ev['time']}"
                        notified_ids.add(key)
                    save_json(NOTIFIED_FILE, list(notified_ids))
                    print(f"✅ Notification envoyée pour {len(updates)} événement(s).")
                else:
                    print("⚠️ Échec de l'envoi de la notification.")

            # Mettre à jour le cache
            save_json(CACHE_FILE, current_events)
            previous_events = current_events

            time.sleep(WATCH_INTERVAL)

        except KeyboardInterrupt:
            print("⏹️ Arrêt demandé.")
            break
        except Exception as e:
            print(f"❌ Erreur dans la boucle : {e}")
            time.sleep(60)

# ============================================================
# INTERFACE STREAMLIT
# ============================================================
def show_streamlit_interface():
    st.set_page_config(page_title="Annonces Économiques US", layout="wide")
    st.title("📊 Annonces Économiques US – Jour en cours")
    st.caption("Dernière mise à jour : " + datetime.now(timezone.utc).strftime("%H:%M:%S UTC"))

    @st.cache_data(ttl=1800)
    def get_cached_events():
        return get_events()

    events = get_cached_events()
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
            st.markdown(f"**Impact USD :** {a['dollar_impact']}")
            st.markdown(f"**Stratégie :** {a['strategy']}")

    if st.button("🔄 Forcer le rafraîchissement"):
        st.cache_data.clear()
        st.rerun()

# ============================================================
# ENTRÉE PRINCIPALE
# ============================================================
if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "--batch":
            run_batch_once()
            sys.exit(0)
        elif sys.argv[1] == "--continuous":
            run_batch_continuous()
            sys.exit(0)
        elif sys.argv[1] == "--watch":
            run_batch_watch()
            sys.exit(0)
    else:
        # Par défaut, afficher l'interface Streamlit ou exécuter un batch simple
        try:
            # Si streamlit est importé, on lance l'interface
            if 'streamlit' in sys.modules:
                show_streamlit_interface()
            else:
                # Sinon, mode batch par défaut
                run_batch_once()
        except ImportError:
            run_batch_once()
