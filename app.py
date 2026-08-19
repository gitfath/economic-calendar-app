#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BOT QUOTIDIEN D'ANNONCES ÉCONOMIQUES
Version avec Parse.bot en API REST (corrigée)
Utilise le scraper "get_daily_events" pour récupérer tous les événements du jour,
puis filtre sur les États-Unis (USD).

Fonctionnalités ajoutées :
- Cache persistant des événements (events_cache.json)
- Détection des nouvelles valeurs réelles (actual) et notifications Telegram ciblées
- Envoi du rapport complet à 01:00 UTC (détection automatique)
- Mode --check pour exécuter uniquement la vérification des mises à jour
"""

import os
import sys
import re
import json
import requests
from datetime import datetime, timezone, timedelta
from typing import List, Dict
from dotenv import load_dotenv

# Chargement des variables d'environnement
try:
    dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
    load_dotenv(dotenv_path)
except ImportError:
    print("⚠️ python-dotenv non installé.")

# ============================================================
# 1. CONFIGURATION
# ============================================================

PARSEBOT_API_KEY = os.getenv("PARSEBOT_API_KEY", "")
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "VOTRE_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "VOTRE_CHAT_ID")
TIMEZONE_STR = os.getenv("TIMEZONE", "UTC")

# Fichiers de cache
CACHE_FILE = "events_cache.json"
NOTIFIED_FILE = "notified_events.json"

# ============================================================
# 2. DATE DU JOUR EN UTC
# ============================================================

def get_today_utc() -> tuple:
    now_utc = datetime.now(timezone.utc)
    return now_utc.strftime("%Y-%m-%d"), now_utc.strftime("%d/%m/%Y")

TODAY, TODAY_DISPLAY = get_today_utc()

# ============================================================
# 3. SOURCE PRINCIPALE : PARSE.BOT (API REST) - CORRIGÉE
# ============================================================

def get_parsebot_events_rest() -> List[Dict]:
    """
    Récupère les annonces US du jour via l'API REST Parse.bot.
    Utilise le scraper "get_daily_events" (UUID fixe) qui donne tous les événements du jour.
    Filtre ensuite sur la devise USD pour ne garder que les annonces américaines.
    """
    if not PARSEBOT_API_KEY:
        print("⚠️ PARSEBOT_API_KEY non définie.")
        return []

    # Bonne URL (scraper ID pour "get_daily_events")
    url = "https://api.parse.bot/scraper/5d47f2a9-c902-4fe9-ac2d-3e00c66f7b7a/get_daily_events"
    headers = {
        "X-API-Key": PARSEBOT_API_KEY  # Header attendu par Parse.bot
    }
    # Pas de paramètres, l'endpoint renvoie tous les événements du jour (UTC)

    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"❌ Parse.bot API REST échec : {e}")
        return []

    # La réponse a la forme : {"status":"success","data":{"date":"...","events":[...],"total":...}}
    if data.get("status") != "success":
        print(f"⚠️ Statut Parse.bot non OK : {data.get('status')}")
        return []

    events_data = data.get("data", {}).get("events", [])
    if not events_data:
        print("ℹ️ Aucun événement renvoyé par Parse.bot.")
        return []

    # Filtrer pour ne garder que les événements des États-Unis (currency = USD)
    us_events = []
    exclude_keywords = ["ECB", "German", "RPI", "Final", "Loan Prime", "Lagarde", "Trump",
                        "Current Account", "Trade Balance", "HPI", "MI Inflation"]

    for item in events_data:
        currency = item.get("currency", "").upper()
        if currency != "USD":
            continue

        event_name = item.get("event", item.get("event_long", "Unknown"))
        # Exclure les événements non américains (par sécurité) ou les discours européens mal étiquetés
        if any(kw in event_name for kw in exclude_keywords):
            continue

        # Reformater pour correspondre à la structure attendue par le bot
        # L'heure est au format ISO (ex: "2026-08-19T14:30:00Z")
        # On peut la convertir en HH:MM pour l'affichage
        time_raw = item.get("time", "N/A")
        if time_raw != "N/A" and "T" in time_raw:
            try:
                dt = datetime.fromisoformat(time_raw.replace("Z", "+00:00"))
                time_display = dt.strftime("%H:%M UTC")
            except:
                time_display = time_raw
        else:
            time_display = time_raw

        us_events.append({
            "event_id": item.get("event_id"),  # pour identifier l'événement de manière unique
            "time": time_display,
            "event": event_name,
            "country": "US",
            "impact": item.get("importance", "low").lower(),
            "actual": item.get("actual", "N/A"),
            "forecast": item.get("forecast", "N/A"),
            "previous": item.get("previous", "N/A"),
            "source": "Parse.bot REST (daily events)"
        })

    print(f"✅ {len(us_events)} annonces US récupérées depuis Parse.bot.")
    return us_events

# ============================================================
# 4. SOURCE FALLBACK : FLUX JSON FOREXFACTORY
# ============================================================

def get_forexfactory_json() -> List[Dict]:
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"❌ Erreur flux JSON : {e}")
        return []

    events = []
    today_utc = datetime.now(timezone.utc).date()

    # Liste des événements à exclure (européens ou fictifs)
    exclude_keywords = ["ECB", "German", "RPI", "Final", "Loan Prime", "Lagarde", "Trump", "Current Account", "Trade Balance", "HPI", "MI Inflation"]

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
        if currency != "USD":
            continue

        title = item.get("title", "")
        if any(kw in title for kw in exclude_keywords):
            continue

        events.append({
            "event_id": None,  # pas d'ID pour ce fallback
            "time": item.get("time", "N/A"),
            "event": title,
            "country": "US",
            "impact": item.get("impact", "low").lower(),
            "actual": item.get("actual", "N/A"),
            "forecast": item.get("forecast", "N/A"),
            "previous": item.get("previous", "N/A"),
            "source": "JSON Fallback"
        })

    return events

# ============================================================
# 5. FONCTION PRINCIPALE DE RÉCUPÉRATION
# ============================================================

def get_events() -> tuple:
    events = []

    # 1. Parse.bot (API REST)
    print("🔍 Tentative Parse.bot API REST...")
    events = get_parsebot_events_rest()
    if events:
        return events, get_fred_snapshot()

    # 2. ForexFactory JSON (fallback)
    print("⚠️ Parse.bot indisponible. Tentative flux JSON ForexFactory...")
    events = get_forexfactory_json()
    if events:
        print(f"✅ {len(events)} annonces US récupérées depuis le flux JSON.")
        return events, get_fred_snapshot()

    print("❌ Aucune annonce US trouvée.")
    return [], get_fred_snapshot()

# ============================================================
# 6. FRED API (inchangé)
# ============================================================

FRED_SERIES = {
    "CPIAUCSL": {"name": "CPI (Inflation)", "impact": "high"},
    "UNRATE": {"name": "Taux de chômage", "impact": "high"},
    "PAYEMS": {"name": "NFP (Emplois)", "impact": "high"},
    "DGS10": {"name": "Taux 10 ans", "impact": "medium"},
    "FEDFUNDS": {"name": "Taux Fed Funds", "impact": "high"},
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
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
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
# 7. BASE DE CONNAISSANCES (ANALYSE - inchangée)
# ============================================================

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Application Streamlit + Bot Telegram (version complète)
Reproduit intégralement le comportement du script console, avec interface web en plus.
Annonces économiques US du jour, cache, notifications, rapport à 01:00 UTC.
"""

