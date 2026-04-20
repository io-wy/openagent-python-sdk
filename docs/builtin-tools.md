# 内置工具参考

本页列出 OpenAgents SDK 0.3.x 提供的所有内置工具插件。所有工具的 `type` 键均可直接在 `agent.json` 的 `tools` 数组中引用，无需额外安装（MCP 工具除外）。

如需了解工具系统的整体架构，参见[插件开发指南](plugin-development.md)。如需配置 `tool_executor`（权限控制、重试策略），参见[配置参考](configuration.md)。

---

## 搜索

### `builtin_search`

搜索内置知识库文档片段。这是一个演示用的桩工具，使用硬编码的小型文档语料库按关键词得分排名，适合示例和测试，无需外部依赖。

**配置示例**

```json
{"id": "search", "type": "builtin_search"}
```

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | `string` | 是 | 搜索关键词，以空格分词后按词频打分 |
| `limit` | `integer` | 否 | 最多返回的结果数，默认 `3` |

**返回值**

```json
{
  "query": "memory",
  "items": [
    {"title": "Agent Memory Design", "snippet": "..."}
  ]
}
```

---

## 文件操作

### `read_file`

读取文件内容，以 UTF-8 编码返回全部文本。

**配置示例**

```json
{"id": "read_file", "type": "read_file"}
```

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `path` | `string` | 是 | 要读取的文件路径（绝对路径或相对路径均可） |

**返回值**

```json
{
  "path": "src/main.py",
  "content": "...",
  "size": 1024
}
```

**注意事项**

- 始终以 UTF-8 编码读取，二进制文件会报错
- 文件不存在时抛出 `FileNotFoundError`

---

### `write_file`

向文件写入内容（覆盖或追加），若父目录不存在会自动创建。

**配置示例**

```json
{"id": "write_file", "type": "write_file"}
```

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `path` | `string` | 是 | 目标文件路径 |
| `content` | `string` | 是 | 要写入的文本内容 |
| `mode` | `string` | 否 | `"w"`（覆盖，默认）或 `"a"`（追加） |

**返回值**

```json
{
  "path": "output/result.txt",
  "bytes_written": 512,
  "mode": "w"
}
```

**注意事项**

- 始终以 UTF-8 编码写入
- 建议与 `filesystem_aware` tool_executor 配合使用以限制可写路径

---

### `list_files`

列出目录下的文件，支持 glob 模式和递归遍历。

**配置示例**

```json
{"id": "list_files", "type": "list_files"}
```

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `path` | `string` | 否 | 目标目录，默认 `"."` |
| `pattern` | `string` | 否 | Glob 匹配模式，默认 `"*"`（匹配所有文件） |
| `recursive` | `boolean` | 否 | 是否递归遍历子目录，默认 `false` |

**返回值**

```json
{
  "path": "src",
  "pattern": "*.py",
  "files": ["main.py", "utils.py"],
  "count": 2
}
```

**注意事项**

- 只返回文件，不返回目录条目
- 递归模式返回相对于 `path` 的路径；非递归模式仅返回文件名

---

### `delete_file`

删除文件或整个目录树。

**配置示例**

```json
{"id": "delete_file", "type": "delete_file"}
```

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `path` | `string` | 是 | 要删除的文件或目录路径 |

**返回值**

```json
{
  "path": "tmp/cache",
  "type": "directory",
  "deleted": true
}
```

!!! danger "不可逆操作"
    删除目录时使用 `shutil.rmtree`，操作不可撤销。在生产环境中强烈建议配合 `filesystem_aware` tool_executor 设置允许删除的路径白名单。

---

## 文本处理

### `grep_files`

用正则表达式搜索文件内容，纯 Python 实现，无需外部依赖。返回前 100 条匹配。

**配置示例**

```json
{"id": "grep_files", "type": "grep_files"}
```

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `pattern` | `string` | 是 | Python 正则表达式 |
| `path` | `string` | 否 | 搜索路径（文件或目录），默认 `"."` |
| `case_sensitive` | `boolean` | 否 | 是否区分大小写，默认 `true` |

**返回值**

```json
{
  "pattern": "def \\w+",
  "matches": [
    {"file": "src/main.py", "line": 5, "content": "def main():"}
  ],
  "total": 1
}
```

