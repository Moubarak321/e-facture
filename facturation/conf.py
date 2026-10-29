"""Paramètres fiscaux et d'affichage, avec valeurs par défaut (aucune modification de settings.py requise).

Chaque valeur peut être redéfinie dans config/settings.py.
"""
from django.conf import settings

# Taux de taxe (%) par groupe. Seuls A (0 %) et B (18 %) sont confirmés par l'usage actuel ;
# C, D, E et F sont à VALIDER avec la DGI (voir « Infos API > Groupes de taxe »).
DEFAULT_TAX_RATES = {"A": 0, "B": 18, "C": 0, "D": 18, "E": 0, "F": 0}

# AIB : taux proposés dans l'éditeur. L'AIB est une information destinée au client
# (Total à payer = Total TTC − AIB) ; il n'est JAMAIS transmis à l'API e-MCF.
DEFAULT_AIB = {
    "A": {"label": "AIB 1 %", "rate": 1},
    "B": {"label": "AIB 5 %", "rate": 5},
    "C": {"label": "AIB 3 %", "rate": 3},
    "D": {"label": "AIB 8 %", "rate": 8},
}


def tax_rates():
    return getattr(settings, "EMCF_TAX_RATES", DEFAULT_TAX_RATES)


def aib_definitions():
    return getattr(settings, "EMCF_AIB", DEFAULT_AIB)


def aib_rate(code):
    return aib_definitions().get(code, {}).get("rate", 0) if code else 0


CURRENCY_SYMBOL = getattr(settings, "EMCF_CURRENCY_SYMBOL", "FCFA")
CURRENCY_WORDS = getattr(settings, "EMCF_CURRENCY_WORDS", "Francs CFA")

# Écart toléré (FCFA) entre le total local et le total renvoyé par la DGI.
TOTAL_TOLERANCE = getattr(settings, "EMCF_TOTAL_TOLERANCE", 1)
