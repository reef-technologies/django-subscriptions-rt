from django.core.management.base import BaseCommand

from ...tasks import check_duplicated_payments


class Command(BaseCommand):
    def handle(self, *args, **options):
        check_duplicated_payments()
