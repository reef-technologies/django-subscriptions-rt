class QuotaLimitExceeded(Exception):
    pass


class InconsistentQuotaCache(Exception):
    pass


class ProviderNotFound(Exception):
    pass


class ProlongationImpossible(Exception):
    pass


class SubscriptionError(Exception):
    pass


class PaymentError(Exception):
    user_message: str = 'unknown error'  # TODO: won't work with __init__()
    code = 'unknown'


class BadReferencePayment(PaymentError):
    pass


class InvalidOperation(Exception):
    pass
