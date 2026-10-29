请继续当前已经完成的 Phase AI-5A Prepared Project Autopilot。

本阶段实现：

Phase AI-5B：Full Project Autopilot / AI 完整制作

目标：

用户已经拥有：

- Project
- 完整故事 / Scene[]
- Project Characters

但允许：

- 某些 Scene 没有 Shot
- 某些 Scene 有 Shot 但没有 Production Plan
- 某些 Production Plan 还是 draft
- 某些 Production Plan stale

用户只点击一次：

[✨ AI 完整制作]

系统自动：

项目检查
↓
补齐所有缺失 Shot
↓
补齐 / 确认所有 Production Plan
↓
全项目准备完成
↓
冻结准备后的项目
↓
调用现有 ProjectProductionRun
↓
全部 SceneAutopilotRun
↓
全部图片 / 视频
↓
compose
↓
final.mp4

整个流程中途不等待：

- Director 确认
- Production Planner 确认
- 图片候选确认
- Scene 确认

本阶段仍然不是开放式 Agent。

FullProjectController 是确定性 Controller。

真正需要语义判断时，只调用已经存在的：

- DirectorWorkflow
- ProductionPlannerWorkflow

生成执行继续完全委托：

- ProjectProductionManager
- SceneAutopilotManager

==================================================
一、最重要的总体架构
==================================================

不要扩展 ProjectProductionManager 让它同时负责 AI Director。

增加上一层：

FullProjectManager
        ↓
Preparation
        ↓
ProjectProductionManager
        ↓
SceneAutopilotManager
        ↓
JobQueue

职责：

FullProjectManager
= 检查当前项目缺什么，并调用已有 Workflow 补齐。

ProjectProductionManager
= 已准备项目 → 多 SceneRun → composition。

SceneAutopilotManager
= 一个 Scene 的真实生成。

不要建立：

UniversalAgent
AgentManager
MultiAgentCoordinator

之类泛化框架。

==================================================
二、本阶段完整执行顺序
==================================================

非常重要：

不要：

Scene 1 prepare
→ Scene 1 generate
→ Scene 2 prepare
→ Scene 2 generate

必须：

Phase A：Preparation

Scene 1 prepare
Scene 2 prepare
Scene 3 prepare
...
所有 Scene ready

↓

Phase B：Production

冻结准备完成后的整个 Project

↓

ProjectProductionRun

↓

Scene 1 generate
Scene 2 generate
...

↓

compose

原因：

如果 Scene 7 的 AI Director / Production Planner 无法完成，
应该在任何长时间视频生产开始以前发现。

这样：

prepare 是快速、文本 AI 为主的阶段。

production 才是昂贵的长任务阶段。

==================================================
三、本阶段不处理 Story Organizer / Character Designer
==================================================

AI-5B 的起点仍然是：

Project.scenes 已经存在。

如果：

Project.story_text 有内容
但 Project.scenes == []

本阶段不要自动：

AI 整理故事。

返回：

NO_SCENES

UI：

“当前项目还没有故事段。
请先使用 AI 整理故事。

未来完整故事直达影片模式将自动处理这一步。”

这留给 Phase AI-5C。

同理：

不要在 AI-5B 自动调用：

- Story Continuation
- Character Designer
- 三视图自动生成

==================================================
四、角色 readiness 在实现前检查当前真实规则
==================================================

正式写代码前，请确认当前 first-frame generation：

如果 Scene.characters 中角色：

Character.views == {}

系统究竟：

A. 可以安全使用 description_en 继续生成；

还是：

B. 当前生成流程实际上依赖角色参考图。

不要凭假设新增限制。

如果当前生成系统允许 text-only Character：

AI-5B 可以继续，但 Preflight 给 warning：

CHARACTER_WITHOUT_VIEWS

“林夏尚无三视图，本次仍会使用文字设定生成，
人物一致性可能降低。”

如果当前真实生成路径要求至少一个 Character view：

则把它作为 blocker：

CHARACTER_ASSET_NOT_READY

本阶段不要自动生成三视图。

Phase AI-5C 再解决。

无论哪种情况：

如果 Scene.characters 引用了根本不存在于 Project.characters
中的角色名称：

必须 blocker：

MISSING_PROJECT_CHARACTER

不要让 Full Run 到图片阶段才失败。

==================================================
五、Scene Preparation State 分类
==================================================

FullProjectManager 不让 Agnes 决定“下一步做什么”。

普通代码检查 Scene 状态。

每一个 Scene 分类为：

READY

NEED_DIRECTOR

NEED_PRODUCTION_PLAN

或者 BLOCKED。

基本规则：

--------------------------------
Case 1
--------------------------------

scene.shots 为空

→ NEED_DIRECTOR

--------------------------------
Case 2
--------------------------------

scene.shots 非空
+
approved Production Plan
+
Plan not stale

→ READY

--------------------------------
Case 3
--------------------------------

scene.shots 非空
+
没有 Production Plan

→ NEED_PRODUCTION_PLAN

--------------------------------
Case 4
--------------------------------

scene.shots 非空
+
Production Plan draft
+
not stale

→ NEED_PRODUCTION_PLAN
但可复用当前 draft。

--------------------------------
Case 5
--------------------------------

scene.shots 非空
+
Production Plan stale

→ NEED_PRODUCTION_PLAN
必须重新生成。

--------------------------------
Case 6
--------------------------------

Scene / Character 当前存在无法进入生成流程的真实结构错误

→ BLOCKED

==================================================
六、已有 Shot 的核心红线
==================================================

如果：

scene.shots 非空

AI-5B 绝对不要调用 AI Director 去：

- 添加 Shot
- 删除 Shot
- 改 Shot
- 重排 Shot
- 优化 Shot.description
- 改 Shot.characters
- 改 target_duration_s

即使 Agnes 认为：

