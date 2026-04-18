# Builtin Tools Reference

This page documents all builtin tool plugins shipped with OpenAgents SDK 0.3.x. Every tool's `type` key can be referenced directly in the `tools` array of `agent.json` with no additional installation (except the MCP tool).

For an overview of the tool system architecture, see [Plugin Development Guide](plugin-development.md). For configuring `tool_executor` (permission control, retry policies), see [Configuration Reference](configuration.md).

---

## Search

### `builtin_search`

Search the built-in knowledge corpus for relevant document snippets. This is a demo stub tool that ranks three hard-coded corpus entries by keyword frequency against the query. Suitable for examples and tests; no external dependencies required.

**Config example**

```json
{"id": "search", "type": "builtin_search"}
```

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | `string` | Yes | Search keywords; tokenized on whitespace, scored by frequency |
| `limit` | `integer` | No | Maximum results to return; default `3` |

**Return value**

```json
{
  "query": "memory",
  "items": [
    {"title": "Agent Memory Design", "snippet": "..."}
  ]
}
```

---

## File Operations

### `read_file`

Read the full content of a text file, decoded as UTF-8.

**Config example**

```json
{"id": "read_file", "type": "read_file"}
```

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | `string` | Yes | Path to the file (absolute or relative) |

**Return value**

```json
{
  "path": "src/main.py",
  "content": "...",
  "size": 1024
}
```

**Notes**

- Always reads as UTF-8; binary files will raise an error
- Raises `FileNotFoundError` if the file does not exist

---

### `write_file`

Write content to a file (overwrite or append). Parent directories are created automatically if they do not exist.

**Config example**

```json
{"id": "write_file", "type": "write_file"}
```

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | `string` | Yes | Destination file path |
| `content` | `string` | Yes | Text content to write |
| `mode` | `string` | No | `"w"` (overwrite, default) or `"a"` (append) |

**Return value**

```json
{
  "path": "output/result.txt",
  "bytes_written": 512,
  "mode": "w"
}
```

**Notes**

- Always writes as UTF-8
- Recommended: pair with the `filesystem_aware` tool_executor to restrict writable paths

---

### `list_files`

List files in a directory, with optional glob pattern and recursive traversal.

**Config example**

```json
{"id": "list_files", "type": "list_files"}
```

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | `string` | No | Target directory; default `"."` |
| `pattern` | `string` | No | Glob pattern; default `"*"` (all files) |
| `recursive` | `boolean` | No | Whether to recurse into subdirectories; default `false` |

**Return value**

```json
{
  "path": "src",
  "pattern": "*.py",
  "files": ["main.py", "utils.py"],
  "count": 2
}
```

**Notes**

- Only files are returned; directory entries are excluded
- Recursive mode returns paths relative to `path`; non-recursive mode returns bare filenames

---

### `delete_file`

Delete a single file or an entire directory tree.

**Config example**

```json
{"id": "delete_file", "type": "delete_file"}
```

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | `string` | Yes | Path of the file or directory to delete |

**Return value**

```json
{
  "path": "tmp/cache",
  "type": "directory",
  "deleted": true
}
```

!!! danger "Irreversible operation"
    Directory deletion uses `shutil.rmtree` and cannot be undone. In production, strongly recommend pairing with the `filesystem_aware` tool_executor to whitelist the paths that may be deleted.

---

## Text Processing

### `grep_files`

Search file contents using a Python regex. Pure-Python implementation; no external binaries required. Returns up to 100 matches.

**Config example**

```json
{"id": "grep_files", "type": "grep_files"}
```

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `pattern` | `string` | Yes | Python regular expression |
| `path` | `string` | No | Search path (file or directory); default `"."` |
| `case_sensitive` | `boolean` | No | Whether the match is case-sensitive; default `true` |

**Return value**

```json
{
  "pattern": "def \\w+",
  "matches": [
    {"file": "src/main.py", "line": 5, "content": "def main():"}
  ],
  "total": 1
}
```

