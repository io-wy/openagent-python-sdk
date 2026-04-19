from __future__ import annotations

import asyncio
import json
import shutil

import pytest

from openagents.errors.exceptions import ToolError, ToolTimeoutError
from openagents.interfaces.tool import ToolExecutionRequest, ToolExecutionSpec
from openagents.plugins.builtin.tool.common import BuiltinSearchTool
from openagents.plugins.builtin.tool.datetime_tools import CurrentTimeTool, DateDiffTool, DateParseTool
from openagents.plugins.builtin.tool.file_ops import DeleteFileTool, ListFilesTool, ReadFileTool, WriteFileTool
from openagents.plugins.builtin.tool.http_ops import HttpRequestTool
from openagents.plugins.builtin.tool.math_tools import CalcTool, MinMaxTool, PercentageTool
from openagents.plugins.builtin.tool.network_tools import HostLookupTool, QueryParamTool, URLBuildTool, URLParseTool
from openagents.plugins.builtin.tool.random_tools import RandomChoiceTool, RandomIntTool, RandomStringTool, UUIDTool
from openagents.plugins.builtin.tool.system_ops import ExecuteCommandTool, GetEnvTool, SetEnvTool
from openagents.plugins.builtin.tool.text_ops import GrepFilesTool, JsonParseTool, RipgrepTool, TextTransformTool
from openagents.plugins.builtin.tool_executor.safe import SafeToolExecutor


class _FakeHttpResponse:
    def __init__(self, *, status: int = 200, body: str = "ok", headers: dict[str, str] | None = None):
        self.status = status
        self._body = body
        self.headers = headers or {"Content-Type": "text/plain"}

    def read(self) -> bytes:
        return self._body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeProc:
    def __init__(self, *, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self):
        return self._stdout, self._stderr


class _ValidatorTool:
    def __init__(self, *, valid: bool = True, delay: float = 0.0, raises: Exception | None = None):
        self._valid = valid
        self._delay = delay
        self._raises = raises

    def validate_params(self, params):
        return self._valid, "bad params" if not self._valid else None

    async def invoke(self, params, context):
        if self._delay:
            import asyncio

            await asyncio.sleep(self._delay)
        if self._raises:
            raise self._raises
        return {"params": params, "context": context}

    async def invoke_stream(self, params, context):
        yield {"type": "chunk", "data": params, "context": context}


@pytest.mark.asyncio
async def test_builtin_search_and_datetime_tools_cover_success_and_error_paths(monkeypatch):
    search = BuiltinSearchTool()
    date_parse = DateParseTool()
    date_diff = DateDiffTool()
    current_time = CurrentTimeTool()

    search_result = await search.invoke({"query": "memory runtime", "limit": 1}, None)
    empty_search = await search.invoke({"query": "", "limit": "bad"}, None)
    parsed = await date_parse.invoke({"date": "2026-04-15"}, None)
    diff_hours = await date_diff.invoke(
        {"date1": "2026-04-15 00:00:00", "date2": "2026-04-15 12:00:00", "unit": "hours"},
        None,
    )
    fallback_time = await current_time.invoke({"timezone": "Definitely/Invalid"}, None)

    assert search.schema()["required"] == ["query"]
    assert search_result["items"][0]["title"] == "Agent Memory Design"
    assert len(empty_search["items"]) == 3
    assert parsed["year"] == 2026
    assert diff_hours["result"] == 12
    assert fallback_time["timezone"] == "UTC"

    with pytest.raises(ValueError):
        await date_parse.invoke({"date": "not-a-date"}, None)
    with pytest.raises(ValueError):
        await date_diff.invoke({"date1": "2026", "date2": "bad"}, None)


