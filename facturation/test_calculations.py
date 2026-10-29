"""Tests sans Django : calculs financiers et montant en lettres (python -m unittest)."""
import unittest
from decimal import Decimal as D

try:
    from . import calculations as calc
    from .numwords import amount_in_words, int_to_words
except ImportError:  # exécution directe hors package
    import calculations as calc
    from numwords import amount_in_words, int_to_words

RATES = {"A": 0, "B": 18, "C": 0, "D": 18, "E": 0, "F": 0}


def item(name, qty, ht, group="B"):
    return {"kind": "item", "name": name, "quantity": D(qty), "unit_ht": D(ht), "tax_group": group}


class LineTests(unittest.TestCase):
    def test_exemple_du_cahier_des_charges(self):
        line = calc.compute_line(2, 18, unit_ht=D("100000"))
        self.assertEqual((line["ht"], line["tax"], line["ttc"]), (D("200000"), D("36000"), D("236000")))
        self.assertEqual(line["unit_ttc"], D("118000.00"))

    def test_exonere_ne_genere_pas_de_tva(self):
        line = calc.compute_line(1, 0, unit_ht=D("15000"))
        self.assertEqual((line["tax"], line["ttc"]), (D("0.00"), D("15000.00")))

    def test_arrondi_demi_superieur(self):
        self.assertEqual(calc.q2(D("0.005")), D("0.01"))
        self.assertEqual(calc.q0(D("2.5")), D("3"))
        self.assertEqual(calc.q0(D("3.5")), D("4"))  # pas d'arrondi bancaire

    def test_pas_de_flottants(self):
        line = calc.compute_line(3, 18, unit_ht=D("0.10"))
        self.assertEqual(line["ttc"], D("0.36"))  # 3 × 0,12

    def test_historique_en_ttc(self):
        line = calc.compute_line(1, 18, unit_ttc=D("118000"))
        self.assertEqual((line["ht"], line["tax"]), (D("100000.00"), D("18000.00")))

    def test_ni_ht_ni_ttc(self):
        with self.assertRaises(ValueError):
            calc.compute_line(1, 18)


