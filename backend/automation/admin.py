from django.contrib import admin

from .models import AISignal, AutomationExecution, AutomationRule, ReviewTask

admin.site.register(AISignal)
admin.site.register(ReviewTask)
admin.site.register(AutomationRule)
admin.site.register(AutomationExecution)