**Notes**

- Files that cannot be read (encoding errors, permission denied) are silently skipped
- At most 100 matches are returned; `total` reports the actual count before the cap

---

### `ripgrep`

Fast file search via the `rg` binary. Output format is identical to `grep_files`.

**Config example**

```json
{"id": "ripgrep", "type": "ripgrep"}
```

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `pattern` | `string` | Yes | Regular expression |
| `path` | `string` | No | Search path; default `"."` |
| `case_sensitive` | `boolean` | No | Whether the match is case-sensitive; default `true` |
| `file_type` | `string` | No | File type filter, e.g. `"py"`, `"js"`, `"md"` |

**Return value**

```json
{
  "pattern": "TODO",
  "matches": [
    {"file": "src/main.py", "line": 12, "content": "# TODO: fix this"}
  ],
  "total": 1
}
```

!!! warning "Requires `rg` on PATH"
    If `rg` is not installed, the invocation raises `RuntimeError`. Install via the system package manager or `pip install ripgrep`. Use `grep_files` as a fallback when `rg` is unavailable.

---

### `json_parse`

Parse a JSON string and return the deserialized value plus its Python type name.

**Config example**

```json
{"id": "json_parse", "type": "json_parse"}
```

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `text` | `string` | Yes | JSON string to parse |

**Return value**

```json
{
  "parsed": {"key": "value"},
  "type": "dict"
}
```

---

### `text_transform`

Apply case, capitalization, strip, or reverse transformations to a string.

**Config example**

```json
{"id": "text_transform", "type": "text_transform"}
```

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `text` | `string` | Yes | Input text |
| `operation` | `string` | No | Transformation to apply; default `"lower"` |

**Available operations**

| `operation` | Description |
|-------------|-------------|
| `upper` | Convert to uppercase |
| `lower` | Convert to lowercase |
| `title` | Title-case each word |
| `capitalize` | Capitalize first character of the string |
| `strip` | Strip leading and trailing whitespace |
| `reverse` | Reverse the string |

**Return value**

```json
{
  "original": "Hello World",
  "operation": "upper",
  "result": "HELLO WORLD"
}
```

---

## HTTP / Network

### `http_request`

Make HTTP requests via `urllib.request`, run on a worker thread to avoid blocking the event loop. Supports GET, POST, PUT, DELETE, PATCH.

**Config example**

```json
{
  "id": "http",
  "type": "http_request",
  "config": {"timeout": 30}
}
```

**Tool-level config (in `config` key)**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `timeout` | `integer` | `30` | Default timeout in seconds for this tool instance |

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url` | `string` | Yes | Request URL |
| `method` | `string` | No | HTTP method; default `"GET"` |
| `headers` | `object` | No | Request headers as key-value pairs |
| `body` | `any` | No | Request body; dicts are JSON-serialized and `Content-Type: application/json` is set automatically |
| `timeout` | `integer` | No | Per-call timeout in seconds; overrides the tool-level default |

**Return value (success)**

```json
{
  "url": "https://api.example.com/data",
  "method": "GET",
  "status": 200,
  "headers": {"Content-Type": "application/json"},
  "body": "{\"result\": \"ok\"}",
  "success": true
}
```

**Return value (failure)**

```json
{
  "url": "https://api.example.com/data",
  "method": "GET",
  "error": "Connection refused",
  "success": false
}
```

**Notes**

- No automatic retry on 5xx errors; use the `retry` tool_executor for retries
- Recommended: pair with a `network_allowlist` execution policy in production to restrict accessible domains

---

### `url_parse`

Parse a URL into its constituent components.

**Config example**

```json
{"id": "url_parse", "type": "url_parse"}
```

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url` | `string` | Yes | URL to parse |

**Return value**

