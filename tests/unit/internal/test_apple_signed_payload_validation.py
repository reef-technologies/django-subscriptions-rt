from __future__ import annotations

import base64
import unittest.mock
from typing import NamedTuple

import jwt.utils
import pytest
from OpenSSL import crypto

from subscriptions.v0.providers.apple_in_app.app_store import (
    validate_and_fetch_apple_signed_payload,
)
from subscriptions.v0.providers.apple_in_app.exceptions import (
    PayloadValidationError,
)

# TODO(kkalinowski): replace with the actual algorithm obtained from a request from Apple.
ALG_JWT_HEADER = "RS256"
TEST_PAYLOAD = {"test": "value"}


class CertificateGroup(NamedTuple):
    certificate: crypto.X509
    key: crypto.PKey


def make_cert_group(
    serial: int, is_ca: bool = False, is_leaf: bool = False, issuer_group: CertificateGroup | None = None
) -> CertificateGroup:
    # Procedure taken from
    # https://stackoverflow.com/questions/45873832/how-do-i-create-and-sign-certificates-with-pythons-pyopenssl
    # and cleaned up.
    cert_key = crypto.PKey()
    cert_key.generate_key(crypto.TYPE_RSA, 2048)

    certificate = crypto.X509()
    certificate.set_version(2)
    certificate.set_serial_number(serial)

    issuer_certificate = certificate if issuer_group is None else issuer_group.certificate
    issuer_key = cert_key if issuer_group is None else issuer_group.key

    subject = certificate.get_subject()
    subject.commonName = f"Certificate {serial}"

    # These have to be added separately.
    certificate.add_extensions(
        [
            crypto.X509Extension(b"basicConstraints", False, b"CA:TRUE" if is_ca else b"CA:FALSE"),
            crypto.X509Extension(b"subjectKeyIdentifier", False, b"hash", subject=certificate),
        ]
    )
    extensions = [
        crypto.X509Extension(b"authorityKeyIdentifier", False, b"keyid:always", issuer=issuer_certificate),
        crypto.X509Extension(b"keyUsage", False, b"digitalSignature" if is_leaf else b"keyCertSign, cRLSign"),
    ]
    if is_leaf:
        extensions.append(crypto.X509Extension(b"extendedKeyUsage", False, b"clientAuth"))

    certificate.add_extensions(extensions)

    # No issuer means self-signed.
    certificate.set_issuer(issuer_certificate.get_subject())
    certificate.set_pubkey(cert_key)

    certificate.gmtime_adj_notBefore(0)
    certificate.gmtime_adj_notAfter(3600)  # 1 hour from now.

    certificate.sign(issuer_key, "sha256")
    return CertificateGroup(certificate, cert_key)


@pytest.fixture(scope="function")
def root_certificate_group() -> CertificateGroup:
    certificate_group = make_cert_group(serial=1, is_ca=True)
    # Assign is as a root apple certificate.
    with unittest.mock.patch(
        "subscriptions.v0.providers.apple_in_app.app_store.get_original_apple_certificate"
    ) as mock_get_original_apple_certificate:
        mock_get_original_apple_certificate.return_value = certificate_group.certificate
        yield certificate_group


def make_x5c_header(certificate_chain: list[crypto.X509]) -> list[str]:
    result = []

    for certificate in certificate_chain:
        # https://www.pyopenssl.org/en/stable/api/crypto.html#OpenSSL.crypto.FILETYPE_ASN1
        # `The format used by FILETYPE_ASN1 is also sometimes referred to as DER.`
        # https://datatracker.ietf.org/doc/html/rfc7515#section-4.1.6 <– should be base64 encoded DER
        der_format = crypto.dump_certificate(crypto.FILETYPE_ASN1, certificate)
        base64_der_format = base64.b64encode(der_format)
        ascii_der_format = base64_der_format.decode("ascii")  # it's base64, nothing weird to encode.
        result.append(ascii_der_format)

    return result


def get_signed_payload_with_certificates(
    payload: dict, cert_chain: list[CertificateGroup], private_key: crypto.PKey
) -> str:
    headers = {
        "alg": ALG_JWT_HEADER,
        "x5c": make_x5c_header([group.certificate for group in cert_chain[::-1]]),
    }
    return jwt.encode(
        payload,
        private_key.to_cryptography_key(),
        algorithm=ALG_JWT_HEADER,
        headers=headers,
    )


def test__apple__proper_signature(root_certificate_group: CertificateGroup):
    intermediate_cert_group = make_cert_group(serial=2, is_ca=True, issuer_group=root_certificate_group)
    final_cert_group = make_cert_group(serial=3, is_leaf=True, issuer_group=intermediate_cert_group)

    signed_payload = get_signed_payload_with_certificates(
        TEST_PAYLOAD,
        [root_certificate_group, intermediate_cert_group, final_cert_group],
        final_cert_group.key,
    )

    received_payload = validate_and_fetch_apple_signed_payload(signed_payload)
    assert received_payload == TEST_PAYLOAD


def test__apple__no_certificates_in_the_header(root_certificate_group: CertificateGroup):
    signed_payload = get_signed_payload_with_certificates(
        TEST_PAYLOAD,
        [],  # No certs here.
        root_certificate_group.key,
    )
    with pytest.raises(PayloadValidationError):
        validate_and_fetch_apple_signed_payload(signed_payload)


def test__apple__root_certificate_from_jwt_doesnt_match_apple_root(root_certificate_group: CertificateGroup):
    fake_root_cert_group = make_cert_group(serial=5, is_ca=True)
    intermediate_cert_group = make_cert_group(serial=2, is_ca=True, issuer_group=fake_root_cert_group)
    final_cert_group = make_cert_group(serial=3, is_leaf=True, issuer_group=intermediate_cert_group)

    signed_payload = get_signed_payload_with_certificates(
        TEST_PAYLOAD,
        [fake_root_cert_group, intermediate_cert_group, final_cert_group],
        final_cert_group.key,
    )

    with pytest.raises(PayloadValidationError):
        validate_and_fetch_apple_signed_payload(signed_payload)


def test__apple__invalid_leaf_certificate_from_jwt(root_certificate_group: CertificateGroup):
    intermediate_cert_group = make_cert_group(serial=2, is_ca=True, issuer_group=root_certificate_group)

    fake_root_cert_group = make_cert_group(serial=5, is_ca=True)
    final_cert_group = make_cert_group(serial=3, is_leaf=True, issuer_group=fake_root_cert_group)

    signed_payload = get_signed_payload_with_certificates(
        TEST_PAYLOAD,
        [root_certificate_group, intermediate_cert_group, final_cert_group],
        final_cert_group.key,
    )

    with pytest.raises(PayloadValidationError):
        validate_and_fetch_apple_signed_payload(signed_payload)


def test__apple__invalid_signature_of_the_jwt(root_certificate_group: CertificateGroup):
    intermediate_cert_group = make_cert_group(serial=2, is_ca=True, issuer_group=root_certificate_group)
    final_cert_group = make_cert_group(serial=3, is_leaf=True, issuer_group=intermediate_cert_group)

    signed_payload = get_signed_payload_with_certificates(
        TEST_PAYLOAD,
        [root_certificate_group, intermediate_cert_group, final_cert_group],
        final_cert_group.key,
    )

    # Last part of the signed payload is the signature.
    header, payload, _signature = signed_payload.split(".")
    bad_signature = jwt.utils.base64url_encode(b"invalid signature").decode("ascii")
    bad_signed_payload = f"{header}.{payload}.{bad_signature}"

    with pytest.raises(PayloadValidationError):
        validate_and_fetch_apple_signed_payload(bad_signed_payload)
