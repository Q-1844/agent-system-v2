# Visual Review Tool V1 — 实现报告

> 阶段：给 Director 增加一个**独立、无状态、fresh-context、只读、结构化反馈**的视觉审查
> 工具 `visual.review_image`（真实 agnes-2.5-flash Vision）。
> 基线：`feat/producer-v1-bridge` @ `9aff8a4`（Producer V1 三阶段冻结 + AI-7D-R 修复收口）。
> 本报告所有"证据"均为真实执行产物；判定从证据，不从文本自述。
> scratch 约定：probe/smoke 驱动脚本（`_vision_probe.py`/`_vision_smoke.py`）与
> `_vision_probe_evidence/` 未提交（§三十 scratch 规则），路径在 §17 引用。

---

## 1. Agnes 2.5 Flash 最终真实 Vision API 契约（全部真机 probe 实证，非文档转述）

- 端点：`POST /v1/chat/completions`（与文本同端点），`model: "agnes-2.5-flash"` 显式指定；
- `messages[].content` 支持数组：`[{"type":"text","text":…}, {"type":"image_url","image_url":{"url":…}}]`，多图按序追加；
- **image_url 同时接受**：
  - 公网 URL（项目候选里保留的 `cos-platform-outputs.agnes-ai.cn` / `platform-outputs.agnes-ai.space` 原始 URL，probe A/B 直接复用成功）；
  - **base64 data URI（`data:image/png;base64,…`）**——probe C3 单图/混合/纯 base64 双图 4/4 成功。这是本工具的生产路径：**零公网托管依赖**；
  - 🔴 **imgbb 公网 URL 恒失败**：500 `OpenAIException - loading IMAGE data at index …`，
    换图、单图、双图任意顺序均失败（probe C2/C3 隔离矩阵 4/4 FAIL，确定性错误非瞬态）。
    与 2026-07-31 视频管线"imgbb 唯一可用"的旧结论相反——vision 后端下载不了 i.ibb.co。
    **审查图禁止走 imgbb**；也因 data URI 可用，本阶段**不需要任何新的公开文件服务器**；
- usage 正常返回，且图片输入单独计量：`prompt_tokens_details.image_tokens`
  （1312×736 分镜帧 ≈ 943 tokens/张，角色设定竖图 ≈ 1281）；
- thinking 默认关（不传参即关），低 temperature（0.2）下输出稳定；
- 延迟（真实测量）：单图审查 3.2–20s（典型 5–15s），双图 5–31s，服务端波动大（probe E 同请求 6.1s–71.6s）。

## 2-4. 单图 / 多图 / URL 要求（probe A/C2/C3/D）

| probe | 结果 | 证据 |
| --- | --- | --- |
| A 单图理解 | ✅ 真实读图：准确描述跪姿机甲/红色纹路/右肩三角/机库；"有几个人物"诚实回答"无法确认（无可见人类人物）"——是读取不是按 prompt 编造 | `A_single_image.json` |
| 多图 2 张 | ✅ 16.24s，两图分别正确描述（C2-2） | probe 日志 |
| 多图 4 张 | ✅ 机械上接受并全部处理（image_tokens=3772≈4×943，10.93s），但**逐图编号标注不精确**（报告跳过"图3"）→ 连续性以 2 图相邻对照为主（能力边界） | `D_continuity_4img.json` |
| URL 要求 | 公网 URL 或 data URI；imgbb 不可用（§1） | `C3_*` |

## 5. Asset access 方案

**本地文件 → base64 data URI（主路径）**。生成资产（Candidate/角色三视图）本地文件恒存在，
data URI 直接可用，不依赖原始 URL 存活、不需要 imgbb（对 vision 恰好是坏的）、不建新托管。
仅当本地文件缺失时退回 candidate.url（Agnes 原始公网 URL，probe 证实仍存活可用）。
与 `routers/shots.py` 角色参考图既有的 data URI 做法同源（互相印证）。

## 6. Structured output 方案（probe E，10 次真机对比）