“这里应该再加一镜”

也不要。

已有 Shot 代表 canonical storyboard。

本阶段尊重它。

所以：

已有 Shot
→ 只检查 Production Plan。

只有：

scene.shots == []

才允许 AI Director 创建 Shot。

真正“已有 Shot 也允许 AI 自主重新设计”的能力留给：

Production Variant / Run-scoped Storyboard。

==================================================
七、有效 Director Draft 优先复用
==================================================

Scene 无 Shot 时：

先检查当前是否已有 Director Draft。

如果：

Director Draft 存在
+
对应当前 Scene.summary / characters
+
not stale
+
Shot plan 本身合法
+
角色覆盖满足

则：

不要重新请求 Agnes。

因为用户可能之前已经：

AI 设计分镜
→ 手工调整 draft
→ 但还没点击采用

用户此时点击：

[AI 完整制作]

等价于授权：

“可以自动采用当前有效的准备草稿。”

因此：

优先 auto-apply 现有有效 Director Draft。

UI 开始确认时明确告诉用户：

“已有且仍有效的 AI 分镜 / 制作规划草稿将优先采用。”

==================================================
八、Director Draft 不合法时怎么办
==================================================

例如：

Director Draft：

Scene.characters =
["赤焰守卫", "林夏"]

但 Draft Shots 只覆盖：

["赤焰守卫"]

现有 Director apply 会阻止。

AI-5B 不应该自动手工补角色。

策略：

1. 当前 Draft invalid
2. 调用 DirectorWorkflow 重新规划一次
3. 检查 coverage
4. 如果仍 invalid：

允许一次“修复规划”请求。

给 Director 明确：

“角色林夏尚未安排到任何镜头，
请重新规划并确保所有 Scene characters
至少出现在一个 Shot 中。”

最多：

initial plan
+
1 repair

仍失败：

Preparation failed。

不要无限循环。

不要普通代码随便把林夏塞进最后一 Shot。

==================================================
九、Director 生成新 Draft
==================================================

继续使用现有：

DirectorWorkflow.plan_scene(...)

不要复制 AI Director system prompt。

不要通过 HTTP localhost。

FullProjectManager
直接调用内部 workflow / business helper。

如果现有：

plan
apply

逻辑部分仍在 Router，

允许做最小 helper 提取：

plan_scene_business(...)
apply_director_draft_business(...)

要求：

手工 DirectorStudio
与
Full Project Autopilot

共用同一套：

validation
coverage
Shot creation
shot_id allocation

==================================================
十、Auto Apply Director
==================================================

Scene 没 Shot，并得到合法 Director proposal 后：

自动 apply。

仍然使用当前 AI-3 的原则：

- 一次 load
- 确认 Scene 仍为空
- stale 检查
- coverage 检查
- 创建 Shot[]
- 一次 save

正式 Shot：

description = intent
characters = draft characters
target_duration_s = AI duration

三个 prompt 仍为空。

opening_source 使用默认 independent。

link_mode 使用当前模型默认。

不要在 Director apply 时顺便做 Production Plan。

==================================================
十一、Director Apply 前的 Write-Ahead Checkpoint
==================================================

这是本阶段非常重要的 crash safety。

危险窗口：

Agnes Director 返回 Shot Plan
↓
直接写 project.json
↓
FullProjectRun 还没保存这次计划
↓
backend crash

恢复后 Controller 不知道这些 Shot
是不是自己刚刚创建的。

所以顺序必须是：

Director result
↓
保存到 FullProjectRun Preparation Manifest
↓
atomic save
↓
再 apply 到 project.json

即：

在 side effect 之前，
Full Run 已经记录：

expected_director_plan

例如：

{
  "scene_id": "s03",
  "director_plan": {
      "overview": "...",
      "shots": [...]
  },
  "stage": "director_ready_to_apply"
}

然后才：

apply_director_draft_business()

==================================================
十二、Director Apply 后 crash 的 reconciliation
==================================================

另一个危险窗口：

apply project.json 成功
↓
FullProjectRun 还没标 director_applied
↓
crash

恢复以后：

不要再调用 Director。

Controller 看到：

state.stage == director_ready_to_apply
+
scene.shots 已经存在

此时比较：

当前 scene.shots

与保存的：

expected_director_plan

至少比较：

- Shot 数量
- 顺序
- description
- characters
- target_duration_s

如果语义结构匹配：

认为上一轮 apply 已经成功。

标记：

director_applied

继续 Planner。

如果 Scene 已存在 Shot
但与 expected plan 不匹配：

blocked：

EXTERNAL_STRUCTURE_CHANGED

不要：

- 覆盖当前 Shot
- 重新 apply
- 猜是不是自己创建的

==================================================
十三、不要为了 origin 给所有 Shot 增加复杂字段
==================================================

第一版不必：

Shot.origin_full_run_id

因为 FullRun Preparation Manifest
已经保存了 expected Director result。

通过：

write-ahead proposal
+
canonical reconciliation

足够解决 crash window。

如果当前 Shot model 已经有通用 metadata，
可以使用。

但不要只为了 5B 给正式 Shot
增加大量 automation 字段。

==================================================
十四、Director 完成以后立刻进入 Production Planner
==================================================

新 Shot 创建成功：

Scene 当前状态自动变成：

NEED_PRODUCTION_PLAN

不要结束 Preparation。

继续同一 FullProjectRun：

Director
↓
apply
↓
Planner
↓
approve
↓
READY

==================================================
十五、已有 Production Plan 的规则
==================================================

检查当前 production_plans.json。

--------------------------------
Approved + not stale
--------------------------------

保留。

不重新请求 Agnes。

状态：

READY_EXISTING_PLAN

--------------------------------
Draft + not stale
--------------------------------

优先采用现有 Draft。

用户点击：

AI 完整制作

已经是项目级授权。

