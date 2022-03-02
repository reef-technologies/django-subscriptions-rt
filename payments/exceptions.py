

class QuotaLimitExceeded(Exception):
    pass


class NoQuotaApplied(Exception):
    pass


class NoActiveSubscription(Exception):
    pass


class InconsistentQuotaCache(Exception):
    pass