**选 Option A：JSON-only prompt + strict parse + 一次 repair + 明确失败。**
- Option A（JSON-only）：5/5 解析成功，平均 15.41s / 1836 tokens；
- Option B（function calling `submit_visual_review`）：5/5 成功，平均 19.52s / 2119.6 tokens；
- 两者判断完全一致（10/10 报 needs_fix + 同一目镜问题）→ 取更简单、更快、更省的 A；
- 实现中发现并修复一个真实契约缺陷：模型会把 `critical` 写进 verdict（严重度与结论混淆）。
  修复 = SYSTEM_PROMPT 增加"字段纪律"段 + repair 报错给出合法值
  （`verdict 必须是 pass/needs_fix/uncertain 之一（当前为 'critical'；严重程度请放 severity 字段）`）。
  修复后 smoke 3 案例 repairs=0，零复发。判断本身始终正确，是契约层问题。

## 7-9. Prompt / Tool schema / Result schema

- **System**（`director_vision_review.py`）：审查器角色 + 只判断提供的要求与图片 + 不虚构 +
  无法确认→uncertain + issues≤5 + 字段纪律 + JSON 契约（verdict/severity/summary/issues[
  category/severity/observation/expected/suggested_fix]）；
- **User**（context builder，§十：只发真正需要的）：检查重点（focus 对应检查项）+ 目标镜头
  canonical 要求（description/characters/shot_size/camera/angle/composition/opening_state/
  important_details/required_elements/dialogue"不应画成画面文字"）+ 角色设定（仅
  character_consistency 时，≤2 个：名字+description_en，参考图不可用明示并提示 uncertain）+
  图片清单标注（图1=本镜画面/图N=角色参考图/图N=邻镜）——标注是 probe D 教训的直接对策；
- **Tool**：`visual.review_image(shot_id, focus?, frame?)`，focus ∈ shot_adherence /
  visual_defects（默认）/ character_consistency / continuity；description 写明适用/不适用/
  truth gate（§十八/§十九）；图上限 = 本镜 + 邻镜 + ≤2 参考 = 4（probe D 实证可处理）；
- **Result**：verdict(pass/needs_fix/uncertain) / severity(none/minor/major/critical) /
  summary(≤300字) / issues≤5（字段白名单+截断，§二十：Director 吃可行动反馈不是散文）；
- 附带 **uncertain**（§十三）：无参考图、镜头无角色字段、无法确认等场景明确提示模型用 uncertain。

## 10. Director integration

- `director.py` 只做注册接线（与 director_visual 同模式）：import、`DIRECTOR_TOOLS`、
  `TOOL_EXECUTORS`、`_TOOL_LABELS`、`_op_human` 渲染分支、系统提示新增【视觉审查（只读，
  真实看图）】块（含 truth gate 与"结果不是 canonical 事实、不自动重生成"纪律）；
- 核心冻结面零改动：tool-loop / OCC / truth gate / 权限集合 / 写域账本逐字节未动——
  审查是只读工具，回执不进写域 `receipts` 账本（与 shot.read 同语义），经 `trace` 如实
  记录（N2 收口同口径：`trace=[('visual.review_image', ok=True)]`）；
- 前端：`agentTurn.ts` 工具标签映射 +（执行时间线/刷新重建均经此渲染）"视觉审查"。
  写域回执卡（receiptLabel）不涉及只读工具，未改动；
- Producer 无直接视觉工具（§八领域 ownership）：Producer → director.delegate → Director
  → visual.review_image，桥接链路原样可用。

## 11. Truth gate（§十九 no fake vision）

- 工具 description + 系统提示双写：**Director 没有任何直接看图能力，对画面内容下判断只有
  一条正路——调用本工具**；禁止凭 prompt/文件名/描述假装看过图；
- 失败路径全部诚实：无生成记录/文件缺失 → `no_image`（不假看）；provider 失败 →
  `provider_error`；两次输出不合契约 → `review_parse_failed`（不提供假结论）；客户端不支持
  视觉 → `not_configured`；
- mock 铁证：`test_no_image_honest_failure` / `test_parse_fail_twice_honest` /
  `test_provider_error_translated` / `test_client_without_vision_not_configured`——
  失败一律 ok=False + 结构化 code，无任何伪造审查结论。

## 12. Canonical read-only 证明（T9）

`test_review_basic_receipt_context_telemetry`：审查前后
- `project.json` **逐字节相等**；
- 项目文件树快照对比：除新增 `output/agents/director/visual_reviews.jsonl`（非 canonical
  观测工件，append-only，同 bridge_messages 惯例）外**零新增零改动**。