@pytest.mark.asyncio
async def test_file_tools_handle_read_write_list_and_delete(tmp_path):
    read_tool = ReadFileTool()
    write_tool = WriteFileTool()
    list_tool = ListFilesTool()
    delete_tool = DeleteFileTool()

    target = tmp_path / "nested" / "note.txt"
    await write_tool.invoke({"path": str(target), "content": "hello"}, None)
    await write_tool.invoke({"path": str(target), "content": " world", "mode": "a"}, None)
    read_result = await read_tool.invoke({"path": str(target)}, None)
    root_list = await list_tool.invoke({"path": str(tmp_path), "recursive": True}, None)
    file_delete = await delete_tool.invoke({"path": str(target)}, None)

    dir_target = tmp_path / "to-delete"
    dir_target.mkdir()
    (dir_target / "child.txt").write_text("x", encoding="utf-8")
    dir_delete = await delete_tool.invoke({"path": str(dir_target)}, None)

    assert read_tool.execution_spec().reads_files is True
    assert write_tool.execution_spec().writes_files is True
    assert read_result["content"] == "hello world"
    assert root_list["count"] >= 1
    assert file_delete == {"path": str(target), "type": "file", "deleted": True}
    assert dir_delete == {"path": str(dir_target), "type": "directory", "deleted": True}

    ok, error = read_tool.validate_params({})
    assert ok is False and "required" in (error or "")

    with pytest.raises(ValueError):
        await write_tool.invoke({"path": str(tmp_path / "bad.txt"), "content": "x", "mode": "bad"}, None)
    with pytest.raises(FileNotFoundError):
        await read_tool.invoke({"path": str(tmp_path / "missing.txt")}, None)
    with pytest.raises(RuntimeError):
        await list_tool.invoke({"path": str(tmp_path / "missing")}, None)
    with pytest.raises(RuntimeError):
        await delete_tool.invoke({"path": str(tmp_path / "missing")}, None)


@pytest.mark.asyncio
async def test_http_and_network_tools_cover_common_branches(monkeypatch):
    http_tool = HttpRequestTool({"timeout": 5})
    url_parse = URLParseTool()
    url_build = URLBuildTool()
    query_tool = QueryParamTool()
    host_lookup = HostLookupTool()

    monkeypatch.setattr(
        "openagents.plugins.builtin.tool.http_ops.request.urlopen",
        lambda req, timeout=0: _FakeHttpResponse(status=201, body='{"ok": true}'),
    )
    http_result = await http_tool.invoke(
        {"url": "https://example.com/api", "method": "POST", "body": {"ok": True}},
        None,
    )
    parsed = await url_parse.invoke({"url": "https://user:pw@example.com:8443/path?q=1#frag"}, None)
    built = await url_build.invoke(
        {"scheme": "https", "host": "example.com", "path": "/docs", "query": "a=1", "fragment": "part"},
        None,
    )
    listed = await query_tool.invoke({"url": built["url"], "action": "list"}, None)
    fetched = await query_tool.invoke({"url": built["url"], "action": "get", "key": "a"}, None)
    host = await host_lookup.invoke({"url": "https://example.com:9443"}, None)

    assert http_result["status"] == 201
    assert http_result["success"] is True
    assert parsed["hostname"] == "example.com"
    assert built["url"] == "https://example.com/docs?a=1#part"
    assert listed["params"] == {"a": "1"}
    assert fetched == {"key": "a", "value": "1"}
    assert host == {"host": "example.com", "port": 9443, "has_https": True, "domain": "com"}

    with pytest.raises(ValueError):
        await http_tool.invoke({"url": "", "method": "GET"}, None)
    with pytest.raises(ValueError):
        await http_tool.invoke({"url": "https://example.com", "method": "TRACE"}, None)
    with pytest.raises(ValueError):
        await url_parse.invoke({"url": "http://example.com:bad"}, None)
    with pytest.raises(ValueError):
        await url_build.invoke({"host": ""}, None)
    with pytest.raises(ValueError):
        await query_tool.invoke({"url": built["url"], "action": "get"}, None)
    with pytest.raises(ValueError):
        await query_tool.invoke({"url": built["url"], "action": "set"}, None)
    with pytest.raises(ValueError):
        await host_lookup.invoke({"url": ""}, None)


