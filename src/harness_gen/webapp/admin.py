from django.contrib import admin

from harness_gen.webapp.models import ProviderProfile


@admin.register(ProviderProfile)
class ProviderProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "provider", "model_name")
    readonly_fields = ("encrypted_api_key",)
    exclude = ("encrypted_api_key",)
