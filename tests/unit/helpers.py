from datetime import datetime

from dateutil.relativedelta import relativedelta
from djmoney.money import Money


def usd(value) -> Money:
    return Money(value, "USD")


def days(n: int):
    return relativedelta(days=n)


def datetime_to_api(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")  # .replace(microsecond=0)
