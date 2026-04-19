import pytest

from openagents.interfaces.tool import ToolExecutionRequest, ToolExecutionSpec
from openagents.plugins.builtin.tool_executor.filesystem_aware import FilesystemAwareExecutor


class _MockTool:
    async def invoke(self, params, context):
        return "result"


def _req(tool_id="read_file", params=None, reads_files=False, writes_files=False):
    spec = ToolExecutionSpec(reads_files=reads_files, writes_files=writes_files)
    return ToolExecutionRequest(
        tool_id=tool_id, tool=_MockTool(), params=params or {}, execution_spec=spec
    )


@pytest.mark.asyncio
async def test_allow_tools_blocks_unlisted():
    ex = FilesystemAwareExecutor(config={"allow_tools": ["read_file"]})
    result = await ex.execute(_req(tool_id="write_file"))
    assert result.success is False
    assert "not in allow_tools" in result.error


@pytest.mark.asyncio
async def test_allow_tools_permits_listed():
    ex = FilesystemAwareExecutor(config={"allow_tools": ["read_file"]})
    result = await ex.execute(_req(tool_id="read_file", params={"path": "/tmp/x"}))
    assert result.success is True


@pytest.mark.asyncio
async def test_read_roots_blocks_outside_path(tmp_path):
    ex = FilesystemAwareExecutor(config={"read_roots": [str(tmp_path)]})
    result = await ex.execute(
        _req(tool_id="read_file", params={"path": "/etc/passwd"}, reads_files=True)
    )
    assert result.success is False
    assert "outside read_roots" in result.error


@pytest.mark.asyncio
async def test_no_config_allows_all():
    ex = FilesystemAwareExecutor()
    result = await ex.execute(_req(tool_id="anything"))
    assert result.success is True
