import base64
from typing import (
    NamedTuple,
    Optional,
)

import jwt
import pytest
from OpenSSL import crypto

import subscriptions.providers.apple_in_app.app_store
from subscriptions.providers.apple_in_app.app_store import validate_and_fetch_apple_signed_payload

# TODO(kkalinowski): replace with the actual algorithm obtained from a request from Apple.
ALG_JWT_HEADER = 'RS256'


class CertificateGroup(NamedTuple):
    certificate: crypto.X509
    key: crypto.PKey


def make_cert(serial: int,
              is_ca: bool = False,
              is_leaf: bool = False,
              issuer_group: Optional[CertificateGroup] = None) -> CertificateGroup:
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
    subject.commonName = f'Certificate {serial}'

    # These have to be added separately.
    certificate.add_extensions([
        crypto.X509Extension(b'basicConstraints', False, b'CA:TRUE' if is_ca else b'CA:FALSE'),
        crypto.X509Extension(b'subjectKeyIdentifier', False, b'hash', subject=certificate),
    ])
    extensions = [
        crypto.X509Extension(b'authorityKeyIdentifier', False, b'keyid:always', issuer=issuer_certificate),
        crypto.X509Extension(b'keyUsage', False, b'digitalSignature' if is_leaf else b'keyCertSign, cRLSign'),
    ]
    if is_leaf:
        extensions.append(crypto.X509Extension(b'extendedKeyUsage', False, b'clientAuth'))

    certificate.add_extensions(extensions)

    # No issuer means self-signed.
    certificate.set_issuer(issuer_certificate.get_subject())
    certificate.set_pubkey(cert_key)

    certificate.gmtime_adj_notBefore(0)
    certificate.gmtime_adj_notAfter(3600)  # 1 hour from now.

    certificate.sign(issuer_key, 'sha256')
    return CertificateGroup(certificate, cert_key)


@pytest.fixture(scope='module')
def root_certificate_group() -> CertificateGroup:
    certificate_group = make_cert(serial=1, is_ca=True)
    # Assign is as a root apple certificate.
    subscriptions.providers.apple_in_app.app_store.CACHED_APPLE_ROOT_CERT = certificate_group.certificate
    return certificate_group


def make_x5c_header(certificate_chain: list[crypto.X509]) -> list[str]:
    result = []

    for certificate in certificate_chain:
        # https://www.pyopenssl.org/en/stable/api/crypto.html#OpenSSL.crypto.FILETYPE_ASN1
        # `The format used by FILETYPE_ASN1 is also sometimes referred to as DER.`
        # https://datatracker.ietf.org/doc/html/rfc7515#section-4.1.6 <– should be base64 encoded DER
        der_format = crypto.dump_certificate(crypto.FILETYPE_ASN1, certificate)
        base64_der_format = base64.b64encode(der_format)
        ascii_der_format = base64_der_format.decode('ascii')  # it's base64, nothing weird to encode.
        result.append(ascii_der_format)

    return result


def test__ok(root_certificate_group: CertificateGroup):
    intermediate_cert_group = make_cert(serial=2, is_ca=True, issuer_group=root_certificate_group)
    final_cert_group = make_cert(serial=3, is_leaf=True, issuer_group=intermediate_cert_group)

    # NOTE(kkalinowski): this is our assumption about the secret created by Apple.
    #   If it happens to be different, this is the place to fix it.
    original_payload = {
        'test': 'value'
    }
    headers = {
        'alg': ALG_JWT_HEADER,
        'x5c': make_x5c_header([
            final_cert_group.certificate,
            intermediate_cert_group.certificate,
            root_certificate_group.certificate,
        ]),
    }

    signed_payload = jwt.encode(
        original_payload,
        # Signing with a private key.
        final_cert_group.key.to_cryptography_key(),
        algorithm=ALG_JWT_HEADER,
        headers=headers,
    )

    received_payload = validate_and_fetch_apple_signed_payload(signed_payload)
    assert received_payload == original_payload, f'{received_payload=}, {original_payload=}'


def test__apple_root_certificate_not_set_up():
    pass


def test__no_certificates_in_the_header():
    pass


def test__invalid_root_certificate_from_jwt():
    pass


def test__invalid_intermediate_certificate_from_jwt():
    pass


def test__invalid_signature_of_the_jwt():
    pass
