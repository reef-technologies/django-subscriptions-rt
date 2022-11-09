import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from functools import partialmethod, wraps
from logging import getLogger
from typing import Callable, ClassVar, Iterator, List, Optional
from urllib.parse import urlencode
from tenacity import retry, retry_base, wait_incrementing, stop_after_attempt, retry_if_result

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
                payload = json.loads(request.body.decode('utf8')) if request.body else {}
                payload.update(params)
                request.body = json.dumps(payload).encode('utf8')
            else:
                request.body = (f'{request.body}&' if request.body else '') + urlencode(params, doseq=False)
        return request


def paddle_result(fn: Callable) -> Callable:
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
    # TODO: replace response `dict` type with pydantic

    _session: requests.Session = None
    TIMEOUT: ClassVar[timedelta] = timedelta(seconds=30)

    _retry: retry_base = retry(
        retry=retry_if_result(
            lambda response: response.status_code in {
                requests.codes.too_many_requests,
                requests.codes.internal_server_error,
                requests.codes.bad_gateway,
                requests.codes.service_unavailable,
                requests.codes.gateway_timeout,
            },
        ),
        stop=stop_after_attempt(10),
        wait=wait_incrementing(start=1, increment=2),
    )

    def __post_init__(self):
        self._session = requests.Session()
        self._session.auth = PaddleAuth(self.vendor_id, self.vendor_auth_code)
        self.request = self._retry(self.request)

    def request(self, method, endpoint, *args, **kwargs) -> requests.Response:
        assert endpoint.startswith('/')
        kwargs.setdefault('timeout', int(self.TIMEOUT.total_seconds()))
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
    ) -> dict:
        if len(name) > 50:
            log.warning(f'Name exceeds the limit of 50 chars: {name}')
            name = name[:50]

        response = self.post(f'/subscription/{subscription_id}/charge', json={
            'amount': str(amount),
            'charge_name': name,
        })
        response.raise_for_status()
        return response.json()

    @paddle_result
    def get_payments(
        self,
        subscription_id: Optional[int] = None,
        plans: List[int] = None,
        is_paid: Optional[bool] = None,
        from_: Optional[date] = None,
        to: Optional[date] = None,
        is_one_off_charge: Optional[bool] = None,
    ) -> List[dict]:
        params = {}

        if subscription_id is not None:
            params['subscription_id'] = subscription_id

        if plans is not None:
            params['plan'] = ','.join(map(str, plans))

        if is_paid is not None:
            params['is_paid'] = int(is_paid)

        if from_:
            params['from'] = from_.strftime('%Y-%m-%d')

        if to:
            params['to'] = to.strftime('%Y-%m-%d')

        if is_one_off_charge is not None:
            params['is_one_off_charge'] = int(is_one_off_charge)

        response = self.post('/subscription/payments', json=params)
        response.raise_for_status()
        return response.json()

    @paddle_result
    def get_webhook_history(
        self,
        page: Optional[int] = None,
        alerts_per_page: Optional[int] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> dict:
        params = {}

        if page is not None:
            assert page > 0
            params['page'] = page

        if alerts_per_page is not None:
            assert alerts_per_page > 0
            params['alerts_per_page'] = alerts_per_page

        if start_date:
            params['query_tail'] = start_date.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

        if end_date:
            params['query_head'] = end_date.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

        response = self.post('/alert/webhooks', json=params)
        response.raise_for_status()
        return response.json()

    def iter_webhook_history(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        max_pages: int = 100,
    ) -> Iterator[dict]:

        for page in range(1, max_pages + 1):
            result = self.get_webhook_history(page=page, start_date=start_date, end_date=end_date)
            yield from result['data']
            if page == result['total_pages']:
                break
