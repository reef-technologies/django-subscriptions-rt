import json
from dataclasses import dataclass
from decimal import Decimal
from functools import partialmethod, wraps
from logging import getLogger
from typing import ClassVar, List, Optional
from urllib.parse import urlencode

import requests
from djmoney.money import Money
from requests.auth import AuthBase

log = getLogger(__name__)


@dataclass
class PaddleAuth(AuthBase):
    vendor_id: str
    vendor_auth_code: str

    def __call__(self, request: requests.PreparedRequest):
        params = {
            'vendor_id': self.vendor_id,
            'vendor_auth_code': self.vendor_auth_code,
        }
        if request.method == 'GET':
            request.prepare_url(request.url, params)
        else:
            if isinstance(request.body, bytes):
                payload = json.loads(request.body.decode('utf8'))
                payload.update(params)
                request.body = json.dumps(payload).encode('utf8')
            else:
                request.body += (request.body and '&') + urlencode(params, doseq=False)
        return request


def paddle_result(fn: callable) -> callable:
    @wraps(fn)
    def wrapper(*args, **kwargs):
        result = fn(*args, **kwargs)
        if not result['success']:
            raise ValueError(result)
        return result['response']

    return wrapper


@dataclass
class Paddle:
    vendor_id: int
    vendor_auth_code: str
    endpoint: str = 'https://vendors.paddle.com/api/2.0'

    _session: requests.Session = None
    TIMEOUT: ClassVar[int] = 30

    def __post_init__(self):
        self._session = requests.Session()
        self._session.auth = PaddleAuth(self.vendor_id, self.vendor_auth_code)

    def request(self, method, endpoint, *args, **kwargs) -> requests.Response:
        assert endpoint.startswith('/')
        kwargs.setdefault('timeout', self.TIMEOUT)
        return self._session.request(method, self.endpoint + endpoint, *args, **kwargs)

    get = partialmethod(request, 'get')
    post = partialmethod(request, 'post')

    @paddle_result
    def list_subscription_plans(self) -> List[dict]:
        response = self.get('/subscription/plans')
        response.raise_for_status()
        return response.json()

    @paddle_result
    def generate_payment_link(
        self,
        product_id: int,
        prices: List[Money],
        email: str,
        message: str = '',
        metadata: Optional[dict] = None,
    ) -> str:
        metadata_str = json.dumps(metadata or {})
        if len(metadata_str) > 1000:
            log.warning(f'Metadata string exceeds the limit of 1000 chars: {metadata_str}')
            metadata_str = metadata_str[:1000]

        response = self.post('/product/generate_pay_link', json={
            'product_id': product_id,
            'prices': [f'{price.currency}:{price.amount}' for price in prices],
            'custom_message': message,
            'customer_email': email,
            'passthrough': metadata_str,
        })
        response.raise_for_status()
        return response.json()

    @paddle_result
    def one_off_charge(
        self,
        subscription_id: int,
        amount: Decimal,
        name: str = '',
    ):
        if len(name) > 50:
            log.warning(f'Name exceeds the limit of 50 chars: {name}')
            name = name[:50]

        response = self.post(f'/subscription/{subscription_id}/charge', json={
            'amount': str(amount),
            'charge_name': name,
        })
        response.raise_for_status()
        return response.json()