```json
{
  "scheme": "https",
  "netloc": "api.example.com:8080",
  "hostname": "api.example.com",
  "port": 8080,
  "path": "/v1/data",
  "params": "",
  "query": "key=value",
  "fragment": "",
  "username": null,
  "password": null
}
```

---

### `url_build`

Assemble a URL from its components.

**Config example**

```json
{"id": "url_build", "type": "url_build"}
```

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `host` | `string` | Yes | Hostname, e.g. `api.example.com` |
| `scheme` | `string` | No | Protocol; default `"https"` |
| `path` | `string` | No | Path component; default `"/"` |
| `query` | `string` | No | Query string (without leading `?`) |
| `fragment` | `string` | No | Fragment (without leading `#`) |

**Return value**

```json
{"url": "https://api.example.com/v1/data?key=value"}
```

**Notes**

- Performs simple string concatenation; special characters in `path` or `query` are not URL-encoded

---

### `query_param`

Extract or list query parameters from a URL.

**Config example**

```json
{"id": "qparam", "type": "query_param"}
```

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url` | `string` | Yes | Full URL |
| `action` | `string` | No | `"get"` (retrieve one parameter, default) or `"list"` (all parameters) |
| `key` | `string` | Conditional | Parameter name; required when `action="get"` |

**Return value (`action="get"`)**

```json
{"key": "page", "value": "2"}
```

**Return value (`action="list"`)**

```json
{"params": {"page": "2", "size": "10"}}
```

---

### `host_lookup`

Extract host information from a URL: hostname, port, HTTPS flag, and TLD.

**Config example**

```json
{"id": "host_lookup", "type": "host_lookup"}
```

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url` | `string` | Yes | Full URL |

**Return value**

```json
{
  "host": "api.example.com",
  "port": null,
  "has_https": true,
  "domain": "com"
}
```

---

## System Operations

### `execute_command`

Run a shell command in a subprocess; captures stdout, stderr, and return code.

**Config example**

```json
{
  "id": "exec",
  "type": "execute_command",
  "config": {"timeout": 30}
}
```

**Tool-level config (in `config` key)**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `timeout` | `integer` | `30` | Default timeout in seconds |

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `command` | `string` | Yes | Shell command string |
| `timeout` | `integer` | No | Per-call timeout in seconds; overrides the tool-level default |

**Return value**

```json
{
  "command": "ls -la",
  "stdout": "total 48\n...",
  "stderr": "",
  "returncode": 0,
  "success": true
}
```

!!! danger "Security warning"
    `execute_command` executes arbitrary shell commands in a subprocess. In production you **must**:
    1. Pair with the `filesystem_aware` tool_executor to restrict accessible paths, or
    2. Implement a custom `execution_policy` that validates command content against an allowlist.
    3. Never concatenate unsanitized user input into the `command` string.

---

### `get_env`

Read a process environment variable.

**Config example**

```json
{"id": "get_env", "type": "get_env"}
```

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `key` | `string` | Yes | Environment variable name |
| `default` | `any` | No | Value to return if the variable is not set; default `null` |

**Return value**

```json
{
  "key": "PATH",
  "value": "/usr/bin:/bin",
  "exists": true
}
```

---

### `set_env`

Set an environment variable in the current process (does not propagate to child processes).

**Config example**

```json
{"id": "set_env", "type": "set_env"}
```

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `key` | `string` | Yes | Environment variable name |
| `value` | `string` | Yes | Variable value (non-strings are coerced to str) |

**Return value**

```json
{"key": "MY_FLAG", "value": "1", "set": true}
```

---

## Date & Time

### `current_time`

Get the current wall-clock time with timezone support. Returns ISO 8601, Unix timestamp, and formatted string.

**Config example**

```json
{"id": "now", "type": "current_time"}
```

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `timezone` | `string` | No | IANA timezone name; default `"UTC"` |

**Return value**

