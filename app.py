#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Application Streamlit + Bot Telegram (mode batch inclus)
Annonces économiques US du jour, cache, notifications, rapport à 01:00 UTC.
"""

import os
import sys
import json
import requests
from datetime import datetime, timezone
from typing import List, Dict

# --- Import conditionnel de streamlit et pandas (seulement en mode interface) ---
if "--batch" not in sys.argv and "action" not in os.environ.get("QUERY_STRING", ""):
    import streamlit as st
    import pandas as pd
else:
    # Mode batch : stubs pour éviter les erreurs
    class DummySt:
        def cache_data(self, *args, **kwargs):
            return lambda f: f
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
# 1. CONFIGURATION (secrets Streamlit ou variables d'environnement)
# ============================================================
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
    # Filtrage minimal : seulement les événements clairement non-US
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
    # Fusion avec fallback (évite les doublons)
    ff_events = get_forexfactory_json()
    existing_keys = {(e["event"], e["time"]) for e in events}
    for ev in ff_events:
        if (ev["event"], ev["time"]) not in existing_keys:
            events.append(ev)
    return events

# ============================================================
# 5. FRED API
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
# 6. BASE DE CONNAISSANCES (INTÉGRALE)
# ============================================================
INDICATOR_KNOWLEDGE = {
    # ============================================================
    # 1. INDICATEURS MONÉTAIRES (FED)
    # ============================================================
    "FOMC": {
        "category": "Monétaire",
        "description": "Décision de taux de la Fed. Le plus important pour le Dollar.",
        "thresholds": {
            "Hawkish (hausse de taux ou ton restrictif)": "Dollar très haussier (anticipation de resserrement monétaire)",
            "Dovish (baisse de taux ou ton accommodant)": "Dollar très baissier (assouplissement attendu)"
        },
        "strategy": "Attendre la conférence de presse. Ne pas trader les 15 premières minutes. Suivre le 'dot plot' et les prévisions économiques."
    },
    "FOMC Minutes": {
        "category": "Monétaire",
        "description": "Compte‑rendu détaillé de la réunion du FOMC. Révèle les débats internes.",
        "thresholds": {
            "Hawkish (préoccupation sur l'inflation)": "Dollar haussier (taux plus élevés)",
            "Dovish (préoccupation sur l'emploi)": "Dollar baissier (maintien des taux)"
        },
        "strategy": "Comparer le ton avec le communiqué précédent. Les divergences sont importantes."
    },
    "Fed Speech": {
        "category": "Monétaire",
        "description": "Discours d'un membre de la Fed. Peut modifier les anticipations de taux.",
        "thresholds": {
            "Hawkish (inflation prioritaire)": "Dollar haussier",
            "Dovish (emploi prioritaire)": "Dollar baissier"
        },
        "strategy": "Surveiller les mots clés : 'persistent', 'transitory', 'patient', 'data‑dependent'."
    },
    "Fed Funds Rate": {
        "category": "Monétaire",
        "description": "Taux des fonds fédéraux. Le principal levier de la politique monétaire.",
        "thresholds": {
            "Hausse de 25 pb ou plus": "Dollar haussier (attractivité des rendements)",
            "Baisse de 25 pb ou plus": "Dollar baissier (fuite vers d'autres devises)"
        },
        "strategy": "Anticiper les mouvements via le marché des futures Fed Funds (CME FedWatch)."
    },

    # ============================================================
    # 2. INFLATION
    # ============================================================
    "CPI": {
        "category": "Inflation",
        "description": "Indice des prix à la consommation. Mesure l'inflation au niveau des ménages.",
        "thresholds": {
            "Core CPI (mensuel) > 0.4%": "Dollar très haussier (inflation persistante → Fed hawkish)",
            "Core CPI (mensuel) < 0.1%": "Dollar baissier (désinflation → Fed dovish)"
        },
        "strategy": "Regarder le Core CPI (hors alimentation et énergie). Attendre 15 minutes après la publication."
    },
    "Core CPI": {
        "category": "Inflation",
        "description": "Inflation sous‑jacente (hors alimentation et énergie). L'indicateur préféré des marchés.",
        "thresholds": {
            "> 0.4% (mensuel)": "Dollar très haussier",
            "< 0.1% (mensuel)": "Dollar baissier"
        },
        "strategy": "Le Core est plus important que le Headline. Une tendance haussière durable = dollar fort."
    },
    "PPI": {
        "category": "Inflation",
        "description": "Indice des prix à la production. Indicateur avancé de l'inflation future.",
        "thresholds": {
            "> 0.5% (mensuel)": "Dollar haussier (pression des coûts → inflation à venir)",
            "< 0.1% (mensuel)": "Dollar baissier (pas de pression inflationniste)"
        },
        "strategy": "Anticipe le CPI dans 2 à 6 semaines. Surveiller le 'Core PPI'."
    },
    "Core PCE": {
        "category": "Inflation",
        "description": "Indicateur d'inflation officiel de la Fed. Mesure la dépense de consommation personnelle hors alimentation/énergie.",
        "thresholds": {
            "> 0.3% (mensuel)": "Dollar très haussier (alerte pour la Fed)",
            "< 0.2% (mensuel)": "Dollar baissier (pas de menace inflationniste)"
        },
        "strategy": "L'indicateur roi. Une surprise fait bouger le Dollar violemment. Suivre aussi le PCE global."
    },
    "PCE": {
        "category": "Inflation",
        "description": "Indice des prix de la consommation personnelle (global).",
        "thresholds": {
            "> 0.4%": "Dollar haussier",
            "< 0.2%": "Dollar baissier"
        },
        "strategy": "Moins suivi que le Core PCE, mais peut influencer la Fed si écart important."
    },

    # ============================================================
    # 3. EMPLOI
    # ============================================================
    "NFP": {
        "category": "Emploi",
        "description": "Créations d'emplois non‑agricoles (Non‑Farm Payrolls). Le plus volatil des indicateurs.",
        "thresholds": {
            "> +200k": "Dollar haussier (économie robuste)",
            "+100k à +200k": "Neutre (dans la moyenne)",
            "< +100k": "Dollar baissier (ralentissement)",
            "Négatif": "Dollar très baissier (récession)"
        },
        "strategy": "Regarder les révisions des mois précédents et les salaires (AHE)."
    },
    "Unemployment Rate": {
        "category": "Emploi",
        "description": "Taux de chômage officiel. Un taux bas soutient la consommation.",
        "thresholds": {
            "En baisse": "Dollar haussier (marché du travail tendu)",
            "En hausse > 0.2%": "Dollar baissier (alerte Sahm)"
        },
        "strategy": "Vérifier le taux de participation et la durée du chômage."
    },
    "AHE": {
        "category": "Emploi",
        "description": "Salaires horaires moyens (Average Hourly Earnings). Moteur de l'inflation persistante.",
        "thresholds": {
            "> 0.4% (mensuel)": "Dollar très haussier (risque inflationniste)",
            "< 0.2% (mensuel)": "Dollar baissier (pas de pression salariale)"
        },
        "strategy": "Un AHE élevé est plus important qu'un NFP élevé pour l'inflation."
    },
    "Jobless Claims": {
        "category": "Emploi",
        "description": "Inscriptions hebdomadaires au chômage. Indicateur avancé du marché du travail.",
        "thresholds": {
            "MA4 < 200k": "Dollar haussier (marché du travail tendu)",
            "MA4 > 220k": "Dollar baissier (dégradation)"
        },
        "strategy": "Suivre la moyenne mobile sur 4 semaines (MA4) pour lisser la volatilité."
    },
    "JOLTS": {
        "category": "Emploi",
        "description": "Offres d'emploi (Job Openings and Labor Turnover Survey). Mesure la demande de travail.",
        "thresholds": {
            "Ratio offres/chômeurs > 1.5": "Dollar haussier (tension salariale)",
            "Ratio < 1.0": "Dollar baissier (marché du travail détendu)"
        },
        "strategy": "Regarder le taux de démission (quit rate) : signe de confiance des salariés."
    },
    "ADP": {
        "category": "Emploi",
        "description": "Rapport privé sur l'emploi (publié par ADP). 2 jours avant le NFP.",
        "thresholds": {
            "> +200k": "Signal haussier pour l'emploi (mais à confirmer avec NFP)"
        },
        "strategy": "⚠️ Ne pas trader sur l'ADP seul. Servir de pré‑indicateur pour le NFP."
    },

    # ============================================================
    # 4. CONSOMMATION
    # ============================================================
    "Retail Sales": {
        "category": "Consommation",
        "description": "Ventes au détail. Mesure la consommation des ménages (2/3 du PIB).",
        "thresholds": {
            "Control Group > 0.5%": "Dollar haussier (consommation robuste)",
            "Control Group < 0.1%": "Dollar baissier (ralentissement de la consommation)"
        },
        "strategy": "Regarder le 'Control Group' (exclut auto, essence, matériaux de construction) pour une tendance de base."
    },
    "Consumer Confidence": {
        "category": "Consommation",
        "description": "Indice de confiance des consommateurs (Conference Board). Anticipe les dépenses.",
        "thresholds": {
            "> 110": "Dollar haussier (optimisme → consommation)",
            "< 90": "Dollar baissier (pessimisme → épargne)"
        },
        "strategy": "Un indicateur avancé de la consommation. À surveiller avec l'indice du Michigan."
    },
    "University of Michigan Consumer Sentiment": {
        "category": "Consommation",
        "description": "Indice de confiance des consommateurs (Université du Michigan). Version préliminaire et finale.",
        "thresholds": {
            "Préliminaire > 75": "Dollar haussier",
            "Préliminaire < 65": "Dollar baissier"
        },
        "strategy": "La version préliminaire a plus d'impact. Regarder la composante 'inflation anticipée'."
    },

    # ============================================================
    # 5. CROISSANCE
    # ============================================================
    "GDP": {
        "category": "Croissance",
        "description": "Produit intérieur brut. Taux de croissance annualisé de l'économie.",
        "thresholds": {
            "> 3.0%": "Dollar haussier (croissance soutenue)",
            "2.0% - 3.0%": "Neutre",
            "< 1.5%": "Dollar baissier (ralentissement)"
        },
        "strategy": "Regarder la composante consommation (PCE) et les investissements des entreprises."
    },
    "ISM Manufacturing": {
        "category": "Croissance",
        "description": "PMI manufacturier (Institute for Supply Management). Seuil 50 = expansion/contraction.",
        "thresholds": {
            "> 50": "Dollar haussier (activité industrielle en expansion)",
            "< 50": "Dollar baissier (contraction industrielle)"
        },
        "strategy": "Regarder la composante 'Prix Payés' (indicateur avancé de l'inflation)."
    },
    "ISM Services": {
        "category": "Croissance",
        "description": "PMI des services. 4x plus important que le Manufacturing pour l'économie US.",
        "thresholds": {
            "> 55": "Dollar haussier (expansion solide)",
            "< 50": "Dollar baissier (contraction)"
        },
        "strategy": "Regarder la composante 'Prices Paid' > 65 → signal inflationniste → dollar haussier."
    },
    "Industrial Production": {
        "category": "Industrie",
        "description": "Production industrielle (usines, mines, services publics).",
        "thresholds": {
            "> 0.5% (mensuel)": "Dollar haussier (activité industrielle dynamique)",
            "< 0.1%": "Dollar baissier (contraction)"
        },
        "strategy": "À coupler avec le taux d'utilisation des capacités."
    },
    "Capacity Utilization": {
        "category": "Industrie",
        "description": "Taux d'utilisation des capacités de production. Au‑delà de 80%, tensions inflationnistes.",
        "thresholds": {
            "> 79%": "Dollar haussier (pression sur les prix)",
            "< 75%": "Dollar baissier (sous‑utilisation)"
        },
        "strategy": "Un taux élevé renforce les anticipations de hausse de taux."
    },
    "Factory Orders": {
        "category": "Industrie",
        "description": "Commandes aux usines (biens durables et non durables).",
        "thresholds": {
            "> 0.5%": "Dollar haussier (demande industrielle forte)",
            "< -0.5%": "Dollar baissier"
        },
        "strategy": "Privilégier les commandes de biens durables (core)."
    },
    "Durable Goods Orders": {
        "category": "Industrie",
        "description": "Commandes de biens durables (avions, machines, équipements).",
        "thresholds": {
            "> 0.5% (hors transport)": "Dollar haussier (investissements des entreprises)",
            "< -0.5%": "Dollar baissier"
        },
        "strategy": "Exclure le transport pour avoir le 'core' plus fiable."
    },

    # ============================================================
    # 6. COMMERCE EXTÉRIEUR
    # ============================================================
    "Trade Balance": {
        "category": "Commerce extérieur",
        "description": "Balance commerciale (exportations – importations). Un déficit élevé pèse sur le dollar.",
        "thresholds": {
            "Déficit plus important que prévu": "Dollar baissier (fuite de capitaux)",
            "Déficit moins important que prévu": "Dollar haussier (meilleure compétitivité)"
        },
        "strategy": "Surveiller l'évolution sur 3 mois. Un déficit qui se creuse est baissier."
    },
    "Current Account": {
        "category": "Commerce extérieur",
        "description": "Balance des paiements courants (biens, services, revenus, transferts).",
        "thresholds": {
            "Déficit en hausse": "Dollar baissier (besoin de financement extérieur)",
            "Déficit en baisse": "Dollar haussier"
        },
        "strategy": "Un déficit > 3% du PIB est un signal baissier à long terme."
    },

    # ============================================================
    # 7. IMMOBILIER
    # ============================================================
    "HPI": {
        "category": "Immobilier",
        "description": "Indice des prix des logements (House Price Index). Reflète la richesse immobilière.",
        "thresholds": {
            "HPI en hausse (> 0.5%)": "Dollar haussier (effet de richesse → consommation)",
            "HPI en baisse ou stable": "Dollar baissier (faiblesse du secteur)"
        },
        "strategy": "L'immobilier est un pilier de l'économie US. Une hausse soutenue est positive pour le dollar."
    },
    "Building Permits": {
        "category": "Immobilier",
        "description": "Nombre de permis de construire délivrés. Indicateur avancé de l'activité immobilière.",
        "thresholds": {
            "En hausse (> 2%)": "Dollar haussier (confiance des promoteurs)",
            "En baisse (< -2%)": "Dollar baissier (ralentissement à venir)"
        },
        "strategy": "À surveiller avec les mises en chantier."
    },
    "Housing Starts": {
        "category": "Immobilier",
        "description": "Nombre de logements commencés. Mesure la construction neuve.",
        "thresholds": {
            "> 1,5 million (annualisé)": "Dollar haussier (économie dynamique)",
            "< 1,2 million": "Dollar baissier"
        },
        "strategy": "Un chiffre élevé soutient la croissance et l'emploi."
    },
    "NAHB Housing Market Index": {
        "category": "Immobilier",
        "description": "Indice du marché immobilier des constructeurs (National Association of Home Builders).",
        "thresholds": {
            "> 50": "Dollar haussier (optimisme des constructeurs)",
            "< 40": "Dollar baissier (secteur en crise)"
        },
        "strategy": "Un indice en hausse annonce une reprise de la construction."
    },

    # ============================================================
    # 8. ÉNERGIE (rapports EIA)
    # ============================================================
    "Stocks de pétrole brut": {
        "category": "Énergie",
        "description": "Rapport hebdomadaire des stocks de pétrole brut (hors réserve stratégique) publié par l'EIA. Mesure l'équilibre offre/demande.",
        "thresholds": {
            "Baisse inattendue (> 1M)": "Offre tendue → Prix du pétrole haussier. Dollar réagit via l'inflation attendue.",
            "Hausse inattendue (> 1M)": "Offre abondante → Prix du pétrole baissier.",
            "Stocks de Cushing en baisse": "Haussier pour le WTI (point de livraison)."
        },
        "strategy": "Comparez strictement la variation réelle au consensus. La variation des stocks de Cushing est un bonus."
    },
    "EIA": {
        "category": "Énergie",
        "description": "Rapport complet de l'Energy Information Administration sur la production, les stocks et la demande.",
        "thresholds": {
            "Stocks d'essence en baisse": "Signe de forte consommation intérieure → soutient le USD.",
            "Taux d'utilisation des raffineries en hausse": "Demande de brut élevée → pétrole haussier.",
            "Importations en hausse": "Compense l'offre intérieure → neutre à baissier."
        },
        "strategy": "Regardez le sous‑total 'Produits raffinés fournis' comme proxy de la demande réelle."
    },

    # ============================================================
    # 9. IMMOBILIER (suite – MBA, adjudications)
    # ============================================================
    "MBA": {
        "category": "Immobilier",
        "description": "Enquête hebdomadaire de la Mortgage Bankers Association sur les demandes de prêts immobiliers (achat et refinancement).",
        "thresholds": {
            "Indice d'achat en hausse": "Forte demande immobilière → USD haussier.",
            "Taux hypothécaire en hausse": "Ralentit le marché → USD baissier."
        },
        "strategy": "L'Indice d'achat est plus important que le Refinance pour la santé économique réelle."
    },
    "Adjudication": {
        "category": "Dette",
        "description": "Vente aux enchères d'obligations du Trésor américain. Teste l'appétit des investisseurs pour la dette US.",
        "thresholds": {
            "Rendement en baisse": "Forte demande → USD baissier (fuite vers la sécurité).",
            "Rendement en hausse": "Faible demande → USD haussier (les taux longs montent)."
        },
        "strategy": "Regarder le ratio 'Bid-to-Cover' (couvertures) et le rendement indirect (demande étrangère)."
    },

    # ============================================================
    # 10. INDICATEURS SUPPLÉMENTAIRES (à compléter)
    # ============================================================
    "Consumer Sentiment": {
        "category": "Consommation",
        "description": "Sentiment des consommateurs (peut être utilisé comme alias pour Michigan).",
        "thresholds": {},
        "strategy": "Voir University of Michigan Consumer Sentiment."
    },
    "Redbook": {
        "category": "Consommation",
        "description": "Ventes au détail hebdomadaires (Redbook). Indicateur avancé des ventes.",
        "thresholds": {
            "> 3%": "Dollar haussier (consommation robuste)",
            "< 1%": "Dollar baissier"
        },
        "strategy": "Peu volatil, mais suivi pour les tendances court terme."
    },
    "New Home Sales": {
        "category": "Immobilier",
        "description": "Ventes de logements neufs. Donnée mensuelle.",
        "thresholds": {
            "> 700k": "Dollar haussier (marché immobilier dynamique)",
            "< 600k": "Dollar baissier"
        },
        "strategy": "Souvent révisé. À combiner avec les permis de construire."
    },
    "Existing Home Sales": {
        "category": "Immobilier",
        "description": "Ventes de logements existants. Majorité du marché immobilier.",
        "thresholds": {
            "> 5 millions": "Dollar haussier",
            "< 4 millions": "Dollar baissier"
        },
        "strategy": "Moins important que les ventes de logements neufs, mais donne le pouls du marché."
    }
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
# 8. GÉNÉRATION DU RAPPORT (pour le mode batch)
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
# 9. MODE BATCH (exécution console)
# ============================================================
def run_batch_mode():
    print(f"🚀 BOT UTC - Annonces du {TODAY_DISPLAY}")
    events = get_events()
    fred_data = get_fred_snapshot() if FRED_API_KEY else {}

    if not events:
        print("❌ Aucune annonce US trouvée.")
        return

    check_and_notify_updates(events)

    now_utc = datetime.now(timezone.utc)
    if now_utc.hour == 1 and now_utc.minute < 5:
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
        print("ℹ️ Heure actuelle : pas d'envoi de rapport complet (attendu entre 01:00 et 01:05 UTC).")

# ============================================================
# 10. INTERFACE STREAMLIT
# ============================================================
def show_streamlit_interface():
    st.set_page_config(page_title="Annonces Économiques US", layout="wide")
    st.title("📊 Annonces Économiques US – Jour en cours")
    last_refresh = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    st.caption(f"Dernier rafraîchissement : {last_refresh} (automatique toutes les 30 min)")

    @st.cache_data(ttl=1800)
    def get_cached_events():
        return get_events()

    events = get_cached_events()
    if events:
        check_and_notify_updates(events)

    if not events:
        st.warning("Aucune annonce économique US prévue aujourd'hui. Consultez plus tard.")
        return

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

    # CORRECTION : applymap → map
    styled_df = df.style.map(highlight_impact, subset=["Impact"])
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
