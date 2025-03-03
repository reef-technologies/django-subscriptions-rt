from json import JSONEncoder

from dateutil.relativedelta import relativedelta
from django.db import models
from djmoney.models.fields import MoneyField as DjMoneyField


def MoneyField(**kwargs) -> DjMoneyField:
    return DjMoneyField(max_digits=14, decimal_places=2, default_currency="USD", **kwargs)


def relativedelta_to_dict(value: relativedelta) -> dict:
    return {k: v for k, v in value.__dict__.items() if not k.startswith("_") and v}


class RelativedeltaEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, relativedelta):
            return relativedelta_to_dict(obj)

        return super().default(obj)


class RelativeDurationField(models.JSONField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("encoder", RelativedeltaEncoder)
        super().__init__(*args, **kwargs)

    def from_db_value(self, value, *args, **kwags):
        value = super().from_db_value(value, *args, **kwags)
        return relativedelta(**value)