import os
import sys
import json
import requests
from datetime import datetime, timezone, timedelta
from typing import List, Dict
import streamlit as st
import pandas as pd
from dotenv import load_dotenv

# Chargement des variables d'environnement (pour le mode batch / local)
try:
    dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
    load_dotenv(dotenv_path)
except ImportError:
    pass

# ============================================================
# 1. CONFIGURATION
# ============================================================
# Pour Streamlit, on utilise les secrets ; pour le batch, on utilise les variables d'environnement
try:
    PARSEBOT_API_KEY = st.secrets["PARSEBOT_API_KEY"]
except Exception:
    PARSEBOT_API_KEY = os.getenv("PARSEBOT_API_KEY", "")

FRED_API_KEY = os.getenv("FRED_API_KEY", "")
TELEGRAM_BOT_TOKEN = st.secrets.get("TELEGRAM_BOT_TOKEN", os.getenv("TELEGRAM_BOT_TOKEN", "VOTRE_TOKEN"))
TELEGRAM_CHAT_ID = st.secrets.get("TELEGRAM_CHAT_ID", os.getenv("TELEGRAM_CHAT_ID", "VOTRE_CHAT_ID"))

CACHE_FILE = "events_cache.json"
NOTIFIED_FILE = "notified_events.json"

# ============================================================
# 2. DATE DU JOUR EN UTC
# ============================================================
def get_today_utc() -> tuple:
    now_utc = datetime.now(timezone.utc)
    return now_utc.strftime("%Y-%m-%d"), now_utc.strftime("%d/%m/%Y")

