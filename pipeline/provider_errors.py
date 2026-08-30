from __future__ import annotations

import re
from enum import Enum


class ProviderErrorType(str, Enum):
    DAILY_QUOTA = "daily_quota"
    RATE_LIMIT = "rate_limit"
    AUTHENTICATION = "authentication"
    MODEL_NOT_FOUND = "model_not_found"
    REQUEST_TOO_LARGE = "request_too_large"
    TEMPORARY = "temporary"
    UNKNOWN = "unknown"


def get_status_code(error: Exception) -> int | None:
    for attribute in ("status_code", "code"):
        value = getattr(error, attribute, None)
        if isinstance(value, int):
            return value

    response = getattr(error, "response", None)
    response_code = getattr(response, "status_code", None)
    if isinstance(response_code, int):
        return response_code

    match = re.search(
        r"\b(400|401|403|404|408|413|429|500|502|503|504)\b",
        str(error),
    )
    return int(match.group(1)) if match else None


def classify_provider_error(error: Exception) -> ProviderErrorType:
    status_code = get_status_code(error)
    message = str(error).lower()

    daily_quota_markers = (
        "requestsperdayperprojectpermodel",
        "generate requests per day",
        "requests per day",
        "tokens per day",
        "generaterequestsperday",
        "daily quota",
        "daily token limit",
        "rpd limit",
        "tpd limit",
    )
    if any(marker in message for marker in daily_quota_markers):
        return ProviderErrorType.DAILY_QUOTA

    authentication_markers = (
        "invalid api key",
        "invalid_api_key",
        "api_key_invalid",
        "api key not valid",
        "access_token_type_unsupported",
        "authentication failed",
        "unauthorized",
        "gemini_api_key bulunamadı",
        "groq_api_key ortam değişkeni bulunamadı",
    )
    if status_code in (401, 403) or any(
        marker in message for marker in authentication_markers
    ):
        return ProviderErrorType.AUTHENTICATION

    model_markers = (
        "model_not_found",
        "model not found",
        "model is not available",
        "do not have access to model",
    )
    if status_code == 404 or any(
        marker in message for marker in model_markers
    ):
        return ProviderErrorType.MODEL_NOT_FOUND

    request_size_markers = (
        "request too large",
        "maximum context length",
        "context_length_exceeded",
        "context window exceeded",
        "prompt is too long",
    )
    if status_code == 413 or any(
        marker in message for marker in request_size_markers
    ):
        return ProviderErrorType.REQUEST_TOO_LARGE

    rate_limit_markers = (
        "rate limit",
        "rate_limit",
        "too many requests",
        "resource exhausted",
        "quota exceeded",
    )
    if status_code == 429 or any(
        marker in message for marker in rate_limit_markers
    ):
        return ProviderErrorType.RATE_LIMIT

    temporary_markers = (
        "temporarily unavailable",
        "timeout",
        "timed out",
        "connection reset",
        "connection error",
        "internal error",
        "server error",
        "service unavailable",
    )
    if status_code in (408, 500, 502, 503, 504) or any(
        marker in message for marker in temporary_markers
    ):
        return ProviderErrorType.TEMPORARY

    return ProviderErrorType.UNKNOWN


def parse_retry_after_seconds(error: Exception | str) -> float | None:
    message = str(error)

    minute_second_match = re.search(
        r"(?:try again|retry)(?:\s+in|\s+after)?\s+"
        r"(?:(\d+(?:\.\d+)?)m)?\s*(\d+(?:\.\d+)?)s",
        message,
        flags=re.IGNORECASE,
    )
    if minute_second_match:
        minutes = float(minute_second_match.group(1) or 0)
        seconds = float(minute_second_match.group(2))
        return minutes * 60 + seconds

    millisecond_match = re.search(
        r"(?:try again|retry)(?:\s+in|\s+after)?\s+"
        r"(\d+(?:\.\d+)?)ms",
        message,
        flags=re.IGNORECASE,
    )
    if millisecond_match:
        return float(millisecond_match.group(1)) / 1000

    seconds_match = re.search(
        r"(?:try again|retry)(?:\s+in|\s+after)?\s+"
        r"(\d+(?:\.\d+)?)s?\b",
        message,
        flags=re.IGNORECASE,
    )
    if seconds_match:
        return float(seconds_match.group(1))

    return None
