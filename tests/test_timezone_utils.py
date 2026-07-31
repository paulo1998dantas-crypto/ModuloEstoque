from datetime import datetime, timezone

from timezone_utils import format_sao_paulo, to_sao_paulo


def test_naive_utc_inventory_timestamp_is_displayed_in_sao_paulo():
    stored_value = datetime(2026, 7, 30, 20, 56, 11)

    assert format_sao_paulo(stored_value, "%d/%m/%Y %H:%M:%S") == "30/07/2026 17:56:11"


def test_aware_timestamp_preserves_its_instant_when_converted():
    utc_value = datetime(2026, 7, 30, 20, 56, 11, tzinfo=timezone.utc)
    local_value = to_sao_paulo(utc_value)

    assert local_value is not None
    assert local_value.isoformat() == "2026-07-30T17:56:11-03:00"
