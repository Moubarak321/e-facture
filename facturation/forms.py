from decimal import Decimal

from django import forms
from django.forms import formset_factory

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
            "bank_details", "footer_text", "accent_color", "operator_name",
            "emcf_env", "emcf_token",
        ]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
            "bank_details": forms.Textarea(attrs={"rows": 3}),
            "footer_text": forms.Textarea(attrs={"rows": 3}),
            "accent_color": forms.TextInput(attrs={"type": "color"}),
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

    def clean_emcf_token(self):
        token = (self.cleaned_data.get("emcf_token") or "").strip()
        if not token and self.instance.pk:
            return self.instance.emcf_token  # on garde l'ancien jeton
        return token


# --------------------------------------------------------------------------- clients
class ClientForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Client
        fields = ["name", "ifu", "contact", "address"]
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}

    def clean_ifu(self):
        return _validate_ifu(self.cleaned_data["ifu"])


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
    type = forms.ChoiceField(label="Type de facture", choices=Invoice.TYPES, initial="FV")
    reference = forms.CharField(
        label="UID de la facture d'origine (obligatoire pour un avoir)",
        required=False,
        max_length=64,
    )
    aib = forms.ChoiceField(label="AIB", choices=Invoice.AIB_CHOICES, required=False)
    confirm_now = forms.BooleanField(
        label="Confirmer (finaliser) immédiatement après l'envoi", required=False
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("type") in ("FA", "EA") and not cleaned.get("reference"):
            self.add_error("reference", "Un avoir doit référencer l'UID de la facture d'origine.")
        return cleaned


class ItemForm(BootstrapMixin, forms.Form):
    name = forms.CharField(label="Désignation", max_length=200)
    price = forms.DecimalField(label="Prix unitaire TTC", min_value=0, decimal_places=2, max_digits=16)
    quantity = forms.DecimalField(
        label="Quantité", min_value=Decimal("0.001"), decimal_places=3, max_digits=12, initial=1
    )
    tax_group = forms.ChoiceField(label="Taxe", choices=TAX_GROUPS, initial="B")


class PaymentForm(BootstrapMixin, forms.Form):
    method = forms.ChoiceField(label="Mode de paiement", choices=PAYMENT_METHODS)
    amount = forms.DecimalField(
        label="Montant", required=False, min_value=0, decimal_places=2, max_digits=16
    )


ItemFormSet = formset_factory(ItemForm, extra=0, min_num=1, validate_min=True, can_delete=True)
PaymentFormSet = formset_factory(PaymentForm, extra=1, can_delete=True)
