from __future__ import annotations
import dataclasses

from contextlib import contextmanager, suppress
from datetime import timedelta
from typing import Callable, Optional, Union, TYPE_CHECKING, ClassVar

from django.contrib.auth import get_user_model
from django.db.models import QuerySet, Max, Q
from django.utils.timezone import now

from subscriptions.models import SubscriptionNotificationEvent
from subscriptions.utils import HardDBLock


if TYPE_CHECKING:
    from typing import ParamSpec, TypeVar
    P = ParamSpec('P')
    R = TypeVar('R')
F = Callable[['P'], 'R']


@dataclasses.dataclass
class _Notification:
    name: str
    function: F
    queryset: Optional[QuerySet]
    parameters: Optional[dict[str, Union[str, int]]]
    forget_after: int = None
    ignore_default_plan: bool = True


class NotificationManager:
    """
    Protected class, only one instance should be necessary.
    """
    _notifications: ClassVar[dict[str, _Notification]] = {}
    default_queryset: QuerySet

    def __init__(self, default_queryset: QuerySet = None, subscriptions_plan_to_exclude: list[int] = None):
        if default_queryset:
            self.default_queryset = default_queryset
        else:
            self.default_queryset = get_user_model().objects.all()
        self.subscriptions_plan_to_exclude = subscriptions_plan_to_exclude if subscriptions_plan_to_exclude else []

    @classmethod
    def execute_all(cls):
        """ Execute all notifications. """
        for notification in cls._notifications.keys():
            cls.execute(notification)

    def register(
            self,
            name: str,
            queryset: QuerySet = None,
            forget_after: int = None,
            **parameters
    ) -> Callable[[F], F]:

        if not queryset:
            queryset = self.default_queryset

        def decorator(function: F) -> F:
            if name in self._notifications:
                notification = self._notifications[name]
                raise ValueError(f'Notification {name} already registered for {notification.function}')
            self._notifications[name] = _Notification(name, function, queryset, parameters, forget_after=forget_after)
            return function
        return decorator

    @staticmethod
    def _get_queryset(notification: _Notification) -> QuerySet:
        filters = {}

        queryset = notification.queryset.exclude(
            subscription_notification_events__name=notification.name,
        )
        for parameter, value in notification.parameters.items():
            # in the case of days since we do an aggregation.
            # we do want to be sure it is the last item
            if 'days_since_' in parameter:
                parameter = parameter[len('days_since_'):]
                since = now() - timedelta(days=value)
                filters.update({
                    f'{parameter}__gte': since,
                    f'{parameter}__lte': since + timedelta(1)
                })
            else:
                filters[parameter] = value
        return queryset.filter(**filters)

    @classmethod
    def execute(cls, name: str) -> None:
        """ Fetch all the missing notifications. """

        # to ensure the same entry has been generated only once
        with HardDBLock(cls.__name__, name):
            notification = cls._notifications[name]
            # eventually some notification might be designed to be sent after enough time passed.
            # for example, see this scenario
            # --- unsubscribe -> coupon -> subscribe -> unsubscribe -> coupon ? ---
            #  THe coupon might be sent if enough time has passed since last time.
            if notification.forget_after is not None:
                # TODO: There is a reason why not to delete these?
                SubscriptionNotificationEvent.objects.filter(
                    name=notification.name,
                    created__lte=now() - timedelta(notification.forget_after)
                ).delete()
            queryset = cls._get_queryset(notification)
            events = [
                SubscriptionNotificationEvent(user=user, name=name)
                for user in queryset
            ]
            SubscriptionNotificationEvent.objects.bulk_create(
                events
            )
        for event in events:
            cls._execute_event(event, notification)

    @classmethod
    def _execute_event(cls, event: SubscriptionNotificationEvent, notification: _Notification):
        with cls._event_handler(event):
            notification.function(event.user)

    @classmethod
    @contextmanager
    def _event_handler(cls, event: SubscriptionNotificationEvent):

        event.status = event.Status.ONGOING
        event.save()
        try:
            yield
            event.status = event.Status.COMPLETED
            event.save()
        except Exception:
            event.status = event.Status.ERROR
            event.save()
            raise


def get_default_notification_manager() -> NotificationManager:
    """
    This notification manager is aware of the most common use case.
    Which is filter by subscription end, ignore default subscription

    Available custom filter fields:
        - subscriptions_end: the max end date of the user paid subscriptions.
    """
    plan_id = _get_default_plan()
    queryset = get_user_model().objects.all()

    # if we have a plan id ignore it.
    if plan_id:
        queryset = queryset.annotate(
            **{f'subscriptions_end': Max('subscriptions__end', filter=~Q(
                subscriptions__plan_id__in=[plan_id]
            ))}
        )
    else:
        queryset = queryset.annotate(
            **{f'subscription_end': Max('subscription__end')}
        )
    return NotificationManager(default_queryset=queryset)


def _get_default_plan() -> Optional[int]:
    with suppress(ImportError):
        from constance import config
        return config.SUBSCRIPTIONS_DEFAULT_PLAN_ID
    return None
