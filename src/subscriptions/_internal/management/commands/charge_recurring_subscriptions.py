import logging

from django.core.management.base import BaseCommand

from ...tasks import charge_recurring_subscriptions


class Command(BaseCommand):
    def add_arguments(self, parser) -> None:
        parser.add_argument("--dry-run", action="store_true", help="Run in dry-run mode")

    def handle(self, *args, **options) -> None:
        logging.basicConfig(level=logging.DEBUG)
        charge_recurring_subscriptions(dry_run=options["dry_run"])
