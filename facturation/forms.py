from decimal import Decimal

from django import forms
from django.forms import BaseFormSet, formset_factory
from django.utils import timezone

from .models import PAYMENT_METHODS, TAX_GROUPS, Client, CompanySettings, Invoice


class BootstrapMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs["class"] = "form-check-input"
            elif isinstance(widget, forms.Select):
                widget.attrs["class"] = "form-select"
            else:
                widget.attrs["class"] = "form-control"


def _validate_ifu(value):
    value = (value or "").strip()
    if value and not (value.isdigit() and len(value) == 13):
        raise forms.ValidationError("L'IFU doit comporter exactement 13 chiffres.")
    return value


# --------------------------------------------------------------------------- sociétés
class CompanyForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = CompanySettings
        fields = [
            "name", "logo", "ifu", "rccm", "address", "phone", "email", "website",
            "bank_details", "footer_text", "accent_color", "secondary_color", "operator_name",
            "invoice_prefix", "credit_prefix", "emcf_env", "emcf_token",
        ]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
            "bank_details": forms.Textarea(attrs={"rows": 3}),
            "footer_text": forms.Textarea(attrs={"rows": 3}),
            "accent_color": forms.TextInput(attrs={"type": "color"}),
            "secondary_color": forms.TextInput(attrs={"type": "color"}),
            "emcf_token": forms.PasswordInput(render_value=False),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        has_token = bool(self.instance.pk and self.instance.emcf_token)
        self.fields["emcf_token"].required = False
        self.fields["emcf_token"].help_text = (
            "Un jeton est déjà enregistré : laissez vide pour le conserver."
            if has_token
            else "Le jeton est lié à l'IFU de cette société. Laissez vide pour utiliser EMCF_TOKEN du .env."
        )

    def clean_ifu(self):
        return _validate_ifu(self.cleaned_data["ifu"])

    def _clean_prefix(self, name):
        value = self.cleaned_data[name].strip().upper()
        if not value or not value.isalnum():
            raise forms.ValidationError("Lettres et chiffres uniquement (ex. FAC), sans « / » ni espace.")
        return value

    def clean_invoice_prefix(self):
        return self._clean_prefix("invoice_prefix")

    def clean_credit_prefix(self):
        return self._clean_prefix("credit_prefix")

    def clean_emcf_token(self):
        token = (self.cleaned_data.get("emcf_token") or "").strip()
        if not token and self.instance.pk:
            return self.instance.emcf_token  # on garde l'ancien jeton
        return token


# --------------------------------------------------------------------------- clients
class ClientForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Client
        fields = ["name", "code", "ifu", "contact", "address", "shipping_address"]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
            "shipping_address": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_ifu(self):
        return _validate_ifu(self.cleaned_data["ifu"])

    def clean_code(self):
        code = self.cleaned_data["code"].strip()
        if code and Client.objects.filter(code__iexact=code).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Ce code client est déjà utilisé par un autre client.")
        return code


# --------------------------------------------------------------------------- facture
class CompanyChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.name} · IFU {obj.ifu or '?'} · {obj.env_label}"


class ClientChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.name} · IFU {obj.ifu}" if obj.ifu else obj.name


