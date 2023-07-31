import datetime
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from more_itertools import one

from subscriptions.models import SubscriptionPayment
from subscriptions.providers.apple_in_app import AppleInAppProvider


@pytest.mark.django_db(transaction=True)
def test__apple__parallel_receipts(user, plan):
    provider = AppleInAppProvider()
    num_threads = 16
    starting_barrier = threading.Barrier(num_threads, timeout=5)
    original_transaction_id = 'original_transaction_id'

    def runner():
        starting_barrier.wait()
        provider._get_or_create_payment(
            'transaction_id',
            original_transaction_id,
            user,
            plan,
            datetime.datetime(2023, 1, 1),
            datetime.datetime(2023, 2, 1),
        )

    assert SubscriptionPayment.objects.count() == 0

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(runner) for _ in range(num_threads)]
        for future in as_completed(futures, timeout=3):
            future.result(timeout=1)

    assert SubscriptionPayment.objects.count() == 1
    payment = one(SubscriptionPayment.objects.all())
    assert payment.status == SubscriptionPayment.Status.COMPLETED
