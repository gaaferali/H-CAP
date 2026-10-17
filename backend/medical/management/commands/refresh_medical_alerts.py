from django.core.management.base import BaseCommand

from medical.services import refresh_medical_alerts


class Command(BaseCommand):
    help = "Refresh expiry, quarantine, damage, and low-stock Medical Assistance alerts."

    def handle(self, *args, **options):
        created = refresh_medical_alerts()
        self.stdout.write(self.style.SUCCESS(f"Medical alerts refreshed; {created} new alert(s) created."))
