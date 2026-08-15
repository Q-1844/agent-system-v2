# Phase AI-5A：Prepared Project Autopilot

继续当前已经完成的：

* Phase AI-1：AI 整理故事
* Phase AI-1B：AI 续写故事
* Phase AI-2：AI 角色设计
* Phase AI-3：AI Director，Scene → Shot[]
* Phase AI-4A：Production Planner
* Phase AI-4B：Scene Autopilot Executor

当前系统已经真实验证：

```text
approved Production Plan
→ SceneAutopilotRun
→ 自动 prompt
→ 自动首帧
→ 自动候选
→ 自动尾帧（需要时）
→ 自动视频
→ 自动设 final
→ 实际视频末帧
→ 下一 Shot
→ Scene completed
```

并且已经具备：

* Run checkpoint
* pause / resume / cancel
* backend restart recovery
* JobQueue idempotency
* origin_run_id
* exact batch / video version tracking
* seeded candidate selection
* locked Shot
* six opening/link execution paths

本阶段实现：

# Prepared Project Autopilot

即：

> 当整个项目中的所有 Scene 都已经拥有 Shot，并且每个 Scene 都已经有 approved、not-stale 的 Production Plan 时，用户只点击一次，系统自动顺序制作所有 Scene，最后合成为一条完整影片。

---

# 一、本阶段的唯一核心流程

```text
Prepared Project
↓
ProjectProductionRun
↓
Scene 1
  └ SceneAutopilotRun
↓
Scene 2
  └ SceneAutopilotRun
↓
Scene 3
  └ SceneAutopilotRun
↓
……
↓
生成 Composition Manifest
↓
合成完整影片
↓
ProjectProductionRun completed
```

本阶段只负责：

```text
Scene Run orchestration
+
final composition
```

不要重新实现 Scene 内部：

* prompt generation
* image generation
* candidate selection
* continuity
* video generation
* video recovery

这些全部继续由 `SceneAutopilotManager` 负责。

---

# 二、本阶段仍然不要自动准备项目

AI-5A 只接受已经准备完成的项目。

以下 Scene 可以执行：

```text
Scene.shots 非空
+
存在 approved Production Plan
+
Production Plan not stale
```

如果项目中出现：

### Case A

```text
Scene 有 Shot
Plan approved
```

✓ ready。

### Case B

```text
Scene 有 Shot
没有 Plan / Plan 还是 draft
```

✕ 不 ready。

### Case C

```text
Scene 没有 Shot
```

✕ 不 ready。

本阶段不要自动调用：

* AI Director
* Production Planner

这些留到：

**Phase AI-5B：Auto Preparation**

---

# 三、为什么先做 Prepared Project

本阶段必须先证明：

```text
所有 Scene 都已经准备好
↓
一个按钮
↓
所有视频都自动生成
↓
完整影片自动合成
```

真正稳定。

不要同时混入：

```text
缺 Shot → AI Director
缺 Plan → Planner
```

否则 Project 层失败时很难判断究竟是：

* AI 规划失败
* Production Plan 失败
* Scene Executor 失败
* composition 失败

AI-5A 只做 Controller。

---

# 四、新增正式 ProjectProductionRun

现在正式增加一个项目级 Run。

用户面对的名称可以叫：

**完整制作任务**

代码建议：

```python
class ProjectProductionRun(BaseModel):
    run_id: str
    project_name: str

    status: str
    stage: str

    respect_locked: bool = True

    pause_requested: bool = False
    cancel_requested: bool = False

    current_scene_id: str | None = None
    current_child_run_id: str | None = None

    scenes: list[ProjectSceneExecutionState]

    source_snapshot_file: str
    plans_snapshot_file: str

    composition_manifest_file: str | None = None
    final_output: str | None = None

    error: str | None = None
    error_code: str | None = None

    created_at: float
    updated_at: float
```

status 建议：

```text
pending
running
pausing
paused
failed
blocked
composing
completed
cancelled
```

stage 可以：

```text
preflight
scenes
compose
done
```

不要建立复杂通用 Workflow framework。

---

# 五、每个 Scene 的父级执行状态

建议：

```python
class ProjectSceneExecutionState(BaseModel):
    scene_id: str
    index: int

    status: str

    plan_id: str
    child_run_id: str | None = None

    shot_count: int

    error: str | None = None
```

status：

```text
pending
running
paused
failed
blocked
completed
cancelled
```

不要复制：

```text
first frame
last frame
video
```

等 Shot checkpoint。

这些已经存在于：

`SceneAutopilotRun`

ProjectProductionRun 只需要知道：

> 这个 Scene child Run 到什么状态。

---

# 六、ProjectRun 与 SceneRun 必须建立父子关系

建议给当前：

```python
SceneAutopilotRun
```

增加一个向后兼容字段：

```python
parent_project_run_id: str | None = None
```

默认：

```python
None
```

手工点击：

```text
自动制作本故事段
```

产生：

```text
parent_project_run_id = None
```

Project Autopilot 创建的 child：

```text
parent_project_run_id = ProjectProductionRun.run_id
```

这样未来：

