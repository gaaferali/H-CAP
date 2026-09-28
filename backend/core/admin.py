from django.contrib import admin

from .models import (
    AISignal,
    AuditEvent,
    AutomationExecution,
    AutomationRule,
    ActivityDependency,
    Beneficiary,
    Budget,
    Complaint,
    Enrollment,
    Household,
    PaymentChannelConfig,
    PaymentEvent,
    PaymentInstruction,
    PaymentBatch,
    PDMResponse,
    Program,
    ProgramActivity,
    ReconciliationItem,
    ReviewTask,
    Tenant,
    User,
)

admin.site.register(Tenant)
admin.site.register(User)
admin.site.register(Program)
admin.site.register(ProgramActivity)
admin.site.register(ActivityDependency)
admin.site.register(Household)
admin.site.register(Beneficiary)
admin.site.register(Enrollment)
admin.site.register(PaymentChannelConfig)
admin.site.register(PaymentInstruction)
admin.site.register(PaymentEvent)
admin.site.register(Complaint)
admin.site.register(Budget)
admin.site.register(AuditEvent)
admin.site.register(AISignal)
admin.site.register(PaymentBatch)
admin.site.register(ReconciliationItem)
admin.site.register(ReviewTask)
admin.site.register(AutomationRule)
admin.site.register(AutomationExecution)
admin.site.register(PDMResponse)