```json
{
  "iso": "2026-04-18T12:30:00+00:00",
  "timestamp": 1745000000.0,
  "formatted": "2026-04-18 12:30:00",
  "timezone": "UTC"
}
```

**Notes**

- Non-UTC timezones require `pytz`; if `pytz` is not installed, the tool silently falls back to UTC
- Timezone names follow IANA format, e.g. `"Asia/Shanghai"`, `"America/New_York"`

---

### `date_parse`

Parse a date string into a structured representation. Automatically tries several common formats.

**Config example**

```json
{"id": "date_parse", "type": "date_parse"}
```

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `date` | `string` | Yes | Date string to parse |

**Supported input formats**

| Format | Example |
|--------|---------|
| `%Y-%m-%d` | `2026-04-18` |
| `%Y-%m-%d %H:%M:%S` | `2026-04-18 12:30:00` |
| `%Y/%m/%d` | `2026/04/18` |
| `%d/%m/%Y` | `18/04/2026` |
| `%m/%d/%Y` | `04/18/2026` |
| `%B %d, %Y` | `April 18, 2026` |
| `%b %d, %Y` | `Apr 18, 2026` |

**Return value**

```json
{
  "parsed": "2026-04-18T00:00:00",
  "timestamp": 1745020800.0,
  "year": 2026,
  "month": 4,
  "day": 18,
  "weekday": "Saturday"
}
```

---

### `date_diff`

Calculate the absolute difference between two dates.

**Config example**

```json
{"id": "date_diff", "type": "date_diff"}
```

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `date1` | `string` | Yes | First date (same formats as `date_parse`) |
| `date2` | `string` | Yes | Second date |
| `unit` | `string` | No | Result unit: `"days"` (default), `"hours"`, `"minutes"`, `"seconds"` |

**Return value**

```json
{
  "seconds": 86400.0,
  "result": 1
}
```

**Notes**

- Supported date formats: `%Y-%m-%d`, `%Y-%m-%d %H:%M:%S`, `%Y/%m/%d`
- The result is always an absolute (non-negative) value

---

## Random

### `random_int`

Generate one or more random integers in a range (inclusive on both ends).

**Config example**

```json
{"id": "rand_int", "type": "random_int"}
```

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `min` | `integer` | No | Minimum value (inclusive); default `0` |
| `max` | `integer` | No | Maximum value (inclusive); default `100` |
| `count` | `integer` | No | Number of integers to generate (1–100); default `1` |

**Return value (`count=1`)**

```json
{"value": 42}
```

**Return value (`count>1`)**

```json
{"values": [7, 23, 58]}
```

---

### `random_choice`

Pick one or more elements from a list without replacement.

**Config example**

```json
{"id": "rand_choice", "type": "random_choice"}
```

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `choices` | `array` | Yes | List of candidate elements (must not be empty) |
| `count` | `integer` | No | Number of elements to pick; default `1`, cannot exceed list length |

**Return value (`count=1`)**

```json
{"value": "apple"}
```

**Return value (`count>1`)**

```json
{"values": ["banana", "cherry"]}
```

---

### `random_string`

Generate a random string from a named character set.

**Config example**

```json
{"id": "rand_str", "type": "random_string"}
```

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `length` | `integer` | No | String length (1–1000); default `16` |
| `charset` | `string` | No | Character set name; default `"alphanumeric"` |

**Available character sets**

| Value | Characters included |
|-------|---------------------|
| `alphanumeric` | `a-zA-Z0-9` |
| `alpha` | `a-zA-Z` |
| `numeric` | `0-9` |
| `hex` | `0-9a-f` |
| `ascii` | `a-zA-Z0-9!@#$%^&*` |

**Return value**

```json
{"value": "aB3kLmP9xQ2rNtY7", "length": 16}
```

---

### `uuid`

Generate one or more UUIDs. Supports v1 (time-based) and v4 (random).

**Config example**

