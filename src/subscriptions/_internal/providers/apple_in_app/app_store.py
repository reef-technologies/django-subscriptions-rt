from __future__ import annotations

import base64
import datetime
import enum
import functools
import logging
import pathlib
from typing import Any

import jwt
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509 import load_der_x509_certificate
from django.conf import settings
from OpenSSL import crypto
from pydantic import (
    BaseModel,
    Field,
)

from subscriptions.v0.providers.apple_in_app.exceptions import ConfigurationError

from .enums import AppleEnvironment
from .exceptions import PayloadValidationError

logger = logging.getLogger(__name__)

# A month before the warning appears. Assuming that instances are restarted every few days.
CERTIFICATE_GRACE_PERIOD = datetime.timedelta(days=30)


def load_certificate_from_bytes(certificate_data: bytes) -> crypto.X509:
    basic_cert = load_der_x509_certificate(certificate_data)
    return crypto.X509.from_cryptography(basic_cert)


def load_certificate_from_x5c(x5c_entry: str) -> crypto.X509:
    # Each string in the array is a base64-encoded (not base64url-encoded) DER [ITU.X690.2008] PKIX certificate value.
    certificate_data = base64.b64decode(x5c_entry)
    return load_certificate_from_bytes(certificate_data)


def are_certificates_identical(cert_1: crypto.X509, cert_2: crypto.X509) -> bool:
    # There are a few ways to compare these, but checking their binary representation seems good enough.
    return cert_1.to_cryptography().public_bytes(Encoding.DER) == cert_2.to_cryptography().public_bytes(Encoding.DER)


DEFAULT_ROOT_CERTIFICATE_PATH = pathlib.Path(__file__).resolve().parent / "certs" / "AppleRootCA-G3.cer"


def provide_warnings_for_old_certificate(certificate: crypto.X509) -> None:
    # Provided in format YYYYMMDDhhmmssZ
    certificate_timestamp_bytes = certificate.get_notAfter()
    if certificate_timestamp_bytes is None:
        logger.warning("Provided certificate has no expiration date.")
        return

    certificate_end_time = datetime.datetime.strptime(certificate_timestamp_bytes.decode("ascii"), "%Y%m%d%H%M%SZ")
    grace_period_start = certificate_end_time - CERTIFICATE_GRACE_PERIOD
    if datetime.datetime.now() > grace_period_start:
        # TODO(kkalinowski): Consider downloading new one instead of providing this error.
        logger.warning("Provided certificate ends at %s, consider replacing it.", certificate_end_time.isoformat())


@functools.cache
def get_original_apple_certificate() -> crypto.X509:
    # TODO(kkalinowski): get not one, but all root certificates. There's 5 of them and the current one was handpicked.
    if cert_path_str := getattr(settings, "APPLE_ROOT_CERTIFICATE_PATH", ""):
        cert_path = pathlib.Path(cert_path_str)
    else:
        logger.info("Apple root certificate not provided. Using embedded one.")
        cert_path = DEFAULT_ROOT_CERTIFICATE_PATH

    if not cert_path.exists() or not cert_path.is_file():
        raise ConfigurationError(
            "No root certificate for Apple provided (even the default one is missing). "
            "Check Django configuration settings."
        )

    apple_root_certificate = load_certificate_from_bytes(cert_path.read_bytes())
    provide_warnings_for_old_certificate(apple_root_certificate)
    return apple_root_certificate


def validate_and_fetch_apple_signed_payload(signed_payload: str) -> dict[str, Any]:
    """
    https://developer.apple.com/documentation/appstoreservernotifications/responsebodyv2
    https://developer.apple.com/documentation/appstoreservernotifications/jwsdecodedheader
    https://datatracker.ietf.org/doc/html/rfc7515#section-5.2

    This consists of three steps:
    1. Checking whether the root certificate is the same as the Apple root certificate.
    2. Checking whether the certificate chain "works as intended" (root signed intermediate, store signed final one).
    3. Using public key from the last certificate to validate the JWT payload.

    Things to look after (I know it's old).
    https://mail.python.org/pipermail/cryptography-dev/2016-August/000676.html
    """
    header = jwt.get_unverified_header(signed_payload)

    # Load certificates chain.
    certificates_chain = header["x5c"]
    if not isinstance(certificates_chain, list) or len(certificates_chain) == 0:
        raise PayloadValidationError(
            "Invalid certificate chain format or no certificates provided in the certificate chain."
        )

    # Fetch first certificate and confirm that it's the same as the Apple root certificate.
    apple_root_certificate = get_original_apple_certificate()

    root_certificate_x5c = certificates_chain[-1]  # Root should be at the end, according to docs.
    root_certificate = load_certificate_from_x5c(root_certificate_x5c)

    if not are_certificates_identical(apple_root_certificate, root_certificate):
        raise PayloadValidationError("Root certificate differs from the Apple certificate.")

    # Check that the whole certificate chain is valid.
    certificate_store = crypto.X509Store()
    certificate_store.add_cert(apple_root_certificate)

    # Go from the back, excluding the one that we've already validated.
    # NOTE(kkalinowski): While it could be done with a single X509StoreContext
    # using untrusted cert list, I'm unsure about safety of this method (and far from understanding it enough
    # to be able to determine this myself). The one presented below is said to be secure by someone smarter than me.
    assert len(certificates_chain) > 1, "There should be at least two certificates in the chain"
    for certificate_x5c in reversed(certificates_chain[:-1]):
        current_certificate: crypto.X509 = load_certificate_from_x5c(certificate_x5c)

        # Verify whether this certificate is valid.
        context = crypto.X509StoreContext(certificate_store, current_certificate)
        try:
            context.verify_certificate()
        except crypto.X509StoreContextError:
            # TODO(kkalinowski): consider more information in this place.
            raise PayloadValidationError("Validation of one of certificates failed.")

        # Add it to the store.
        certificate_store.add_cert(current_certificate)

    # Fetch public key from the last certificate and validate the payload.
    algorithm = header["alg"]
    try:
        payload = jwt.decode(signed_payload, current_certificate.get_pubkey().to_cryptography_key(), algorithm)  # type: ignore[arg-type]
    except jwt.PyJWTError as ex:
        raise PayloadValidationError(str(ex))

    return payload


