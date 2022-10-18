# Django-subscriptions-rt: subscriptions and payments for your Django app

# Features

TODO

# Usage

## Subscriptions

\

```
|--------subscription-------------------------------------------->
start             (subscription duration)                end or inf
#
|-----------------------------|---------------------------|------>
charge   (charge period)    charge                      charge
#
|------------------------------x
quota   (quota lifetime)       quota burned
#
(quota recharge period) |------------------------------x
#
(quota recharge period) (quota recharge period) |----------------->
```

# Cases

# Cache

# Humanize

# Payment providers

## Apple in-app purchase

Workflow from the mobile application perspective:

1) App fetches data from backend's `/plans/` endpoint. Each plan will have metadata entry `apple_in_app`
   -> `string`, where `string` represents an
   Apple [Product identifier](https://developer.apple.com/documentation/storekit/product/3851116-products)
2) When user performs a purchase,
   app [fetches the receipt data](https://developer.apple.com/documentation/storekit/in-app_purchase/original_api_for_in-app_purchase/validating_receipts_with_the_app_store)
3) Receipt is sent to backend using `POST /webhook/apple_in_app/ {"transaction_receipt": "<base64 encoded receipt>"}`.
   This request needs to be authenticated on behalf of the user that this operation is performed for
4) If everything is fine, new `SubscriptionPayment` and `Subscription` will be created and returned to the app. The json
   returned to the app is the same as when querying `/payments/<uid>/` endpoint
5) In case of an error, a retry should be performed with an exponentially increased timeout and after each application
   restart. Utmost care should be taken when handling the receipt. It should be stored on the device until the server
   accepts the operation. Failure to comply will result in client dissatisfaction and a potential refund

Workflow from the backend perspective – handling client operation:

1) Server side part of
   the [validation](https://developer.apple.com/documentation/storekit/in-app_purchase/original_api_for_in-app_purchase/validating_receipts_with_the_app_store)
   process is performed
2) User provided from the authentication is the one having a `Subscription` and `SubscriptionPayment` created for
3) [Transaction id](https://developer.apple.com/documentation/appstorereceipts/responsebody/receipt/in_app) for this operation is kept with `SubscriptionPayment`

Workflow from the backend perspective – handling renewals:

This assumes that the `/webhook/apple_in_app/` endpoint is assigned
as [notifications service](https://developer.apple.com/documentation/appstoreservernotifications/enabling_app_store_server_notifications)
Currently, only `version 2` of the notifications is supported.

1) Whenever a notification is received from Apple, we discard anything that's not a renewal operation. It is assumed that we, ourselves, can handle expiration, and other events are to be handled in the future
2) Renewal operation contains original transaction id (the first transaction that initiated the whole subscription) – this is used (in turns) to fetch user for which this operation is performed
3) New `SubscriptionPayment` is created, using expiration date provided in the notification


# Development setup

Install the `subscriptions` as a development module using
```bash
pip install -e .
```

Set environmental variables
```bash
export POSTGRES_DB=postgres
export POSTGRES_HOST=localhost
export POSTGRES_PORT=8432
export POSTGRES_USER=postgres
export POSTGRES_PASSWORD=12345
```

Start database (in another terminal with the same variables) with
```bash
docker-compose -f demo/docker-compose.yml up
```

Prepare everything by running
```bash
cd demo
python manage.py migrate
python manage.py runserver
```

Run tests with
```bash
nox -s test
```
