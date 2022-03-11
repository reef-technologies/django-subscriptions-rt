

class QuotaLimitExceeded(Exception):
    pass


class NoQuotaApplied(Exception):
    pass


class InconsistentQuotaCache(Exception):
    pass


class ProviderNotFound(Exception):
    pass
