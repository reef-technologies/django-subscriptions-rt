import logging

from django.core.management.base import BaseCommand

from ...tasks import charge_recurring_subscriptions


class Command(BaseCommand):
    def handle(self, *args, **options):
        logging.basicConfig(level=logging.DEBUG)
        charge_recurring_subscriptions()
