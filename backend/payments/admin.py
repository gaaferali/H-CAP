from django.contrib import admin

from .models import PaymentBatch, PaymentChannelConfig, PaymentEvent, PaymentInstruction, ReconciliationItem

admin.site.register(PaymentChannelConfig)
admin.site.register(PaymentInstruction)
admin.site.register(PaymentEvent)
admin.site.register(PaymentBatch)
admin.site.register(ReconciliationItem)
