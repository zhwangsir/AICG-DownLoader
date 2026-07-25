# MCP / GLM-5.2 兼容性排查清单

> 适用场景：在 Trae IDE、自研 Agent 平台或 MCP Client 中调用 GLM-5.2（EXO 部署）时出现
> `Invalid tool: non-support parallel function call` 或 `argument schema 与 GLM-5.2 不兼容`。
>
> 本排查清单基于 AICG-DownLoader 项目 2026-07-13 的扫描结果：
> 项目自身业务代码中**未使用 MCP / function calling**，问题大概率出在 IDE 配置层或外部系统，
> 因此本清单仅做只读诊断与规避建议，不修改项目业务代码。

---

## 一、先确认报错发生位置

打开 Trae IDE 的开发者工具或相关服务日志，定位完整堆栈：

1. **Trae IDE 输出面板**：查看报错前后是否有 `mcp` / `tool` / `function` 字样。
2. **Trae 开发者工具**：`Help → Toggle Developer Tools → Console/Network`，观察 MCP 请求体。
3. **后端服务日志**：如果报错发生在自己的后端，查看对应日志中的请求参数和响应。
4. **第三方库内部**：如果堆栈里有 `huggingface_hub/inference/_mcp/mcp_client.py`，
   说明是 HuggingFace Hub 的 MCP 客户端在调用模型，与项目业务无关。

区分清楚是以下哪一类：

| 报错位置 | 处理方向 |
|---------|---------|
| Trae IDE 调用 MCP server | 检查 `~/.trae-cn/mcps/` 配置，关闭并行工具调用 |
| 自研后端调用 LLM tools | 在调用处设置 `parallel_tool_calls=False`，简化 schema |
| HuggingFace Hub 内部 | 升级/降级 `huggingface_hub`，或绕过其 MCP 客户端 |

---

## 二、检查 MCP server 配置

本地 MCP server 描述符路径示例：

```text
~/.trae-cn/mcps/s_AICG-DownLoader-main-e55dee2e/solo_agent/<server_name>/
├── SERVER_METADATA.json
└── tools/
    ├── tool_a.json
    └── tool_b.json
```

检查项：

1. `SERVER_METADATA.json` 中的 `mcpVersion` 是否与 Trae 当前版本兼容。
2. `capabilities.tools` 是否声明正确。
3. 每个 `tools/*.json` 的 `arguments` 是否使用标准 JSON Schema draft-07。
   - 已确认本地 descriptor 均为标准格式，形如：
     ```json
     {
       "type": "object",
       "properties": { "msg": { "type": "string" } },
       "required": ["msg"],
       "$schema": "http://json-schema.org/draft-07/schema#"
     }
     ```
4. 如果 schema 包含 `anyOf` / `oneOf` / `additionalProperties` / 嵌套 `$defs`，
   建议先临时移除，改为扁平对象。

---

## 三、关闭并行工具调用（最可能原因）

GLM-5.2 通过 EXO 暴露的 OpenAI 兼容接口**大概率不支持 `parallel_tool_calls`**。

### 3.1 自研后端调用时

显式关闭：

```python
client.chat.completions.create(
    model="mlx-community/GLM-5.2-fp8",
    messages=messages,
    tools=tools,
    tool_choice="auto",          # 不要设为 "required"
    parallel_tool_calls=False,   # 关键：关闭并行调用
)
```

### 3.2 Trae IDE 内部配置

尝试在模型设置中关闭以下选项（不同版本名称可能不同）：

- `Parallel Function Calling`
- `Multi-tool`
- `parallel_tool_calls`

关闭后重启 Trae，重新触发 MCP 调用。

---

## 四、简化工具参数 schema

GLM-5.2 对复杂 schema 的支持可能不完整。按以下原则简化：

1. **只保留基础类型**：`string` / `integer` / `number` / `boolean` / `array`。
2. **避免复杂组合**：移除 `anyOf` / `oneOf` / `allOf` / `not`。
3. **避免动态属性**：移除 `additionalProperties: true`。
4. **避免嵌套对象**：如果必须嵌套，最多一层。
5. **移除默认值**：`default` 可能导致部分解析器出错。
6. **枚举值不要太多**：`enum` 建议不超过 20 项。

简化前（不推荐）：

```json
{
  "type": "object",
  "properties": {
    "filters": {
      "anyOf": [
        { "type": "string" },
        {
          "type": "object",
          "additionalProperties": true
        }
      ]
    }
  }
}
```