```text
完整版本 v1
↓
到底包含哪些 SceneRun
```

可以准确追溯。

---

# 七、parent-child 创建必须防 crash duplication

存在一个危险窗口：

```text
Project Executor
↓
创建 SceneAutopilotRun
↓
child run 文件已经保存
↓
ProjectProductionRun 还没来得及保存 child_run_id
↓
backend crash
```

恢复以后不能再创建第二个 child Run。

因此 SceneAutopilotManager 建议增加：

```python
find_by_parent_project_run(
    project_name,
    parent_project_run_id,
    scene_id,
)
```

Project Controller 创建 child 前：

```python
existing = scene_manager.find_by_parent_project_run(
    project,
    project_run_id,
    scene_id,
)

if existing:
    reuse existing
else:
    create new child
```

这和 AI-4B：

```text
autopilot_stage_key
```

防重复 submit 是同一个原则。

---

# 八、项目级 Run 保存位置

从本阶段开始，正式影片不要放：

```text
output/ai/
```

建议：

```text
projects/{project}/output/production_runs/{run_id}/
```

目录：

```text
run.json

source_snapshot.json

production_plans.json

composition_manifest.json

final.mp4
```

其中：

### run.json

高频更新：

```text
status
current scene
child run ids
errors
final path
```

### source_snapshot.json

启动时写一次。

### production_plans.json

启动时冻结所有 approved Plan。

### composition_manifest.json

所有 Scene 完成以后生成。

### final.mp4

本次完整影片。

不要每次更新 Run 时重写整个完整故事和角色 snapshot。

---

# 九、source_snapshot 是什么

这是以后“成片版本”的基础。

本阶段开始 ProjectProductionRun 时冻结：

## Project

```text
project_name
story_text
style
aspect_ratio
```

字段按当前真实 Project model 调整。

## Scenes

按正式顺序保存：

```text
scene_id
summary
characters
```

并保存当前 Shot 的 canonical 结构：

```text
shot_id
description
characters
target_duration_s
locked
```

不要保存：

* prompt
* frame candidate
* final video
* opening_source
* source_version
* JobQueue

这些是生成状态，不是创作母版。

## Characters

保存：

```text
name
description_en
views
```

`views` 不需要复制图片文件。

只记录当前项目引用中的：

```text
asset/version/url/path
```

以当前实际模型能够获取的信息为准。

---

# 十、为什么必须冻结 source_snapshot

例如：

ProjectRun #1 开始时：

```text
林夏 = 黑发版本
```

运行期间用户后来重新生成：

```text
林夏 = 白发版本
```

未来查看 Run #1 时必须知道：

> Run #1 开始制作时使用的是黑发版本。

同样：

```text
story_text
Scene.summary
Shot.description
project style
```

也必须可追溯。

本阶段不做复杂“创作版本” UI。

只把 snapshot 正确保存。

---

# 十一、Production Plan 也必须全部冻结

启动 ProjectProductionRun 时：

不要后续每进入一个 Scene 再去读取：

```text
production_plans.json
```

作为权威执行计划。

ProjectRun start 时一次性读取：

```text
Scene 1 approved plan
Scene 2 approved plan
Scene 3 approved plan
...
```

并保存到：

```text
production_plans.json
```

因此本次完整制作真正使用的是：

> Run 开始那一刻确认好的所有 Production Plan。

---

# 十二、SceneAutopilotRun 应支持从 frozen plan snapshot 启动

当前 SceneAutopilot start 很可能是：

```text
读取当前 production_plans.json
→ 找 approved plan
→ freeze 到 SceneRun
```

Project 层不要要求运行到 Scene 5 时：

> 当前 production_plans.json 必须仍然和三小时前一模一样。

更好的内部接口：

```python
async def start_scene_run_from_snapshot(
    project_name: str,
    scene_id: str,
    plan_snapshot: dict,
    *,
    respect_locked: bool,
    parent_project_run_id: str,
) -> SceneAutopilotRun:
    ...
```

这个方法不是公开给前端传 Plan。

只有 ProjectProductionManager 内部使用。

启动 child 时仍然验证：

```text
当前 Scene / Shot canonical structure
```

与 frozen Plan snapshot 相符。

如果不符：

ProjectRun blocked。

不要偷偷改用新的 Plan。

---

# 十三、Plan snapshot 仍然需要 Runtime validation

冻结 Plan 不等于无条件执行。

进入一个尚未启动的 Scene 前重新 load Project。

至少检查：

```text
Scene.summary
Scene.characters
Shot list/order
Shot.description
Shot.characters
target_duration_s
```

与 run 开始时 snapshot 一致。

如果变化：

```text
ProjectRun = blocked
error_code = SOURCE_STRUCTURE_CHANGED
```

提示：

```text
项目的故事段或镜头规划在完整制作期间发生了变化。

当前制作任务已安全停止。
请确认最新制作计划后重新开始一次完整制作。
```

不要自动：

* replan
* merge
* 覆盖用户修改

---

# 十四、Project source signature

建议实现：

```python
def build_project_source_signature(project) -> dict:
    ...
```

只包含真正影响完整 Run 一致性的创作输入：

