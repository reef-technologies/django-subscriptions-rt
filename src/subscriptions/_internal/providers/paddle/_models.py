# from django.db import models

# from ...models import SubscriptionPayment


# class PaddlePayment(SubscriptionPayment):
#     # https://developer.paddle.com/api-reference/89c1805d821c2-list-transactions
#     # https://developer.paddle.com/api-reference/e33e0a714a05d-list-users

#     order_id = models.CharField(max_length=255)
#     checkout_id = models.CharField(max_length=255)
#     subscription_id = models.CharField(max_length=255)

#     user_id = models.PositiveIntegerField()
#     email = models.EmailField()

#     coupon_code = models.CharField(max_length=255, blank=True)
#     card_type = models.CharField(max_length=16)
#     last_four_digits = models.CharField(max_length=4)
#     expiry_date = models.CharField(max_length=7)
