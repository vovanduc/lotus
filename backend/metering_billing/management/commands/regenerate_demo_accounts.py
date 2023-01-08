from django.core.management.base import BaseCommand
from metering_billing.demos import setup_demo3, setup_paas_demo
from metering_billing.models import Organization


class Command(BaseCommand):
    "Django command to pause execution until the database is available"

    def handle(self, *args, **options):
        for org in Organization.objects.filter(
            organization_type=Organization.OrganizationType.EXTERNAL_DEMO
        ):
            setup_demo3(company_name=org.company_name, mode="regenerate")