```text
story_text
style
aspect_ratio

Scene:
  scene_id
  summary
  characters

Shot:
  shot_id
  description
  characters
  target_duration_s
  locked

Character:
  name
  description_en
  views identity/version/url
```

不要包含：

```text
prompt
frame batches
video versions
video_end_frame
opening_source
source_version
JobQueue
updated_at
```

因为这些字段会被 Autopilot 自己不断修改。

不能让自己的生成行为把 ProjectRun 判 stale。

---

# 十五、关于角色 views 变化

ProjectRun 中：

```text
角色 views
```

属于 source。

原因：

前 3 个 Scene 使用角色三视图 A，
后 3 个 Scene 如果突然使用三视图 B，
这个完整版本不再一致。

所以：

**尚未开始下一个 Scene 前**

如果发现：

```text
Character.description_en
或 Character.views
```

与 source snapshot 不一致：

ProjectRun blocked。

第一版不需要在当前正在执行的 Scene 中途强制杀任务。

当前 active SceneRun 允许安全到 checkpoint。

然后父 Run 不再进入下一 Scene。

---

# 十六、UI 在 ProjectRun active 时减少冲突

本阶段不需要大规模给所有 Router 增加“ProjectRun 全局写锁”。

但是前端应提供明显的运行状态：

```text
完整影片正在自动制作
```

在 ProjectRun running / pausing 时，尽量禁用或提示以下高风险入口：

* 编辑完整故事
* 修改 Scene
* AI 设计分镜
* AI 制作规划
* 删除 / 重排 Shot
* 修改角色 description
* 重新生成角色三视图

提示：

```text
当前项目正在完整自动制作。

修改故事、角色或镜头规划会使本次完整制作失效。
如需修改，请先暂停或停止当前任务。
```

不要为了这一点大规模重写全部 backend 权限系统。

后端仍依靠 source validation 做最终保护。

---

# 十七、Project Preflight 必须是正式后端能力

增加项目级 preflight。

建议：

```python
class ProjectProductionPreflight:
    ready: bool
    scene_count: int
    shot_count: int
    locked_shot_count: int

    scenes: list[ScenePreflightResult]
```

每个 Scene：

```python
class ScenePreflightResult:
    scene_id: str
    ready: bool

    shot_count: int
    plan_id: str | None
    plan_status: str | None

    issues: list[str]
```

可能 issue：

```text
NO_SHOTS
NO_PRODUCTION_PLAN
PLAN_NOT_APPROVED
PLAN_STALE
ACTIVE_SCENE_RUN
LOCKED_SHOT_NO_FINAL
LOCKED_SOURCE_NO_VIDEO_END
```

---

# 十八、Preflight 不要复制 SceneAutopilot 验证逻辑

AI-4B 已经有：

```text
Scene Run preflight
```

如果这些逻辑当前在 start 内：

建议提取成共享：

```python
def preflight_scene_autopilot(
    project,
    scene,
    plan,
    respect_locked=True,
) -> SceneRunPreflight:
    ...
```

然后：

### Scene 手工 Autopilot

复用。

### Project Preflight

对每一个 Scene 调用。

不要出现：

```text
Project Preflight 说 ready
↓
第 4 个 Scene 运行时
↓
Scene Autopilot 说 locked invalid
```

两层应使用同一个规则。

---

# 十九、Project start 时必须再次执行 Preflight

前端：

```text
GET preflight
```

只是用户查看。

真正：

```text
POST start
```

必须后端重新 load Project，再完整检查一次。

不能信任几秒前前端得到的 preflight。

start 流程：

```text
load Project
↓
检查没有 active ProjectRun
↓
project preflight
↓
所有 Scene ready
↓
冻结 source snapshot
↓
冻结所有 Plan snapshot
↓
创建 run directory
↓
写 run.json
↓
启动 Project Executor
```

---

# 二十、一个 Project 第一版只允许一个 active ProjectRun

active 定义：

```text
pending
running
pausing
paused
composing
```

再次点击 start：

返回：

```text
409
existing_run_id
```

前端：

```text
这个项目已经有完整制作任务。

[查看当前任务]
```

failed：

允许用户：

```text
继续执行
```

或者：

```text
开始新的完整制作
```

必须明确选择。

blocked：

建议只能：

```text
停止旧任务
→ 修复项目
→ 新建 Run
```

不要 resume blocked Run 后偷偷采用新的 source。

completed / cancelled：

不阻止新 Run。

---

# 二十一、ProjectProductionManager

建议新增：

```text
src/ai/project_autopilot.py
```

或：

```text
src/production/project_autopilot.py
```

根据当前项目目录风格选择。

主要结构：

```python
class ProjectProductionManager:

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

    async def _start_or_reuse_child(...):
        ...

    async def _wait_child(...):
        ...

    async def _build_composition_manifest(...):
        ...

    async def _compose(...):
        ...
```

不要把 Project Orchestration 塞进：

```text
src/ai/autopilot.py
```

Scene Executor 和 Project Controller 是不同层。

---

# 二十二、Project `_run_loop`

大致：

