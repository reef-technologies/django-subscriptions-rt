from contextlib import suppress

from .._internal.signals import (  # noqa: F401
    create_default_subscription_for_new_user,
)

with suppress(ImportError):
    from .._internal.signals import constance_updated  # noqa: F401
