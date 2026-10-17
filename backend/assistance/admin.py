from django.contrib import admin

from .models import Beneficiary, Enrollment, Household, HouseholdModalityDecision

admin.site.register(Household)
admin.site.register(Beneficiary)
admin.site.register(Enrollment)
admin.site.register(HouseholdModalityDecision)