@enum.unique
class AppStoreNotificationTypeV2(str, enum.Enum):
    """
    https://developer.apple.com/documentation/appstoreservernotifications/notificationtype
    """

    CONSUMPTION_REQUEST = "CONSUMPTION_REQUEST"
    DID_CHANGE_RENEWAL_PREF = "DID_CHANGE_RENEWAL_PREF"
    DID_CHANGE_RENEWAL_STATUS = "DID_CHANGE_RENEWAL_STATUS"
    DID_FAIL_TO_RENEW = "DID_FAIL_TO_RENEW"
    DID_RENEW = "DID_RENEW"
    EXPIRED = "EXPIRED"
    GRACE_PERIOD_EXPIRED = "GRACE_PERIOD_EXPIRED"
    OFFER_REDEEMED = "OFFER_REDEEMED"
    PRICE_INCREASE = "PRICE_INCREASE"
    REFUND = "REFUND"
    REFUND_DECLINED = "REFUND_DECLINED"
    RENEWAL_EXTENDED = "RENEWAL_EXTENDED"
    REVOKE = "REVOKE"
    SUBSCRIBED = "SUBSCRIBED"
    TEST = "TEST"


@enum.unique
class AppStoreNotificationTypeV2Subtype(str, enum.Enum):
    """
    https://developer.apple.com/documentation/appstoreservernotifications/subtype
    """

    INITIAL_BUY = "INITIAL_BUY"
    RESUBSCRIBE = "RESUBSCRIBE"
    DOWNGRADE = "DOWNGRADE"
    UPGRADE = "UPGRADE"
    AUTO_RENEW_ENABLED = "AUTO_RENEW_ENABLED"
    AUTO_RENEW_DISABLED = "AUTO_RENEW_DISABLED"
    VOLUNTARY = "VOLUNTARY"
    BILLING_RETRY = "BILLING_RETRY"
    PRICE_INCREASE = "PRICE_INCREASE"
    GRACE_PERIOD = "GRACE_PERIOD"
    BILLING_RECOVERY = "BILLING_RECOVERY"
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"


class AppStoreTransactionInfo(BaseModel):
    """
    https://developer.apple.com/documentation/appstoreservernotifications/jwstransactiondecodedpayload
    """

    class Config:
        extra = "ignore"

    # UUID set by the application, missing if not set.
    app_account_token: str = Field(alias="appAccountToken", default=None)
    bundle_id: str = Field(alias="bundleId")

    purchase_date: datetime.datetime = Field(alias="purchaseDate")
    expires_date: datetime.datetime = Field(alias="expiresDate")
    revocation_date: datetime.datetime | None = Field(alias="revocationDate", default=None)

    product_id: str = Field(alias="productId")
    transaction_id: str = Field(alias="transactionId")
    original_transaction_id: str = Field(alias="originalTransactionId")

    web_order_line_item_id: str = Field(alias="webOrderLineItemId")

    @classmethod
    def from_signed_payload(cls, signed_payload_data: str) -> AppStoreTransactionInfo:
        payload = validate_and_fetch_apple_signed_payload(signed_payload_data)
        return cls.parse_obj(payload)


class AppStoreNotificationData(BaseModel):
    """
    https://developer.apple.com/documentation/appstoreservernotifications/data
    Renewal field doesn't seem to carry anything interesting.
    """

    class Config:
        extra = "ignore"

    # Not present in sandbox env.
    app_apple_id: int = Field(alias="appAppleId", default=-1)
    bundle_id: str = Field(alias="bundleId")
    bundle_version: str = Field(alias="bundleVersion")
    environment: AppleEnvironment

    signed_transaction_info: str = Field(alias="signedTransactionInfo")

    @property
    def transaction_info(self) -> AppStoreTransactionInfo:
        return AppStoreTransactionInfo.from_signed_payload(self.signed_transaction_info)


class AppStoreNotification(BaseModel):
    """
    https://developer.apple.com/documentation/appstoreservernotifications/responsebodyv2decodedpayload
    """

    class Config:
        extra = "ignore"

    notification: AppStoreNotificationTypeV2 = Field(alias="notificationType")
    # May be absent.
    subtype: AppStoreNotificationTypeV2Subtype | None = Field(default=None)
    # Used to deduplicate notifications.
    notification_uuid: str = Field(alias="notificationUUID")

    data: AppStoreNotificationData

    @property
    def transaction_info(self) -> AppStoreTransactionInfo:
        return self.data.transaction_info

    @classmethod
    def from_signed_payload(cls, signed_payload_data: str) -> AppStoreNotification:
        payload = validate_and_fetch_apple_signed_payload(signed_payload_data)
        return cls.parse_obj(payload)


class AppleAppStoreNotification(BaseModel):
    class Config:
        extra = "forbid"

    signed_payload: str = Field(alias="signedPayload")
