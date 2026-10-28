"""Tests Django du module de facturation : brouillon, proforma, confirmation, AIB, code client…

Lancer :  python manage.py test facturation
(l'API e-MCF est simulée : aucun appel réseau, aucune facture réelle)
"""
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .forms import ClientForm
from .models import TAX_GROUPS, Client as ClientModel, CompanySettings, Invoice, InvoiceItem


def post_data(company, client=None, rows=None, **extra):
    """Données POST de l'éditeur (formulaire + lignes + paiements)."""
    rows = rows if rows is not None else [
        {"kind": "section", "name": "A — PORTAGE SALARIAL"},
        {"kind": "item", "name": "Prestation de portage salarial", "quantity": "2", "unit_ht": "100000", "tax_group": "B"},
        {"kind": "item", "name": "Frais de gestion", "quantity": "1", "unit_ht": "20000", "tax_group": "B"},
        {"kind": "note", "name": "Prestations de la période du contrat."},
        {"kind": "section", "name": "B — NOTE DE FRAIS"},
        {"kind": "item", "name": "Frais administratifs", "quantity": "1", "unit_ht": "15000", "tax_group": "A"},
    ]
    data = {
        "company": company.pk, "client": client.pk if client else "", "shipping_address": "",
        "invoice_date": "2026-10-01", "due_date": "2026-10-15", "type": "FV", "reference": "",
        "aib": "", "payment_status": "unpaid", "internal_number": "", "payment_communication": "",
        "note": "", "pays-TOTAL_FORMS": "0", "pays-INITIAL_FORMS": "0",
        "pays-MIN_NUM_FORMS": "0", "pays-MAX_NUM_FORMS": "1000",
        "items-TOTAL_FORMS": str(len(rows)), "items-INITIAL_FORMS": "0",
        "items-MIN_NUM_FORMS": "0", "items-MAX_NUM_FORMS": "1000",
    }
    for i, row in enumerate(rows):
        data[f"items-{i}-kind"] = row["kind"]
        data[f"items-{i}-name"] = row.get("name", "")
        data[f"items-{i}-quantity"] = row.get("quantity", "")
        data[f"items-{i}-unit_ht"] = row.get("unit_ht", "")
        data[f"items-{i}-tax_group"] = row.get("tax_group", "B")
        data[f"items-{i}-ORDER"] = str(i + 1)
    data.update(extra)
    return data


