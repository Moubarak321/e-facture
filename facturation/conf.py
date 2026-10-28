"""Paramètres fiscaux et d'affichage, avec valeurs par défaut (aucune modification de settings.py requise).

Chaque valeur peut être redéfinie dans config/settings.py.
"""
from django.conf import settings

# Taux de taxe (%) par groupe. Seuls A (0 %) et B (18 %) sont confirmés par l'usage actuel ;
# C, D, E et F sont à VALIDER avec la DGI (voir « Infos API > Groupes de taxe »).
DEFAULT_TAX_RATES = {"A": 0, "B": 18, "C": 0, "D": 18, "E": 0, "F": 0}

# AIB : taux et code envoyé à l'API e-MCF (`api`). L'API connaît « A » (1 %) et « B » (5 %) ;
# pour 3 % et 8 % aucun code API n'est défini : `api: None` bloque l'envoi à la DGI tant qu'il
# n'est pas renseigné (la facture reste enregistrable en brouillon et prévisualisable).
DEFAULT_AIB = {
    "A": {"label": "AIB 1 %", "rate": 1, "api": "A"},
    "B": {"label": "AIB 5 %", "rate": 5, "api": "B"},
    "C": {"label": "AIB 3 %", "rate": 3, "api": None},
    "D": {"label": "AIB 8 %", "rate": 8, "api": None},
}


def tax_rates():
    return getattr(settings, "EMCF_TAX_RATES", DEFAULT_TAX_RATES)


def aib_definitions():
    return getattr(settings, "EMCF_AIB", DEFAULT_AIB)


def aib_rate(code):
    return aib_definitions().get(code, {}).get("rate", 0) if code else 0


# L'AIB est-il AJOUTÉ au total à payer ? Règle non définie dans le projet : par défaut non
# (il est affiché à part). Pour les codes A/B, l'écart avec le total calculé par la DGI est
# détecté à la confirmation et bloque la facture.
AIB_ADDED_TO_TOTAL = getattr(settings, "EMCF_AIB_ADDED_TO_TOTAL", False)

CURRENCY_SYMBOL = getattr(settings, "EMCF_CURRENCY_SYMBOL", "FCFA")
CURRENCY_WORDS = getattr(settings, "EMCF_CURRENCY_WORDS", "Francs CFA")

# Écart toléré (FCFA) entre le total local et le total renvoyé par la DGI.
TOTAL_TOLERANCE = getattr(settings, "EMCF_TOTAL_TOLERANCE", 1)
