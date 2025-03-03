from datetime import timedelta

from django.core.management.base import BaseCommand

from ...tasks import check_unfinished_payments


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--within", type=int, default=24)

    def handle(self, *args, **options):
        check_unfinished_payments(within=timedelta(hours=options["within"]))
