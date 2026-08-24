#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BOT AUTOMATIQUE D'ANNONCES ÉCONOMIQUES
Version continue avec rafraîchissement toutes les 30 minutes
Sources : Parse.bot (API) → fallback JSON ForexFactory
Filtrage : annonces US du jour, lien vers Investing.com
"""

import os
import sys
import time
import json
import requests
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("⚠️ python-dotenv non installé. Utilisation des variables d'environnement système.")

# ============================================================
# 1. CONFIGURATION
# ============================================================

PARSEBOT_API_KEY = os.getenv("PARSEBOT_API_KEY", "")
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "VOTRE_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "VOTRE_CHAT_ID")
TIMEZONE_STR = os.getenv("TIMEZONE", "UTC")
REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL", "1800"))  # 30 minutes en secondes

# ============================================================
# 2. FONCTIONS UTILITAIRES
# ============================================================

def get_today_utc() -> tuple:
    now_utc = datetime.now(timezone.utc)
    return now_utc.strftime("%Y-%m-%d"), now_utc.strftime("%d/%m/%Y")

def log_message(msg: str):
    """Affiche un message avec timestamp."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{timestamp}] {msg}")

# ============================================================
# 3. SOURCE PRINCIPALE : PARSE.BOT API REST
# ============================================================

def get_parsebot_events() -> List[Dict]:
    """Récupère les annonces US du jour via l'API Parse.bot (REST)."""
    if not PARSEBOT_API_KEY:
        log_message("⚠️ PARSEBOT_API_KEY non définie.")
        return []

    url = "https://parse.bot/api/investing/economic-calendar"
    today, _ = get_today_utc()
    params = {
        "start_date": today,
        "end_date": today,
        "countries": "united states",
        "importance": "all"
    }
    headers = {
        "Authorization": f"Bearer {PARSEBOT_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.get(url, params=params, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        log_message(f"❌ Parse.bot API REST échec : {e}")
        return []

    events = []
    for item in data:
        event_name = item.get("event", item.get("event_name", "Unknown"))
        # Exclure les événements non-US mal étiquetés
        exclude_keywords = ["ECB", "German", "RPI", "Final", "Loan Prime", "Lagarde", "Trump"]
        if any(kw in event_name for kw in exclude_keywords):
            continue

        events.append({
            "time": item.get("time", item.get("release_time", "N/A")),
            "event": event_name,
            "country": "US",
            "impact": item.get("importance", item.get("impact", "low")).lower(),
            "actual": item.get("actual", item.get("actual_value", "N/A")),
            "forecast": item.get("forecast", item.get("forecast_value", "N/A")),
            "previous": item.get("previous", item.get("previous_value", "N/A")),
            "source": "Parse.bot REST"
        })

    return events

# ============================================================
# 4. SOURCE FALLBACK : FLUX JSON FOREXFACTORY
# ============================================================

def get_forexfactory_json() -> List[Dict]:
    """Récupère les annonces depuis le flux JSON public (fallback)."""
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        log_message(f"❌ Erreur flux JSON : {e}")
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

        currency = item.get("currency", "").upper()
        if currency != "USD":
            continue

        title = item.get("title", "")
        if any(kw in title for kw in exclude_keywords):
            continue

        events.append({
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
# 5. FRED API (snapshot)
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
            log_message(f"⚠️ FRED {series_id} échec : {e}")

    return result

# ============================================================
# 6. RÉCUPÉRATION DES ÉVÉNEMENTS
# ============================================================

def get_events() -> tuple:
    """Récupère les événements en priorisant Parse.bot."""
    events = []

    # 1. Parse.bot
    log_message("🔍 Récupération via Parse.bot...")
    events = get_parsebot_events()
    if events:
        log_message(f"✅ {len(events)} annonces US récupérées depuis Parse.bot.")
    else:
        # 2. Fallback JSON
        log_message("⚠️ Parse.bot indisponible. Tentative flux JSON ForexFactory...")
        events = get_forexfactory_json()
        if events:
            log_message(f"✅ {len(events)} annonces US récupérées depuis le flux JSON.")
        else:
            log_message("❌ Aucune annonce US trouvée.")

    fred_data = get_fred_snapshot()
    if fred_data:
        log_message("✅ Snapshot FRED récupéré.")

    return events, fred_data

# ============================================================
# 7. BASE DE CONNAISSANCES (ANALYSE)
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
    }
}

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
# 8. GÉNÉRATION DU RAPPORT (avec lien Investing.com)
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
    today, today_display = get_today_utc()
    lines = []
    lines.append("=" * 80)
    lines.append(f"📊 ANNONCES ÉCONOMIQUES DU {today_display} (UTC)")
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
            # ✅ Lien vers Investing.com (au lieu de ForexFactory)
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
# 9. ENVOI TELEGRAM
# ============================================================

def send_telegram_message(message: str) -> bool:
    token = TELEGRAM_BOT_TOKEN
    chat_id = TELEGRAM_CHAT_ID

    if token == "VOTRE_TOKEN" or chat_id == "VOTRE_CHAT_ID":
        log_message("⚠️ Token ou Chat ID non configurés.")
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
            log_message(f"❌ Erreur d'envoi chunk {idx+1} : {e}")
            success = False
    return success

# ============================================================
# 10. TÂCHE PRINCIPALE
# ============================================================

def job():
    """Exécute une itération du bot : récupère les données et envoie le rapport."""
    log_message("🔄 Début de la mise à jour...")
    events, fred_data = get_events()
    report = generate_report(events, fred_data)
    log_message("📄 Rapport généré.")

    if send_telegram_message(report):
        log_message("✅ Rapport envoyé sur Telegram.")
    else:
        log_message("⚠️ Échec de l'envoi Telegram.")

    # Sauvegarde locale avec horodatage
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"logs/rapport_{timestamp}.txt"
    os.makedirs("logs", exist_ok=True)
    with open(filename, "w", encoding="utf-8") as f:
        f.write(report)
    log_message(f"📁 Rapport sauvegardé dans {filename}")

# ============================================================
# 11. BOUCLE PRINCIPALE (rafraîchissement toutes les 30 min)
# ============================================================

def main():
    log_message("🚀 BOT DÉMARRÉ - Mode continu avec rafraîchissement toutes les 30 min.")
    log_message(f"📅 Fuseau horaire : {TIMEZONE_STR}")
    log_message(f"⏱️ Intervalle : {REFRESH_INTERVAL} secondes")

    # Exécution immédiate au démarrage
    job()

    # Boucle infinie
    while True:
        try:
            time.sleep(REFRESH_INTERVAL)
            job()
        except KeyboardInterrupt:
            log_message("⏹️ Arrêt demandé par l'utilisateur.")
            break
        except Exception as e:
            log_message(f"❌ Erreur inattendue dans la boucle : {e}")
            # On attend un peu avant de réessayer
            time.sleep(60)

if __name__ == "__main__":
    main()
