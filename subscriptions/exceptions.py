

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
    user_message: str = 'unknown error'
    code = 'unknown'


class BadReferencePayment(PaymentError):
    pass
