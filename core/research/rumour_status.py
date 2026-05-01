from __future__ import annotations

from enum import Enum


class RumourStatus(str, Enum):
    CONFIRMED = "confirmed"
    NOT_CONFIRMED = "not_confirmed"
    FALSE = "false"
    UNCLEAR = "unclear"
    PARTIALLY_TRUE = "partially_true"


def status_label(status: RumourStatus | str) -> str:
    value = str(status.value if isinstance(status, RumourStatus) else status)
    return {
        "confirmed": "Confirmed",
        "not_confirmed": "Not confirmed",
        "false": "False",
        "unclear": "Unclear",
        "partially_true": "Partially true",
    }.get(value, "Unclear")