```python
async def _run_loop(self, run_id):

    run = self.load_run(run_id)

    run.status = "running"
    run.stage = "scenes"
    save(run)

    while True:

        if run.cancel_requested:
            await self._handle_cancel(run)
            return

        if run.pause_requested:
            await self._handle_pause(run)
            return

        entry = next_incomplete_scene(run)

        if not entry:
            break

        validate_source_for_scene(run, entry)

        child = await self._start_or_reuse_child(
            run,
            entry,
        )

        entry.child_run_id = child.run_id
        entry.status = child.status

        save(run)

        child = await self._wait_child(child.run_id)

        reconcile child status

        if child.completed:
            entry.completed
            save
            continue

        if child.paused:
            project paused
            return

        if child.failed:
            project failed
            return

        if child.blocked:
            project blocked
            return

        if child.cancelled:
            project cancelled
            return

    await self._compose_project(run)
```

根据实际异步结构调整。

---

# 二十三、Scene 顺序

严格按照：

```text
Project.scenes
```

当前 canonical 顺序。

不要根据：

```text
scene_id lexical sort
```

重新排序。

Run snapshot 保存启动时的 Scene 顺序。

以后即使 project.json 顺序变化：

当前 Run 不重新解释。

但是进入尚未执行 Scene 前发现 canonical order 被修改：

Project blocked。

---

# 二十四、第一版 Scene 严格串行

继续采用：

```text
Scene 1 complete
↓
Scene 2
↓
Scene 3
```

不要同时创建两个 active SceneAutopilotRun。

这样最终 Project 执行仍然非常容易理解：

```text
Project Controller
→ 一个 active SceneRun
→ SceneRun 内一个 active Shot
```

不要现在利用 Agnes outstanding concurrency。

---

# 二十五、为什么暂时不做跨 Scene continuity

Production Planner 当前明确：

```text
每个 Scene 第一 Shot = independent
```

保持不变。

Project Controller 不需要：

```text
Scene1 last Shot
→ Scene2 first Shot
```

建立 source。

以后单独实现：

**Cross-Scene Continuity Planning**

不要在 AI-5A 偷偷增加。

---

# 二十六、Project pause

点击：

```text
暂停完整制作
```

行为：

### 如果当前存在 active child SceneRun

```text
Project.pause_requested = True
Project.status = pausing

↓
SceneAutopilotManager.request_pause(child_run_id)

↓
当前 child active Agnes job 正常完成
↓
child paused
↓
Project paused
```

### 如果刚好处于 Scene 与 Scene 之间

立即：

```text
Project paused
```

不要创建下一 child。

---

# 二十七、Project Resume

Resume：

```text
同 ProjectProductionRun
同 source snapshot
同 plans snapshot
```

如果当前 child：

### paused

```text
SceneAutopilotManager.resume_run(child)
```

等待它继续。

### failed

允许 Project Resume 触发：

```text
SceneAutopilotManager.resume_run(child)
```

由 Scene 层决定当前失败阶段是否还能 retry。

### completed

不重新创建。

继续下一个 Scene。

### child_run_id 为空

创建当前待执行 Scene 的新 child。

---

# 二十八、Project cancel

点击：

```text
停止完整制作
```

行为：

如果 child active：

```text
request_cancel(child)
```

允许当前已经提交的 Agnes job 完成并保存。

child cancelled 后：

```text
ProjectRun cancelled
```

如果正处于 Scene 间：

立即 cancelled。

已经完成：

* SceneRun
* prompt
* 图片
* 视频

全部保留。

不要回滚。

---

# 二十九、Project failed 与 blocked 区分

例如：

### Agnes generation 多次失败

```text
SceneRun failed
↓
ProjectRun failed
```

可以 Resume。

### 用户修改 Shot structure

```text
SceneRun / Parent source validation blocked
↓
ProjectRun blocked
```

不应该 Resume 继续使用旧 source。

UI：

```text
完整制作已阻止

项目的故事、角色或镜头规划发生变化。
请停止本次任务并重新确认制作计划。
```

---

# 三十、Backend Restart Recovery

AI-4B 已经：

```text
recover SceneAutopilotRun
```

Project recovery 必须在正确顺序发生。

推荐 startup：

```text
1. JobQueue recovery
2. SceneAutopilotManager.recover_runs()
3. ProjectProductionManager.recover_runs()
```

原因：

Project Controller 恢复时需要看到 child Run 的正确状态。

---

# 三十一、Project Run recovery

扫描：

```text
projects/*/output/production_runs/*/run.json
```

status：

```text
running
pausing
composing
```

需要恢复。

### running + child_run_id 存在

查 child。

如果：

```text
running
```

继续等待。

如果：

```text
completed
```

reconcile parent，进入下一个 Scene。

如果：

```text
paused
```

parent → paused。

如果：

```text
failed
```

parent → failed。

### parent 没保存 child_run_id

通过：

```text
parent_project_run_id
+
scene_id
```

搜索 child。

找得到：

重新绑定。

找不到：

才创建。

不要因为 crash 创建两套 child Run。

---

# 三十二、Project Run 文件也需要并发写保护

继续使用：

```text
_atomic_write_json
```

并为：