所以可以：

自动 approve 当前有效 draft。

不必重新生成。

这特别重要，因为用户可能已经在 Planner Studio
手工修改：

reference_previous
inherit_previous
keyframes

但还没点击确认。

不要把这些修改丢掉。

--------------------------------
Draft + stale
--------------------------------

不能采用。

重新生成。

--------------------------------
Approved + stale
--------------------------------

重新生成新的 Plan。

--------------------------------
无 Plan
--------------------------------

生成新的 Plan。

==================================================
十六、Production Planner 生成
==================================================

继续使用现有：

ProductionPlanner Workflow

Agnes 仍然只决定：

opening_source
link_mode
reason

普通代码继续派生：

source_shot_id
requires_previous_video_end
generate_first_frame
generate_last_frame
generate_video
auto_select_policy

不要因为 Full Autopilot
把这些 deterministic 逻辑重新交给 Agnes。

==================================================
十七、Auto Approve Production Plan
==================================================

Planner 返回并经过：

sanitize
derive
validate

以后：

Full Project Autopilot 自动 approve。

不要等用户打开：

ProductionPlannerStudio。

但是继续保持 AI-4A 的核心不变量：

approve 只设置：

plan.status = approved

不要提前写：

Shot.opening_source
Shot.source_shot_id
Shot.source_version
Shot.link_mode

真正 Shot continuity
仍然由 SceneAutopilot Executor
在 runtime activate 时生效。

==================================================
十八、Planner 失败策略
==================================================

正常调用失败：

自动 retry 1 次。

如果 JSON parse 等问题：

继续使用当前 Planner 已有 retry / sanitizer。

如果最终 Planner 仍无法产生合法计划：

FullProjectRun：

status = failed
stage = preparing
current_scene_id = ...
current_action = production_planner

不要 silent fallback 成：

all independent + i2v

虽然 AI-4A sanitizer 对局部缺失 Shot
可以安全使用默认值，

但如果整个 Planner 请求失败，
不要假装 AI 做出了制作方案。

用户 Resume 时：

从这个 Scene Planner stage 继续。

==================================================
十九、Preparation 必须全项目完成以后才能 Production
==================================================

例如：

Scene 1 READY
Scene 2 READY
Scene 3 NEED_DIRECTOR
Scene 4 NEED_PLAN
Scene 5 READY

执行：

prepare s01
prepare s02
Director s03
Planner s03
Planner s04
prepare s05

最终再次全项目检查：

s01 READY
s02 READY
s03 READY
s04 READY
s05 READY

只有全部 READY：

进入：

PREPARATION_COMPLETE

才允许创建：

ProjectProductionRun。

绝不能：

s01 ready
→ 先开始 s01 video
→ 后面再准备 s03

==================================================
二十、新增 FullProjectRun
==================================================

不要把 Preparation 状态塞入：

ProjectProductionRun。

新增：

class FullProjectRun(BaseModel):

    run_id: str
    project_name: str

    status: str
    stage: str

    pause_requested: bool = False
    cancel_requested: bool = False

    current_scene_id: str | None = None
    current_action: str | None = None

    scenes: list[FullProjectSceneState]

    expected_source_signature: dict

    preparation_manifest_file: str
    initial_source_snapshot_file: str

    production_run_id: str | None = None

    final_output: str | None = None

    error: str | None = None
    error_code: str | None = None

    created_at: float
    updated_at: float

status：

pending
preparing
pausing
paused
producing
failed
blocked
completed
cancelled

stage：

inspect
prepare
finalize_preparation
production
done

不要给它复制：

- ShotExecutionState
- video version
- image batch
- composition clips

这些属于 child Runs。

==================================================
二十一、FullProjectSceneState
==================================================

建议：

class FullProjectSceneState(BaseModel):

    scene_id: str
    index: int

    initial_state: str
    status: str
    action: str | None = None

    used_existing_director_draft: bool = False
    director_plan_snapshot: dict | None = None
    created_shot_ids: list[str] = []

    used_existing_production_plan: bool = False
    production_plan_id: str | None = None

    completed_actions: list[str] = []

    attempts: dict[str, int] = {}

    warnings: list[str] = []
    error: str | None = None

status 可：

pending
preparing
ready
failed
blocked

action 可：

inspect
director_plan
director_apply
planner_plan
planner_approve
done

==================================================
二十二、Preparation Manifest
==================================================

不要把所有 AI Proposal 全塞在频繁更新的 run.json。

建议：

projects/{p}/output/full_runs/{full_run_id}/

run.json
initial_source_snapshot.json
preparation_manifest.json

preparation_manifest.json 保存：

每个 Scene：

- 初始分类
- 是否原本已有 Shot
- 原本 Plan 状态
- Director proposal snapshot
- AI 创建的 shot ids
- Production Plan snapshot / plan_id
- 哪些内容来自 existing draft
- 哪些内容由 Full Run 新生成
- warnings

以后可以回答：

“这次一键制作到底自动帮我补了什么？”

==================================================
二十三、为什么 Full Run 现在单独放 output/full_runs
==================================================

它不是 AI 临时 draft。

也不是最终视频本身。

它是：

跨 Workflow 的长期自动化任务记录。

建议：

projects/{p}/output/full_runs/{run_id}/

ProjectProductionRun 继续保持：

projects/{p}/output/production_runs/{run_id}/

FullProjectRun 最终引用：

production_run_id

和：

production_run.final_output

不要复制 final.mp4。

==================================================
二十四、expected_source_signature
==================================================

Preparation 与 AI-5A Production
有一个非常大的不同：

Preparation 自己会合法修改 canonical Project：

Scene.shots 从空变为有 Shot。

所以不能简单：

start signature
!= current signature
→ stale

否则自己的 Director apply
会把自己判 stale。

因此 FullProjectRun 使用：

expected_source_signature