**注意事项**

- 无法读取的文件（编码错误、权限不足）会被静默跳过
- 最多返回 100 条匹配；`total` 字段反映实际匹配总数

---

### `ripgrep`

调用 `rg` 二进制进行高速文件搜索，输出格式与 `grep_files` 兼容。

**配置示例**

```json
{"id": "ripgrep", "type": "ripgrep"}
```

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `pattern` | `string` | 是 | 正则表达式 |
| `path` | `string` | 否 | 搜索路径，默认 `"."` |
| `case_sensitive` | `boolean` | 否 | 是否区分大小写，默认 `true` |
| `file_type` | `string` | 否 | 文件类型过滤，如 `"py"`、`"js"`、`"md"` |

**返回值**

```json
{
  "pattern": "TODO",
  "matches": [
    {"file": "src/main.py", "line": 12, "content": "# TODO: fix this"}
  ],
  "total": 1
}
```

!!! warning "需要 rg 已安装"
    若 `rg` 不在 `PATH` 中，调用时会抛出 `RuntimeError`。可用 `ripgrep` 包安装：`pip install ripgrep` 或系统包管理器安装。

---

### `json_parse`

将 JSON 字符串解析为 Python 对象，返回解析结果和类型名。

**配置示例**

```json
{"id": "json_parse", "type": "json_parse"}
```

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `text` | `string` | 是 | 待解析的 JSON 字符串 |

**返回值**

```json
{
  "parsed": {"key": "value"},
  "type": "dict"
}
```

---

### `text_transform`

对字符串进行大小写、首字母大写、去空格、反转等变换。

**配置示例**

```json
{"id": "text_transform", "type": "text_transform"}
```

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `text` | `string` | 是 | 输入文本 |
| `operation` | `string` | 否 | 变换操作，默认 `"lower"` |

**可用操作**

| `operation` 值 | 说明 |
|--------------|------|
| `upper` | 全部转大写 |
| `lower` | 全部转小写 |
| `title` | 每个单词首字母大写 |
| `capitalize` | 句子首字母大写 |
| `strip` | 去除首尾空白字符 |
| `reverse` | 字符串反转 |

**返回值**

```json
{
  "original": "Hello World",
  "operation": "upper",
  "result": "HELLO WORLD"
}
```

---

## HTTP / 网络

### `http_request`

通过 `urllib.request` 发起 HTTP 请求，在工作线程中执行以避免阻塞事件循环。支持 GET、POST、PUT、DELETE、PATCH 方法。

**配置示例**

```json
{
  "id": "http",
  "type": "http_request",
  "config": {"timeout": 30}
}
```

**配置参数**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `timeout` | `integer` | `30` | 工具级别的默认超时秒数 |

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `url` | `string` | 是 | 请求 URL |
| `method` | `string` | 否 | HTTP 方法，默认 `"GET"` |
| `headers` | `object` | 否 | 请求头键值对 |
| `body` | `any` | 否 | 请求体；传入 dict 时自动序列化为 JSON 并设置 `Content-Type: application/json` |
| `timeout` | `integer` | 否 | 调用级别超时秒数，覆盖工具配置 |

**返回值（成功）**

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

**返回值（失败）**

```json
{
  "url": "https://api.example.com/data",
  "method": "GET",
  "error": "Connection refused",
  "success": false
}
```

**注意事项**

- 不会对 5xx 错误自动重试；如需重试，使用 `retry` tool_executor
- 建议在生产环境中配合 `network_allowlist` execution policy 限制可访问域名

---

### `url_parse`

将 URL 解析为各组成部分。

**配置示例**

```json
{"id": "url_parse", "type": "url_parse"}
```

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `url` | `string` | 是 | 待解析的 URL |

**返回值**

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

从各组件拼接 URL。

**配置示例**

```json
{"id": "url_build", "type": "url_build"}
```

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `host` | `string` | 是 | 主机名，如 `api.example.com` |
| `scheme` | `string` | 否 | 协议，默认 `"https"` |
| `path` | `string` | 否 | 路径，默认 `"/"` |
| `query` | `string` | 否 | 查询字符串（不含 `?`） |
| `fragment` | `string` | 否 | 片段（不含 `#`） |

