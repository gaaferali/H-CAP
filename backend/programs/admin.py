from django.contrib import admin

from .models import ActivityDependency, Budget, Program, ProgramActivity

admin.site.register(Program)
admin.site.register(ProgramActivity)
admin.site.register(ActivityDependency)
admin.site.register(Budget)