语义：

> “根据这个 Run 已经完成的所有合法修改，
> 我现在预期 canonical Project 应该长这样。”

Start：

expected_source_signature =
build_current_source_signature(project)

每一个有 canonical side effect 的步骤前：

1. load project
2. current_signature
3. compare expected_source_signature

不一致：

BLOCKED
EXTERNAL_SOURCE_CHANGED

每一个由 FullRun 合法执行的 canonical 修改后：

1. reload Project
2. rebuild signature
3. 更新 expected_source_signature
4. atomic save Run

这样可以区分：

A. FullRun 自己新增 Shot
→ 合法，更新 expected

B. 用户同时改 Scene.description
→ 非预期，blocked

==================================================
二十五、哪些 Preparation 动作会改变 source signature
==================================================

Director apply：

会新增 Shot

→ 更新 expected_source_signature。

Production Planner：

只改 production_plans.json

不改 canonical Project

→ 不改变 expected source signature。

Planner approve：

同样不写正式 Shot continuity

→ 不改变 signature。

因此 Preparation 的 canonical mutation
主要只有：

Director apply。

==================================================
二十六、Production Plan 还需要自己的 fingerprint
==================================================

因为 plan 不属于 project source signature。

每 Scene 可以保存：

expected_plan_fingerprint

例如：

hash({
 plan_id,
 scene_summary_snapshot,
 scene_characters_snapshot,
 shot_structure_snapshot,
 shots,
 status
})

如果 FullRun 准备过程中用户打开 Planner Studio
修改当前 Plan：

在下一 action 前检测 fingerprint 不一致。

如果 FullRun 本来准备：

auto approve existing draft

但用户同时修改：

不要盲目 approve 旧 snapshot。

BLOCKED：

PRODUCTION_PLAN_CHANGED_EXTERNALLY

第一版宁愿保守停止。

==================================================
二十七、启动前 Inspect / Preview
==================================================

增加：

GET /full-production/inspect

这不是 AI 调用。

纯代码扫描当前项目。

例如返回：

{
  "ready": true,
  "scene_count": 6,
  "shot_count": 11,
  "estimated_new_shots": null,

  "scenes": [
    {
      "scene_id": "s01",
      "state": "READY",
      "shot_count": 3
    },
    {
      "scene_id": "s02",
      "state": "NEED_DIRECTOR",
      "shot_count": 0,
      "has_valid_director_draft": false
    },
    {
      "scene_id": "s03",
      "state": "NEED_PRODUCTION_PLAN",
      "shot_count": 2,
      "plan_state": "draft"
    }
  ],

  "warnings": [],
  "blockers": []
}

注意：

Scene 没有 Director Plan 时，
不要胡乱预估：

“将创建 3 个 Shot”。

只能显示：

“需要 AI 设计分镜”。

最终 Shot 数由 Director 决定。

==================================================
二十八、Inspect 的 blockers
==================================================

至少考虑：

NO_SCENES

MISSING_PROJECT_CHARACTER

ACTIVE_FULL_PROJECT_RUN

ACTIVE_PROJECT_PRODUCTION_RUN

ACTIVE_SCENE_AUTOPILOT_RUN

以及根据当前真实角色生成要求：

CHARACTER_ASSET_NOT_READY

如果只是允许继续但体验可能下降：

放 warnings，不做 blockers。

==================================================
二十九、Active Run 冲突
==================================================

开始 FullProjectRun 前：

不能存在：

同 Project active FullProjectRun

也不能存在：

active ProjectProductionRun

也不能存在：

任何 active SceneAutopilotRun

原因：

Preparation 会修改 Shot / Plan，
不能和另一个生成 Run 同时竞争。

返回 409 并提供 existing run id。

不要自动取消旧 Run。

==================================================
三十、Full Project Start
==================================================

POST start：

后端重新：

1. load Project
2. inspect
3. 确认无 blocker
4. 创建 full run dir
5. 保存 initial_source_snapshot
6. 保存 initial expected_source_signature
7. 初始化 scene states
8. atomic save
9. 调度 Preparation loop

前端几秒前拿到的 inspect
不能作为 start 的权威状态。

==================================================
三十一、Preparation Scheduler 第一版串行
==================================================

按：

Project.scenes canonical 顺序

逐 Scene prepare。

不需要文本并发。

虽然 Agnes Text RPM 足够，

但 Preparation 一般相对视频很短，
稳定性和 recovery 更重要。

以后如果真的需要性能优化再并行。

==================================================
三十二、Preparation Loop
==================================================

大致：

async def _prepare_loop(run_id):

    run = load_run()

    run.status = PREPARING
    run.stage = PREPARE
    save()

    for scene_state in run.scenes:

        reload run

        if cancel_requested:
            cancel safely
            return

        if pause_requested:
            paused
            return

        if scene_state.ready:
            continue

        validate_expected_source()

        scene = reload current project scene

        if not scene.shots:
            await ensure_director(scene_state)

        validate_expected_source()

        await ensure_production_plan(scene_state)

        mark scene READY
        save

    validate_all_ready()

    run.stage = FINALIZE_PREPARATION
    save

    await _handoff_to_project_production(run)

具体实现不必机械使用 for loop，
可以使用当前 Manager 风格。

重点是：

每个 side effect 前后都有持久 checkpoint。

==================================================
三十三、Preparation checkpoint 原则
==================================================

每个操作至少分：

requested
result_saved
side_effect_applied
completed

不要只记录：

current_action = "director"

因为 crash 后无法判断：

Agnes 是否已经返回？
Shot 是否已经写入？
Plan 是否已经 approve？

例如 Director：

director_plan
↓
director_result_saved
↓
director_apply
↓
director_applied

Planner：

planner_plan
↓
planner_result_saved / persisted
↓
planner_approve
↓
planner_approved