class DocumentTests(unittest.TestCase):
    def sample(self):
        return [
            {"kind": "section", "name": "A — PORTAGE SALARIAL"},
            item("Prestation de portage salarial", 2, 100000),
            item("Frais de gestion", 1, 20000),
            {"kind": "note", "name": "Les prestations correspondent à la période du contrat."},
            {"kind": "section", "name": "B — NOTE DE FRAIS"},
            item("Frais administratifs", 1, 15000, "A"),
        ]

    def test_exemple_a_b_du_cahier_des_charges(self):
        r = calc.compute_document(self.sample(), RATES)
        subs = [x for x in r["rows"] if x["kind"] == "subtotal"]
        self.assertEqual([(s["ht"], s["tax"], s["ttc"]) for s in subs],
                         [(D("220000"), D("39600"), D("259600")), (D("15000"), D("0"), D("15000"))])
        self.assertEqual((r["total_ht"], r["total_tax"], r["total_ttc"]),
                         (D("235000"), D("39600"), D("274600")))
        self.assertEqual(r["item_count"], 3)

    def test_ordre_conserve_et_sous_total_apres_la_section(self):
        kinds = [x["kind"] for x in calc.compute_document(self.sample(), RATES)["rows"]]
        self.assertEqual(kinds, ["section", "item", "item", "note", "subtotal", "section", "item", "subtotal"])

    def test_notes_et_titres_ne_comptent_pas(self):
        r = calc.compute_document([{"kind": "section", "name": "X"}, {"kind": "note", "name": "n"}], RATES)
        self.assertEqual((r["total_ttc"], r["item_count"]), (D("0"), 0))

    def test_section_vide_sans_montant_artificiel(self):
        rows = [{"kind": "section", "name": "Vide"}, {"kind": "section", "name": "B"}, item("x", 1, 100)]
        kinds = [x["kind"] for x in calc.compute_document(rows, RATES)["rows"]]
        self.assertEqual(kinds, ["section", "section", "item", "subtotal"])

    def test_lignes_independantes_sans_section(self):
        r = calc.compute_document([item("seule", 1, 1000)], RATES)
        self.assertEqual([x["kind"] for x in r["rows"]], ["item"])
        self.assertEqual(r["total_ttc"], D("1180"))

    def test_lignes_avant_la_premiere_section_hors_sous_total(self):
        r = calc.compute_document([item("libre", 1, 1000), {"kind": "section", "name": "S"}, item("dans S", 1, 500)], RATES)
        sub = [x for x in r["rows"] if x["kind"] == "subtotal"]
        self.assertEqual(len(sub), 1)
        self.assertEqual(sub[0]["ht"], D("500"))
        self.assertEqual(r["total_ht"], D("1500"))

    def test_coherence_totaux(self):
        r = calc.compute_document(self.sample(), RATES)
        self.assertEqual(r["total_ht"] + r["total_tax"], r["total_ttc"])
        self.assertEqual(sum(g["ttc"] for g in r["tax_summary"]), r["total_ttc"])

    def test_regimes_non_interchangeables(self):
        for group, expected in (("A", D("100")), ("B", D("118")), ("C", D("100")), ("D", D("118")),
                                ("E", D("100")), ("F", D("100"))):
            r = calc.compute_document([item("x", 1, 100, group)], RATES)
            self.assertEqual(r["total_ttc"], expected, group)
        # un régime paramétré autrement est bien pris en compte
        r = calc.compute_document([item("x", 1, 100, "E")], {**RATES, "E": 5})
        self.assertEqual(r["total_ttc"], D("105"))

    def test_total_a_payer_est_ttc_moins_aib(self):
        for rate, aib in ((3, D("3000")), (8, D("8000"))):
            r = calc.compute_document([item("x", 1, 100000)], RATES, aib_rate=rate)
            self.assertEqual(r["aib_amount"], aib)
            self.assertEqual(r["total_ttc"], D("118000"))                 # TTC DGI inchangé
            self.assertEqual(r["total_payable"], D("118000") - aib)       # net à payer

    def test_exemple_de_lecran_aib_3_pour_cent(self):
        # HT 152 174 · taxe 27 391 · TTC 179 565 · AIB 3 % = 4 565 · à payer 175 000
        r = calc.compute_document([item("x", 1, 152174)], RATES, aib_rate=3)
        self.assertEqual((r["total_ht"], r["total_tax"], r["total_ttc"]),
                         (D("152174"), D("27391"), D("179565")))
        self.assertEqual((r["aib_amount"], r["total_payable"]), (D("4565"), D("175000")))

    def test_sans_aib_a_payer_egal_ttc(self):
        r = calc.compute_document([item("x", 1, 1000)], RATES)
        self.assertEqual((r["aib_amount"], r["total_payable"]), (D("0"), r["total_ttc"]))

    def test_total_arrondi_au_franc(self):
        r = calc.compute_document([item("x", 1, D("100.50"))], RATES)  # 118,59
        self.assertEqual(r["total_ttc"], D("119"))
        self.assertEqual(r["total_ht"] + r["total_tax"], r["total_ttc"])

    def test_type_inconnu(self):
        with self.assertRaises(ValueError):
            calc.compute_document([{"kind": "autre"}], RATES)


class WordsTests(unittest.TestCase):
    def test_cas_usuels(self):
        cases = {0: "zéro", 1: "un", 16: "seize", 21: "vingt et un", 71: "soixante et onze",
                 77: "soixante-dix-sept", 80: "quatre-vingts", 81: "quatre-vingt-un",
                 91: "quatre-vingt-onze", 100: "cent", 200: "deux cents", 201: "deux cent un",
                 1000: "mille", 2000: "deux mille", 80000: "quatre-vingt mille",
                 200000: "deux cent mille", 1000000: "un million", 2000000: "deux millions",
                 17752: "dix-sept mille sept cent cinquante-deux"}
        for number, words in cases.items():
            self.assertEqual(int_to_words(number), words, number)

    def test_format_de_la_facture_de_reference(self):
        self.assertEqual(amount_in_words(17752), "Dix-Sept Mille Sept Cent Cinquante-Deux Francs CFA")
        self.assertEqual(amount_in_words(1), "Un Franc CFA")
        self.assertEqual(amount_in_words(D("274600")), "Deux Cent Soixante-Quatorze Mille Six Cents Francs CFA")


if __name__ == "__main__":
    unittest.main()
