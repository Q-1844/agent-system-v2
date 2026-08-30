# Script Writer · Backlog（2026-08-30 梳理）

> 来源：web.search 第一版交付后的全面自查。按用户决策：#1 暂不修（仅记录），其余为已知事项存档。

## 1. 「做了 A 却说做了 A+B」的闸门缺口（用户决策：暂不修，记录在案）

- **现象**：真相闸门 `false_claim` 仅在「本轮零真实变更」时触发；当本轮有任意一次真实变更时，
  模型叙述里谎称的【其它未发生操作】不会被纠正（真机验收 C 轮实例：真实创建 s11 的同时
  谎称"修改了 s08"——那是上一轮 stale 失败的操作）。文末【实际执行记录】事实块仍由
  receipts 确定性生成，是可信的，但正文叙述会带谎言。
- **更广的形态**（SenseNova 实测）：模型用"**本轮实际执行：** …追加…"这类无"了/完成/已"
  完成态标记的措辞，可同时绕过写谎报与搜索谎报正则。
- **用户决策理由**：自然语言判定易误伤，暂不扩大 truth gate；事实块已提供真实结果。
- **未来方向**（若重启此项）：把「声称的操作集合 ⊆ receipts 操作集合」做成结构性校验
  （对照 _op_human 已知操作枚举），避免自然语言判断；或对"本轮实际执行/实际修改"这类
  仿冒权威格式的措辞单独收紧。

## 2. SenseNova 档与原生工具链路不兼容（真机实测，重要）

- **现象**：`text_sensenova_68fl`（SenseNova 6.8 Flash Lite）下，Script Writer 原生
  tool-loop 完全收不到 tool_calls——即使明确指令"立即调用 story.list"，模型也以自然语言
  **编造工具结果**（伪造段数与版本指纹；实测编造 s27 创建但磁盘无此段，receipts/trace 全空）。
- **影响**：模型切换开关（2026-08-29 上线）在 script_writer 上切到 SenseNova 后，story
  工具与 web.search 全部不可用，且模型会自信地谎报成功。web.search 本身无问题（同链路
  Agnes 档 A–G 全过）。
- **建议**：在模型切换 UI 对 script_writer 隐藏/禁用 SenseNova 选项，或切换时提示
  "该档不支持工具调用"；或探测 SenseNova 端点的 function-calling 支持后再放开。

## 3. Agnes 偶发非法 JSON tool-call 参数 → 上游 400 → 整轮失败（预存在）

- **现象**：模型生成的 assistant tool_call.arguments 偶尔不是合法 JSON，把该消息回传
  上游时被 400 拒绝（"Assistant tool call arguments must be valid JSON"），turn 以
  [执行失败] 结束。真机 G 轮实测遇到一次。
- **方向**：在 agnes_client/_run_tool_loop 层参照 timeout recovery 做一次结构化恢复
  （识别 400 invalid-arguments → 剔除/修复该条消息 → nudge 模型重发）。

## 4. 零写恢复对纯「写」字类请求不生效（预存在，影响小）

- `_WRITE_REQUEST_RE` 不含"写一段/写个场景"这类表述，zero-write recovery 不会为它们
  触发；不过 claim-gate 仍能拦住后续的假成功叙述。

## 5. 搜索预算拒绝的文案偶被模型误读（轻微）

- F 轮实测：模型把 `search_budget_exhausted`（轮内次数上限）向用户转述为"额度耗尽"。
  可在拒绝 error 文案中显式加一句"这是本轮搜索次数上限，不是服务故障/额度问题"。