可以用 completed_actions
而不必建复杂状态机库。

==================================================
三十四、Resume 的核心原则
==================================================

同一个 FullProjectRun resume：

不能重新处理：

已经 READY 的 Scene。

不能重新运行：

已经成功 apply 的 Director。

不能重新生成：

已经有效 approve 的 Plan。

例如：

s01 READY
s02 READY
s03 planner failed
s04 pending

Resume：

从：

s03 Planner

继续。

不要重新 AI Director s01/s02。

==================================================
三十五、failed 和 blocked 区分
==================================================

failed：

Workflow / Agnes / internal operation
在允许 retry 后仍失败。

例如：

DIRECTOR_FAILED
PLANNER_FAILED
PROJECT_PRODUCTION_FAILED

允许 resume。

blocked：

canonical source / plan
被外部改变，Run 已不能安全解释。

例如：

EXTERNAL_SOURCE_CHANGED
PRODUCTION_PLAN_CHANGED_EXTERNALLY
DIRECTOR_APPLY_CONFLICT
CHILD_RUN_CONFLICT

blocked 不允许普通 resume。

用户：

停止这个 Run
↓
根据新项目开始新的 Full Run。

==================================================
三十六、Pause 在 Preparation 阶段
==================================================

用户点暂停。

如果当前 Agnes Director / Planner request 已经发出：

不要尝试杀 HTTP request。

允许：

AI response 返回
↓
先保存 proposal checkpoint
↓
暂停

不要继续 apply / approve。

例如：

Director 已经产生方案，
但用户刚要求 pause：

保存：

director_result_saved

然后：

FullRun = paused

Resume：

直接从 apply 开始，

不重新请求 Agnes。

这会使 pause 更可靠。

==================================================
三十七、Cancel 在 Preparation 阶段
==================================================

和过去原则一致：

不回滚。

如果 FullRun 已经：

- 创建 s01 shots
- approve s01 plan
- 创建 s02 shots

用户 cancel：

这些都继续留在 canonical Project。

Run：

cancelled

UI 可以告诉用户：

“已完成的分镜和制作计划已保留。”

不要删除 AI 自动产生的 Shot。

==================================================
三十八、Preparation 结束后的 Final Validation
==================================================

在 Production 开始前最后一次：

load Project

检查全部 Scene：

- Scene exists
- shots 非空
- Production Plan exists
- approved
- not stale
- SceneAutopilot preflight ready

这里必须直接复用：

AI-5A / AI-4B 已有
preflight_scene_autopilot

不要再写第三套 readiness 规则。

如果全部通过：

PREPARATION_COMPLETE。

==================================================
三十九、为什么必须复用 SceneAutopilot Preflight
==================================================

FullProject Inspect：

是高层状态。

真正进入 Production 前：

使用同一个：

preflight_scene_autopilot(...)

确保：

locked Shot
plan coverage
shot/plan snapshot
previous requirements

等规则与真实 Executor 完全一致。

否则会出现：

Full AI 制作说 ready
↓
ProjectProductionRun start
↓
SceneAutopilot 又拒绝

FullProjectController 不应该复制底层规则。

==================================================
四十、Preparation 结束后再冻结 Production
==================================================

Preparation 完成以后：

canonical project 已经是：

完整 Story / Scene
+
完整 Shot[]
+
全部 approved Production Plans

这时才调用：

ProjectProductionManager.start_run(...)

让 AI-5A 自己：

- freeze source snapshot
- freeze plans
- 创建 ProjectProductionRun
- SceneAutopilot
- composition
- final.mp4

不要让 FullProjectManager 再自己复制：

source_snapshot
composition manifest
ffmpeg compose

这些全部属于现有 ProjectProductionRun。

==================================================
四十一、增加 parent_full_run_id
==================================================

给：

ProjectProductionRun

增加向后兼容：

parent_full_run_id: str | None = None

普通手工：

[自动制作完整影片]

产生：

parent_full_run_id = None

FullProjectManager 创建：

parent_full_run_id = full_run.run_id

同时 ProjectProductionManager 增加：

find_by_parent_full_run(
    project_name,
    full_run_id
)

用于 crash recovery。

==================================================
四十二、Production child 的 crash window
==================================================

危险：

FullProjectManager
↓
ProjectProductionRun 已创建
↓
full run 还没保存 production_run_id
↓
backend crash

恢复时：

不要再创建第二个 ProjectProductionRun。

先：

find_by_parent_full_run(full_run_id)

找到：

重新绑定：

full_run.production_run_id

继续等待。

只有确定不存在 child
才创建新的。

==================================================
四十三、FullProjectManager 不要控制 SceneRun
==================================================

一旦进入 Production：

FullProjectManager 只管理：

ProjectProductionRun child。

不要：

FullProjectManager
→ SceneAutopilotManager

Production 阶段应该：

FullProjectManager
        ↓
ProjectProductionManager
        ↓
SceneAutopilotManager

保持层级。

==================================================
四十四、Full Run 如何跟踪 Production
==================================================

production child 创建后：

full.status = PRODUCING
full.stage = PRODUCTION
full.production_run_id = child.run_id

之后等待 child。

如果 child：

completed
→ FullProjectRun completed
→ final_output = child.final_output

paused
→ FullProjectRun paused

failed
→ FullProjectRun failed

blocked
→ FullProjectRun blocked

cancelled
→ FullProjectRun cancelled

不要复制 child 内部 Scene/Shot progress。

UI 需要时读取 ProjectProductionRun。

==================================================
四十五、Production 阶段 Pause
==================================================

FullRun pause：

如果 stage == prepare：

使用本阶段 preparation pause。

如果 stage == production：

调用：

ProjectProductionManager.request_pause(
    production_run_id
)

等待 child paused。

然后：

FullRun paused。

Resume：

同样根据 stage：

