from base64 import b64decode
from enum import Enum
from typing import Any, List, Optional, Union

from pydantic import BaseModel, Extra


class GoogleSubscriptionNotificationType(int, Enum):
    RECOVERED = 1
    RENEWED = 2
    CANCELED = 3
    PURCHASED = 4
    ON_HOLD = 5
    IN_GRACE_PERIOD = 6
    RESTARTED = 7
    PRICE_CHANGE_CONFIRMED = 8
    DEFERRED = 9
    PAUSED = 10
    PAUSE_SCHEDULE_CHANGED = 11
    REVOKED = 12
    EXPIRED = 13


class GoogleSubscriptionNotification(BaseModel):
    version: str
    notificationType: GoogleSubscriptionNotificationType
    purchaseToken: str
    subscriptionId: str

    class Config:
        extra = Extra.forbid


class GoogleTestNotification(BaseModel):
    version: str

    class Config:
        extra = Extra.forbid


class GoogleDeveloperNotification(BaseModel):
    # https://developer.android.com/google/play/billing/rtdn-reference#sub
    version: str
    packageName: str
    eventTimeMillis: str
    oneTimeProductNotification: Optional[Any] = None
    subscriptionNotification: Optional[GoogleSubscriptionNotification] = None
    testNotification: Optional[GoogleTestNotification] = None

    class Config:
        extra = Extra.forbid


class GooglePubSubMessage(BaseModel):
    data: str
    messageId: str
    publishTime: str

    class Config:
        extra = Extra.ignore

    def decode(self) -> str:
        return b64decode(self.data).decode('utf8')


class GooglePubSubData(BaseModel):
    message: GooglePubSubMessage
    subscription: str

    class Config:
        extra = Extra.forbid


class AppNotification(BaseModel):
    purchase_token: str

    class Config:
        extra = Extra.forbid


class MultiNotification(BaseModel):
    notification: Union[AppNotification, GooglePubSubData]

    class Config:
        extra = Extra.forbid


class GoogleBasePlanState(str, Enum):
    # https://developers.google.com/android-publisher/api-ref/rest/v3/monetization.subscriptions#State
    STATE_UNSPECIFIED = 'STATE_UNSPECIFIED'
    DRAFT = 'DRAFT'
    ACTIVE = 'ACTIVE'
    INACTIVE = 'INACTIVE'


class GoogleMoney(BaseModel):
    # https://developers.google.com/android-publisher/api-ref/rest/v3/Money
    currencyCode: str  # ISO4217
    units: str
    nanos: Optional[int] = None

    class Config:
        extra = Extra.forbid


class GoogleRegionalBasePlanConfig(BaseModel):
    # https://developers.google.com/android-publisher/api-ref/rest/v3/monetization.subscriptions#RegionalBasePlanConfig
    regionCode: str  # ISO3166-2
    newSubscriberAvailability: bool
    price: GoogleMoney

    class Config:
        extra = Extra.forbid


class GoogleResubscribeState(str, Enum):
    # https://developers.google.com/android-publisher/api-ref/rest/v3/monetization.subscriptions#ResubscribeState
    UNSPECIFIED = 'RESUBSCRIBE_STATE_UNSPECIFIED'
    ACTIVE = 'RESUBSCRIBE_STATE_ACTIVE'
    INACTIVE = 'RESUBSCRIBE_STATE_INACTIVE'


class GoogleSubscriptionProrationMode(str, Enum):
    # https://developers.google.com/android-publisher/api-ref/rest/v3/monetization.subscriptions#SubscriptionProrationMode
    UNSPECIFIED = 'SUBSCRIPTION_PRORATION_MODE_UNSPECIFIED'
    CHARGE_ON_NEXT_BILLING_DATE = 'SUBSCRIPTION_PRORATION_MODE_CHARGE_ON_NEXT_BILLING_DATE'
    CHARGE_FULL_PRICE_IMMEDIATELY = 'SUBSCRIPTION_PRORATION_MODE_CHARGE_FULL_PRICE_IMMEDIATELY'


class GoogleAutoRenewingBasePlanType(BaseModel):
    # https://developers.google.com/android-publisher/api-ref/rest/v3/monetization.subscriptions#AutoRenewingBasePlanType
    billingPeriodDuration: str
    gracePeriodDuration: str
    resubscribeState: GoogleResubscribeState
    prorationMode: GoogleSubscriptionProrationMode
    legacyCompatible: bool
    legacyCompatibleSubscriptionOfferId: str

    class Config:
        extra = Extra.forbid


