"""Montant en toutes lettres (français) : 17752 -> « Dix-Sept Mille Sept Cent Cinquante-Deux »."""

_UNITS = ["zéro", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf", "dix",
          "onze", "douze", "treize", "quatorze", "quinze", "seize"]
_TENS = {2: "vingt", 3: "trente", 4: "quarante", 5: "cinquante", 6: "soixante"}


def _below_100(n, final=True):
    if n < 17:
        return _UNITS[n]
    if n < 20:
        return "dix-" + _UNITS[n - 10]
    if n < 70:
        tens, unit = divmod(n, 10)
        if unit == 0:
            return _TENS[tens]
        return _TENS[tens] + (" et un" if unit == 1 else "-" + _UNITS[unit])
    if n < 80:  # soixante-dix … soixante-dix-neuf
        return "soixante et onze" if n == 71 else "soixante-" + _below_100(n - 60)
    if n == 80:
        return "quatre-vingts" if final else "quatre-vingt"
    return "quatre-vingt-" + _below_100(n - 80)


def _below_1000(n, final=True):
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        word = "cent" if hundreds == 1 else _UNITS[hundreds] + " cent"
        if hundreds > 1 and rest == 0 and final:
            word += "s"
        parts.append(word)
    if rest:
        parts.append(_below_100(rest, final))
    return " ".join(parts)


def int_to_words(n):
    if n == 0:
        return "zéro"
    if n < 0:
        return "moins " + int_to_words(-n)
    parts = []
    for scale, singular, plural in ((10 ** 9, "milliard", "milliards"), (10 ** 6, "million", "millions")):
        count, n = divmod(n, scale)
        if count:
            parts.append(_below_1000(count, True) + " " + (singular if count == 1 else plural))
    thousands, rest = divmod(n, 1000)
    if thousands:
        parts.append("mille" if thousands == 1 else _below_1000(thousands, False) + " mille")
    if rest:
        parts.append(_below_1000(rest, True))
    return " ".join(parts)


def _title(text):
    words = []
    for word in text.split(" "):
        words.append(word if word == "et" else "-".join(p.capitalize() for p in word.split("-")))
    return " ".join(words)


def amount_in_words(amount, currency="Francs CFA"):
    """Montant entier (FCFA) en lettres, ex. « Dix-Sept Mille Sept Cent Cinquante-Deux Francs CFA »."""
    value = int(round(float(amount))) if not isinstance(amount, int) else amount
    unit = "Franc CFA" if abs(value) == 1 and currency == "Francs CFA" else currency
    return f"{_title(int_to_words(value))} {unit}"