```text
project_run_id
```

创建：

```python
asyncio.Lock
```

因为：

* project executor
* pause endpoint
* cancel endpoint
* recovery

可能同时修改 parent run。

---

# 三十三、所有 Scene 完成以后不要直接读取 Shot.final_video 合成

这是本阶段最重要的第二个不变量。

错误方式：

```python
for scene in project.scenes:
    for shot in scene.shots:
        clips.append(shot.current_final_video)
```

不要这样。

原因：

ProjectRun 必须准确知道：

> 这一次完整制作到底用了哪个 video version。

---

# 三十四、Composition Manifest

所有 Scene child Run completed 后：

根据每个 child `ShotExecutionState` 创建：

```python
class CompositionClip(BaseModel):
    scene_id: str
    shot_id: str

    scene_run_id: str

    video_version_id: str

    local_path: str | None
    url: str | None

    duration_s: float | None
```

manifest：

```python
class CompositionManifest(BaseModel):
    project_run_id: str
    clips: list[CompositionClip]
    created_at: float
```

顺序：

```text
Project snapshot scene order
↓
Scene snapshot shot order
```

---

# 三十五、locked Shot 也必须进入 Manifest

假设：

```text
respect_locked = True
```

一个 Shot 被 child 标：

```text
skipped_locked
```

child state 已记录：

```text
video_version_id = existing final
```

Composition Manifest 仍然记录这个精确版本。

因此一条完整成片可以是：

```text
Shot 1 → 本 Run 新视频
Shot 2 → 用户锁定的旧视频
Shot 3 → 本 Run 新视频
```

这是合理的。

---

# 三十六、Manifest 必须校验 Run consistency

构建 manifest 时：

每个 Shot：

### 普通自动生成

要求：

```text
state.video_version_id 存在
```

并且该 version：

```text
在 Project 当前 video history 中仍存在
```

### skipped_locked

要求：

```text
state.video_version_id
```

指向 preflight / child run 当时记录的版本。

不要重新读取：

```text
shot.final_video
```

覆盖它。

如果当前 final 已经被外部改成别的版本：

Manifest 仍然使用：

> child Run 记录的版本。

这就是 Run consistency。

---

# 三十七、Manifest 在 compose 前先落盘

顺序：

```text
所有 child completed
↓
build manifest
↓
validate every clip
↓
atomic save composition_manifest.json
↓
run.stage = compose
↓
开始 ffmpeg
```

Backend crash 后：

如果 manifest 已经存在：

Resume compose 不重新构建成另一套视频。

---

# 三十八、最终合成必须复用现有 composer

实现前先找到当前项目已有：

* export
* compose
* ffmpeg concat
* final video

相关逻辑。

如果已经存在稳定 composer：

请提取 / 扩展为：

```python
compose_video_versions(
    clips: list[...],
    output_path: Path,
)
```

让：

### 现有手工 export

继续正常调用。

### ProjectProductionRun

传入 Composition Manifest 中的精确 clip。

不要建立完全不同的第二套 FFmpeg 管线。

---

# 三十九、Composer 必须使用 Manifest 指定的视频

不要 composer 内部再次：

```text
load Project
→ current final_video
```

ProjectRun 已经明确传入：

```text
video_version_id / local path
```

Composer 只负责合成这些 clip。

---

# 四十、Agnes 原生音频

当前已经决定：

本项目暂时不重新加入 TTS / 自己的音频 workflow。

Agnes video 可能包含原生声音。

所以请检查当前 composer。

目标：

> 如果输入视频包含音轨，完整合成尽量保留它们。

不要为了 AI-5A 新增：

* 配音
* BGM
* 混音 Agent
* TTS
* 音效生成

只是不要无意义把 Agnes 原生 audio 全部丢掉。

---

# 四十一、音轨兼容性

实现前用 `ffprobe` / 当前 helper 确认现有 generated clips 的：

```text
video codec
audio codec
resolution
fps
audio stream presence
```

如果当前 composer 已能稳定处理：

直接复用。

如果当前 concat 假设：

> 每个 clip 都必定有 audio

但实际存在：

```text
clip A 有 audio
clip B 无 audio
```

需要做最小兼容：

> 无音轨 clip 在合成时补相同长度的 silence，避免 concat 失败。

不要扩大成完整音频系统。

---

# 四十二、最终输出

建议：

```text
projects/{p}/output/production_runs/{run_id}/final.mp4
```

成功后：

```text
run.final_output = final path
run.status = completed
run.stage = done
```

同时可保存：

```text
duration
file_size
```

如果容易取得。

不要覆盖：

```text
上一次 ProjectProductionRun/final.mp4
```

每一个 Run 都保留自己的 final。

---

# 四十三、Compose failure

如果：

```text
所有 Scene 都完成
↓
ffmpeg compose failed
```

不要把整个 Run 重新从 Scene 1 开始。

状态：

```text
failed
stage = compose
error_code = COMPOSE_FAILED
```

Resume：

```text
composition_manifest 已存在
↓
直接 retry compose
```

所有 Scene child Run 保持 completed。

---

# 四十四、Compose 不修改 Shot.final_video