TODAY, TODAY_DISPLAY = get_today_utc()

# ============================================================
# 3. FONCTIONS DE GESTION DU CACHE (persistant)
# ============================================================
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
# 4. RÉCUPÉRATION DES ÉVÉNEMENTS (Parse.bot + fallback ForexFactory)
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
    exclude_keywords = ["ECB", "German", "RPI", "Final", "Loan Prime", "Lagarde", "Trump",
                        "Current Account", "Trade Balance", "HPI", "MI Inflation"]
    for item in events_data:
        if item.get("currency", "").upper() != "USD":
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
    exclude_keywords = ["ECB", "German", "RPI", "Final", "Loan Prime", "Lagarde", "Trump",
                        "Current Account", "Trade Balance", "HPI", "MI Inflation"]
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
        if item.get("currency", "").upper() != "USD":
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
    if events:
        return events
    events = get_forexfactory_json()
    if events:
        return events
    return []

# ============================================================
# 5. FRED API (comme dans le script original)
# ============================================================
FRED_SERIES = {
    "CPIAUCSL": {"name": "CPI (Inflation)", "impact": "high"},
    "UNRATE": {"name": "Taux de chômage", "impact": "high"},
    "PAYEMS": {"name": "NFP (Emplois)", "impact": "high"},
    "DGS10": {"name": "Taux 10 ans", "impact": "medium"},
    "FEDFUNDS": {"name": "Taux Fed Funds", "impact": "high"},
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
# 6. BASE DE CONNAISSANCES (INTÉGRALE – AVEC LES NOUVELLES ENTRÉES)
# ============================================================
INDICATOR_KNOWLEDGE = {
    "FOMC": {
        "category": "Monétaire",
        "description": "Décision de taux de la Fed. Le plus important pour le Dollar.",
        "thresholds": {
            "Hawkish": "Hausse de taux ou ton restrictif → Dollar fort",
            "Dovish": "Baisse de taux ou ton accommodant → Dollar faible"
        },
        "strategy": "Attendre la conférence de presse. Ne pas trader les 15 premières minutes."
    },
    "CPI": {
        "category": "Inflation",
        "description": "Indice des prix à la consommation. Mesure l'inflation.",
        "thresholds": {
            "Core CPI > 0.4%": "Dollar très haussier",
            "Core CPI < 0.1%": "Dollar baissier"
        },
        "strategy": "Regarder le Core CPI, pas le Headline. Attendre 15 minutes."
    },
    "Core CPI": {
        "category": "Inflation",
        "description": "Inflation sous-jacente (hors alimentation et énergie).",
        "thresholds": {
            "> 0.4%": "Dollar très haussier",
            "< 0.1%": "Dollar baissier"
        },
        "strategy": "Le Core est plus important que le Headline."
    },
    "NFP": {
        "category": "Emploi",
        "description": "Créations d'emplois non-agricoles. Le plus volatil.",
        "thresholds": {
            "> +200k": "Dollar haussier",
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
        "description": "Salaires horaires moyens. Moteur de l'inflation persistante.",
        "thresholds": {
            "> 0.4%": "Dollar très haussier",
            "< 0.2%": "Dollar baissier"
        },
        "strategy": "Un AHE élevé est plus important qu'un NFP élevé."
    },
    "PPI": {
        "category": "Inflation",
        "description": "Indice des prix à la production. Indicateur avancé du CPI.",
        "thresholds": {
            "> 0.5%": "Dollar haussier",
            "< 0.1%": "Dollar baissier"
        },
        "strategy": "Anticipe le CPI dans 2 à 6 semaines."
    },
    "Core PCE": {
        "category": "Inflation",
        "description": "Indicateur d'inflation officiel de la Fed.",
        "thresholds": {
            "> 0.3%": "Dollar très haussier",
            "< 0.2%": "Dollar baissier"
        },
        "strategy": "L'indicateur roi. Une surprise fait bouger le Dollar violemment."
    },
    "Retail Sales": {
        "category": "Consommation",
        "description": "Ventes au détail. Mesure la consommation des ménages.",
        "thresholds": {
            "Control Group > 0.5%": "Dollar haussier",
            "Control Group < 0.1%": "Dollar baissier"
        },
        "strategy": "Regarder le Control Group."
    },
    "GDP": {
        "category": "Croissance",
        "description": "Produit intérieur brut. Croissance économique.",
        "thresholds": {
            "> 3.0%": "Dollar haussier",
            "< 1.5%": "Dollar baissier"
        },
        "strategy": "Regarder la composante consommation."
    },
    "ISM Manufacturing": {
        "category": "Croissance",
        "description": "PMI manufacturier. Seuil 50 = expansion/contraction.",
        "thresholds": {
            "> 50": "Dollar haussier",
            "< 50": "Dollar baissier"
        },
        "strategy": "Regarder la composante 'Prix Payés'."
    },
    "ISM Services": {
        "category": "Croissance",
        "description": "PMI des services. 4x plus important que le Manufacturing.",
        "thresholds": {
            "> 55": "Dollar haussier",
            "< 50": "Dollar baissier"
        },
        "strategy": "Regarder 'Prices Paid' > 65 → Dollar haussier."
    },
    "Jobless Claims": {
        "category": "Emploi",
        "description": "Inscriptions au chômage (hebdomadaire).",
        "thresholds": {
            "MA4 < 200k": "Dollar haussier",
            "MA4 > 220k": "Dollar baissier"
        },
        "strategy": "Suivre la moyenne mobile sur 4 semaines."
    },
    "JOLTS": {
        "category": "Emploi",
        "description": "Offres d'emploi. Ratio offres/chômeurs.",
        "thresholds": {
            "Ratio > 1.5": "Dollar haussier",
            "Ratio < 1.0": "Dollar baissier"
        },
        "strategy": "Regarder le taux de démission."
    },
    "ADP": {
        "category": "Emploi",
        "description": "Rapport privé sur l'emploi. Publié 2 jours avant le NFP.",
        "thresholds": {},
        "strategy": "⚠️ Ne pas trader sur l'ADP."
    },
    "FOMC Minutes": {
        "category": "Monétaire",
        "description": "Compte-rendu détaillé de la réunion du FOMC.",
        "thresholds": {
            "Hawkish": "Ton restrictif → Dollar fort",
            "Dovish": "Ton accommodant → Dollar faible"
        },
        "strategy": "Lire les passages sur l'inflation et l'emploi."
    },
    "Fed Speech": {
        "category": "Monétaire",
        "description": "Discours d'un membre de la Fed.",
        "thresholds": {
            "Hawkish": "Ton restrictif → Dollar fort",
            "Dovish": "Ton accommodant → Dollar faible"
        },
        "strategy": "Surveiller les mots clés."
    },
    # --- NOUVELLES ENTRÉES POUR LES ANNONCES MANQUANTES ---
    "Stocks de pétrole brut": {
        "category": "Énergie",
        "description": "Rapport hebdomadaire des stocks de pétrole brut (hors réserve stratégique) publié par l'EIA. Mesure l'équilibre offre/demande aux États-Unis.",
        "thresholds": {
            "Baisse inattendue (> 1M)": "Offre tendue → Prix du pétrole haussier. Le dollar réagit parfois via l'inflation attendue.",
            "Hausse inattendue (> 1M)": "Offre abondante → Prix du pétrole baissier.",
            "Stocks de Cushing en baisse": "Haussier pour le WTI (point de livraison)."
        },
        "strategy": "Comparez strictement la variation réelle au consensus. La variation des stocks de Cushing est un bonus."
    },
    "EIA": {
        "category": "Énergie",
        "description": "Rapport complet de l'Energy Information Administration sur la production, les stocks et la demande de produits pétroliers.",
        "thresholds": {
            "Stocks d'essence en baisse": "Signe de forte consommation intérieure → Soutient le USD (économie robuste).",
            "Taux d'utilisation des raffineries en hausse": "Demande de brut élevée → Haussier pour le pétrole.",
            "Importations en hausse": "Compense l'offre intérieure → Neutre à baissier."
        },
        "strategy": "Regardez le sous‑total 'Produits raffinés fournis' comme proxy de la demande réelle."
    },
    "MBA": {
        "category": "Immobilier / Consommation",
        "description": "Enquête hebdomadaire de la Mortgage Bankers Association sur les demandes de prêts immobiliers (achat et refinancement).",
        "thresholds": {
            "Indice d'achat (Purchase) en hausse": "Forte demande immobilière → Marché résilient → USD haussier.",
            "Indice de refinancement en baisse": "Les taux élevés freinent le refinancement → Impact négatif sur la consommation.",
            "Taux hypothécaire en hausse": "Ralentit le marché immobilier → USD baissier (si anticipe baisse de la Fed)."
        },
        "strategy": "L'Indice d'achat est bien plus important que le Refinance pour la santé économique réelle. Surveillez les tendances sur 4 semaines."
    },
    "Adjudication": {
        "category": "Dette / Monétaire",
        "description": "Vente aux enchères d'obligations du Trésor américain (ici, durée 20 ans). Teste l'appétit des investisseurs pour la dette US.",
        "thresholds": {
            "Rendement en baisse par rapport aux attendus": "Forte demande → Les taux longs baissent → USD baissier (fuite vers la sécurité ou baisse des taux).",
            "Ratio 'Bid-to-Cover' élevé (> 2.5)": "Très forte demande → Sécurise le financement US → Neutre à haussier.",
            "Rendement en hausse": "Faible demande → Les taux longs montent → USD haussier."
        },
        "strategy": "Regardez toujours le ratio couvertures et le rendement indirect (indice de la demande des investisseurs étrangers)."
    }
    # --- FIN DES NOUVELLES ENTRÉES ---
}

def classify_event(event_name: str) -> Dict:
    for key, knowledge in INDICATOR_KNOWLEDGE.items():
        if key.lower() in event_name.lower():
            return knowledge
    return {"category": "Autre", "description": "Événement économique à surveiller.", "thresholds": {}, "strategy": "Consulter les détails de l'annonce."}

def generate_analysis(event: Dict) -> Dict:
    event_name = event.get("event", "Inconnu")
    knowledge = classify_event(event_name)
    interpretation = "\n".join([f"- {k}: {v}" for k, v in knowledge.get("thresholds", {}).items()])
    if not interpretation:
        interpretation = "Consulter les détails de l'annonce."
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
        "interpretation": interpretation
    }

# ============================================================
# 7. NOTIFICATIONS TELEGRAM (avec déduplication)
# ============================================================
def send_telegram_message(message: str) -> bool:
    token = TELEGRAM_BOT_TOKEN
    chat_id = TELEGRAM_CHAT_ID
    if token == "VOTRE_TOKEN" or chat_id == "VOTRE_CHAT_ID":
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
        except Exception as e:
            print(f"❌ Erreur envoi chunk {idx+1} : {e}")
            success = False
    return success

def check_and_notify_updates(new_events: List[Dict]):
    old_events = load_json(CACHE_FILE, [])
    notified_ids = load_json(NOTIFIED_FILE, [])
    notified_set = set(notified_ids)
    updates = []
    old_map = {(e.get("event_id"), e["event"], e["time"]): e for e in old_events}
    for ne in new_events:
        key = (ne.get("event_id"), ne["event"], ne["time"])
        if key in old_map:
            old_actual = old_map[key].get("actual", "N/A")
            new_actual = ne.get("actual", "N/A")
            if old_actual == "N/A" and new_actual != "N/A" and ne.get("event_id") not in notified_set:
                updates.append(ne)
        else:
            if ne.get("event_id") not in notified_set:
                updates.append(ne)

    if updates:
        lines = [f"📊 **Nouvelles annonces économiques – {TODAY}**", ""]
        for ev in updates:
            if ev["actual"] != "N/A":
                lines.append(f"✅ **{ev['event']}** à {ev['time']} (impact: {ev['impact'].upper()})")
                lines.append(f"   🎯 Réel: **{ev['actual']}** | Prévision: {ev['forecast']} | Précédent: {ev['previous']}")
            else:
                lines.append(f"➕ **{ev['event']}** à {ev['time']} (impact: {ev['impact'].upper()})")
                if ev["forecast"] != "N/A":
                    lines.append(f"   Prévision: {ev['forecast']} | Précédent: {ev['previous']}")
            lines.append("")
        lines.append("🔗 [Voir le calendrier](https://www.forexfactory.com/calendar)")
        msg = "\n".join(lines)
        if send_telegram_message(msg):
            for ev in updates:
                if ev.get("event_id"):
                    notified_set.add(ev["event_id"])
            save_json(NOTIFIED_FILE, list(notified_set))
            print(f"✅ Notification envoyée pour {len(updates)} événement(s).")
    save_json(CACHE_FILE, new_events)

# ============================================================
# 8. GÉNÉRATION DU RAPPORT (comme dans le script console)
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
        lines.append("   → Obtenez une clé gratuite sur https://fred.stlouisfed.org/docs/api/api_key.html")
        lines.append("")

    if not events:
        lines.append("📭 Aucune annonce économique US prévue aujourd'hui.")
        lines.append("")
        lines.append("💡 Conseil : Consultez https://www.forexfactory.com/calendar pour vérifier.")
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
            lines.append(f"   🔗 Calendrier : https://www.forexfactory.com/calendar?day={TODAY}")
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
# 9. MODE BATCH (identique au script console)
# ============================================================
def run_batch_mode():
    """Exécute la logique du script console (rapport, notifications, etc.)"""
    print(f"🚀 BOT UTC - Annonces du {TODAY_DISPLAY}")
    events = get_events()
    fred_data = get_fred_snapshot() if FRED_API_KEY else {}

    if not events:
        print("❌ Aucune annonce US trouvée.")
        return

    # Vérifier et notifier les mises à jour
    check_and_notify_updates(events)

    # Envoyer le rapport complet si on est entre 01:00 et 01:05 UTC
    now_utc = datetime.now(timezone.utc)
    if now_utc.hour == 1 and now_utc.minute < 5:
        report = generate_report(events, fred_data)
        print("\n" + report)
        if send_telegram_message(report):
            print("✅ Rapport complet envoyé sur Telegram.")
        else:
            print("⚠️ Échec de l'envoi du rapport complet.")

        # Sauvegarder le rapport en fichier
        with open(f"rapport_annonces_{TODAY}.txt", "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n📁 Rapport sauvegardé.")
    else:
        print("ℹ️ Heure actuelle : pas d'envoi de rapport complet (attendu entre 01:00 et 01:05 UTC).")

# ============================================================
# 10. INTERFACE STREAMLIT
# ============================================================
def show_streamlit_interface():
    st.set_page_config(page_title="Annonces Économiques US", layout="wide")
    st.title("📊 Annonces Économiques US – Jour en cours")
    last_refresh = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    st.caption(f"Dernier rafraîchissement : {last_refresh} (automatique toutes les 30 min)")

    # Récupérer les événements (le cache Streamlit sera utilisé pour l'interface)
    @st.cache_data(ttl=1800)
    def get_cached_events():
        return get_events()

    events = get_cached_events()

    # Vérifier les mises à jour (notifications) – cette partie s'exécute à chaque chargement
    if events:
        check_and_notify_updates(events)

    if not events:
        st.warning("Aucune annonce économique US prévue aujourd'hui. Consultez plus tard.")
        return

    # Filtrer par impact
    impact_filter = st.multiselect(
        "Filtrer par impact",
        options=["high", "medium", "low"],
        default=["high", "medium", "low"]
    )
    filtered = [e for e in events if e["impact"] in impact_filter]

    def sort_key(e):
        t = e["time"].replace(" UTC", "").replace(":", "")
        return int(t) if t.isdigit() else 0
    filtered.sort(key=sort_key)

    df = pd.DataFrame(filtered)
    df = df.rename(columns={
        "event": "Événement",
        "time": "Heure (UTC)",
        "impact": "Impact",
        "actual": "Réel",
        "forecast": "Prévision",
        "previous": "Précédent"
    })

    def highlight_impact(val):
        if val == "high": return "background-color: #ff6b6b; color: white;"
        elif val == "medium": return "background-color: #ffd93d; color: black;"
        elif val == "low": return "background-color: #6bcb77; color: white;"
        return ""
    styled_df = df.style.applymap(highlight_impact, subset=["Impact"])
    st.dataframe(styled_df, use_container_width=True, height=400)

    st.subheader("📖 Interprétations")
    for idx, row in df.iterrows():
        with st.expander(f"{row['Événement']} – {row['Heure (UTC)']} ({row['Impact'].upper()})"):
            col1, col2 = st.columns([2, 1])
            with col1:
                a = generate_analysis(row)
                st.markdown(f"**Description :** {a['description']}")
                st.markdown(f"**Catégorie :** {a['category']}")
                st.markdown(f"**Interprétation :**")
                for line in a['interpretation'].split('\n'):
                    st.markdown(f"   {line}")
                st.markdown(f"**Stratégie :** {a['strategy']}")
            with col2:
                st.metric("Réel", row["Réel"] if row["Réel"] != "N/A" else "⏳ En attente")
                st.metric("Prévision", row["Prévision"])
                st.metric("Précédent", row["Précédent"])

    st.info(f"📌 {len(filtered)} annonces affichées (sur {len(events)} au total)")

    if st.button("🔄 Forcer le rafraîchissement maintenant"):
        st.cache_data.clear()
        st.rerun()

    st.markdown("---")
    st.caption("Données fournies par Parse.bot / ForexFactory | Rafraîchissement automatique toutes les 30 min.")

# ============================================================
# 11. ENTRÉE PRINCIPALE
# ============================================================
if __name__ == "__main__":
    # Détecter le mode batch : paramètre en ligne de commande ou paramètre d'URL
    if len(sys.argv) > 1 and sys.argv[1] == "--batch":
        run_batch_mode()
    else:
        # Pour Streamlit Cloud, on peut aussi utiliser st.query_params
        try:
            query_params = st.query_params
            if query_params.get("action") == ["batch"]:
                run_batch_mode()
                st.write("✅ Exécution batch terminée.")
                sys.exit(0)
        except Exception:
            pass
        show_streamlit_interface()

def classify_event(event_name: str) -> Dict:
    for key, knowledge in INDICATOR_KNOWLEDGE.items():
        if key.lower() in event_name.lower():
            return knowledge
    return {
        "category": "Autre",
        "description": "Événement économique à surveiller.",
        "thresholds": {},
        "strategy": "Consulter les détails de l'annonce."
    }

def generate_analysis(event: Dict) -> Dict:
    event_name = event.get("event", "Inconnu")
    knowledge = classify_event(event_name)
    interpretation = "\n".join([f"- {k}: {v}" for k, v in knowledge.get("thresholds", {}).items()])
    if not interpretation:
        interpretation = "Consulter les détails de l'annonce."
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
        "interpretation": interpretation
    }

