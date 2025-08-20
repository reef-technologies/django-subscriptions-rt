from ...exceptions import InvalidOperation as BaseInvalidOperation


class GoogleInAppException(Exception):
    pass


class InvalidOperation(GoogleInAppException, BaseInvalidOperation):
    pass


class AmbiguousDataError(GoogleInAppException):
    pass