Composition 只是读取。

不要：

* 更换任何 Shot final
* 删除视频版本
* 修改 continuity
* 修改 prompt

它是纯输出阶段。

---

# 四十五、ProjectRun 完成条件

必须：

```text
所有 Scene entries completed
+
Composition Manifest ready
+
final.mp4 compose success
```

才：

```text
ProjectRun completed
```

不能只因为所有 Agnes videos 完成就算完整影片 completed。

---

# 四十六、项目级 API

按当前 API 风格调整。

建议：

```text
GET
/api/project/{p}/production/preflight

POST
/api/project/{p}/production/start

GET
/api/project/{p}/production/runs

GET
/api/project/{p}/production/run/{run_id}

POST
/api/project/{p}/production/run/{run_id}/pause

POST
/api/project/{p}/production/run/{run_id}/resume

POST
/api/project/{p}/production/run/{run_id}/cancel
```

start body：

```json
{
  "respect_locked": true
}
```

不要让前端传：

* source snapshot
* production plans
* scene list
* run id
* video version

后端自己 load。

---

# 四十七、不要通过 HTTP 调 Scene Autopilot

ProjectProductionManager：

不要：

```python
httpx.post(
    "http://localhost:8000/.../autopilot/start"
)
```

应该直接调用：

```python
SceneAutopilotManager
```

内部 service API。

结构：

```text
ProjectProductionManager
        ↓
SceneAutopilotManager
        ↓
JobQueue
```

不是：

```text
ProjectProductionManager
↓ HTTP
Router
↓
SceneAutopilotManager
```

---

# 四十八、ProjectProductionManager 不直接碰 Agnes

项目 Controller 禁止直接调用：

```text
AgnesClient
```

也不要直接：

```text
enqueue image
enqueue video
```

它只管理：

```text
child SceneRun
+
compose
```

这条边界非常重要。

---

# 四十九、UI 入口

仍然不要新增顶级页面。

在：

```text
/storyboard
```

项目级顶部区域增加：

```text
[自动制作完整影片]
```

不要放在某个单独 Scene 卡内部。

它属于整个 Project。

---

# 五十、Preflight UI

点击：

```text
自动制作完整影片
```

先 GET preflight。

显示：

```text
完整影片自动制作

故事段        6
镜头         18

准备情况

✓ 故事段 1
  3 个镜头
  制作计划已确认

✓ 故事段 2
  2 个镜头
  制作计划已确认

✓ 故事段 3
  4 个镜头
  制作计划已确认

⚠ 故事段 4
  尚未确认制作计划

✕ 故事段 5
  尚未创建镜头
```

如果有任何不 ready：

不要显示“开始”。

显示：

```text
还有 2 个故事段没有准备完成。

本阶段完整制作只处理已经完成镜头规划和制作规划的项目。
```

可以让 Scene 行点击后跳到那个 Scene。

不要自动修复。

---

# 五十一、Ready 时的开始确认

全部 ready：

```text
完整影片自动制作

故事段        6
镜头         18

本次会为每个未锁定镜头生成新的：

✓ 提示词
✓ 首帧（计划需要时）
✓ 尾帧（计划需要时）
✓ 视频

图片候选将自动采用，
整个流程不会中途等待人工选择。

第一版采用安全的逐镜、逐故事段执行方式。

☑ 保留已锁定镜头

[取消]
[开始完整制作]
```

---

# 五十二、不要写“使用现有视频”

对于未锁定 Shot：

即使已有：

```text
首帧
尾帧
视频
```

ProjectProductionRun 仍然会启动：

```text
新的 SceneAutopilotRun
```

因此产生新的生成结果。

只有：

```text
locked Shot
+
respect_locked
```

才复用旧 final。

---

# 五十三、Project Run Monitor

启动后：

```text
完整影片自动制作

整体进度
9 / 18 镜头

✓ 故事段 1
  3 / 3

✓ 故事段 2
  2 / 2

⏳ 故事段 3
  2 / 4

○ 故事段 4
  0 / 3

○ 故事段 5
  0 / 4

○ 故事段 6
  0 / 2

当前：
故事段 3 · 镜头 2
视频生成中

[暂停]
[停止自动制作]
```

Project Monitor 不需要复制每个 Shot 的所有 stage。

如果用户想看细节：

```text
[查看当前故事段]
```

打开已有：

```text
AutopilotRunMonitor
```

或者定位到当前 Scene。

---

# 五十四、Overall progress 怎么算

不要只：

```text
completed scenes / total scenes
```

建议：

```text
completed_or_skipped_shots
/
total_shots
```

更符合用户实际等待感。

ProjectProductionRun 可以从 child Run 状态聚合。

不需要把所有 Shot checkpoint复制进 parent run。

---

# 五十五、Compose 状态 UI

所有 Scene 完成后：

```text
18 / 18 镜头完成

正在合成完整影片…
```

此时：

```text
status = composing
```

第一版在 composing 时可以禁用：

```text
暂停
```

因为本地 FFmpeg compose 通常远短于视频生成。

允许它完成。

如果 compose 失败：

