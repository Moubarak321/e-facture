"""Calculs financiers d'une facture (Python pur, sans dépendance Django, 100 % Decimal).

Règles (identiques côté navigateur et côté serveur ; le serveur fait foi) :

* Prix unitaire TTC  = arrondi_0,01( prix unitaire HT × (100 + taux) / 100 )      -> envoyé à la DGI
* Montant TTC ligne  = arrondi_0,01( quantité × prix unitaire TTC )
* Montant HT ligne   = arrondi_0,01( quantité × prix unitaire HT )
* Taxe ligne         = TTC ligne − HT ligne
* Totaux de facture  = sommes des lignes, arrondies au franc (1 FCFA), HT + taxes = TTC
* AIB                = arrondi_1( total HT × taux AIB / 100 ). Information destinée au client,
                       JAMAIS transmise à la DGI : le total TTC reste celui de la facture normalisée.
* Total à payer      = total TTC − AIB (montant net réellement dû par le client).

Arrondi : demi supérieur (ROUND_HALF_UP). Les lignes de type « section » et « note » ne
participent à aucun calcul ; les sections reçoivent un sous-total (lignes qui les suivent
jusqu'à la section suivante). Une section sans ligne n'a pas de sous-total.
"""
from decimal import ROUND_HALF_UP, Decimal

D0 = Decimal(0)
CENT = Decimal("0.01")
UNIT = Decimal(1)


def q2(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def q0(value):
    return Decimal(value).quantize(UNIT, rounding=ROUND_HALF_UP)


def dec(value, default=D0):
    if value is None or value == "":
        return default
    return value if isinstance(value, Decimal) else Decimal(str(value))


def compute_line(quantity, rate, unit_ht=None, unit_ttc=None):
    """Calcule une ligne à partir du prix HT (nouvelles factures) ou du prix TTC (historique)."""
    rate, quantity = dec(rate), dec(quantity)
    if unit_ht is not None:
        unit_ht = q2(unit_ht)
        unit_ttc = q2(unit_ht * (100 + rate) / 100)
        ttc = q2(quantity * unit_ttc)
        ht = q2(quantity * unit_ht)
    elif unit_ttc is not None:
        unit_ttc = q2(unit_ttc)
        unit_ht = q2(unit_ttc * 100 / (100 + rate))
        ttc = q2(quantity * unit_ttc)
        ht = q2(ttc * 100 / (100 + rate))
    else:
        raise ValueError("unit_ht ou unit_ttc est requis")
    return {"unit_ht": unit_ht, "unit_ttc": unit_ttc, "ht": ht, "tax": ttc - ht, "ttc": ttc,
            "rate": rate}


def compute_document(rows, tax_rates, aib_rate=0, tax_labels=None):
    """rows : liste de dicts {kind: item|section|note, name, quantity, unit_ht | unit_ttc, tax_group}.

    Retourne les lignes enrichies (avec sous-totaux), le récapitulatif par groupe de taxe et les
    totaux. Les lignes `subtotal` sont insérées à la fin de chaque section non vide.
    """
    tax_labels = tax_labels or {}
    out, groups = [], {}
    sum_ht = sum_ttc = D0
    section = None  # section ouverte : {"ht", "tax", "ttc", "count"}

    def close_section():
        nonlocal section
        if section and section["count"]:
            out.append({"kind": "subtotal", "ht": section["ht"], "tax": section["tax"],
                        "ttc": section["ttc"]})
        section = None

    for row in rows:
        kind = row.get("kind", "item")
        if kind == "section":
            close_section()
            section = {"ht": D0, "tax": D0, "ttc": D0, "count": 0}
            out.append({"kind": "section", "name": row.get("name", "")})
        elif kind == "note":
            out.append({"kind": "note", "name": row.get("name", "")})
        elif kind == "item":
            group = row.get("tax_group") or "B"
            rate = dec(tax_rates.get(group, 0))
            line = compute_line(row.get("quantity"), rate, row.get("unit_ht"), row.get("unit_ttc"))
            sum_ht += line["ht"]
            sum_ttc += line["ttc"]
            g = groups.setdefault(group, {"group": group, "label": tax_labels.get(group, group),
                                          "rate": rate, "ht": D0, "tax": D0, "ttc": D0})
            g["ht"] += line["ht"]
            g["tax"] += line["tax"]
            g["ttc"] += line["ttc"]
            if section is not None:
                section["ht"] += line["ht"]
                section["tax"] += line["tax"]
                section["ttc"] += line["ttc"]
                section["count"] += 1
            out.append({"kind": "item", "name": row.get("name", ""),
                        "quantity": dec(row.get("quantity")), "tax_group": group,
                        "tax_label": tax_labels.get(group, group), **line})
        else:
            raise ValueError(f"type de ligne inconnu : {kind}")
    close_section()

    total_ttc, total_ht = q0(sum_ttc), q0(sum_ht)
    aib_rate = dec(aib_rate)
    aib_amount = q0(total_ht * aib_rate / 100) if aib_rate else D0
    return {
        "rows": out,
        "tax_summary": [groups[k] for k in sorted(groups)],
        "total_ht": total_ht,
        "total_tax": total_ttc - total_ht,
        "total_ttc": total_ttc,
        "aib_rate": aib_rate,
        "aib_amount": aib_amount,
        "total_payable": total_ttc - aib_amount,
        "item_count": sum(1 for r in out if r["kind"] == "item"),
    }
