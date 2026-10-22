from django.contrib import admin

from .models import CompanySettings, Invoice, InvoiceItem, Payment


class ItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("uid", "type", "client_name", "total", "status", "created_at")
    list_filter = ("status", "type")
    search_fields = ("uid", "client_name", "client_ifu")
    inlines = [ItemInline, PaymentInline]


admin.site.register(CompanySettings)