```json
{"id": "uuid", "type": "uuid"}
```

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `version` | `integer` | No | UUID version: `4` (random, default) or `1` (time-based) |
| `count` | `integer` | No | Number of UUIDs to generate (1–100); default `1` |

**Return value (`count=1`)**

```json
{"uuid": "550e8400-e29b-41d4-a716-446655440000"}
```

**Return value (`count>1`)**

```json
{"uuids": ["550e8400-...", "6ba7b810-..."]}
```

---

## Math

### `calc`

Safely evaluate an arithmetic expression. Uses `ast.parse` with a whitelist of operators (`+`, `-`, `*`, `/`, `**`, `%`). No function calls or name resolution are permitted.

**Config example**

```json
{"id": "calc", "type": "calc"}
```

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `expression` | `string` | Yes | Arithmetic expression, e.g. `"2 + 3 * 4"` |

**Return value**

```json
{"expression": "2 + 3 * 4", "result": 14}
```

**Notes**

- Only the characters `0-9 . + - * / % ( ) **` are allowed; any other character causes an immediate rejection

---

### `percentage`

Compute percentage-based calculations: "X% of value", "value increased by X%", or "value decreased by X%".

**Config example**

```json
{"id": "percent", "type": "percentage"}
```

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `value` | `number` | Yes | Base number |
| `percent` | `number` | Yes | Percentage value (e.g. `15` means 15%) |
| `operation` | `string` | No | Calculation type; default `"of"` |

**Available operations**

| `operation` | Formula | Example (value=200, percent=15) |
|-------------|---------|----------------------------------|
| `of` | `value × percent / 100` | `30.0` |
| `increase` | `value × (1 + percent/100)` | `230.0` |
| `decrease` | `value × (1 - percent/100)` | `170.0` |

**Return value**

```json
{
  "value": 200.0,
  "percent": 15.0,
  "operation": "of",
  "result": 30.0
}
```

---

### `min_max`

Compute aggregate statistics over a list of numbers: min, max, sum, average, or median.

**Config example**

```json
{"id": "minmax", "type": "min_max"}
```

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `numbers` | `array` \| `string` | Yes | List of numbers, or a comma-separated string of numbers |
| `action` | `string` | No | Operation to perform; default `"min"` |

**Available operations**

| `action` | Description |
|----------|-------------|
| `min` | Minimum value |
| `max` | Maximum value |
| `sum` | Sum of all values |
| `avg` | Arithmetic mean |
| `median` | Median value |

**Return value**

```json
{
  "action": "avg",
  "numbers": [1.0, 2.0, 3.0, 4.0, 5.0],
  "result": 3.0
}
```

---

## MCP Bridge

### `mcp`

Forward tool calls to an external MCP (Model Context Protocol) server. Supports both stdio (local subprocess) and HTTP/SSE (remote server) connection modes.

!!! note "Requires the mcp extra"
    ```bash
    uv sync --extra mcp
    # or
    pip install "io-openagent-sdk[mcp]"
    ```

**Config example (stdio, local server)**

```json
{
  "id": "mcp_fs",
  "type": "mcp",
  "config": {
    "server": {
      "command": "python",
      "args": ["mcp_server.py"],
      "env": {"MY_VAR": "value"}
    },
    "tools": ["read_file", "write_file"]
  }
}
```

**Config example (HTTP/SSE, remote server)**

```json
{
  "id": "mcp_remote",
  "type": "mcp",
  "config": {
    "server": {
      "url": "https://mcp.example.com/sse",
      "headers": {"Authorization": "Bearer <token>"}
    },
    "tools": []
  }
}
```

**Tool-level config fields (inside `config`)**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `server.command` | `string` | Conditional | stdio mode: executable command to launch the server |
| `server.args` | `array[string]` | No | stdio mode: command arguments |
| `server.env` | `object` | No | stdio mode: additional environment variables |
| `server.url` | `string` | Conditional | HTTP/SSE mode: server endpoint URL |
| `server.headers` | `object` | No | HTTP/SSE mode: request headers |
| `tools` | `array[string]` | No | Allowlist of tool names to expose; empty list means all tools |