**返回值**

```json
{"url": "https://api.example.com/v1/data?key=value"}
```

**注意事项**

- 此工具进行简单字符串拼接，不会对 path 或 query 中的特殊字符进行 URL 编码

---

### `query_param`

从 URL 中提取或列出查询参数。

**配置示例**

```json
{"id": "qparam", "type": "query_param"}
```

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `url` | `string` | 是 | 完整 URL |
| `action` | `string` | 否 | `"get"`（获取单个参数，默认）或 `"list"`（列出全部参数） |
| `key` | `string` | 条件必填 | `action="get"` 时必填，参数名 |

**返回值（action="get"）**

```json
{"key": "page", "value": "2"}
```

**返回值（action="list"）**

```json
{"params": {"page": "2", "size": "10"}}
```

---

### `host_lookup`

从 URL 中提取主机信息，包括主机名、端口、是否使用 HTTPS 及顶级域名。

**配置示例**

```json
{"id": "host_lookup", "type": "host_lookup"}
```

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `url` | `string` | 是 | 完整 URL |

**返回值**

```json
{
  "host": "api.example.com",
  "port": null,
  "has_https": true,
  "domain": "com"
}
```

---

## 系统操作

### `execute_command`

在子进程的 shell 中执行命令，捕获 stdout、stderr 和返回码。

**配置示例**

```json
{
  "id": "exec",
  "type": "execute_command",
  "config": {"timeout": 30}
}
```

**配置参数**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `timeout` | `integer` | `30` | 工具级别默认超时秒数 |

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `command` | `string` | 是 | Shell 命令字符串 |
| `timeout` | `integer` | 否 | 调用级别超时秒数，覆盖工具配置 |

**返回值**

```json
{
  "command": "ls -la",
  "stdout": "total 48\n...",
  "stderr": "",
  "returncode": 0,
  "success": true
}
```

!!! danger "安全警告"
    `execute_command` 在 shell 子进程中执行任意命令。**强烈建议**在生产环境中：
    1. 配合 `filesystem_aware` tool_executor 限制可访问路径
    2. 或实现自定义 `execution_policy` 对命令内容进行白名单校验
    3. 绝不在此工具中直接拼接来自用户输入的字符串

---

### `get_env`

读取进程环境变量。

**配置示例**

```json
{"id": "get_env", "type": "get_env"}
```

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `key` | `string` | 是 | 环境变量名 |
| `default` | `any` | 否 | 变量不存在时的默认值，默认 `null` |

**返回值**

```json
{
  "key": "PATH",
  "value": "/usr/bin:/bin",
  "exists": true
}
```

---

### `set_env`

在当前进程中设置环境变量（仅影响当前 Python 进程，不传播到子进程）。

**配置示例**

```json
{"id": "set_env", "type": "set_env"}
```

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `key` | `string` | 是 | 环境变量名 |
| `value` | `string` | 是 | 变量值（非字符串会转为字符串） |

**返回值**

```json
{"key": "MY_FLAG", "value": "1", "set": true}
```

---

## 日期时间

### `current_time`

获取当前时间，支持时区（需要 `pytz`），返回 ISO 8601、Unix 时间戳和格式化字符串。

**配置示例**

```json
{"id": "now", "type": "current_time"}
```

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `timezone` | `string` | 否 | 时区名称（IANA 格式），默认 `"UTC"` |

**返回值**

```json
{
  "iso": "2026-04-18T12:30:00+00:00",
  "timestamp": 1745000000.0,
  "formatted": "2026-04-18 12:30:00",
  "timezone": "UTC"
}
```

**注意事项**

- 非 UTC 时区需要安装 `pytz`；若 `pytz` 未安装，自动回退到 UTC
- 时区名称格式为 IANA 标准，如 `"Asia/Shanghai"`、`"America/New_York"`

---

### `date_parse`

将日期字符串解析为结构化表示，自动尝试多种常见格式。

**配置示例**

```json
{"id": "date_parse", "type": "date_parse"}
```

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `date` | `string` | 是 | 日期字符串 |

**支持的输入格式**

