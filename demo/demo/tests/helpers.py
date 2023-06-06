from djmoney.money import Money
from dateutil.relativedelta import relativedelta


def usd(value) -> Money:
    return Money(value, 'USD')


def days(n: int):
    return relativedelta(days=n)
