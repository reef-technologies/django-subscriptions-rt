import dataclasses
import datetime
import enum

from .api import AppleEnvironment


@enum.unique
class AppStoreNotificationTypeV2(str, enum.Enum):
    """
    https://developer.apple.com/documentation/appstoreservernotifications/notificationtype
    """
    CONSUMPTION_REQUEST = 'CONSUMPTION_REQUEST'
    DID_CHANGE_RENEWAL_PREF = 'DID_CHANGE_RENEWAL_PREF'
    DID_CHANGE_RENEWAL_STATUS = 'DID_CHANGE_RENEWAL_STATUS'
    DID_FAIL_TO_RENEW = 'DID_FAIL_TO_RENEW'
    DID_RENEW = 'DID_RENEW'
    EXPIRED = 'EXPIRED'
    GRACE_PERIOD_EXPIRED = 'GRACE_PERIOD_EXPIRED'
    OFFER_REDEEMED = 'OFFER_REDEEMED'
    PRICE_INCREASE = 'PRICE_INCREASE'
    REFUND = 'REFUND'
    REFUND_DECLINED = 'REFUND_DECLINED'
    RENEWAL_EXTENDED = 'RENEWAL_EXTENDED'
    REVOKE = 'REVOKE'
    SUBSCRIBED = 'SUBSCRIBED'
    TEST = 'TEST'


@enum.unique
class AppStoreNotificationTypeV2Subtype(str, enum.Enum):
    """
    https://developer.apple.com/documentation/appstoreservernotifications/subtype
    """
    INITIAL_BUY = 'INITIAL_BUY'
    RESUBSCRIBE = 'RESUBSCRIBE'
    DOWNGRADE = 'DOWNGRADE'
    UPGRADE = 'UPGRADE'
    AUTO_RENEW_ENABLED = 'AUTO_RENEW_ENABLED'
    AUTO_RENEW_DISABLED = 'AUTO_RENEW_DISABLED'
    VOLUNTARY = 'VOLUNTARY'
    BILLING_RETRY = 'BILLING_RETRY'
    PRICE_INCREASE = 'PRICE_INCREASE'
    GRACE_PERIOD = 'GRACE_PERIOD'
    BILLING_RECOVERY = 'BILLING_RECOVERY'
    PENDING = 'PENDING'
    ACCEPTED = 'ACCEPTED'


@dataclasses.dataclass
class AppStoreTransactionInfo:
    # UUID set by the application, empty if not set.
    app_account_token: str
    bundle_id: str

    purchase_date: datetime.datetime
    expires_date: datetime.datetime

    product_id: str
    transaction_id: str
    original_transaction_id: str


@dataclasses.dataclass
class AppStoreNotificationData:
    """
    https://developer.apple.com/documentation/appstoreservernotifications/data
    """
    app_apple_id: int
    bundle_id: str
    bundle_version: str
    environment: AppleEnvironment

    # Renewal doesn't seem to carry anything interesting.
    transaction_info: AppStoreTransactionInfo


@dataclasses.dataclass
class AppStoreNotification:
    """
    https://developer.apple.com/documentation/appstoreservernotifications/responsebodyv2decodedpayload
    """
    notification: AppStoreNotificationTypeV2
    subtype: AppStoreNotificationTypeV2Subtype
    # Used to deduplicate notifications.
    notification_uuid: str

    data: AppStoreNotificationData

    @property
    def transaction_info(self) -> AppStoreTransactionInfo:
        return self.data.transaction_info

    @classmethod
    def from_signed_payload(cls, payload: str) -> 'AppStoreNotification':
        pass