| 格式 | 示例 |
|------|------|
| `%Y-%m-%d` | `2026-04-18` |
| `%Y-%m-%d %H:%M:%S` | `2026-04-18 12:30:00` |
| `%Y/%m/%d` | `2026/04/18` |
| `%d/%m/%Y` | `18/04/2026` |
| `%m/%d/%Y` | `04/18/2026` |
| `%B %d, %Y` | `April 18, 2026` |
| `%b %d, %Y` | `Apr 18, 2026` |

**返回值**

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

计算两个日期之间的绝对差值。

**配置示例**

```json
{"id": "date_diff", "type": "date_diff"}
```

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `date1` | `string` | 是 | 第一个日期（格式同 `date_parse`） |
| `date2` | `string` | 是 | 第二个日期 |
| `unit` | `string` | 否 | 结果单位：`"days"`（默认）、`"hours"`、`"minutes"`、`"seconds"` |

**返回值**

```json
{
  "seconds": 86400.0,
  "result": 1
}
```

**注意事项**

- 支持的日期格式：`%Y-%m-%d`、`%Y-%m-%d %H:%M:%S`、`%Y/%m/%d`
- 结果始终为绝对值（非负数）

---

## 随机

### `random_int`

生成随机整数，支持批量生成。

**配置示例**

```json
{"id": "rand_int", "type": "random_int"}
```

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `min` | `integer` | 否 | 最小值（含），默认 `0` |
| `max` | `integer` | 否 | 最大值（含），默认 `100` |
| `count` | `integer` | 否 | 生成数量（1–100），默认 `1` |

**返回值（count=1）**

```json
{"value": 42}
```

**返回值（count>1）**

```json
{"values": [7, 23, 58]}
```

---

### `random_choice`

从列表中随机抽取元素，无放回抽样。

**配置示例**

```json
{"id": "rand_choice", "type": "random_choice"}
```

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `choices` | `array` | 是 | 候选元素列表，不可为空 |
| `count` | `integer` | 否 | 抽取数量，默认 `1`，不可超过列表长度 |

**返回值（count=1）**

```json
{"value": "apple"}
```

**返回值（count>1）**

```json
{"values": ["banana", "cherry"]}
```

---

### `random_string`

按指定字符集生成随机字符串。

**配置示例**

```json
{"id": "rand_str", "type": "random_string"}
```

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `length` | `integer` | 否 | 字符串长度（1–1000），默认 `16` |
| `charset` | `string` | 否 | 字符集名称，默认 `"alphanumeric"` |

**可用字符集**

| 值 | 包含字符 |
|----|---------|
| `alphanumeric` | `a-zA-Z0-9` |
| `alpha` | `a-zA-Z` |
| `numeric` | `0-9` |
| `hex` | `0-9a-f` |
| `ascii` | `a-zA-Z0-9!@#$%^&*` |

**返回值**

```json
{"value": "aB3kLmP9xQ2rNtY7", "length": 16}
```

---

### `uuid`

生成 UUID，支持 v1（基于时间）和 v4（随机）。

**配置示例**

```json
{"id": "uuid", "type": "uuid"}
```

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `version` | `integer` | 否 | UUID 版本：`4`（随机，默认）或 `1`（基于时间） |
| `count` | `integer` | 否 | 生成数量（1–100），默认 `1` |

**返回值（count=1）**

```json
{"uuid": "550e8400-e29b-41d4-a716-446655440000"}
```

**返回值（count>1）**

```json
{"uuids": ["550e8400-...", "6ba7b810-..."]}
```

---

## 数学

### `calc`

安全地计算算术表达式。使用 `ast.parse` 解析后仅允许数字常量和 `+`、`-`、`*`、`/`、`**`、`%` 运算符，不允许函数调用或名称解析。

**配置示例**

```json
{"id": "calc", "type": "calc"}
```

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `expression` | `string` | 是 | 算术表达式，如 `"2 + 3 * 4"` |

**返回值**

```json
{"expression": "2 + 3 * 4", "result": 14}
```

**注意事项**

- 表达式仅允许字符 `0-9 . + - * / % ( ) **`，包含其他字符时会被拒绝

---

### `percentage`

计算百分比相关运算：求百分之多少、增加百分比、减少百分比。

**配置示例**

