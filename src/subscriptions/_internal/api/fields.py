from dateutil.relativedelta import relativedelta
from rest_framework.serializers import Field


class RelativedeltaField(Field):
    def to_representation(self, value):
        return value.__dict__

    def to_internal_value(self, data):
        return relativedelta(**data)


# class MoneyField(Field):

#     def to_representation(self, value):
#         return
