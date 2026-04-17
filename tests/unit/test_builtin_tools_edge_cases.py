"""WP5 backfill: cover validation/error branches in builtin tools."""

from __future__ import annotations

import pytest

from openagents.plugins.builtin.tool.datetime_tools import (
    CurrentTimeTool,
    DateDiffTool,
    DateParseTool,
)
from openagents.plugins.builtin.tool.math_tools import CalcTool, MinMaxTool, PercentageTool
from openagents.plugins.builtin.tool.network_tools import (
    HostLookupTool,
    QueryParamTool,
    URLBuildTool,
    URLParseTool,
)
from openagents.plugins.builtin.tool.random_tools import (
    RandomChoiceTool,
    RandomIntTool,
    RandomStringTool,
    UUIDTool,
)
from openagents.plugins.builtin.tool.system_ops import GetEnvTool, SetEnvTool
from openagents.plugins.builtin.tool.text_ops import (
    JsonParseTool,
    TextTransformTool,
)


@pytest.mark.asyncio
async def test_calc_rejects_disallowed_characters():
    with pytest.raises(ValueError):
        await CalcTool().invoke({"expression": "abc"}, None)


@pytest.mark.asyncio
async def test_calc_rejects_empty_expression():
    with pytest.raises(ValueError):
        await CalcTool().invoke({"expression": ""}, None)


@pytest.mark.asyncio
async def test_percentage_rejects_unknown_operation():
    with pytest.raises(ValueError):
        await PercentageTool().invoke({"value": 1, "percent": 1, "operation": "xyz"}, None)


@pytest.mark.asyncio
async def test_percentage_rejects_non_numeric():
    with pytest.raises(ValueError):
        await PercentageTool().invoke({"value": "x", "percent": "y"}, None)


@pytest.mark.asyncio
async def test_min_max_unknown_action():
    with pytest.raises(ValueError):
        await MinMaxTool().invoke({"numbers": [1, 2, 3], "action": "wat"}, None)


@pytest.mark.asyncio
async def test_min_max_csv_string_input():
    out = await MinMaxTool().invoke({"numbers": "1,2,3", "action": "sum"}, None)
    assert out["result"] == 6


@pytest.mark.asyncio
async def test_min_max_median_even_count():
    out = await MinMaxTool().invoke({"numbers": [1, 2, 3, 4], "action": "median"}, None)
    assert out["result"] == 2.5


@pytest.mark.asyncio
async def test_min_max_avg_branch():
    out = await MinMaxTool().invoke({"numbers": [2, 4], "action": "avg"}, None)
    assert out["result"] == 3.0


@pytest.mark.asyncio
async def test_min_max_max_branch():
    out = await MinMaxTool().invoke({"numbers": [1, 5, 2], "action": "max"}, None)
    assert out["result"] == 5


@pytest.mark.asyncio
async def test_random_int_rejects_invalid_bounds():
    with pytest.raises(ValueError):
        await RandomIntTool().invoke({"min": 5, "max": 3}, None)


@pytest.mark.asyncio
async def test_random_int_rejects_non_integer():
    with pytest.raises(ValueError):
        await RandomIntTool().invoke({"min": 0.5, "max": 1.5}, None)


@pytest.mark.asyncio
async def test_random_int_rejects_invalid_count():
    with pytest.raises(ValueError):
        await RandomIntTool().invoke({"min": 0, "max": 10, "count": 0}, None)


@pytest.mark.asyncio
async def test_random_choice_requires_choices():
    with pytest.raises(ValueError):
        await RandomChoiceTool().invoke({"choices": []}, None)


@pytest.mark.asyncio
async def test_random_choice_rejects_excessive_count():
    with pytest.raises(ValueError):
        await RandomChoiceTool().invoke({"choices": ["a"], "count": 5}, None)


@pytest.mark.asyncio
async def test_uuid_v1_branch():
    out = await UUIDTool().invoke({"version": 1, "count": 2}, None)
    assert "uuids" in out