```json
{"id": "percent", "type": "percentage"}
```

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `value` | `number` | 是 | 基数 |
| `percent` | `number` | 是 | 百分比值（如 `15` 代表 15%） |
| `operation` | `string` | 否 | 运算类型，默认 `"of"` |

**可用运算**

| `operation` 值 | 计算公式 | 示例（value=200, percent=15） |
|--------------|---------|-------------------------------|
| `of` | `value × percent / 100` | `30.0` |
| `increase` | `value × (1 + percent/100)` | `230.0` |
| `decrease` | `value × (1 - percent/100)` | `170.0` |

**返回值**

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

对数字列表执行统计运算：最小值、最大值、求和、平均值、中位数。

**配置示例**

```json
{"id": "minmax", "type": "min_max"}
```

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `numbers` | `array` \| `string` | 是 | 数字列表，或逗号分隔的数字字符串 |
| `action` | `string` | 否 | 运算类型，默认 `"min"` |

**可用运算**

| `action` 值 | 说明 |
|------------|------|
| `min` | 最小值 |
| `max` | 最大值 |
| `sum` | 求和 |
| `avg` | 平均值 |
| `median` | 中位数 |

**返回值**

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

将调用转发到外部 MCP（Model Context Protocol）服务器，支持 stdio 本地子进程和 HTTP/SSE 远程服务器两种连接方式。

!!! note "需要 mcp extra"
    ```bash
    uv sync --extra mcp
    # 或者
    pip install "io-openagent-sdk[mcp]"
    ```

**配置示例（stdio 本地服务器）**

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

**配置示例（HTTP/SSE 远程服务器）**

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

**工具级配置参数（config 字段）**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `server.command` | `string` | 条件必填 | stdio 模式：启动服务器的可执行命令 |
| `server.args` | `array[string]` | 否 | stdio 模式：命令参数 |
| `server.env` | `object` | 否 | stdio 模式：附加环境变量。若 `env_passthrough` 为空（默认），此字段含义与底层 MCP SDK 一致——提供时**替换**父环境变量；不提供时继承父环境变量 |
| `server.cwd` | `string` | 否 | stdio 模式：子进程工作目录，直接透传给 `StdioServerParameters(cwd=...)` |
| `server.env_passthrough` | `array[string]` | 否 | stdio 模式：父进程环境变量白名单（如 `["PATH","HOME"]`）。非空时会把命中名字的父变量合入子进程 env；`server.env` 的显式键优先级更高（覆盖 passthrough）。默认空列表 = 保持历史行为 |
| `server.init_timeout_ms` | `int` | 否 | `ClientSession.initialize()` 的超时（毫秒）。超时抛 `TimeoutError`，连接与会话照常回滚；默认 `None` = 不限 |
| `server.url` | `string` | 条件必填 | HTTP/SSE 模式：服务器端点 URL |
| `server.headers` | `object` | 否 | HTTP/SSE 模式：请求头 |
| `tools` | `array[string]` | 否 | 暴露的工具名白名单，空列表表示暴露服务器上的全部工具 |
| `connection_mode` | `string` | 否 | `per_call`（默认）或 `pooled`，控制会话生命周期，详见下方 |
| `prelaunch` | `string` | 否 | `off`（默认）或 `eager`。`eager` 必须配合 `connection_mode=pooled`——会在 session 首次 run 时就打开共享连接，第一次 `invoke` 零进程启动延迟；误配到 `per_call` 将在初始化时抛 `ConfigError` |
| `probe_on_preflight` | `bool` | 否 | 默认 `false`；开启后 `preflight()` 会尝试连接服务器并调用一次 `list_tools` |
| `dedup_inflight` | `bool` | 否 | 默认 `true`；在 `per_call` 模式下合并同 `(tool, arguments)` 的并发调用 |

!!! tip "环境变量插值（`${VAR}`）"
    `server.env`、`server.headers`、`server.url`、`server.args` 里的 `${VAR}` / `${VAR:-default}` 占位符由 config loader 在文件文本层统一展开，缺失变量会在 `load_config` 阶段抛 `ConfigLoadError`。注意：**仅通过 `Runtime.from_config(path)` 加载才有插值**；直接 `Runtime.from_dict(payload)` 不做文本展开。

