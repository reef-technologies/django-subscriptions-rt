import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import cached_property, partialmethod, wraps
from logging import getLogger
from typing import ClassVar
from urllib.parse import urlencode

import requests
from djmoney.money import Money
from requests.auth import AuthBase
from tenacity import (
    retry,
    retry_if_result,
    stop_after_attempt,
    wait_incrementing,
)

log = getLogger(__name__)


class PaddleError(Exception):
    def __init__(self, message, code: int):
        super().__init__(message)
        self.code = code


@dataclass
class PaddleAuth(AuthBase):
    vendor_id: int
    vendor_auth_code: str

    def __call__(self, request: requests.PreparedRequest):
        params = {
            "vendor_id": str(self.vendor_id),
            "vendor_auth_code": self.vendor_auth_code,
        }
        if request.method == "GET":
            request.prepare_url(request.url, params)
        elif isinstance(request.body, bytes):
            payload = json.loads(request.body.decode("utf8")) if request.body else {}
            payload.update(params)
            request.body = json.dumps(payload).encode("utf8")
        else:
            request.body = (f"{request.body}&" if request.body else "") + urlencode(params, doseq=False)

        return request


def paddle_result(fn: Callable) -> Callable:
    @wraps(fn)
    def wrapper(*args, **kwargs):
        response = fn(*args, **kwargs)

        try:
            result = response.json()
        except requests.JSONDecodeError:
            assert not response.ok
            response.raise_for_status()

        if not result["success"]:
            raise PaddleError(result["error"]["message"], code=result["error"]["code"])

        return result["response"]

    return wrapper


@dataclass
class Paddle:
    vendor_id: int
    vendor_auth_code: str
    endpoint: str = "https://vendors.paddle.com/api/2.0"
    # TODO: replace response `dict` type with pydantic

    TIMEOUT: ClassVar[timedelta] = timedelta(seconds=30)

    @cached_property
    def _session(self) -> requests.Session:
        session = requests.Session()
        session.auth = PaddleAuth(self.vendor_id, self.vendor_auth_code)
        return session

    @retry(
        retry=retry_if_result(
            lambda response: response.status_code
            in {
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
    def request(self, method: str, endpoint: str, *args, **kwargs) -> requests.Response:
        assert endpoint.startswith("/")
        kwargs.setdefault("timeout", int(self.TIMEOUT.total_seconds()))
        return self._session.request(method, self.endpoint + endpoint, *args, **kwargs)

    get = partialmethod(request, "get")
    post = partialmethod(request, "post")

    @paddle_result
    def list_subscription_plans(self) -> list[dict]:
        return self.get("/subscription/plans")

    @paddle_result
    def generate_payment_link(
        self,
        product_id: int,
        prices: list[Money],
        email: str,
        message: str = "",
        metadata: dict | None = None,
    ) -> str:
        metadata_str = json.dumps(metadata or {})
        if len(metadata_str) > 1000:
            log.warning(f"Metadata string exceeds the limit of 1000 chars: {metadata_str}")
            metadata_str = metadata_str[:1000]

        return self.post(
            "/product/generate_pay_link",
            json={
                "product_id": product_id,
                "prices": [f"{price.currency}:{price.amount}" for price in prices],
                "custom_message": message,
                "customer_email": email,
                "passthrough": metadata_str,
            },
        )

    @paddle_result
    def one_off_charge(
        self,
        subscription_id: int,
        amount: Decimal,
        name: str = "",
    ) -> dict:
        if len(name) > 50:
            log.warning(f"Name exceeds the limit of 50 chars: {name}")
            name = name[:50]

        return self.post(
            f"/subscription/{subscription_id}/charge",
            json={
                "amount": str(amount),
                "charge_name": name,
            },
        )

    @paddle_result
    def get_payments(
        self,
        subscription_id: int | None = None,
        plans: list[int] = [],
        is_paid: bool | None = None,
        from_: date | None = None,
        to: date | None = None,
        is_one_off_charge: bool | None = None,
    ) -> list[dict]:
        params: dict[str, str] = {}

        if subscription_id is not None:
            params["subscription_id"] = str(subscription_id)

        if plans:
            params["plan"] = ",".join(map(str, plans))

        if is_paid is not None:
            params["is_paid"] = str(int(is_paid))

        if from_:
            params["from"] = from_.strftime("%Y-%m-%d")

        if to:
            params["to"] = to.strftime("%Y-%m-%d")

        if is_one_off_charge is not None:
            params["is_one_off_charge"] = str(int(is_one_off_charge))

        return self.post("/subscription/payments", json=params)

    @paddle_result
    def get_webhook_history(
        self,
        page: int | None = None,
        alerts_per_page: int | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> dict:
        params: dict[str, str] = {}

        if page is not None:
            assert page > 0
            params["page"] = str(page)

        if alerts_per_page is not None:
            assert alerts_per_page > 0
            params["alerts_per_page"] = str(alerts_per_page)

        if start_date:
            params["query_tail"] = start_date.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")

        if end_date:
            params["query_head"] = end_date.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")

        return self.post("/alert/webhooks", json=params)

    def iter_webhook_history(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        max_pages: int = 100,
    ) -> Iterator[dict]:
        for page in range(1, max_pages + 1):
            result = self.get_webhook_history(page=page, start_date=start_date, end_date=end_date)
            yield from result["data"]
            if page == result["total_pages"]:
                break
