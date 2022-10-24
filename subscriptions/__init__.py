from typing import NamedTuple

from django.contrib.auth.models import User


# Used only in case when real Users are not used.
class UserData(NamedTuple):
    id_: str
    tier: str

    def get_or_create(self) -> User:
        return User.objects.get_or_create(username=self.id_)