class BaseCase(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("tester", password="x")
        self.client.force_login(user)
        self.company = CompanySettings.objects.create(
            name="ESA TEST SARL", ifu="3201642618813", emcf_token="jeton-test", emcf_env="test")
        self.customer = ClientModel.objects.create(
            name="UPL MAURICE", code="CLI-SECRET-42", ifu="", address="Port Louis",
            shipping_address="Entrepôt 5, Port Louis")
        self.editor = reverse("invoice_create")

    def save_draft(self, **kw):
        resp = self.client.post(self.editor, post_data(self.company, self.customer, action="draft", **kw))
        invoice = Invoice.objects.latest("id")
        return resp, invoice


class ModelAndFormTests(BaseCase):
    def test_libelles_des_regimes_de_taxe(self):
        self.assertEqual([label for _, label in TAX_GROUPS], [
            "A — EXONÉRÉ", "B — TAXABLE 18 %", "C — EXPORTATION",
            "D — TVA RÉGIME D’EXCEPTION 18 %", "E — RÉGIME TPS", "F — RÉSERVE"])
        self.assertEqual([code for code, _ in TAX_GROUPS], list("ABCDEF"))  # codes inchangés

    def test_code_client_unique_mais_blancs_autorises(self):
        form = ClientForm({"name": "Autre", "code": "cli-secret-42"})
        self.assertFalse(form.is_valid())
        self.assertIn("code", form.errors)
        ClientModel.objects.create(name="Sans code 1")
        ClientModel.objects.create(name="Sans code 2")          # plusieurs codes vides : accepté
        self.assertTrue(ClientForm({"name": "Autre", "code": "NOUVEAU"}).is_valid())

    def test_fiches_clients_existantes_restent_utilisables(self):
        old = ClientModel.objects.create(name="Ancien client", ifu="3202286912562")
        self.assertEqual((old.code, old.shipping_address), ("", ""))


class DraftTests(BaseCase):
    def test_brouillon_conserve_sections_notes_lignes_et_dates(self):
        resp, inv = self.save_draft()
        self.assertEqual(inv.status, Invoice.Status.DRAFT)
        self.assertIsNone(inv.uid)
        self.assertEqual([i.kind for i in inv.items.all()],
                         ["section", "item", "item", "note", "section", "item"])
        self.assertEqual((str(inv.invoice_date), str(inv.due_date)), ("2026-10-01", "2026-10-15"))
        self.assertEqual((inv.total_ht, inv.total_tax, inv.total), (Decimal("235000"), Decimal("39600"), Decimal("274600")))
        self.assertTrue(inv.internal_number.startswith("FAC/"))

    def test_brouillon_se_rouvre_sans_perte(self):
        _, inv = self.save_draft()
        page = self.client.get(reverse("invoice_draft_edit", args=[inv.pk]))
        self.assertEqual(page.status_code, 200)
        for text in ("A — PORTAGE SALARIAL", "Frais de gestion", "Prestations de la période du contrat."):
            self.assertContains(page, text)

    def test_modification_du_brouillon_remplace_les_lignes(self):
        _, inv = self.save_draft()
        rows = [{"kind": "item", "name": "Seule ligne", "quantity": "1", "unit_ht": "5000", "tax_group": "B"}]
        self.client.post(reverse("invoice_draft_edit", args=[inv.pk]),
                         post_data(self.company, self.customer, rows=rows, action="draft",
                                   internal_number=inv.internal_number))
        inv.refresh_from_db()
        self.assertEqual(inv.items.count(), 1)
        self.assertEqual(inv.total, Decimal("5900"))
        self.assertEqual(Invoice.objects.count(), 1)            # mise à jour, pas de doublon

    def test_dates_incoherentes_refusees(self):
        self.client.post(self.editor, post_data(self.company, self.customer, action="draft",
                                                due_date="2026-09-01"))
        self.assertEqual(Invoice.objects.count(), 0)

    def test_suppression_dun_brouillon_uniquement(self):
        _, inv = self.save_draft()
        self.client.post(reverse("invoice_delete", args=[inv.pk]))
        self.assertEqual(Invoice.objects.count(), 0)


class PreviewAndPrintTests(BaseCase):
    def test_previsualisation_sans_effet_de_bord(self):
        resp = self.client.post(self.editor, post_data(self.company, self.customer, action="preview"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "PROFORMA")
        self.assertContains(resp, "A — PORTAGE SALARIAL")
        self.assertEqual(Invoice.objects.count(), 0)

    def test_code_client_jamais_imprime(self):
        resp = self.client.post(self.editor, post_data(self.company, self.customer, action="preview"))
        self.assertNotContains(resp, "CLI-SECRET-42")
        _, inv = self.save_draft()
        printed = self.client.get(reverse("invoice_print", args=[inv.pk]))
        self.assertNotContains(printed, "CLI-SECRET-42")
        self.assertContains(printed, "UPL MAURICE")
        self.assertContains(printed, "Port Louis")
        detail = self.client.get(reverse("invoice_detail", args=[inv.pk]))
        self.assertContains(detail, "CLI-SECRET-42")             # visible dans l'interface interne

    def test_facture_historique_en_ttc_toujours_imprimable(self):
        inv = Invoice.objects.create(company=self.company, uid="old-1", status="confirmed",
                                     internal_number="FAC/2024/00001", client_name="Client ancien")
        InvoiceItem.objects.create(invoice=inv, name="Prestation", price=Decimal("118000"), quantity=1, tax_group="B")
        page = self.client.get(reverse("invoice_print", args=[inv.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Facture FAC/2024/00001")
        self.assertEqual(inv.compute()["total_ttc"], Decimal("118000"))
        self.assertEqual(inv.compute()["total_ht"], Decimal("100000"))


class ConfirmTests(BaseCase):
    def confirm(self, **kw):
        return self.client.post(self.editor, post_data(self.company, self.customer, action="confirm", **kw))

    @mock.patch("facturation.views.EmcfClient")
    def test_confirmation_complete(self, api):
        api.return_value.create.return_value = {"uid": "uid-1", "total": 274600}
        api.return_value.confirm.return_value = {
            "codeMECeFDGI": "AAAA-BBBB", "qrCode": "F;EM01;AAAA;3201642618813;20261001120000", "nim": "EM01"}
        self.confirm()
        inv = Invoice.objects.get()
        self.assertEqual((inv.status, inv.uid, inv.number), ("confirmed", "uid-1", "AAAA-BBBB"))
        self.assertEqual(inv.total, Decimal("274600"))
        sent = api.return_value.create.call_args[0][0]
        self.assertEqual([i["taxGroup"] for i in sent["items"]], ["B", "B", "A"])   # sections/notes non envoyées
        self.assertEqual(sent["items"][0]["price"], 118000)                          # prix TTC unitaire

    @mock.patch("facturation.views.EmcfClient")
    def test_confirmation_unique(self, api):
        api.return_value.create.return_value = {"uid": "uid-1", "total": 274600}
        api.return_value.confirm.return_value = {"codeMECeFDGI": "X", "qrCode": ""}
        self.confirm()
        inv = Invoice.objects.get()
        again = self.client.post(reverse("invoice_draft_edit", args=[inv.pk]),
                                 post_data(self.company, self.customer, action="confirm"))
        self.assertEqual(api.return_value.create.call_count, 1)    # pas de seconde émission
        self.assertEqual(again.status_code, 302)

    @mock.patch("facturation.views.EmcfClient")
    def test_ecart_de_total_bloque_la_confirmation(self, api):
        api.return_value.create.return_value = {"uid": "uid-2", "total": 999}
        self.confirm()
        inv = Invoice.objects.get()
        self.assertEqual(inv.status, "pending")
        api.return_value.confirm.assert_not_called()

    @mock.patch("facturation.views.EmcfClient")
    def test_rejet_de_lapi_laisse_le_brouillon(self, api):
        from .emcf_client import EmcfError
        api.return_value.create.side_effect = EmcfError("refusé", error_code="12")
        self.confirm()
        self.assertEqual(Invoice.objects.get().status, "draft")

    @mock.patch("facturation.views.EmcfClient")
    def test_confirmation_exige_une_ligne_darticle(self, api):
        rows = [{"kind": "section", "name": "Vide"}]
        self.client.post(self.editor, post_data(self.company, self.customer, rows=rows, action="confirm"))
        api.return_value.create.assert_not_called()
        self.assertEqual(Invoice.objects.count(), 0)


class AibTests(BaseCase):
    def test_aib_3_et_8_calcules_sur_la_base_ht_et_affiches_a_part(self):
        for code, expected in (("C", Decimal("3000")), ("D", Decimal("8000"))):
            rows = [{"kind": "item", "name": "x", "quantity": "1", "unit_ht": "100000", "tax_group": "B"}]
            self.client.post(self.editor, post_data(self.company, self.customer, rows=rows, action="draft", aib=code))
            inv = Invoice.objects.latest("id")
            self.assertEqual(inv.aib_amount, expected)
            self.assertEqual(inv.total, Decimal("118000"))      # non ajouté au TTC par défaut

    @mock.patch("facturation.views.EmcfClient")
    def test_aib_sans_code_api_bloque_lenvoi_a_la_dgi(self, api):
        self.client.post(self.editor, post_data(self.company, self.customer, action="confirm", aib="C"))
        api.return_value.create.assert_not_called()
        self.assertEqual(Invoice.objects.get().status, "draft")

    @mock.patch("facturation.views.EmcfClient")
    def test_aib_a_et_b_transmis_avec_leur_code(self, api):
        api.return_value.create.return_value = {"uid": "u", "total": 274600}
        api.return_value.confirm.return_value = {"codeMECeFDGI": "X", "qrCode": ""}
        self.client.post(self.editor, post_data(self.company, self.customer, action="confirm", aib="B"))
        self.assertEqual(api.return_value.create.call_args[0][0]["aib"], "B")


class PaymentStatusTests(BaseCase):
    def test_quatre_statuts_et_independance_du_statut_documentaire(self):
        _, inv = self.save_draft()
        for status in ("in_progress", "partial", "paid", "unpaid"):
            self.client.post(reverse("invoice_payment", args=[inv.pk]), {"payment_status": status})
            inv.refresh_from_db()
            self.assertEqual((inv.payment_status, inv.status), (status, "draft"))
        self.client.post(reverse("invoice_payment", args=[inv.pk]), {"payment_status": "inconnu"})
        inv.refresh_from_db()
        self.assertEqual(inv.payment_status, "unpaid")