# ============================================================
# 8. FONCTIONS DE GESTION DU CACHE ET NOTIFICATIONS (NOUVEAU)
# ============================================================

def load_json(filename: str, default: List = None) -> List:
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else []

def save_json(filename: str, data: List):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

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
            response = requests.post(url, json={"chat_id": chat_id, "text": chunk}, timeout=10)
            response.raise_for_status()
        except Exception as e:
            print(f"❌ Erreur d'envoi chunk {idx+1} : {e}")
            success = False
    return success

def check_and_notify_updates(new_events: List[Dict]):
    """
    Compare les nouveaux événements avec le cache.
    Envoie une notification Telegram pour chaque événement :
    - dont 'actual' passe de "N/A" à une valeur réelle,
    - ou qui est complètement nouveau (rare en cours de journée).
    Utilise un fichier 'notified_events.json' pour ne pas renvoyer deux fois le même.
    """
    old_events = load_json(CACHE_FILE, [])
    notified_ids = load_json(NOTIFIED_FILE, [])

    notified_set = set(notified_ids)
    updates = []
    old_map = {(e.get("event_id"), e["event"], e["time"]): e for e in old_events}

    for ne in new_events:
        key = (ne.get("event_id"), ne["event"], ne["time"])
        if key in old_map:
            old_actual = old_map[key].get("actual", "N/A")
            new_actual = ne.get("actual", "N/A")
            if old_actual == "N/A" and new_actual != "N/A" and ne.get("event_id") not in notified_set:
                updates.append(ne)
        else:
            # Nouvel événement (rare)
            if ne.get("event_id") not in notified_set:
                updates.append(ne)

    if updates:
        lines = [f"📊 **Nouvelles annonces économiques – {TODAY}**", ""]
        for ev in updates:
            if ev["actual"] != "N/A":
                lines.append(f"✅ **{ev['event']}** à {ev['time']} (impact: {ev['impact'].upper()})")
                lines.append(f"   🎯 Réel: **{ev['actual']}** | Prévision: {ev['forecast']} | Précédent: {ev['previous']}")
            else:
                lines.append(f"➕ **{ev['event']}** à {ev['time']} (impact: {ev['impact'].upper()})")
                if ev["forecast"] != "N/A":
                    lines.append(f"   Prévision: {ev['forecast']} | Précédent: {ev['previous']}")
            lines.append("")
        lines.append("🔗 [Voir le calendrier](https://www.forexfactory.com/calendar)")
        msg = "\n".join(lines)
        if send_telegram_message(msg):
            for ev in updates:
                if ev.get("event_id"):
                    notified_set.add(ev["event_id"])
            save_json(NOTIFIED_FILE, list(notified_set))
            print(f"✅ Notification envoyée pour {len(updates)} événement(s).")
        else:
            print("❌ Échec de l'envoi de la notification.")
    else:
        print("ℹ️ Aucune nouvelle publication détectée.")

    # Sauvegarder le nouveau cache (même sans mise à jour)
    save_json(CACHE_FILE, new_events)