```text
完整影片合成失败

所有镜头视频已经生成完成，
重新尝试只会重新合成，不会重新生成视频。

[重新合成]
```

`重新合成` 可以直接使用 Project Resume。

---

# 五十六、完成 UI

成功：

```text
完整影片制作完成

18 个镜头
6 个故事段

总时长 1:42

[播放完整影片]

[查看制作记录]

[重新制作一个版本]
```

其中：

```text
重新制作一个版本
```

现在只是：

> 再创建一个新的 ProjectProductionRun。

它仍然使用当前 canonical Shot + approved Plans。

不要在 AI-5A 重新运行 AI Director。

真正“每一版本连 Shot Plan 都不同”留到 AI-6。

---

# 五十七、制作记录

本阶段后端已经保存多个 Run。

UI 可以做一个很轻量的历史：

```text
完整制作记录

#3  生成中
#2  已完成 · 1:42
#1  已完成 · 1:38
```

不要现在做：

* 版本对比
* side-by-side
* 自动评分
* 选最佳版本

只要未来可进入即可。

如果 UI 工作量明显扩大，可以第一版只显示 latest Run。

但后端历史必须保留。

---

# 五十八、Project Run 不要覆盖旧 final

例如：

```text
Run A
output/production_runs/A/final.mp4

Run B
output/production_runs/B/final.mp4
```

两者同时保留。

不要只有：

```text
output/final_video.mp4
```

不断被覆盖。

如果当前项目已有一个“导出最终视频”的快捷字段，可以让它指向 latest completed Run，但不要删除历史文件。

---

# 五十九、Run source snapshot 现在就是未来“成片版本”的基础

请注意本阶段虽不做 AI-6，但当前数据结构必须支持未来：

```text
Production Run v1
Production Run v2
Production Run v3
```

每一版都能回答：

```text
使用的故事是什么？
使用的 Scene 是什么？
使用的角色形象是什么？
使用的 Production Plan 是什么？
使用的 SceneRun 是什么？
使用的每个 video_version 是什么？
最终 final.mp4 在哪里？
```

如果现在这些信息都能追溯，未来 AI-6 会非常轻松。

---

# 六十、当前 Run 不等于 canonical Project

ProductionRun 是：

> 一次生产记录。

不要把：

```text
source snapshot
composition manifest
ProjectRun status
```

塞进 `project.json`。

`project.json` 继续做 canonical current project。

Production Run 独立保存。

---

# 六十一、本阶段不要实现 AI-5B

再次明确，不要自动：

### Scene 无 Shot

```text
AI Director
```

### Scene 没 Plan

```text
Production Planner
```

现在遇到这些情况只：

```text
Preflight failed
```

这是下一小阶段的目标。

---

# 六十二、本阶段不要实现 Production Variant

不要增加：

```text
生成完整影片数量
[5]
```

AI-5A 一次只创建：

```text
1 个 ProjectProductionRun
```

否则还没有解决：

> 下一版本是否重新生成 Shot 结构。

---

# 六十三、本阶段不要重新运行 AI Director

这是必须明确的。

AI-5A 的完整 Run：

```text
Canonical Shot structure 固定
↓
每次生成新的 prompt / frames / video
```

但是 Shot.description / Shot 数量不重新规划。

因此：

```text
Run #1
和
Run #2
```

这一阶段差异来自：

* Agnes prompt generation 非确定性
* image generation
* seeded candidate selection
* video generation

而不是 Shot Plan 差异。

真正 Shot Plan variants 留 AI-6。

---

# 六十四、未来为什么还要 AI-6

用户最终希望：

```text
同一个完整故事

版本1
→ Shot Plan A
→ frames A
→ videos A

版本2
→ Shot Plan B
→ frames B
→ videos B
```

当前正式：

```text
Scene.shots
```

只有一套 canonical Shot。

所以不能现在简单让 AI Director覆盖它。

AI-6 会正式设计：

```text
Run-scoped Shot Plan
```

不要在 AI-5A提前解决。

---

# 六十五、当前 source snapshot 与未来 run-scoped Shot Plan 不冲突

AI-5A：

```text
source_snapshot
→ canonical Scene/Shot snapshot
```

AI-6 以后：

可以增加：

```text
creative_plan_snapshot
```

或者：

```text
run_storyboard
```

现有 ProjectRun 结构仍然可以沿用。

所以当前不要把数据结构写死成：

```text
run 永远引用 project 当前 Shot
```

它应该已经拥有：

> Run 开始时的 Shot snapshot。

---

# 六十六、后端代码建议

建议新增：

```text
src/ai/project_autopilot.py
routers/project_autopilot.py
```

或者如果你认为 `src/production/` 更符合现有结构，可以使用：

```text
src/production/project_run.py
```

重点是职责分开：

```text
ProductionPlanner
   → 规划一个 Scene 怎么生成

SceneAutopilotManager
   → 执行一个 Scene

ProjectProductionManager
   → 顺序执行多个 Scene + compose
```

不要创建一个万能：

```text
AgentManager
```

---

# 六十七、前端组件

建议新增：

```text
ProjectProductionStudio.vue
```

或者：