**Invoke parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `tool` | `string` | Yes | Name of the tool on the MCP server |
| `arguments` | `object` | No | Arguments to pass to the MCP tool |

**Return value**

```json
{
  "content": ["file content here"],
  "isError": false
}
```

**Notes**

- The connection is established on the first invocation and reused for subsequent calls
- Invoking a tool not in the `tools` allowlist raises `ValueError`
- The MCP connection is not automatically re-established after a server disconnect; re-initialize the tool instance to reconnect

---

## Complete Configuration Example

A representative `agent.json` using several builtin tools together:

```json
{
  "agents": [
    {
      "id": "coding-agent",
      "model": "claude-sonnet-4-6",
      "pattern": {"type": "react"},
      "tools": [
        {"id": "read_file",  "type": "read_file"},
        {"id": "write_file", "type": "write_file"},
        {"id": "list_files", "type": "list_files"},
        {
          "id": "exec",
          "type": "execute_command",
          "config": {"timeout": 60}
        },
        {
          "id": "http",
          "type": "http_request",
          "config": {"timeout": 30}
        },
        {"id": "calc",  "type": "calc"},
        {"id": "now",   "type": "current_time"},
        {"id": "uuid",  "type": "uuid"}
      ],
      "tool_executor": {
        "type": "filesystem_aware",
        "config": {
          "allowed_read_paths": ["./src", "./docs"],
          "allowed_write_paths": ["./output"]
        }
      }
    }
  ]
}
```

---

## Important Notes

- **`execute_command`**: Executes arbitrary shell commands in a subprocess. In production, always pair with the `filesystem_aware` tool_executor or a custom `execution_policy` that restricts what commands can run.
- **`http_request`**: Does not automatically retry on 5xx errors. Configure the `retry` tool_executor for retry behaviour (see [Configuration Reference](configuration.md)).
- **`mcp`**: Requires the `[mcp]` extra: `uv sync --extra mcp`. An empty `tools` list exposes all tools from the server — specify an explicit allowlist in production.
- **`ripgrep`**: Requires the `rg` binary on `PATH`. If unavailable, the call raises `RuntimeError`; use `grep_files` as a fallback.
- **`write_file` / `delete_file`**: Filesystem write operations are irreversible. Use in a sandboxed environment or with restricted paths.

---

## New builtins (0.4.0)

### `shell_exec`

Allowlist-aware shell command execution: `asyncio.create_subprocess_exec` + timeout + argv[0] allowlist.

Config:
- `cwd`: working directory
- `env_passthrough`: allowlist of env var names inherited from parent
- `command_allowlist`: allowlist of argv[0] values (`None` = unrestricted)
- `default_timeout_ms`: default timeout (milliseconds)
- `capture_bytes`: max bytes captured for stdout/stderr

Invoke: `{"command": str | list[str], "cwd"?, "timeout_ms"?, "env"?}` → `{"exit_code", "stdout", "stderr", "timed_out", "truncated"}`.

### `tavily_search`

Tavily REST search tool (fallback for Tavily MCP). API key read from `TAVILY_API_KEY`.

Invoke: `{"query": str, "max_results"?, "search_depth"?, "include_domains"?, "exclude_domains"?}` → `{"query", "results", "search_depth"}`.

### `remember_preference`

Companion to `markdown_memory`: queues `{category, rule, reason}` into `context.state['_pending_memory_writes']`, which `markdown_memory.writeback` drains to disk.

---

## Related Documentation

- [Configuration Reference](configuration.md) — Full JSON schema for `tool` and `tool_executor`
- [Plugin Development Guide](plugin-development.md) — Writing a custom tool plugin
- [Seams & Extension Points](seams-and-extension-points.md) — Decision tree for the `tool` seam
