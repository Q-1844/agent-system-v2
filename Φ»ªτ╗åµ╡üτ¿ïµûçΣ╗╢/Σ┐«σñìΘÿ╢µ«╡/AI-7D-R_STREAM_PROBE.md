# AI-7D-R · Agnes agnes-2.5-flash Streaming Capability Probe（2026-08-27）

一次性探针 `scripts/probe_stream_agnes.py`（镜像 `agnes_client.chat_completion` 请求格式，仅加 `stream=true`，不改正式业务代码）。目标：为「思考摘要 + 工具执行过程 + 最终文字流式」的 UI/parser 设计提供**实测事实**。

端点：`POST https://api.agnes-ai.cn/v1/chat/completions`，model `agnes-2.5-flash`，Bearer auth。

---

## 组1 · 纯文本 · body 与 chat_completion 完全一致 + 仅 stream=true

- HTTP 200，**Content-Type: `text/event-stream`**（无 charset）
- **无 `event:` 行、无 `: ping` 注释行**；每行 `data: {json}`，终结 `data: [DONE]`
- 首块是 role 标记：`delta:{"role":"assistant","content":""}`
- 之后逐块 `delta:{"content":"1"}`（粒度到单字符/单 token）
- 尾块：`delta:{}` + `finish_reason:"stop"`；**再一个 usage-only 块**（`choices` 里 delta 为空，`usage` 在 chunk 顶层）；最后 `[DONE]`
- **usage 无需 stream_options 就存在**（组1b 加 `stream_options={"include_usage":true}` 亦被接受，输出无差别）
- TTFT 3.17s / 总 3.18s（5 token 短输出：整包几乎同时到达）
- usage 样本：`{"completion_tokens":10,"prompt_tokens":303,"total_tokens":313,"completion_tokens_details":{"reasoning_tokens":0},"prompt_tokens_details":{"cached_tokens":256}}`

原始 chunk 示例（脱敏=原样，本就无敏感内容）：
```
data: {"id":"8d5e...","object":"chat.completion.chunk","created":1787828892,"model":"agnes-2.5-flash","choices":[{"index":0,"delta":{"role":"assistant","content":""}}]}
data: {..."choices":[{"index":0,"delta":{"content":"1"}}]}
data: {..."choices":[{"index":0,"delta":{"content":" "}}]}
...
data: {..."choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}
data: {..."choices":[{"index":0,"delta":{}}],"usage":{"completion_tokens":10,...}}
data: [DONE]
```

## 组2 · function tool call（最简 get_weather）

- **`delta.tool_calls` 存在**，OpenAI 风格分片：
  - 首片含 `id` + `function.name` + 空 `arguments`：`tool_calls":[{"id":"call_759cf...","function":{"arguments":"","name":"get_weather"}...`
  - 后续片**只有 `index` + `function.arguments` 字符串片段**（无 id/name）：`{"function":{"arguments":"{"},"type":"function","index":0}`
  - 聚合规则：按 `index` 分组，**直接字符串拼接 arguments**：`"{" + "\"city\": \"北京\"" + "}"` = `{"city": "北京"}`（拼完是合法 JSON）
- `finish_reason: "tool_calls"`
- usage 块照常出现
- ⚠️ **粒度不稳定**：组2 分了 4 片；组3b（同样带 tool）整个 tool_calls 数组在**单块**里出现（`delta:{"content":"","role":"assistant","tool_calls":[完整]}`）。parser 两种都要兼容。注意 `content:""`（空串非 null）可能与 tool_calls 同块出现。

## 组3 · 不开 thinking 时有无 reasoning 字段？

**未开启 enable_thinking 的所有 chunk（组1/1b/2/3c）：无 `reasoning_content`/`reasoning`/`thinking`/`analysis` 任何字段**。唯一"reason"痕迹是 usage 里的 `reasoning_tokens: 0`（计数字段，非内容）。

## 组3a · enable_thinking=true（chat_template_kwargs）+ 纯文本

- **reasoning 字段名 = `reasoning_content`**（DeepSeek-R1 风格），同样在 `delta` 里逐块流
- **顺序严格：role 标记块 → 一串 `delta:{"reasoning_content":"…"}` → `delta:{"content":"…"}` → finish → usage → [DONE]**（本样本无交错）
- reasoning 粒度极细（多为 1-2 字符/块），**真实增量流式**：TTFT 1.55s，总 3.31s，101 块在 ~1.76s 内陆续到达（对比组1整包到达——短正文生成太快没有可摊开的增量，reasoning 是唯一真·实时进度）
- usage：`completion_tokens_details:{"reasoning_tokens":86,"text_tokens":14}`（reasoning 计入 completion_tokens）

```
data: {..."delta":{"role":"assistant","content":""}}
data: {..."delta":{"reasoning_content":"用户"}}
data: {..."delta":{"reasoning_content":"要求"}}
data: {..."delta":{"reasoning_content":"计算"}}
...（86 tokens 的 reasoning）
data: {..."delta":{"content":"\n\n"}}
data: {..."delta":{"content":"17"}}
... 
data: {..."delta":{},"finish_reason":"stop"}
data: {..."choices":[{"index":0,"delta":{}}],"usage":{...}}
data: [DONE]
```

## 组3b · enable_thinking + tool

- **模型直接发 tool_calls，零 reasoning_content**（`enable_thinking` 被接受但该轮没思考）。即：thinking 开着也不保证每轮有 reasoning，UI 不能依赖它必然出现
- 整个 tool_calls 在单块（见组2 ⚠️）

## 组3c · tool call 后回传历史，reasoning 字段是否必须？

- **3c-1 不带 reasoning 字段回传 assistant(tool_calls) + tool result：HTTP 200 正常回答**（"北京当前气温为31度。"）
- **3c-2 带 `reasoning_content` 字段（注入假文本）回传：HTTP 200 正常回答**，无报错
- 结论：**不需要回传，回传也不报错**。与当前 `script_writer.py` 不传 reasoning 的做法兼容；parser 侧可安全丢弃 reasoning（不进 messages 历史）

## 计时（单次样本，冷热差异大，仅供参考）

| 组 | TTFT | 总时长 |
|---|---|---|
| 1 纯文本 | 3.17s | 3.18s |
| 1b +include_usage | 2.04s | 2.04s |
| 2 tool | 1.58s | 1.73s |
| 3a thinking | 1.55s | 3.31s |
| 3b thinking+tool | 1.71s | 1.75s |

thinking 对总时长影响小（本样本 +0.13s）；短正文 TTFT≈总时长（整包到达）。另：prompt_tokens 有 serving 路径波动（相同 prompt 303 vs 269，`cached_tokens` 提示有 prompt cache）。

## Parser 设计要点（据实归纳）

1. 事件源=每 `data:` 行一个 JSON chunk；跳过 `delta:{}` 与 usage-only 块（usage 提取后丢弃）
2. delta 三种内容键互斥出现于不同块：`role`（首块）/`reasoning_content`/`content`/`tool_calls`/`finish_reason`（在 choice 层非 delta 层）
3. `tool_calls` 聚合：按 `index` 分桶，`id`/`name` 只认首片，`arguments` 全片拼接后再 json.loads
4. 终结信号：`finish_reason` ∈ {stop, tool_calls} + `[DONE]` 行
5. reasoning 与 content 严格先 reasoning 后 content；tool_calls 轮可能无 reasoning
6. `content:""` 空串与 tool_calls 同块、整 tool_calls 单块、多片分片三种形态都要兼容
