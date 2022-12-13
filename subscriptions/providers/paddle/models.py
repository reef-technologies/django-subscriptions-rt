import json

from pydantic import BaseModel, Extra, validator, root_validator


class Passthrough(BaseModel):
    subscription_payment_id: str

    class Config:
        extra = Extra.ignore

    @root_validator(pre=True)
    def parse_legacy_subscription_payment_id(cls, values):
        if legacy_payment_id := values.pop('SubscriptionPayment.id', None):
            values['subscription_payment_id'] = legacy_payment_id
        return values


class Alert(BaseModel):
    alert_name: str
    subscription_payment_id: str
    passthrough: Passthrough

    class Config:
        extra = Extra.ignore

    @validator("passthrough", pre=True)
    def parse_passthrough(cls, value):
        return json.loads(value)
