import unittest


class TestAppleReceiptValidator(unittest.TestCase):
    def test__ok(self):
        pass

    def test__retry_on_sandbox_when_status_code_tells_you_so(self):
        pass

    def test__retry_when_failed_request_is_retryable(self):
        pass

    def test__dont_retry_when_failed_request_is_not_retryable(self):
        pass

    def test__retry_in_case_of_service_error(self):
        pass
