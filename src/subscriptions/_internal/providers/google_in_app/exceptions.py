class GoogleInAppException(Exception):
    pass


class InvalidOperation(GoogleInAppException):
    pass


class AmbiguousDataError(GoogleInAppException):
    pass