prepare → 继续 preparation
production → resume existing ProjectProductionRun

不要创建新的 production run。

==================================================
四十六、Production 阶段 Cancel
==================================================

FullRun cancel：

如果 preparation：

checkpoint 后停止。

如果 production：

request_cancel(ProjectProductionRun)

已有：

SceneRun
图片
视频
final partial state

全部按 AI-5A 规则保留。

FullRun cancelled。

==================================================
四十七、Backend Restart Recovery 顺序
==================================================

当前已有：

JobQueue
↓
pending videos
↓
SceneAutopilot
↓
ProjectProduction

现在加 FullProject。

startup 顺序必须：

1. JobQueue recovery
2. recover_pending_videos
3. SceneAutopilotManager.recover_runs()
4. ProjectProductionManager.recover_runs()
5. FullProjectManager.recover_runs()

因为 FullProjectManager 是最外层。

它恢复时需要看到：

ProjectProduction child

已经处于正确状态。

==================================================
四十八、Full Run Recovery
==================================================

扫描：

projects/*/output/full_runs/*/run.json

恢复：

preparing
pausing
producing

等 active state。

如果 preparation：

根据 checkpoint
继续当前 Scene/current_action。

如果 production：

找 production child。

如果 full.production_run_id 缺失：

find_by_parent_full_run。

重新绑定。

不要创建重复 child。

==================================================
四十九、已有 Director Draft / Plan 的来源追踪
==================================================

Preparation Manifest 记录：

例如：

s02:
director:
  source = "existing_draft"

s03:
director:
  source = "generated_by_full_run"

s04:
production_plan:
  source = "existing_approved"

s05:
production_plan:
  source = "existing_draft_auto_approved"

s06:
production_plan:
  source = "generated_by_full_run"

这对未来：

AI Producer
成片版本
debugging

非常有帮助。

但这些 origin 只需要存在 Full Run manifest。

不要污染所有正式 Project models。

==================================================
五十、用户看到的入口
==================================================

Storyboard 项目级顶部目前已有：

[🎬 自动制作完整影片]

这是 AI-5A：

Prepared Project Autopilot。

保留它。

但新增更主要的：

[✨ AI 完整制作]

产品语义：

[✨ AI 完整制作]
→ 缺分镜也会自动补齐
→ 缺制作规划也会自动补齐
→ 最终生成影片

[🎬 自动制作完整影片]
→ 高级 / 精确模式
→ 要求所有 Shot / Plan 已经准备好

不要删除 AI-5A。

以后可以把 Prepared 模式放到更多菜单，
但本阶段先不做大 UI 重排。

==================================================
五十一、AI 完整制作 Inspect UI
==================================================

点击：

[✨ AI 完整制作]

先纯代码 Inspect。

例如：

AI 完整制作

当前项目

故事段       6
现有镜头     11

准备情况

✓ 故事段 1
  3 镜头 · 制作计划已确认

○ 故事段 2
  尚无镜头
  → AI 将设计分镜

○ 故事段 3
  2 镜头
  → AI 将生成制作计划

○ 故事段 4
  3 镜头
  → 将采用当前未确认制作计划

✓ 故事段 5
  已准备

────────────────

AI 将自动完成：

2 个故事段的分镜设计
3 个故事段的制作规划

准备完成后将自动开始完整影片生成。

已有且仍有效的 AI 草稿将优先采用。

整个流程不会中途等待人工确认。

[取消]
[开始 AI 完整制作]

==================================================
五十二、不要假装知道最终 Shot 数
==================================================

如果某些 Scene 没有 Shot：

开始前不能说：

“预计最终 18 镜”

因为 Director 还没运行。

显示：

现有镜头：11
还有 2 个故事段需要 AI 设计分镜

Preparation 完成以后：

UI 再显示：

分镜准备完成
最终共 17 镜
开始生成…

==================================================
五十三、Character warning UI
==================================================

如果 text-only Character 可以继续：

例如：

⚠ 林夏尚未生成三视图

本次仍可继续，
但人物一致性可能较低。

不要因为 warning
取消“一键无人值守”。

如果实际 pipeline 硬性需要角色图：

则：

✕ 林夏缺少必要角色资产

[前往角色设计]

AI-5B 不自动处理。

==================================================
五十四、Preparation Monitor
==================================================

Run 运行以后：

AI 完整制作

阶段 1 / 2
正在准备分镜与制作方案

✓ 故事段 1
  已准备

✓ 故事段 2
  AI 分镜 · 3 镜
  制作计划已确认

⏳ 故事段 3
  正在生成制作计划

○ 故事段 4
○ 故事段 5

当前：
故事段 3
AI 制作规划

[暂停]
[停止]

不要显示：

JSON parsing
Agnes chat endpoint

等内部技术信息。

==================================================
五十五、Preparation 到 Production 的 UI 过渡
==================================================

全部准备好：

✓ 全部故事段准备完成

6 个故事段
17 个镜头

正在开始影片制作…

然后同一个 Studio 切换为：

阶段 2 / 2
完整影片制作

此时直接展示 / 嵌入
现有 ProjectProductionRun 的高层进度：

8 / 17 镜头完成

✓ 故事段 1
✓ 故事段 2
⏳ 故事段 3
...

不要重新写一套 Scene Job monitor。

==================================================
五十六、UI 可以关闭
==================================================

和现有 Run 一样：

关闭 Studio

不停止：

- Preparation
- ProjectProductionRun
- Agnes jobs

重新打开：

GET active FullProjectRun

恢复当前：

preparing / producing / paused / failed。

==================================================
五十七、冲突性 UI 操作
==================================================

FullProjectRun：

PREPARING
或
PRODUCING

时，

Storyboard 上冲突性按钮尽量禁用：

- AI 设计分镜
- AI 制作规划
- 单 Scene Autopilot
- Prepared Project Autopilot
- 删除 / 重排 Shot

