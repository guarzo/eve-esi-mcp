from __future__ import annotations

import os

os.environ.setdefault("EVE_ESI_MCP_CONTACT", "test@example.com")

from eve_esi_mcp.limits import DEFAULT_ROW_LIMIT, MAX_ROW_LIMIT, capped  # noqa: E402


def test_capped_passes_through_small_lists_and_marks_untruncated():
    result = capped([1, 2, 3], limit=10)
    assert result["items"] == [1, 2, 3]
    assert result["returned"] == 3
    assert result["total"] == 3
    assert result["truncated"] is False
    assert "note" not in result


def test_capped_truncates_and_always_signals():
    rows = list(range(100))
    result = capped(rows, limit=10)
    assert result["items"] == list(range(10))
    assert result["returned"] == 10
    assert result["total"] == 100
    assert result["truncated"] is True
    # The signal must be readable by the model, not just a boolean it might ignore.
    assert "note" in result
    assert "100" in result["note"]


def test_capped_none_limit_returns_everything_untruncated():
    rows = list(range(500))
    result = capped(rows, limit=None)
    assert result["returned"] == 500
    assert result["truncated"] is False


def test_capped_applies_default_when_limit_omitted():
    rows = list(range(DEFAULT_ROW_LIMIT + 50))
    result = capped(rows)
    assert result["returned"] == DEFAULT_ROW_LIMIT
    assert result["truncated"] is True


def test_capped_limit_is_clamped_to_ceiling():
    rows = list(range(MAX_ROW_LIMIT + 5000))
    result = capped(rows, limit=999_999)
    # A caller must not be able to opt out of protection with a giant limit.
    assert result["returned"] == MAX_ROW_LIMIT
    assert result["truncated"] is True


def test_capped_carries_extra_context_fields():
    result = capped([1], limit=10, extra={"region_id": 10000002})
    assert result["region_id"] == 10000002