@pytest.mark.asyncio
async def test_math_and_random_tools_cover_validation_and_result_variants(monkeypatch):
    calc = CalcTool()
    percentage = PercentageTool()
    minmax = MinMaxTool()
    randint_tool = RandomIntTool()
    choice_tool = RandomChoiceTool()
    uuid_tool = UUIDTool()
    random_string = RandomStringTool()

    monkeypatch.setattr("openagents.plugins.builtin.tool.random_tools.random.randint", lambda a, b: a + b)
    monkeypatch.setattr("openagents.plugins.builtin.tool.random_tools.random.choice", lambda seq: seq[0])
    monkeypatch.setattr(
        "openagents.plugins.builtin.tool.random_tools.random.sample", lambda seq, count: list(seq)[:count]
    )
    monkeypatch.setattr("openagents.plugins.builtin.tool.random_tools.uuid.uuid1", lambda: "uuid1")
    monkeypatch.setattr("openagents.plugins.builtin.tool.random_tools.uuid.uuid4", lambda: "uuid4")

    assert calc.schema()["required"] == ["expression"]
    assert (await calc.invoke({"expression": "2 + 3 * 4"}, None))["result"] == 14
    assert (await percentage.invoke({"value": 200, "percent": 10, "operation": "decrease"}, None))["result"] == 180
    assert (await minmax.invoke({"numbers": "1, 2, 3, 4", "action": "median"}, None))["result"] == 2.5
    assert (await randint_tool.invoke({"min": 2, "max": 5}, None)) == {"value": 7}
    assert (await choice_tool.invoke({"choices": ["a", "b"], "count": 2}, None)) == {"values": ["a", "b"]}
    assert (await uuid_tool.invoke({"count": 2, "version": 1}, None)) == {"uuids": ["uuid1", "uuid1"]}
    assert (await random_string.invoke({"length": 4, "charset": "hex"}, None))["length"] == 4

    with pytest.raises(ValueError):
        await calc.invoke({"expression": "2 // 2"}, None)
    with pytest.raises(ValueError):
        await calc.invoke({"expression": "import os"}, None)
    with pytest.raises(ValueError):
        await percentage.invoke({"value": "bad", "percent": 2}, None)
    with pytest.raises(ValueError):
        await percentage.invoke({"value": 10, "percent": 2, "operation": "unknown"}, None)
    with pytest.raises(ValueError):
        await minmax.invoke({"numbers": [], "action": "min"}, None)
    with pytest.raises(ValueError):
        await minmax.invoke({"numbers": ["a"], "action": "min"}, None)
    with pytest.raises(ValueError):
        await randint_tool.invoke({"min": 2, "max": 2}, None)
    with pytest.raises(ValueError):
        await choice_tool.invoke({"choices": [], "count": 1}, None)
    with pytest.raises(ValueError):
        await uuid_tool.invoke({"count": 0}, None)
    with pytest.raises(ValueError):
        await random_string.invoke({"length": 0}, None)


@pytest.mark.asyncio
async def test_system_and_text_tools_cover_success_and_failure_paths(monkeypatch, tmp_path):
    execute_tool = ExecuteCommandTool({"timeout": 2})
    getenv_tool = GetEnvTool()
    setenv_tool = SetEnvTool()
    grep_tool = GrepFilesTool()
    ripgrep_tool = RipgrepTool()
    json_tool = JsonParseTool()
    transform_tool = TextTransformTool()

    async def _create_subprocess_shell(*args, **kwargs):
        return _FakeProc(stdout=b"ok", stderr=b"", returncode=0)

    async def _create_subprocess_exec(*args, **kwargs):
        payload = json.dumps(
            {
                "type": "match",
                "data": {
                    "path": {"text": "note.txt"},
                    "line_number": 1,
                    "lines": {"text": "hello world\n"},
                },
            }
        ).encode("utf-8")
        return _FakeProc(stdout=payload, stderr=b"", returncode=0)

    monkeypatch.setattr(
        "openagents.plugins.builtin.tool.system_ops.asyncio.create_subprocess_shell", _create_subprocess_shell
    )
    monkeypatch.setattr(shutil, "which", lambda name: "rg")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _create_subprocess_exec)

    text_file = tmp_path / "note.txt"
    text_file.write_text("Hello\nworld\n", encoding="utf-8")
    binary_file = tmp_path / "data.bin"
    binary_file.write_bytes(b"\xff\xfe\x00")

    exec_result = await execute_tool.invoke({"command": "echo hi"}, None)
    set_result = await setenv_tool.invoke({"key": "OPENAGENTS_TEST_ENV", "value": 123}, None)
    get_result = await getenv_tool.invoke({"key": "OPENAGENTS_TEST_ENV"}, None)
    grep_result = await grep_tool.invoke({"pattern": "hello", "path": str(tmp_path), "case_sensitive": False}, None)
    rg_result = await ripgrep_tool.invoke({"pattern": "hello", "path": str(tmp_path), "file_type": "py"}, None)
    parsed = await json_tool.invoke({"text": '{"ok": true}'}, None)
    transformed = await transform_tool.invoke({"text": " hello ", "operation": "strip"}, None)

    assert exec_result["success"] is True and exec_result["stdout"] == "ok"
    assert set_result == {"key": "OPENAGENTS_TEST_ENV", "value": 123, "set": True}
    assert get_result == {"key": "OPENAGENTS_TEST_ENV", "value": "123", "exists": True}
    assert grep_result["total"] == 1
    assert rg_result["matches"][0]["file"] == "note.txt"
    assert parsed == {"parsed": {"ok": True}, "type": "dict"}
    assert transformed == {"original": " hello ", "operation": "strip", "result": "hello"}

    with pytest.raises(ValueError):
        await execute_tool.invoke({"command": ""}, None)
    with pytest.raises(ValueError):
        await getenv_tool.invoke({"key": ""}, None)
    with pytest.raises(ValueError):
        await grep_tool.invoke({"pattern": "", "path": str(tmp_path)}, None)

    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError):
        await ripgrep_tool.invoke({"pattern": "hello", "path": str(tmp_path)}, None)

    with pytest.raises(ValueError):
        await json_tool.invoke({"text": "{bad json"}, None)
    with pytest.raises(ValueError):
        await transform_tool.invoke({"text": "hello", "operation": "missing"}, None)