角色页 / 剧本页如果检测到 active Full Run，

对修改 canonical source 的操作显示：

“AI 完整制作正在运行。

修改故事、角色或镜头会使当前制作失效。
请先暂停或停止完整制作。”

不要求本阶段给整个 backend 建全局 transaction lock。

后端最终仍靠：

expected_source_signature
+
ProjectProduction source validation

保护。

==================================================
五十八、不要使用 FullRun 来覆盖用户已有有效工作
==================================================

Full Autopilot 原则：

已有 canonical Shot
→ 保留

已有 approved fresh Plan
→ 保留

已有 fresh Director draft（Scene 尚无 Shot）
→ 采用

已有 fresh Plan draft
→ 采用并 approve

只有：

缺失
stale
invalid

才重新请求 AI。

这是非常重要的产品原则：

“一键自动完成剩余工作”

不是：

“一键把用户之前做的所有工作重新做一遍。”

==================================================
五十九、但是 Production 仍然会生成新资产
==================================================

Preparation 尊重已有 Shot/Plan。

进入 ProjectProductionRun 后：

继续遵守 AI-5A / AI-4B：

未 locked Shot：

产生新的：

- prompt
- first frame batch
- last frame batch
- video version

不是复用旧生成结果。

locked Shot：

respect_locked=True 时复用已锁定 final。

也就是说：

结构尽量尊重已有。

生成资产产生一个新的完整版本。

==================================================
六十、FullProjectRun 不保存最终视频资产副本
==================================================

最终：

FullProjectRun.final_output

指向：

ProjectProductionRun.final_output

例如：

output/production_runs/{production_run_id}/final.mp4

不要：

output/full_runs/{full_id}/final.mp4

再复制一次。

Full Run 是 orchestration record。

ProductionRun 才是成片版本 record。

==================================================
六十一、Full Run 完成条件
==================================================

只有：

Preparation 全部 ready
+
ProjectProductionRun.completed
+
child final_output 存在

才：

FullProjectRun.completed

如果：

Preparation 完成
但 Production child failed

FullRun：

failed
stage=production

Resume：

resume existing ProjectProductionRun

不要重新 Director / Planner。

==================================================
六十二、Full Run 的 API
==================================================

按当前 router 风格调整。

建议：

GET
/api/project/{p}/full-production/inspect

POST
/api/project/{p}/full-production/start

GET
/api/project/{p}/full-production/runs

GET
/api/project/{p}/full-production/run/{run_id}

POST
/api/project/{p}/full-production/run/{run_id}/pause

POST
/api/project/{p}/full-production/run/{run_id}/resume

POST
/api/project/{p}/full-production/run/{run_id}/cancel

start 可以：

{
  "respect_locked": true
}

不要让前端传：

- Scene data
- Director draft
- Plan
- expected signature
- run id
- production child id

全部后端获取。

==================================================
六十三、后端建议结构
==================================================

新增：

src/ai/full_project_autopilot.py
routers/full_project_autopilot.py

例如：

class FullProjectManager:

    async def inspect_project(...):
        ...

    async def start_run(...):
        ...

    async def resume_run(...):
        ...

    async def request_pause(...):
        ...

    async def request_cancel(...):
        ...

    async def recover_runs(...):
        ...

    async def _run_loop(...):
        ...

    async def _prepare_scene(...):
        ...

    async def _ensure_director_plan(...):
        ...

    async def _apply_director_plan(...):
        ...

    async def _ensure_production_plan(...):
        ...

    async def _handoff_to_production(...):
        ...

    async def _wait_production_child(...):
        ...

    def _validate_expected_source(...):
        ...

    def _reconcile_director_apply(...):
        ...

不要创建万能通用 Workflow engine。

当前应用规模下，
明确代码比抽象 DSL 更可靠。

==================================================
六十四、业务函数复用
==================================================

请先阅读当前真实：

src/ai/director.py
routers/director.py

src/ai/production_planner.py
routers/production_planner.py

如果：

AI 生成逻辑已经是 service，
直接复用。

如果 apply / approve
仍然主要写在 router：

只提取必要 business helper。

目标：

DirectorStudio
          ↓
Director business
          ↑
FullProjectManager

ProductionPlannerStudio
          ↓
Planner business
          ↑
FullProjectManager

不要：

FullProjectManager
→ localhost HTTP
→ Router

同样不要让 FullProjectManager
直接自己拼 Agnes system prompt。

==================================================
六十五、不要直接调用 AgnesClient 处理 Director/Planner 之外的决策
==================================================

FullProjectManager 不应该问 Agnes：

“这个项目下一步该干什么？”

下一步由代码判断。

Agnes 只在：

DirectorWorkflow
ProductionPlannerWorkflow

内部使用。

这是本阶段的核心 architecture boundary。

==================================================
六十六、不要新增 Agent tools
==================================================

本阶段不要建立：

inspect_project tool
director tool
planner tool
production tool

供 Agnes 2.5 自由调用。

这些以后可以成为 AI Producer 的工具。

现在 Controller 直接调用 service。

先证明完整 workflow 稳定。

==================================================
六十七、失败错误要带 stage + scene
==================================================

例如：

{
  "status": "failed",
  "stage": "prepare",
  "current_scene_id": "s04",
  "current_action": "director_plan",
  "error_code": "DIRECTOR_FAILED",
  "error": "..."
}

UI：

“故事段 4 的 AI 分镜生成失败。”

[继续执行]

而不是：

“完整制作失败。”

让用户能够知道失败位置。

==================================================
六十八、Retry budget
==================================================

Director：

正常 AI 请求失败：
最多 1 retry。

Director proposal coverage invalid：
最多 1 repair plan。

Production Planner：
最多 1 retry。

不要无限循环。

Scene/Video generation retry：