简化后（推荐）：

```json
{
  "type": "object",
  "properties": {
    "filter_text": { "type": "string" },
    "filter_json": { "type": "string" }
  },
  "required": ["filter_text"]
}
```

---

## 五、用 curl 逐步验证 EXO 接口能力

直接请求 Mac 集群 EXO 入口，逐步增加字段，定位触发报错的字段。

### 5.1 基础对话（确认服务正常）

```bash
curl http://100.64.201.37:52415/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mlx-community/GLM-5.2-fp8",
    "messages": [{"role":"user","content":"你好"}],
    "max_tokens": 128
  }'
```

### 5.2 加入 tools（不使用 tool_choice）

```bash
curl http://100.64.201.37:52415/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mlx-community/GLM-5.2-fp8",
    "messages": [{"role":"user","content":"调用 echo 工具，msg=hello"}],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "echo",
          "description": "Echo the input message",
          "parameters": {
            "type": "object",
            "properties": {
              "msg": { "type": "string" }
            },
            "required": ["msg"]
          }
        }
      }
    ],
    "max_tokens": 256
  }'
```

### 5.3 加入 tool_choice=auto

```bash
curl http://100.64.201.37:52415/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mlx-community/GLM-5.2-fp8",
    "messages": [{"role":"user","content":"调用 echo 工具，msg=hello"}],
    "tools": [...],
    "tool_choice": "auto",
    "max_tokens": 256
  }'
```

### 5.4 加入 parallel_tool_calls=false

```bash
curl http://100.64.201.37:52415/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mlx-community/GLM-5.2-fp8",
    "messages": [{"role":"user","content":"调用 echo 工具，msg=hello"}],
    "tools": [...],
    "tool_choice": "auto",
    "parallel_tool_calls": false,
    "max_tokens": 256
  }'
```

### 5.5 解读结果

- 如果 5.1 失败：EXO 服务或模型未就绪。
- 如果 5.2 失败：GLM-5.2 不支持 `tools` 字段。
- 如果 5.3 失败：`tool_choice` 字段不兼容。
- 如果 5.4 失败：参数 schema 或其他字段有问题。

---

## 六、临时规避方案

如果确认 GLM-5.2 完全不支持 tools/function calling，可改用以下方案：

### 6.1 纯文本 prompt + 手动 JSON 解析

```python
prompt = """You have access to the following tool:

Name: echo
Description: Echo the input message
Parameters: {"msg": {"type": "string", "required": true}}

If you need to call the tool, respond ONLY with JSON in this exact format:
{"tool": "echo", "arguments": {"msg": "hello"}}

User: please echo "hello"
"""

response = client.chat.completions.create(
    model="mlx-community/GLM-5.2-fp8",
    messages=[{"role": "user", "content": prompt}],
    response_format={"type": "json_object"},
)
# 解析 response.choices[0].message.content 中的 JSON
```

### 6.2 使用 response_format 强制 JSON

```python
response = client.chat.completions.create(
    model="mlx-community/GLM-5.2-fp8",
    messages=messages,
    response_format={"type": "json_object"},
)
```

后端拿到 JSON 后，自行校验字段并调用本地函数。

### 6.3 换用支持 function calling 的模型

如果当前任务强依赖工具调用，可临时切换到 Kimi-K2.7-Code-4bit（EXO 已部署，支持思考+代码，对工具调用兼容性更好），或部署 Qwen3-30B-A3B 作为快速响应模型。

---

## 七、已知事实与结论

1. AICG-DownLoader 项目业务代码中**没有直接使用 MCP 或 function calling**。
2. 本地 MCP server descriptor 的 JSON Schema 格式是标准的，问题不在 descriptor。
3. 最可能根因：GLM-5.2（EXO 部署）的 OpenAI 兼容接口不支持 `parallel_tool_calls` 或复杂 tool schema。
4. 推荐优先级：
   1. 在调用处设置 `parallel_tool_calls=False`。
   2. 简化 tool parameters schema。
   3. 必要时改用 `response_format={"type":"json_object"}` + 手动解析。

---

## 八、需要进一步排查时提供的信息

如果以上步骤仍无法定位问题，请提供以下信息：

1. 完整错误堆栈（截图或文本）。
2. 触发报错的 MCP server 名称和配置路径。
3. 触发报错的代码片段或请求体。
4. Trae IDE 版本号。
5. EXO 版本号和部署命令。
