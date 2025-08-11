class PaddleException(Exception):
    pass


class AmbiguousPlanList(PaddleException):
    pass


class MissingPlan(PaddleException):
    pass