完全继续由 AI-4B 管。

FullProjectManager 不添加第二层
视频 retry。

==================================================
六十九、不要在父层重复 child retry
==================================================

如果 ProjectProductionRun failed：

FullProjectRun 不应该：

自动创建第二个 ProjectProductionRun。

只记录：

production child failed。

Resume：

调用：

ProjectProductionManager.resume_run(existing child)

让 child 自己按现有 checkpoint 继续。

==================================================
七十、Preparation 和 ProjectProduction 的 run lineage
==================================================

最终链路：

FullProjectRun F1

Preparation:
  s01 existing
  s02 Director generated
  s03 Planner generated

↓

ProjectProductionRun P1
parent_full_run_id = F1

↓

SceneAutopilotRun S1
parent_project_run_id = P1

SceneAutopilotRun S2
parent_project_run_id = P1

...

这样以后任何 final.mp4
都可以追溯：

final.mp4
→ ProjectProductionRun
→ FullProjectRun
→ Preparation decisions

这个 lineage 非常重要。

==================================================
七十一、未来 Production Variant 的边界
==================================================

本阶段不要让 FullRun 每次重新 Director 已有 Shot。

当前：

Canonical Project
↓
FullProjectRun
↓
仅补缺失 Shot
↓
ProjectProductionRun

以后 AI-6：

ProductionVariant
↓
Run-scoped Storyboard
↓
允许 Director：
  keep
  add
  delete
  rebuild

不要现在提前实现。

==================================================
七十二、未来 Story-to-Film 的接口
==================================================

AI-5C 以后可以把 FullProjectRun
继续往前扩：

stage:

story
characters
prepare
production

例如：

只有 story_text
↓
Story Organizer
↓
Character resolution
↓
Character Designer / generation
↓
AI-5B Preparation
↓
Production

所以 FullProjectRun 结构不要假定：

第一个永远是 Scene Director。

但本阶段不要真的实现这些 stage。

==================================================
七十三、未来 AI Producer
==================================================

以后聊天 Agent 可以只拥有高层工具：

inspect_full_project
start_full_production
pause_full_production
resume_full_production
start_variants

但这不是 AI-5B。

AI-5B 正是在为这些工具
提供可靠底层 workflow。

==================================================
七十四、必要验证：不要把测试当主工作
==================================================

重点实际验证以下情况。

Case 1：

3 Scene：

s01
已有 Shot + approved Plan

s02
已有 Shot + 无 Plan

s03
无 Shot

点击：

AI 完整制作

期望：

s01 不改 Shot/Plan
s02 自动 Planner + approve
s03 Director → apply → Planner → approve

↓

所有 Scene ready

↓

一个 ProjectProductionRun

↓

最终 final.mp4。

这是本阶段最核心 E2E。

--------------------------------

Case 2：

Scene 无 Shot，
但已有 fresh Director Draft。

Full Run：

不重新请求 Director。

直接采用现有 Draft。

--------------------------------

Case 3：

Scene 有 fresh Planner Draft。

Full Run：

不重新请求 Planner。

直接 auto approve。

--------------------------------

Case 4：

Director apply 后
模拟 crash，
但 parent checkpoint 仍在
director_ready_to_apply。

恢复：

根据 expected proposal
发现 canonical Shot 已经匹配。

不得重复创建 Shot。

--------------------------------

Case 5：

Preparation 完成 s01/s02，
s03 Planner failed。

Resume：

不重新处理 s01/s02。

从 s03 Planner 继续。

--------------------------------

Case 6：

Preparation 中用户修改
已经存在的 Shot.description。

下一 checkpoint：

FullRun blocked
EXTERNAL_SOURCE_CHANGED。

不要覆盖用户修改。

--------------------------------

Case 7：

FullRun 创建 ProjectProductionRun
以后，
parent 尚未保存 production_run_id
模拟 crash。

恢复：

find_by_parent_full_run
找到原 child。

不得创建第二个 ProjectProductionRun。

--------------------------------

Case 8：

Production child failed / paused。

FullRun 正确映射状态。

Resume 使用同 child。

--------------------------------

Case 9：

真实 Agnes 小项目。

建议：

2–3 Scene

其中：

一个 Scene 已准备
一个 Scene 无 Plan
一个 Scene 无 Shot

真实走：

Director Agnes 2.5
→ Planner Agnes 2.5
→ ProjectProduction
→ Agnes Image / Video
→ composition
→ final.mp4

全程除了最开始点击“开始”，
不进行任何人工确认。

==================================================
七十五、完成后重点汇报
==================================================

完成后重点告诉我：

1. FullProjectRun 最终数据结构
2. 为什么没有直接扩 ProjectProductionRun
3. Scene 状态如何分类 READY / NEED_DIRECTOR / NEED_PLAN
4. 已有 Shot 为什么绝不会被 AI-5B 修改
5. fresh Director Draft 如何复用
6. fresh Planner Draft 如何自动 approve
7. stale Draft / Plan 如何处理
8. Director coverage 不通过如何 repair
9. Director write-ahead checkpoint 如何避免 crash 后重复创建 Shot
10. expected_source_signature 如何允许自己的修改但阻止外部修改
11. Production Plan fingerprint 如何防止并发修改
12. Preparation 为什么全部完成后才开始视频生成
13. Preparation pause/resume/cancel 如何工作
14. Preparation failed 后如何只继续失败 Scene
15. ProjectProductionRun parent_full_run_id 如何建立
16. crash 后如何避免重复创建 production child
17. backend recovery 顺序
18. Full Run 最终如何取得 final.mp4
19. FullProjectRun / ProjectProductionRun / SceneRun lineage
20. 实际 E2E 是否做到：
   缺 Shot + 缺 Plan
   → 自动补齐
   → 完整影片
21. 哪些能力明确留给 AI-5C / AI-6 / AI Producer
