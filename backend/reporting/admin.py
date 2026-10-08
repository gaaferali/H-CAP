from django.contrib import admin

from .models import AuditEvent, PDMResponse

admin.site.register(AuditEvent)
admin.site.register(PDMResponse)
