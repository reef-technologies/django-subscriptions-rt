import pytest

from subscriptions.models import SubscriptionPayment
from subscriptions.providers import get_provider, get_providers
from subscriptions.providers.paddle import PaddleProvider

from ..helpers import usd


@pytest.fixture
def paddle(settings) -> str:
    settings.SUBSCRIPTIONS_PAYMENT_PROVIDERS = [
        "subscriptions.providers.paddle.PaddleProvider",
    ]
    get_provider.cache_clear()
    get_providers.cache_clear()
    provider = get_provider()
    assert isinstance(provider, PaddleProvider)
    return provider


@pytest.fixture
def paddle_unconfirmed_payment(db, paddle, plan, user) -> SubscriptionPayment:
    return SubscriptionPayment.objects.create(
        user=user,
        plan=plan,
        subscription=None,
        provider_codename=paddle.codename,
        provider_transaction_id="12345",
        amount=usd(100),
        metadata={"subscription_id": "888999"},
    )


@pytest.fixture
def paddle_payment(paddle_unconfirmed_payment) -> SubscriptionPayment:
    paddle_unconfirmed_payment.status = SubscriptionPayment.Status.COMPLETED
    paddle_unconfirmed_payment.save()
    return paddle_unconfirmed_payment


@pytest.fixture
def paddle_webhook_payload(db, paddle, paddle_unconfirmed_payment) -> dict:
    return {
        "alert_id": 970811351,
        "alert_name": "subscription_payment_succeeded",
        "balance_currency": "USD",
        "balance_earnings": "80.00",
        "balance_fee": "20.00",
        "balance_gross": "100.00",
        "balance_tax": "33.00",
        "checkout_id": "2-6cad1ee6f850e26-243da69933",
        "country": "DE",
        "coupon": "Coupon 8",
        "currency": "USD",
        "customer_name": "customer_name",
        "earnings": "577.96",
        "email": "feil.jackson@example.net",
        "event_time": "2022-06-03 18:20:23",
        "fee": "0.28",
        "initial_payment": False,
        "instalments": 4,
        "marketing_consent": 1,
        "next_bill_date": "2022-06-24",
        "next_payment_amount": "200.00",
        "order_id": 6,
        "passthrough": f'{{"subscription_payment_id": "{paddle_unconfirmed_payment.id}"}}',
        "payment_method": "paypal",
        "payment_tax": "0.94",
        "plan_name": "Example String",
        "quantity": 9,
        "receipt_url": "https://sandbox-my.paddle.com/receipt/5/93efff2bc9436b9-4fbe55cfe6",
        "sale_gross": "328.85",
        "status": "active",
        "subscription_id": 4,
        "subscription_payment_id": 2,
        "subscription_plan_id": 8,
        "unit_price": "unit_price",
        "user_id": 3,
        "p_signature": "abracadabra",
    }


@pytest.fixture
def paddle_test_email(settings) -> str:
    return settings.PADDLE_TEST_EMAIL