## 13-15. Telemetry / latency / error-retry

- 每次审查回执携带 telemetry：model / prompt/completion/total_tokens / image_tokens /
  latency_s / repairs / image_count / focus / frame，并追加到
  `output/agents/director/visual_reviews.jsonl`；
- 实测成本画像：单图审查 ≈1900-2100 tokens（其中图 943），带参考双图 ≈2600 tokens，
  3-5s；对比"完整 Review Agent"路径：那需要挂载工具面 + 携带 Director 历史/系统提示 +
  多步 loop（每步 ≥65536 max_tokens 窗口与 reasoning 通道），而本工具是**单次请求、
  context 只有需求文本 + 图片**（需求文本实测仅数百 token）——这就是工具路径 context 更小
  的结构性原因（不虚构精确美元价）；
- 错误恢复（§二十三）：429/5xx/网络异常由 `AgnesClient._request` 既有退避重试承担
  （RETRYABLE 集，指数退避，**未新造第二套 retry**）；耗尽后 `AgnesError → AgentError
  ("provider_error")` 保留 status 分类；超时走 httpx 360s read + 网络级重试；
  cancel 语义：审查为 5-30s 级短工具，走主 loop 既有步边界取消（非 LONG_TASK）。

## 16-17. Tests 与真机质量案例

**mock 单测（`tests/test_director_vision_review.py`，14/14 绿）**：注册与边界、bad
arguments/not_found、基础回执+context 构建+T9 只读证明、no_image 诚实失败、URL 退回、
角色参考+邻镜组装（3 图 + 标签 + roles 断言）、参考缺失诚实 uncertain 提示、repair 一次
复原、两次失败诚实、字段非法触发 repair、provider_error 转译、not_configured、
_op_human/trace、director_turn 全链路（T7：用户"检查这张生成图"→ 真实发起
visual.review_image 调用 → trace 如实记录）。
**T1-T4（真实 API）= probe A/B/C/D**，证据在 `_vision_probe_evidence/`。
**家族回归**：test_director_visual/agent/director/bridge_execution + producer_bridge +
production_room = **103 passed，零回退**（T10）。全量回归见 §22 后附结果。

**真实质量 smoke（§二十五；ground truth 由审查者亲自看图 + 读 canonical 建立后再跑工具）**：

| 案例 | ground truth（亲验） | 工具输出 | 判定 |
| --- | --- | --- | --- |
| A：s01_c01 | 要求"目镜尚未点亮"，实图目镜明显发橙光（真偏差） | `needs_fix/major`，1 条 issue 精准命中目镜，无噪声（4.4s） | ✅ TP |
| D：s01_c02 | 构图符合"右侧+门侧留白"、目镜亮是本镜明确要求；景别偏全景 vs 要求近景（边界） | `needs_fix/minor`：1 条真（景别偏大）+ 1 条**假**（见 §18） | ⚠️ 1 TP + 1 FP |
| C-：设定图测试机参考 vs 赤焰守卫画面 | 两角色完全不同（白色球形小机器人 vs 重型机甲） | `needs_fix`，character_consistency/critical"两者完全不同"精准命中，另如实复报目镜偏差（5.3s） | ✅ TP |

证据：`_vision_probe_evidence/smoke_caseA.json / smoke_caseD.json / smoke_caseCneg.json`。

## 18-20. False Positives / False Negatives / 能力边界（§二十六，如实降级）

- **FP×1（重要）**：Case D 中模型称"主体位于画面**左下方**、没有留出视觉空间"——亲验实图
  主体在**中偏右**、左侧是大门亮区留白。**左右方位/构图位置判断不可靠**，降级记录：
  `composition_left_right = weak`（该类 observation 需人工复核）；
- **TP 稳定项**：主体身份/数量（人数、机甲 vs 机器人）、状态对照（点亮/熄灭——probe B、
  Case A、Case C- 三次独立命中同一真实偏差，零漏报）、场景/道具、景别偏大（Case D 真
  minor）、连续性突变（probe D：道具消失/飞机突现，2 图模式）；
- **FN：零**——本阶段所有亲验确认的真实偏差（目镜状态、错误角色、连续性突变）均被检出；
  但样本量小（3 案例 + 2 probe 对照），不据此宣称全面可靠；
