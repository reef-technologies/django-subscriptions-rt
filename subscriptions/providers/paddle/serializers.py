from rest_framework.serializers import BooleanField, CharField, DecimalField, IntegerField
from subscriptions.api.serializers import WebhookSerializer


class PaddleWebhookSerializer(WebhookSerializer):
    alert_id = IntegerField()  # => 970811351
    alert_name = CharField()  # => subscription_payment_succeeded
    balance_currency = CharField()  # => GBP
    balance_earnings = DecimalField(max_digits=10, decimal_places=2, )  # => 609.79
    balance_fee = DecimalField(max_digits=10, decimal_places=2, )  # => 705.59
    balance_gross = DecimalField(max_digits=10, decimal_places=2, )  # => 173.95
    balance_tax = DecimalField(max_digits=10, decimal_places=2, )  # => 839.98
    checkout_id = CharField()  # => 2-6cad1ee6f850e26-243da69933
    country = CharField()  # => DE
    coupon = CharField()  # => Coupon 8
    currency = CharField()  # => EUR
    customer_name = CharField()  # => customer_name
    earnings = DecimalField(max_digits=10, decimal_places=2, )  # => 577.96
    email = CharField()  # => feil.jackson@example.net
    event_time = CharField()  # => 2022-06-03 18:20:23
    fee = DecimalField(max_digits=10, decimal_places=2, )  # => 0.28
    initial_payment = BooleanField()  # => false
    instalments = IntegerField()  # => 4
    marketing_consent = IntegerField()  # => 1
    next_bill_date = CharField()  # => 2022-06-24
    next_payment_amount = CharField()  # => next_payment_amount
    order_id = IntegerField()  # => 6
    passthrough = CharField()  # => Example String
    payment_method = CharField()  # => paypal
    payment_tax = DecimalField(max_digits=10, decimal_places=2, )  # => 0.94
    plan_name = CharField()  # => Example String
    quantity = IntegerField()  # => 9
    receipt_url = CharField()  # => https://sandbox-my.paddle.com/receipt/5/93efff2bc9436b9-4fbe55cfe6
    sale_gross = DecimalField(max_digits=10, decimal_places=2, )  # => 328.85
    status = CharField()  # => active
    subscription_id = IntegerField()  # => 4
    subscription_payment_id = IntegerField()  # => 2
    subscription_plan_id = IntegerField()  # => 8
    unit_price = CharField()  # => unit_price
    user_id = IntegerField()  # => 3
    p_signature = CharField()  # =>