class GoogleTimeExtension(str, Enum):
    # https://developers.google.com/android-publisher/api-ref/rest/v3/monetization.subscriptions#TimeExtension
    UNSPECIFIED = 'TIME_EXTENSION_UNSPECIFIED'
    ACTIVE = 'TIME_EXTENSION_ACTIVE'
    INACTIVE = 'TIME_EXTENSION_INACTIVE'


class GooglePrepaidBasePlanType(BaseModel):
    # https://developers.google.com/android-publisher/api-ref/rest/v3/monetization.subscriptions#PrepaidBasePlanType
    billingPeriodDuration: str
    timeExtension: GoogleTimeExtension

    class Config:
        extra = Extra.forbid


class GoogleBasePlan(BaseModel):
    # https://developers.google.com/android-publisher/api-ref/rest/v3/monetization.subscriptions#BasePlan
    basePlanId: str
    state: GoogleBasePlanState
    regionalConfigs: List[GoogleRegionalBasePlanConfig]
    autoRenewingBasePlanType: Optional[GoogleAutoRenewingBasePlanType]
    prepaidBasePlanType: Optional[GooglePrepaidBasePlanType]

    class Config:
        extra = Extra.ignore


class GoogleSubscription(BaseModel):
    # https://developers.google.com/android-publisher/api-ref/rest/v3/monetization.subscriptions
    packageName: str
    productId: str
    basePlans: List[GoogleBasePlan]
    archived: Optional[bool] = None

    class Config:
        extra = Extra.ignore


class GoogleSubscriptionState(str, Enum):
    # https://developers.google.com/android-publisher/api-ref/rest/v3/purchases.subscriptionsv2#SubscriptionState
    UNSPECIFIED = 'SUBSCRIPTION_STATE_UNSPECIFIED'
    PENDING = 'SUBSCRIPTION_STATE_PENDING'
    ACTIVE = 'SUBSCRIPTION_STATE_ACTIVE'
    PAUSED = 'SUBSCRIPTION_STATE_PAUSED'
    IN_GRACE_PERIOD = 'SUBSCRIPTION_STATE_IN_GRACE_PERIOD'
    ON_HOLD = 'SUBSCRIPTION_STATE_ON_HOLD'
    CANCELED = 'SUBSCRIPTION_STATE_CANCELED'
    EXPIRED = 'SUBSCRIPTION_STATE_EXPIRED'


class GoogleAcknowledgementState(str, Enum):
    # https://developers.google.com/android-publisher/api-ref/rest/v3/purchases.subscriptionsv2#AcknowledgementState
    UNSPECIFIED = 'ACKNOWLEDGEMENT_STATE_UNSPECIFIED'
    PENDING = 'ACKNOWLEDGEMENT_STATE_PENDING'
    ACKNOWLEDGED = 'ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED'


class GoogleAutoRenewingPlan(BaseModel):
    # https://developers.google.com/android-publisher/api-ref/rest/v3/purchases.subscriptionsv2#AutoRenewingPlan
    autoRenewEnabled: Optional[bool] = None

    class Config:
        extra = Extra.forbid


class GooglePrepaidPlan(BaseModel):
    # https://developers.google.com/android-publisher/api-ref/rest/v3/purchases.subscriptionsv2#PrepaidPlan
    allowExtendAfterTime: bool

    class Config:
        extra = Extra.forbid


class GoogleOfferDetails(BaseModel):
    # https://developers.google.com/android-publisher/api-ref/rest/v3/purchases.subscriptionsv2#OfferDetails
    offerTags: List[str] = []
    basePlanId: str
    offerId: Optional[str] = None

    class Config:
        extra = Extra.forbid


class GoogleSubscriptionPurchaseLineItem(BaseModel):
    # https://developers.google.com/android-publisher/api-ref/rest/v3/purchases.subscriptionsv2#SubscriptionPurchaseLineItem
    productId: str
    expiryTime: str
    autoRenewingPlan: Optional[GoogleAutoRenewingPlan] = None
    prepaidPlan: Optional[GooglePrepaidPlan] = None
    offerDetails: GoogleOfferDetails

    class Config:
        extra = Extra.forbid


class GoogleSubscriptionPurchaseV2(BaseModel):
    # https://developers.google.com/android-publisher/api-ref/rest/v3/purchases.subscriptionsv2#resource:-subscriptionpurchasev2
    lineItems: List[GoogleSubscriptionPurchaseLineItem]
    startTime: str
    subscriptionState: GoogleSubscriptionState
    linkedPurchaseToken: Optional[str] = None
    acknowledgementState: GoogleAcknowledgementState

    class Config:
        extra = Extra.ignore


class Metadata(BaseModel):
    purchase: GoogleSubscriptionPurchaseV2

    class Config:
        extra = Extra.forbid