@pytest.mark.asyncio
async def test_safe_tool_executor_handles_validation_success_timeout_exception_and_non_passthrough_stream():
    validator_failure = _ValidatorTool(valid=False)
    success_tool = _ValidatorTool()
    timeout_tool = _ValidatorTool(delay=0.05)
    error_tool = _ValidatorTool(raises=RuntimeError("boom"))

    request = ToolExecutionRequest(tool_id="demo", tool=success_tool, params={"value": 1}, context={"ctx": True})
    invalid_request = ToolExecutionRequest(tool_id="bad", tool=validator_failure, params={})
    timeout_request = ToolExecutionRequest(
        tool_id="slow",
        tool=timeout_tool,
        params={},
        execution_spec=ToolExecutionSpec(default_timeout_ms=1),
    )
    error_request = ToolExecutionRequest(tool_id="err", tool=error_tool, params={})

    executor = SafeToolExecutor({"default_timeout_ms": 50, "allow_stream_passthrough": False})
    invalid = await executor.execute(invalid_request)
    success = await executor.execute(request)
    timeout = await executor.execute(timeout_request)
    error = await executor.execute(error_request)
    chunks = [chunk async for chunk in executor.execute_stream(request)]

    assert invalid.success is False and "bad params" in (invalid.error or "")
    assert isinstance(invalid.exception, ToolError)
    assert success.success is True and success.data["params"] == {"value": 1}
    assert success.metadata == {"timeout_ms": 50}
    assert timeout.success is False and "timed out" in (timeout.error or "")
    assert isinstance(timeout.exception, ToolTimeoutError)
    assert error.success is False and "boom" in (error.error or "")
    assert isinstance(error.exception, ToolError)
    assert chunks == [{"type": "result", "data": {"params": {"value": 1}, "context": {"ctx": True}}, "error": None}]


def test_safe_tool_executor_warns_on_unknown_config_keys(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="openagents.interfaces.typed_config"):
        executor = SafeToolExecutor({"default_timeout_ms": 100, "totally_unknown": 1})

    assert executor._default_timeout_ms == 100
    assert any(
        "unknown config keys" in r.message
        and "SafeToolExecutor" in r.message
        and "totally_unknown" in r.message
        for r in caplog.records
    )


def test_http_request_tool_warns_on_unknown_config_keys(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="openagents.interfaces.typed_config"):
        tool = HttpRequestTool({"timeout": 60, "totally_unknown": 1})

    assert tool._timeout == 60
    assert any(
        "unknown config keys" in r.message
        and "HttpRequestTool" in r.message
        and "totally_unknown" in r.message
        for r in caplog.records
    )