# ============================================================
# 9. GÉNÉRATION DU RAPPORT (inchangé)
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
        lines.append("   → Obtenez une clé gratuite sur https://fred.stlouisfed.org/docs/api/api_key.html")
        lines.append("")

    if not events:
        lines.append("📭 Aucune annonce économique US prévue aujourd'hui.")
        lines.append("")
        lines.append("💡 Conseil : Consultez https://www.forexfactory.com/calendar pour vérifier.")
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
            lines.append(f"   🔗 Calendrier : https://www.forexfactory.com/calendar?day={TODAY}")
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
# 10. EXÉCUTION PRINCIPALE (MODIFIÉE)
# ============================================================

def main():
    # Vérifier si on est en mode "check" (uniquement mises à jour)
    check_mode = "--check" in sys.argv

    print(f"🚀 BOT UTC - Annonces du {TODAY_DISPLAY}" + (" (mode vérification)" if check_mode else ""))

    # Récupérer les événements
    events, fred_data = get_events()

    if not events:
        print("❌ Aucune annonce US trouvée.")
        # On quitte sans envoyer de rapport (on ne veut pas spammer)
        return

    # --- Étape 1 : Toujours vérifier et notifier les mises à jour ---
    check_and_notify_updates(events)

    # --- Étape 2 : Envoyer le rapport complet si on est entre 01:00 et 01:05 UTC ---
    # et si on n'est pas en mode check (ou on peut le faire quand même)
    now_utc = datetime.now(timezone.utc)
    if now_utc.hour == 1 and now_utc.minute < 5 and not check_mode:
        print("🕐 Envoi du rapport complet (01:00 UTC)...")
        report = generate_report(events, fred_data)
        print("\n" + report)
        if send_telegram_message(report):
            print("✅ Rapport complet envoyé sur Telegram.")
        else:
            print("⚠️ Échec de l'envoi du rapport complet.")

        with open(f"rapport_annonces_{TODAY}.txt", "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n📁 Rapport sauvegardé.")
    else:
        if not check_mode:
            print("ℹ️ Heure actuelle : pas d'envoi de rapport complet (attendu entre 01:00 et 01:05 UTC).")

if __name__ == "__main__":
    main()
