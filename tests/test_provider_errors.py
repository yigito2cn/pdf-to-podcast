import unittest

from pipeline.provider_errors import (
    ProviderErrorType,
    classify_provider_error,
    parse_retry_after_seconds,
)


class ProviderErrorTests(unittest.TestCase):
    def test_daily_quota_is_distinct_from_temporary_rate_limit(self) -> None:
        daily = Exception(
            "429 TPD limit: tokens per day exhausted; "
            "please try again in 7m49.152s"
        )
        temporary = Exception("429 rate limit; retry in 2.5s")

        self.assertEqual(
            classify_provider_error(daily),
            ProviderErrorType.DAILY_QUOTA,
        )
        self.assertEqual(
            classify_provider_error(temporary),
            ProviderErrorType.RATE_LIMIT,
        )

    def test_provider_configuration_errors_are_classified(self) -> None:
        self.assertEqual(
            classify_provider_error(Exception("401 API_KEY_INVALID")),
            ProviderErrorType.AUTHENTICATION,
        )
        self.assertEqual(
            classify_provider_error(Exception("404 model_not_found")),
            ProviderErrorType.MODEL_NOT_FOUND,
        )
        self.assertEqual(
            classify_provider_error(Exception("413 request too large")),
            ProviderErrorType.REQUEST_TOO_LARGE,
        )

    def test_retry_after_parser_supports_api_formats(self) -> None:
        self.assertAlmostEqual(
            parse_retry_after_seconds("try again in 7m49.152s"),
            469.152,
        )
        self.assertAlmostEqual(
            parse_retry_after_seconds("retry in 850ms"),
            0.85,
        )
        self.assertAlmostEqual(
            parse_retry_after_seconds("retry after 12.5s"),
            12.5,
        )
        self.assertIsNone(parse_retry_after_seconds("no retry advice"))


if __name__ == "__main__":
    unittest.main()