- **能力边界（写入 tool description/docs）**：
  - `character face identity`：未测（现有资产无机甲面部/人脸细部对照样本）——不夸大；
  - `composition_left_right = weak`（§上 FP）；
  - `multi-image labeling`：>3 图时逐图归属可能串号——连续性用 2 图相邻对照；
  - `focus` 是优先级不是硬过滤器（模型可跨 focus 报真实问题，Case C- 即如此）；
  - 延迟波动大（3-70s，provider 侧），不适合逐帧自动全量审查（本阶段也不做，§三十）。

## 21. 与被冻结核心的关系

`git diff` 证明：producer.py / script_writer.py / character_designer.py / scene sub-agent /
DiffEngine / Clean Review / session_governance **零改动**；director.py 仅注册接线 + 系统提示
新增块（新增行，不改既有行）；agnes_client.py 仅**新增** `chat_completion_vision` 方法
（既有方法零 diff）。Clean Review 审查路由未动——"检查这张图"类指令不命中
`_REVIEW_INTENT_RE`（全片/整体/逐镜级词表），自然留在主 loop 走视觉工具（T7 实证）。

## 22. BLOCKER / NON-BLOCKER

**BLOCKER（§二十九）逐条：无。**
Agnes 真实读取项目图片 ✅（data URI 5/5）；不需要危险的永久公网资产 ✅（imgbb 方案被
probe 否决，data URI 零托管）；模型不经常声称看到不存在内容 ✅（三次真实偏差全部命中、
无目镜外编造、无法确认诚实降级）；结构化结果稳定 ✅（probe E 10/10 + 契约修复后 smoke
0 repair）；不写 canonical ✅（T9 字节级）；图片不可访问时不假装审查 ✅（no_image 诚实失败）；
未改/未破坏 Director 冻结链路 ✅（§21 + 103 项家族回归）。

**NON-BLOCKER（backlog）**：
- NB1 左右方位 FP——prompt 无法根治（模型级空间推理弱），留待多模态模型升级或人工复核约定；
- NB2 人脸级 identity 对照未验证（无样本资产）；
- NB3 review 结果暂无前端卡片（经 trace/时间线 + assistant 文本呈现已够 V1）；
- NB4 4 图标注串号——连续性限定 2 图（已实现为同段上一镜）；
- NB5 自动 regenerate loop / review-all-shots / video 理解 / Visual Review Agent 化——
  §三十明确不做，等反馈质量在真机使用中积累信任后再议。

## 23. Commits / 最终判定

Commits（不 push）：
1. `feat(director)`：visual.review_image 工具 + AgnesClient.chat_completion_vision + 注册接线 + 时间线标签
2. `test(director)`：test_director_vision_review.py（14 项）
3. `docs`：本报告

全量回归（`:8000` 空闲确认后执行，46:50）：`_vrt_full_regression.log`——
**34 failed / 1165 passed / 1 skipped**。失败清单与 Phase 2/3 基线**逐条一致**（34 个同族
已知噪声：runtime_control / autopilot / interface / shot_request_rules 等；上一轮出现的
1 例套件内自干扰 `test_case6_external_source_change_blocked` 本轮未复现，印证其单跑复绿的
噪声分类）。failed 集合 diff = 零新增、零新增回归；passed +15 = 本阶段 14 项新测试 + 1。

---

> **Visual Review Tool 是否已经能够作为一个低上下文成本、fresh-context、只读的视觉反馈器，
> 让 Director 基于真实图片而不是文本猜测来判断生成结果？**

**是。** 真机证据链：单图理解/Shot 对照/角色参考/结构化输出全部 probe 实证；Director 在
"检查这张生成图"时真实发起 `visual.review_image` 调用并获得结构化反馈（T7）；三次真实
偏差（目镜状态×3、错误角色×1、连续性突变×2）全部检出、零漏报、无法确认时诚实
uncertain；审查前后 canonical 逐字节不变；单次审查 ≈2000 tokens / 3-5s，无 Agent loop、
无共享 context、无 canonical 写权限；能力边界（左右方位弱、多图标号、人脸未测）如实写入
文档而非隐藏。

**判定：PASS（含 §二十八 PASS WITH LIMITATIONS——能力边界已文档化，不阻碍上线）。**
按 §三十一：到此停止，**不开始** automatic regenerate loop。