```text
ProjectAutopilotMonitor.vue
```

可以同一个大型 Overlay 处理：

```text
preflight
confirm
running
completed
failed
```

不需要为每一步再开一个 Modal。

视觉继续沿用：

* DirectorStudio
* ProductionPlannerStudio
* AutopilotRunMonitor

建立统一“制作工作区”语言。

---

# 六十八、前端 API 类型

建议：

```ts
interface ProjectProductionPreflight
interface ProjectScenePreflight
interface ProjectProductionRun
interface ProjectProductionSceneState
interface CompositionManifest
```

Project Monitor 不需要引入所有：

```text
ShotExecutionState
```

详情可以继续用当前 scene autopilot API。

---

# 六十九、实现时优先复用现有 Run manager

需要特别检查：

SceneAutopilotManager 当前是否已经有：

```text
start
resume
pause
cancel
load_run
find active
```

Project Manager 直接使用。

如果缺少：

```text
internal start from frozen plan
find child by parent id
```

只补这两个最小能力。

不要为了 Project Controller 重构 AI-4B 整个 Executor。

---

# 七十、启动前先检查现有 composer

真正编码前先回答：

1. 当前最终视频 export/composer 在哪里？
2. 它如何查 Shot final video？
3. 是否允许传显式 clip paths？
4. 是否保留 Agnes 原生 audio？
5. 输入 clip 无 audio 时会不会失败？
6. 输出路径现在是否固定？
7. ffmpeg failure 如何返回？
8. 是否已经有 duration probe/helper？

根据真实代码做最小复用。

不要机械新增第二套 composer。

---

# 七十一、必要的验证

不要把本阶段主要时间花在大量单元测试。

至少覆盖这些关键行为。

### Case 1：完整 prepared project

```text
3 Scene
每 Scene 2 Shot
所有 Plan approved
```

启动：

```text
ProjectRun
```

最终：

```text
6 Shot 自动生成
3 Scene child completed
composition manifest = 6 clips
final.mp4 存在
ProjectRun completed
```

---

### Case 2：缺 Production Plan

其中 Scene 2：

```text
Plan missing
```

Preflight：

```text
ready = false
```

ProjectRun 不创建。

---

### Case 3：Project pause / resume

Scene 2 执行中暂停。

当前 active child 安全 paused。

Project paused。

Resume 后：

```text
同 child run
```

继续。

不重新运行 Scene 1。

---

### Case 4：Backend restart

ProjectRun 在 Scene 2 期间 restart。

恢复后：

* 不重新创建 Scene 1 child
* 不重复创建 Scene 2 child
* 继续等待 / 恢复 Scene 2
* 之后 Scene 3
* 最终 compose

---

### Case 5：Compose failure

人工让第一次 compose fail。

ProjectRun：

```text
failed
stage=compose
```

Resume：

只重新 compose。

不要重新生成任何视频。

---

### Case 6：Run consistency

某 Shot 在 child completed 后，手工把：

```text
shot.final_video
```

改到另一个旧版本。

最终 composition manifest：

仍然使用：

```text
child ShotExecutionState.video_version_id
```

而不是后来被修改的 current final。

这个测试非常重要。

---

### Case 7：Source changed

ProjectRun 完成 Scene 1 后，

修改：

```text
Scene 3.description / Character.views / Project style
```

进入 Scene 2/3 前检测到 source signature 变化。

ProjectRun：

```text
blocked
```

不要继续生成混合版本。

---

# 七十二、真实 E2E 最好只做一个很小项目

为了节省时间：

准备：

```text
2 Scene
每 Scene 1–2 Shot
```

全部：

```text
independent+i2v
```

先验证：

```text
Project start
↓
SceneRun A completed
↓
SceneRun B completed
↓
manifest
↓
final.mp4
```

这一条是真正核心。

reference/inherit 已经由 AI-4B 负责，不需要 Project 层重新证明一遍。

如果你现有测试项目里已经有 continuity ready 的 Scene，可以额外跑，不是本阶段硬要求。

---

# 七十三、完成后重点汇报

完成后不用给我大量普通测试细节。

重点告诉我：

1. ProjectProductionRun 最终结构
2. 为什么 ProjectRun 与 SceneAutopilotRun 分层
3. source_snapshot 具体保存了什么
4. Production Plan 如何在 ProjectRun start 时冻结
5. child SceneRun 如何从 frozen Plan 启动
6. parent-child 如何避免 backend crash 创建重复 child
7. Project Preflight 如何复用 Scene Autopilot preflight
8. Scene 顺序怎么确定
9. Project pause/resume/cancel 如何传递到 child
10. backend restart 恢复顺序
11. Composition Manifest 如何构建
12. 为什么 compose 不读取当前 Shot.final_video
13. locked Shot 如何进入 Manifest
14. final.mp4 保存在哪里
15. Agnes 原生 audio 是否被保留
16. compose failure 如何只重试 compose
17. source / character views 中途变化如何阻止混合版本
18. 修改了哪些文件
19. 实际 E2E 是否完成：
    Project → 多 SceneRun → manifest → final.mp4
20. 哪些能力明确留给 AI-5B / AI-6
