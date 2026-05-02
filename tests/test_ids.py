from __future__ import annotations

import os

os.environ.setdefault("EVE_ESI_MCP_CONTACT", "test@example.com")

import pytest  # noqa: E402

from eve_esi_mcp.ids import hub, load_hubs, load_regions, region_id  # noqa: E402


def test_load_regions_contains_the_forge():
    regions = load_regions()
    assert regions["The Forge"] == 10000002


def test_region_id_accepts_name_case_insensitively():
    assert region_id("the forge") == 10000002


def test_region_id_accepts_numeric_string():
    assert region_id("10000002") == 10000002


def test_region_id_rejects_unknown():
    with pytest.raises(ValueError):
        region_id("Not A Real Region")


def test_hub_jita_known():
    h = hub("jita")
    assert h["system_id"] == 30000142
    assert h["region_id"] == 10000002


def test_hubs_all_have_required_fields():
    for name, meta in load_hubs().items():
        for field in ("system_id", "region_id", "station_id", "system_name", "region_name"):
            assert field in meta, f"{name} missing {field}"
