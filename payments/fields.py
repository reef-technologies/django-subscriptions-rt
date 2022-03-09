from dateutil.relativedelta import relativedelta
from django.db import models
from djmoney.models.fields import MoneyField as DjMoneyField


def MoneyField(**kwargs) -> DjMoneyField:
    return DjMoneyField(max_digits=14, decimal_places=2, default_currency='USD', **kwargs)


class RelativeDurationField(models.JSONField):

    def from_db_value(self, value, *args, **kwags):
        value = super().from_db_value(value, *args, **kwags)
        return relativedelta(**value)

    def get_prep_value(self, value):
        dict_ = {k: v for k, v in value.__dict__.items() if not k.startswith('_') and v}
        return super().get_prep_value(dict_)
