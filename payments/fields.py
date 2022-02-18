from djmoney.models.fields import MoneyField as DjMoneyField


def MoneyField(**kwargs) -> DjMoneyField:
    return DjMoneyField(max_digits=14, decimal_places=2, default_currency='USD', **kwargs)