class InvoiceForm(BootstrapMixin, forms.Form):
    company = CompanyChoiceField(
        label="Société émettrice",
        queryset=CompanySettings.objects.all(),
        empty_label="— Choisir la société —",
    )
    client = ClientChoiceField(
        label="Client",
        queryset=Client.objects.all(),
        required=False,
        empty_label="— Aucun client (client de passage) —",
    )
    shipping_address = forms.CharField(
        label="Adresse d'expédition", required=False, widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Reprise de la fiche client ; modifiable pour cette facture.",
    )
    invoice_date = forms.DateField(
        label="Date de la facture", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    due_date = forms.DateField(
        label="Date d'échéance", required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    type = forms.ChoiceField(label="Type de facture", choices=Invoice.TYPES, initial="FV")
    reference = forms.CharField(
        label="UID de la facture d'origine (obligatoire pour un avoir)",
        required=False,
        max_length=64,
    )
    aib = forms.ChoiceField(label="AIB", choices=Invoice.AIB_CHOICES, required=False)
    payment_status = forms.ChoiceField(
        label="Statut de paiement", choices=Invoice.PaymentStatus.choices,
        initial=Invoice.PaymentStatus.UNPAID,
    )
    internal_number = forms.CharField(
        label="N° de facture", required=False, max_length=50,
        help_text="Proposé automatiquement (FAC/2025/00001) ; vous pouvez le modifier.",
    )
    payment_communication = forms.CharField(
        label="Communication de paiement", required=False, max_length=200,
        help_text="Par défaut : le n° de facture.",
    )
    note = forms.CharField(
        label="Note", required=False, widget=forms.Textarea(attrs={"rows": 2})
    )

    def __init__(self, *args, invoice_pk=None, **kwargs):
        self.invoice_pk = invoice_pk  # facture (brouillon) en cours d'édition, exclue des contrôles d'unicité
        super().__init__(*args, **kwargs)
        if not self.is_bound and not self.initial.get("invoice_date"):
            self.initial["invoice_date"] = timezone.localdate()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("type") in ("FA", "EA") and not cleaned.get("reference"):
            self.add_error("reference", "Un avoir doit référencer l'UID de la facture d'origine.")
        number = (cleaned.get("internal_number") or "").strip()
        company = cleaned.get("company")
        if number and company and Invoice.objects.filter(
            company=company, internal_number=number
        ).exclude(pk=self.invoice_pk).exists():
            self.add_error("internal_number", f"Le n° {number} existe déjà pour cette société.")
        invoice_date, due_date = cleaned.get("invoice_date"), cleaned.get("due_date")
        if invoice_date and due_date and due_date < invoice_date:
            self.add_error("due_date", "L'échéance ne peut pas précéder la date de la facture.")
        return cleaned


class ItemForm(BootstrapMixin, forms.Form):
    """Une ligne de l'éditeur : article (item), titre de section (section) ou note (note)."""

    KINDS = [("item", "Ligne"), ("section", "Section"), ("note", "Note")]

    kind = forms.ChoiceField(choices=KINDS, widget=forms.HiddenInput, initial="item")
    name = forms.CharField(label="Désignation", required=False, max_length=1000)
    quantity = forms.DecimalField(
        label="Quantité", required=False, min_value=Decimal("0.001"), decimal_places=3, max_digits=12,
        initial=1,
    )
    unit_ht = forms.DecimalField(
        label="Prix HT", required=False, min_value=Decimal(0), decimal_places=2, max_digits=16,
    )
    tax_group = forms.ChoiceField(label="Taxe", choices=TAX_GROUPS, required=False, initial="B")

    def clean(self):
        cleaned = super().clean()
        kind, name = cleaned.get("kind"), (cleaned.get("name") or "").strip()
        cleaned["name"] = name
        if kind == "item":
            if not name:
                self.add_error("name", "Désignation requise.")
            elif len(name) > 200:
                self.add_error("name", "200 caractères maximum pour une ligne d'article.")
            if cleaned.get("quantity") is None:
                self.add_error("quantity", "Quantité requise.")
            if cleaned.get("unit_ht") is None:
                self.add_error("unit_ht", "Prix HT requis.")
            if not cleaned.get("tax_group"):
                self.add_error("tax_group", "Taxe requise.")
        elif kind in ("section", "note") and not name:
            self.add_error("name", "Texte requis (remplissez ou supprimez cette ligne).")
        return cleaned


class BaseItemFormSet(BaseFormSet):
    def add_fields(self, form, index):
        super().add_fields(form, index)
        if "ORDER" in form.fields:  # position gérée par le JavaScript (glisser / monter / descendre)
            form.fields["ORDER"].widget = forms.HiddenInput()


class PaymentForm(BootstrapMixin, forms.Form):
    method = forms.ChoiceField(label="Mode de paiement", choices=PAYMENT_METHODS)
    amount = forms.DecimalField(
        label="Montant", required=False, min_value=0, decimal_places=2, max_digits=16
    )


ItemFormSet = formset_factory(
    ItemForm, formset=BaseItemFormSet, extra=0, can_delete=True, can_order=True
)
PaymentFormSet = formset_factory(PaymentForm, extra=1, can_delete=True)


class InvoiceInfoForm(BootstrapMixin, forms.Form):
    """Édition des champs propres au logiciel (n°, communication, note) d'une facture existante."""

    internal_number = forms.CharField(label="N° de facture", required=False, max_length=50)
    payment_communication = forms.CharField(
        label="Communication de paiement", required=False, max_length=200
    )
    note = forms.CharField(label="Note", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, invoice, **kwargs):
        self.invoice = invoice
        super().__init__(*args, **kwargs)

    def clean_internal_number(self):
        number = self.cleaned_data["internal_number"].strip()
        if number and Invoice.objects.filter(
            company=self.invoice.company, internal_number=number
        ).exclude(pk=self.invoice.pk).exists():
            raise forms.ValidationError(f"Le n° {number} est déjà utilisé par une autre facture.")
        return number