@pytest.mark.asyncio
async def test_random_string_rejects_excessive_length():
    with pytest.raises(ValueError):
        await RandomStringTool().invoke({"length": 0}, None)


@pytest.mark.asyncio
async def test_date_parse_unparseable():
    with pytest.raises(ValueError):
        await DateParseTool().invoke({"date": "not a date"}, None)


@pytest.mark.asyncio
async def test_date_parse_requires_input():
    with pytest.raises(ValueError):
        await DateParseTool().invoke({"date": ""}, None)


@pytest.mark.asyncio
async def test_date_diff_unparseable():
    with pytest.raises(ValueError):
        await DateDiffTool().invoke({"date1": "x", "date2": "y"}, None)


@pytest.mark.asyncio
async def test_date_diff_unit_branches():
    base = {"date1": "2024-01-01", "date2": "2024-01-02"}
    for unit in ("hours", "minutes", "seconds", "weeks"):
        out = await DateDiffTool().invoke({**base, "unit": unit}, None)
        assert "result" in out


@pytest.mark.asyncio
async def test_current_time_invalid_timezone_falls_back_to_utc():
    out = await CurrentTimeTool().invoke({"timezone": "no-such-tz"}, None)
    assert out["timezone"] == "UTC"


@pytest.mark.asyncio
async def test_url_parse_invalid_url():
    out = await URLParseTool().invoke({"url": "https://example.com:99/path?q=1"}, None)
    assert out["scheme"] == "https"
    assert out["port"] == 99


@pytest.mark.asyncio
async def test_url_parse_requires_url():
    with pytest.raises(ValueError):
        await URLParseTool().invoke({}, None)


@pytest.mark.asyncio
async def test_url_build_with_fragment_and_query():
    out = await URLBuildTool().invoke(
        {"scheme": "https", "host": "x", "path": "/p", "query": "a=1", "fragment": "anchor"}, None
    )
    assert out["url"] == "https://x/p?a=1#anchor"


@pytest.mark.asyncio
async def test_url_build_requires_host():
    with pytest.raises(ValueError):
        await URLBuildTool().invoke({"host": ""}, None)


@pytest.mark.asyncio
async def test_query_param_list_action():
    out = await QueryParamTool().invoke(
        {"url": "https://x/?a=1&b=2", "action": "list"}, None
    )
    assert out["params"] == {"a": "1", "b": "2"}


@pytest.mark.asyncio
async def test_query_param_get_missing_key_returns_none():
    out = await QueryParamTool().invoke(
        {"url": "https://x/?a=1", "key": "z", "action": "get"}, None
    )
    assert out["value"] is None


@pytest.mark.asyncio
async def test_query_param_unknown_action():
    with pytest.raises(ValueError):
        await QueryParamTool().invoke(
            {"url": "https://x/?a=1", "key": "a", "action": "xx"}, None
        )


@pytest.mark.asyncio
async def test_host_lookup_with_port():
    out = await HostLookupTool().invoke({"url": "https://x.example.com:8080/p"}, None)
    assert out["port"] == 8080
    assert out["has_https"] is True


@pytest.mark.asyncio
async def test_host_lookup_requires_url():
    with pytest.raises(ValueError):
        await HostLookupTool().invoke({}, None)


@pytest.mark.asyncio
async def test_set_env_requires_key():
    with pytest.raises(ValueError):
        await SetEnvTool().invoke({"value": "v"}, None)


@pytest.mark.asyncio
async def test_get_env_requires_key():
    with pytest.raises(ValueError):
        await GetEnvTool().invoke({}, None)


@pytest.mark.asyncio
async def test_text_transform_unknown_op():
    with pytest.raises(ValueError):
        await TextTransformTool().invoke({"text": "x", "operation": "exotic"}, None)


@pytest.mark.asyncio
async def test_json_parse_invalid():
    with pytest.raises(ValueError):
        await JsonParseTool().invoke({"text": "not json"}, None)


@pytest.mark.asyncio
async def test_json_parse_requires_text():
    with pytest.raises(ValueError):
        await JsonParseTool().invoke({"text": ""}, None)
