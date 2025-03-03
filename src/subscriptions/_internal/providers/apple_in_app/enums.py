import enum


@enum.unique
class AppleEnvironment(str, enum.Enum):
    SANDBOX = "Sandbox"
    PRODUCTION = "Production"


@enum.unique
class AppleValidationStatus(int, enum.Enum):
    OK = 0
    NOT_A_POST = 21000
    __NO_LONGER_SENT = 21001
    MALFORMED_DATA_OR_SERVICE_ISSUE = 21002
    RECEIPT_AUTHENTICATION_FAILED = 21003
    INVALID_SHARED_SECRET = 21004
    SERVICE_UNAVAILABLE = 21005
    # Only returned for iOS 6-style transaction receipts for auto-renewable subscriptions.
    SUBSCRIPTION_EXPIRED = 21006
    SANDBOX_RECEIPT_ON_PRODUCTION_ENV = 21007
    PRODUCTION_RECEIPT_ON_SANDBOX_ENV = 21008
    INTERNAL_SERVICE_ERROR = 21009
    USER_ACCOUNT_DOESNT_EXIST = 21010