**调用参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `tool` | `string` | 是 | MCP 服务器上的工具名 |
| `arguments` | `object` | 否 | 传递给 MCP 工具的参数 |

**返回值**

```json
{
  "content": ["file content here"],
  "isError": false
}
```

**连接模式：per_call vs pooled**

- `per_call`（默认）：每次 `invoke()` 都会打开并关闭一个全新的 stdio 子进程（或 SSE 会话）。好处是 anyio 的 cancel scope 始终绑定在单次调用内，子进程崩溃不会连带取消调用方后续的 `await`。代价是重量级服务器（如 node-based tavily-mcp）每次工具调用都要付一次进程启动成本。
- `pooled`：首次调用时打开一个长生命周期的会话，后续所有调用复用它，通过内部 `asyncio.Lock` 串行化。N 次工具调用只开一次进程。**代价**：若池内子进程异常退出，残留的 cancel scope 可能泄漏到调用方。我们通过"死会话在下一次调用时才替换（而不是在失败的那次调用里）"来缓解这种泄漏。`DefaultRuntime` 会在 `runtime.close()` / `runtime.release_session()` 时级联关闭共享连接；runtime 被意外 kill 时 `atexit` 钩子兜底。
- 何时开启 `pooled`：ReAct 等循环里会对同一个 MCP 服务器连续发起很多次工具调用时；服务器启动成本很高时。
- 何时保留默认 `per_call`：服务器本身不稳定、经常崩溃；或者你希望每次调用都有干净的 cancel scope 边界。

**Session 级共享池（0.4.x 新增）**

`DefaultRuntime` 会为每个 `session_id` 维护一个共享的 MCP 池，按 `server.identifier()`（stdio 的 `command` 或 HTTP 的 `url`）聚合：

- 同一 session 内、`connection_mode=pooled` 的多个 `McpTool` 若指向同一服务器配置，会复用**同一个**子进程 / SSE 会话，并通过一条 `asyncio.Lock` 串行化（MCP stdio 协议本身是单流）。两个 agent 共用一台服务器不会开两份进程。
- 池与 session 生命周期绑定：**跨 run 常驻**。同一个 `session_id` 上的第 N 次 `runtime.run_detailed` 会直接复用第 1 次建立的连接。
- `prelaunch="eager"` 的工具会在 session 首次 run 的"preflight 之后、pattern 执行之前"触发一次 warmup，把共享连接提前建好；此时第一次 `invoke` 不需要等进程启动。
- 池在以下任一时机统一排空：`runtime.close()`、`runtime.release_session(session_id)`、`runtime.reload()` 检测到 agent 配置变化；非 `DefaultRuntime` 场景或没有把 pool 注入到 context 的情况下，`McpTool._PooledStrategy` 自动回退到旧的每实例池路径（向后兼容）。

可以通过 runtime-level 配置调节池容量：

```json
{
  "runtime": {
    "type": "default",
    "config": {
      "mcp": {
        "max_pooled_sessions": 32,
        "max_idle_seconds": 300,
        "preflight_cache_success_ttl": null
      }
    }
  }
}
```

- `max_pooled_sessions`：同时活跃的 session 池上限；超限时按 LRU（`last_used` 最旧）驱逐并关闭对应共享连接。默认 `None` = 不限。
- `max_idle_seconds`：session 池 idle 超时；任何 `get_or_create` 调用到来时会先清掉超时的 idle 池。默认 `None` = 不限。
- `preflight_cache_success_ttl`：保留字段，当前未启用（成功 preflight 随 session 池生命周期常驻）。

**预启动检查（preflight）**

`McpTool.preflight()` 在每次 `runtime.run_detailed` 进入 agent 循环之前、tool binding 之后被调用，用于尽早暴露配置错误：

1. 检查 `mcp` Python SDK 是否可导入（未安装时抛 `PermanentToolError`，附带 `uv sync --extra mcp` 安装提示）；
2. 校验 `server` 配置：stdio 模式用 `shutil.which` 检查 `command` 是否在 PATH 上；HTTP/SSE 模式用 `urllib.parse` 确认 URL 格式合法；
3. 若 `probe_on_preflight=true`，额外打开一次临时连接调用 `list_tools` 验证服务器可达性（会产生一次额外的子进程 fork，默认关闭）。

