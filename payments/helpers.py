from collections import defaultdict
from datetime import datetime
from functools import reduce
from typing import Optional

from django.contrib.auth.models import AbstractUser
from django.utils.timezone import now

from payments.models import Quota, QuotaEvent, Resource


def get_remaining(
    user: AbstractUser,
    since: Optional[datetime] = None,
    at: Optional[datetime] = None,
    initial: Optional[dict[Resource, int]] = None,
) -> dict[Resource, int]:
    initial = initial or {}
    at = at or now()

    events: dict[Resource, list[QuotaEvent]] = defaultdict(list)
    for event in Quota.iter_events(user, since=since):
        events[event.resource].append(event)

    for resource in events:
        events[resource].sort()

    return {
        resource: reduce(lambda remains, event: max(0, remains + event.value), event_list, initial.get(resource, 0))
        for resource, event_list in events.items()
    }