**Session 级 dedup**：`DefaultRuntime` 会在 session 池里缓存每个 `tool_id` 的成功 preflight，后续同 session 的 run 会直接命中缓存（事件 payload `result="cached-ok"`）。失败 **不缓存**——用户修好配置后下次 run 自动重试。

preflight 失败会让 `runtime.run()` 直接返回 `stop_reason=failed` 的 `RunResult`，错误消息里会带上失败工具的 `id`，不会进入 agent 循环。

**事件**

MCP 工具在配置了 event bus 的 `RunContext` 下会发射以下结构化事件，payload 仅包含工具 id、服务器标识、耗时和成功与否 —— 永远不会包含参数或工具返回值：

- `tool.mcp.preflight`：preflight 结束（成功或失败）。
- `tool.mcp.connect`：会话打开时。
- `tool.mcp.call`：每次调用完成（含 `success` 和 `duration_ms`）。
- `tool.mcp.close`：池化会话被 `close()` 排空时。

**注意事项**

- `per_call` 模式下，连接随每次调用打开并关闭；`pooled` 模式下首次调用建立并复用；开启 `prelaunch=eager` 后，连接会在 session 首次 run 的 warmup 阶段提前建好。
- 调用 `tools` 白名单之外的工具会抛出 `ValueError`。
- `connection_mode="pooled"` 在 `DefaultRuntime` 下的排空责任已交给 runtime：`runtime.close()` / `runtime.release_session(sid)` / `runtime.reload()` 会自动级联关闭共享会话；`atexit` 和 `McpTool.close()` 是降级路径。
- `env_passthrough` 和 `${VAR}` 插值不会绕过安全策略：插值发生在 config load，运行时看到的已是明文值，请避免把密钥写进 JSON 快照类工件。

---

## 完整配置示例

以下是一个包含多种工具的 `agent.json` 示例：

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

## 注意事项

- **`execute_command`**：在 shell 子进程中执行任意命令。生产环境中必须配合 `filesystem_aware` tool_executor 或自定义 `execution_policy` 限制权限。
- **`http_request`**：不对 5xx 错误自动重试。如需重试，配置 `retry` tool_executor（见[配置参考](configuration.md)）。
- **`mcp`**：需要 `[mcp]` extra，安装命令：`uv sync --extra mcp`。`tools` 字段为空列表时暴露服务器所有工具，建议生产环境明确指定白名单。
- **`ripgrep`**：依赖系统中安装的 `rg` 二进制，若不可用会报错，可用 `grep_files` 作为备选。
- **`write_file` / `delete_file`**：文件系统写操作不可撤销，建议在沙箱或限制路径下使用。

---

## 新增内建（0.4.0）

### `shell_exec`

受限 shell 命令执行工具：`asyncio.create_subprocess_exec` + allowlist + timeout。

配置：
- `cwd`: 工作目录
- `env_passthrough`: 允许从父进程继承的环境变量白名单
- `command_allowlist`: 允许执行的命令 argv[0] 白名单（`None` = 不限）
- `default_timeout_ms`: 默认超时（毫秒）
- `capture_bytes`: stdout/stderr 各自上限

调用：`{"command": str | list[str], "cwd"?, "timeout_ms"?, "env"?}` → `{"exit_code", "stdout", "stderr", "timed_out", "truncated"}`。

### `tavily_search`

Tavily REST 搜索工具（Tavily MCP 的 fallback 路径）。API key 从 `TAVILY_API_KEY` 读取。

调用：`{"query": str, "max_results"?, "search_depth"?, "include_domains"?, "exclude_domains"?}` → `{"query", "results", "search_depth"}`。

### `remember_preference`

与 `markdown_memory` 配套的工具：把 `{category, rule, reason}` 推入 `context.state['_pending_memory_writes']`，由 `markdown_memory.writeback` 持久化。

---

## 相关文档

- [配置参考](configuration.md) — tool 和 tool_executor 的完整 JSON schema
- [插件开发指南](plugin-development.md) — 自定义工具插件开发
- [Seams 与扩展点](seams-and-extension-points.md) — tool seam 的决策树
