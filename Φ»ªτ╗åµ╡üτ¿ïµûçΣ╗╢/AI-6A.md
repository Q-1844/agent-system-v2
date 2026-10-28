

下面是完整执行文档。

---

# Phase AI-6A：Project Revision Foundation

## Creative Workspace · Semantic ChangeSet · Impact Analysis · Visual Diff

### 本阶段定位

AI-6A 是未来：

* Production Variant
* Run-scoped Storyboard
* 全局项目 Agent
* 自然语言批量修改
* 版本比较
* 局部重新生产

共同依赖的底层项目修改系统。

本阶段**不调用 Agnes，不生成图片，不生成视频，也不实现聊天 Agent。**

本阶段首先证明：

```text
正式项目
↓
创建实验工作区
↓
在工作区修改 Scene / Shot
↓
修改被记录成结构化 ChangeSet
↓
系统计算影响范围
↓
用户能够可视化看到 Before / After
↓
正式项目完全不受影响
```

---

# 一、核心架构：从现在开始正式区分三层

未来整个系统使用三个明确概念。

```text
Canonical Project
正式项目
        │
        │ create workspace
        ▼
Creative Workspace
实验创作版本
        │
        │ production
        ▼
Production Run
实际生成版本
```

三者绝不能混成一个“版本”概念。

---

## 1. Canonical Project

继续是当前：

```text
project.json
```

代表：

> 用户目前认可的正式创作版本。

包括：

```text
story_text
characters
scenes
shots
style
当前生成历史
```

AI-6A 不改变它的现有职责。

---

## 2. Creative Workspace

代表：

> 基于当前正式项目进行的一次安全实验。

例如：

```text
动作更紧凑版
林夏戏份加强版
第二幕悬疑版
```

Workspace 可以：

* 修改 Scene
* 修改 Shot
* 新增 Shot
* 删除 Shot
* 重排 Shot

但：

**Canonical Project 不变化。**

---

## 3. Production Run

代表：

> 对某一个确定创作版本进行的一次实际生成。

以后：

```text
Workspace A
├─ Production Run 1
├─ Production Run 2
└─ Production Run 3
```

同一套创意结构仍然可以生成多个不同结果。

AI-6A 暂时不让 Workspace 进入 Production。

这留给 AI-6C。

---

# 二、本阶段最重要的不变量

必须始终满足：

```text
修改 Workspace
≠
修改 canonical project
```

例如正式项目：

```text
Scene 3
├─ Shot A
├─ Shot B
└─ Shot C
```

Workspace：

```text
Scene 3
├─ Shot A
├─ Shot D
├─ Shot B
└─ Shot C
```

此时：

```text
project.json
```

仍然必须：

```text
A B C
```

只有：

```text
workspace working snapshot
```

是：

```text
A D B C
```

---

# 三、第一版不要做“覆盖正式项目”

AI-6A 不实现：

```text
[设为正式项目]
```

不实现：

```text
merge workspace → project.json
```

也不实现：

```text
rebase
three-way merge
冲突合并
```

当前 Workspace 是：

> 实验分支。

可以：

```text
创建
修改
查看 Diff
继续修改
归档
删除
```

但不能回写 canonical。

以后 Agent 稳定后再做：

```text
Promote Workspace
```

那会是一个独立阶段。

---

# 四、存储结构

建议将 Workspace 提升为 Project 的一等结构，不放进 `output/ai`。

例如：

```text
projects/{project}/
│
├─ project.json
│
├─ output/
│
└─ workspaces/
   │
   ├─ ws_a83f.../
   │  ├─ workspace.json
   │  ├─ base_project.json
   │  ├─ working_project.json
   │  └─ changesets/
   │     ├─ cs_001.json
   │     └─ cs_002.json
   │
   └─ ws_b91d.../
```

---

## `base_project.json`

Workspace 创建瞬间的 Project 快照。

此后永不修改。

它回答：

> 这个实验版本最初是从什么状态开始的？

---

## `working_project.json`

Workspace 当前实际状态。

用户所有修改都作用这里。

---

## 不复制图片和视频文件

`base_project.json` 和 `working_project.json` 可以继续保存当前 Project model 中的：

```text
local
url
version
asset ref
```

但是：

```text
PNG
MP4
```

文件本身仍然共用现有文件。

不要复制：

```text
workspace/xxx/front.png
workspace/xxx/video.mp4
```

AI-6A 是结构版本，不是资产 duplication 系统。

---

# 五、Workspace 数据结构

建议：

```python
class ProjectWorkspace(BaseModel):
    workspace_id: str
    project_name: str

    name: str

    status: str
    revision: int

    base_project_signature: str
    working_project_signature: str

    base_project_file: str
    working_project_file: str

    created_at: float
    updated_at: float

    archived_at: float | None = None
```

status 第一版：

```text
active
archived
```

不要把：

```text
stale
```

直接塞进 status。

因为：

> Workspace 自己仍然是正常的，只是它所基于的 canonical project 后来变了。

建议 GET 时派生：

```python
base_is_stale: bool
```

---

# 六、Canonical 变化后 Workspace 怎么办

例如：

```text
T1
正式项目
→ 创建 Workspace A

T2
用户回正式项目
→ 修改 Scene 2

T3
重新打开 Workspace A
```

此时：

```text
Workspace A
```

仍然完全可以查看、编辑。

但是 UI 显示：

```text
⚠ 正式项目在这个实验版本创建后发生过变化。

当前实验版本仍基于旧的正式项目。
```

不要自动：

```text
rebase
```

不要偷偷把最新 canonical 合进来。

AI-6A 只负责检测。

---

# 七、ProjectSourceRef

这一层建议现在就建立。

新增类似：

```python
from typing import Literal
from pydantic import BaseModel


class ProjectSourceRef(BaseModel):
    project_name: str

    kind: Literal[
        "canonical",
        "workspace",
    ] = "canonical"

    workspace_id: str | None = None
```

---

## Canonical

```python
ProjectSourceRef(
    project_name="demo",
    kind="canonical",
)
```

---

## Workspace

```python
ProjectSourceRef(
    project_name="demo",
    kind="workspace",
    workspace_id="ws_xxx",
)
```

---

# 八、ProjectSourceStore

增加一个很薄的统一读取层。

例如：

```python
class ProjectSourceStore:

    def __init__(
        self,
        project_store,
        workspace_store,
    ):
        self.project_store = project_store
        self.workspace_store = workspace_store

    def load(
        self,
        ref: ProjectSourceRef,
    ) -> Project:

        if ref.kind == "canonical":
            return self.project_store.load(
                ref.project_name
            )

        if ref.kind == "workspace":
            if not ref.workspace_id:
                raise ValueError(
                    "workspace_id required"
                )

            return (
                self.workspace_store
                .load_working_project(
                    ref.project_name,
                    ref.workspace_id,
                )
            )

        raise ValueError(
            f"Unknown source kind: {ref.kind}"
        )
```

注意：

**本阶段不要重构整个项目全部改用 ProjectSourceStore。**

先建立基础能力。

AI-6B 开始逐步迁移：

```text
Director
Planner
```

AI-6C 再迁移：

```text
Production
```

不要现在大爆炸式修改所有 Router。

---

# 九、ProjectSourceStore 第一版只提供统一读取

不要现在提供：

```python
source_store.save(ref, project)
```

这种任意保存能力。

原因：

canonical 的写入规则和 workspace 的写入规则完全不同。

Workspace 必须通过：

```text
ChangeSet
```

修改。

所以第一版：

```python
load()
```

统一。

写操作仍然：

```text
Canonical → 原有业务 API

Workspace → ChangeSetManager
```

这样未来 Agent 不可能绕过 Revision Layer。

---

# 十、真正核心：Semantic ChangeSet

未来 Agent 的修改不能是：

```json
[
  {
    "op": "replace",
    "path": "/scenes/2/shots/1/description",
    "value": "..."
  }
]
```

不要使用 raw JSON Patch 作为业务协议。

原因：

```text
数组 reorder
路径漂移
Agent 难理解
用户难展示
Impact 难分析
```

我们需要：

> 语义修改操作。

---

# 十一、ChangeSet 数据结构

建议：

```python
class WorkspaceChangeSet(BaseModel):
    changeset_id: str
    workspace_id: str
    project_name: str

    title: str = ""
    intent: str = ""

    actor: str

    base_workspace_revision: int

    status: str

    operations: list[ChangeOperation]

    before_signature: str
    after_signature: str | None = None

    impact_report: dict | None = None

    created_at: float
    applied_at: float | None = None
```

actor 第一版：

```text
user
system
```

未来直接支持：

```text
agent
```

因此现在就允许：

```python
actor: Literal[
    "user",
    "system",
    "agent",
]
```

只是 AI-6A 暂时不会产生 agent changeset。

---

# 十二、ChangeSet 生命周期

建议非常简单：

```text
draft
previewed
ready_to_apply
applied
rejected
conflict
```

正常流程：

```text
创建 Operations
↓
Preview
↓
Visual Diff + Impact
↓
Apply
↓
working_project.json 改变
```

不要：

```text
点一下表单
→ 立刻修改 working_project
```

第一版就把 Preview / Apply 建好。

这就是未来 Agent：

```text
proposal
→ preview
→ approve
```

的基础。

---

# 十三、第一版 Semantic Operations

不要一上来实现二十种操作。

AI-6A 聚焦：

```text
Scene
Shot
```

建议第一版支持五类。

---

## 1. `UpdateScene`

```python
class UpdateSceneOperation(BaseModel):
    type: Literal["update_scene"]

    scene_id: str

    summary_before: str | None = None
    summary_after: str | None = None

    characters_before: list[str] | None = None
    characters_after: list[str] | None = None
```

只有实际修改的字段需要存在。

---

## 2. `UpdateShot`

建议不要给每个 Shot 字段写一个独立 operation。

使用语义 field changes。

```python
class FieldChange(BaseModel):
    before: object
    after: object


class UpdateShotOperation(BaseModel):
    type: Literal["update_shot"]

    scene_id: str
    shot_id: str

    description: FieldChange | None = None

    characters: FieldChange | None = None

    target_duration_s: FieldChange | None = None
```

AI-6A 第一版只允许修改：

```text
description
characters
target_duration_s
```

不要允许 ChangeSet 直接改：

```text
prompt_en
last_frame_prompt
video_prompt
first_frame
last_frame
final_video
source_version
```

这些是生成层。

---

# 十四、InsertShot

```python
class InsertShotOperation(BaseModel):
    type: Literal["insert_shot"]

    scene_id: str

    after_shot_id: str | None

    proposed_shot_id: str

    description: str
    characters: list[str]
    target_duration_s: float | None
```

如果：

```text
after_shot_id = None
```

表示：

插入到 Scene 第一个。

---

# 十五、DeleteShot

```python
class DeleteShotOperation(BaseModel):
    type: Literal["delete_shot"]

    scene_id: str
    shot_id: str

    before: dict
```

`before` 保存完整的：

```text
shot_id
description
characters
target_duration_s
```

不用把所有生成历史塞进 ChangeSet。

working snapshot 中原来的历史仍存在于 base snapshot。

ChangeSet 只描述结构删除。

---

# 十六、ReorderShots

```python
class ReorderShotsOperation(BaseModel):
    type: Literal["reorder_shots"]

    scene_id: str

    before_order: list[str]
    after_order: list[str]
```

必须验证：

```text
before / after
```

包含完全相同的 Shot ID 集合。

不能借 reorder 偷偷增加或删除 Shot。

---

# 十七、Shot ID 是这一阶段的一个关键难点

当前系统的 Shot ID 类似：

```text
s03_c01
s03_c02
s03_c03
```

它看起来包含“序号”。

但是一旦开始：

```text
insert
delete
reorder
```

绝对不要为了保持：

```text
c01 c02 c03
```

而每次把所有既有 Shot ID 重命名。

否则：

```text
ChangeSet
Impact
Agent reference
Production lineage
历史视频
```

全部不稳定。

---

# 十八、从 AI-6A 开始明确：

> **Shot ID 是身份，不是当前位置。**

真正顺序：

```text
Scene.shots 数组顺序
```

决定。

例如：

```text
[
  s03_c01,
  s03_c04,
  s03_c02,
  s03_c03
]
```

完全合法。

UI 显示：

```text
镜头 1
镜头 2
镜头 3
镜头 4
```

继续根据数组 index。

不要把：

```text
shot_id suffix
```

当 UI 序号。

---

# 十九、实现前必须检查 shot_id 的隐含假设

请全局检查实际代码有没有：

```python
sorted(shots, key=shot_id)
```

或者：

```python
int(shot_id.split("_c")[1])
```

用于真实执行顺序。

如果有：

需要逐个判断。

原则：

```text
Project / Workspace 中 Scene.shots 数组
```

才是顺序权威。

`shot_id` 只用于身份引用。

不要在本阶段大规模重命名已有 Shot。

---

# 二十、新插入 Shot ID 如何生成

不要复用：

```text
当前 index + 1
```

然后把所有后续重新编号。

建议仍然兼容现有格式：

读取这个 Scene 历史存在的：

```text
_cNN
```

最大 sequence。

例如：

```text
已有：
c01
c02
c03
```

插入一个：

```text
c04
```

即使它位于数组第二。

再插：

```text
c05
```

例如：

```python
def allocate_workspace_shot_id(scene):
    used = set()

    for shot in scene.shots:
        seq = parse_existing_sequence(
            shot.shot_id
        )
        if seq is not None:
            used.add(seq)

    next_seq = (
        max(used) + 1
        if used
        else 1
    )

    while True:
        candidate = (
            f"{scene.scene_id}_c"
            f"{next_seq:02d}"
        )

        if candidate not in {
            s.shot_id for s in scene.shots
        }:
            return candidate

        next_seq += 1
```

---

# 二十一、Preview 时就分配 Shot ID

不要：

```text
Preview 显示 temp_1

Apply 后变 c04
```

否则 Visual Diff 与最终结果不一致。

ChangeSet 创建 / Preview 时分配：

```text
proposed_shot_id
```

并保存。

Apply 使用同一个 ID。

---

# 二十二、Changeset Preview

这是未来 Agent 最重要的 API 之一。

建议：

```python
async def preview_changeset(
    workspace_id,
    operations,
):
```

流程：

```text
load Workspace
↓
load working_project
↓
确认 workspace revision
↓
复制 Project 到 memory
↓
对 clone 应用 operations
↓
validate final structure
↓
build structural diff
↓
ImpactEngine.analyze
↓
保存 changeset preview
↓
返回 Visual Diff 数据
```

关键：

**Preview 不修改 working_project.json。**

---

# 二十三、Operations 应该一次应用到 Clone 再整体验证

不要：

```text
operation 1
验证
operation 2
验证
```

导致一组本来合法的联合修改被中间状态阻止。

例如：

```text
1. Scene 移除 林夏
2. Shot 同时移除 林夏
```

单独执行第一步时可能暂时不一致。

所以：

```python
candidate = deepcopy(working)

for op in operations:
    apply_op(candidate, op)

validate_final_project(candidate)
```

最终状态才是主要验证对象。

---

# 二十四、Hard Validation 和 Soft Validation 分开

Workspace 是实验空间。

不要让它像 canonical Production 那么死。

---

## Hard errors

真正不能保存：

```text
Scene 不存在
Shot 不存在
重复 Shot ID
Reorder 缺失 Shot
Shot.characters 引用了 Scene.characters 外角色
引用不存在 Project Character
非法 duration
```

这些：

```text
Preview failed
```

---

## Soft warnings

允许 Workspace 保留：

例如：

```text
Scene.characters 中 林夏
但当前没有任何 Shot 使用林夏
```

不要强制阻止。

返回：

```text
CHARACTER_NOT_COVERED
```

warning。

Production 前再由：

```text
Director / Preflight
```

要求修复。

因为 Workspace 的意义就是允许用户分多步修改。

---

# 二十五、before 字段必须严格验证

例如 ChangeSet：

```text
Shot s03_c02
duration:
before = 8
after = 5
```

Apply 时当前值已经是：

```text
6
```

就不能继续。

返回：

```text
CHANGESET_CONFLICT
```

这就是 optimistic concurrency。

不要：

```text
无视 before
直接写 5
```

未来 Agent 多轮修改时非常危险。

---

# 二十六、workspace revision

Workspace 增加：

```python
revision: int
```

创建：

```text
revision = 0
```

每成功 Apply 一个 ChangeSet：

```text
revision += 1
```

ChangeSet 保存：

```text
base_workspace_revision
```

Apply 必须：

```python
if (
    changeset.base_workspace_revision
    != workspace.revision
):
    raise WorkspaceConflict(...)
```

这样避免：

```text
Agent proposal A
User 修改 B
再 Apply 旧 proposal A
```

覆盖新状态。

---

# 二十七、Apply ChangeSet 的 crash safety

这里不要只：

```python
save working
save changeset
```

因为两文件之间可能 crash。

使用和我们之前 Run 系统一样的：

> Write-ahead + reconciliation。

流程：

```text
Preview complete
↓
ChangeSet.status = ready_to_apply
before_signature
after_signature
↓
保存 ChangeSet
↓
atomic write working_project.json
↓
workspace.revision += 1
workspace working signature 更新
↓
changeset.status = applied
```

---

# 二十八、危险 Crash Window

可能：

```text
working_project.json 已经成功修改
↓
进程 crash
↓
ChangeSet 还显示 ready_to_apply
```

恢复时不能重新 Apply。

---

# 二十九、Changeset reconciliation

Workspace 加载时检查：

```text
ready_to_apply
```

ChangeSet。

例如：

```python
def reconcile_pending_changeset(
    workspace,
    changeset,
):
    working = load_working_project()

    current_sig = signature(working)

    if current_sig == changeset.after_signature:
        # side effect 已经完成
        changeset.status = "applied"

        if workspace.revision == (
            changeset.base_workspace_revision
        ):
            workspace.revision += 1

        save metadata
        return

    if current_sig == changeset.before_signature:
        # side effect 尚未发生
        return

    changeset.status = "conflict"

    raise WorkspaceCorruptionOrConflict(...)
```

不要重复写项目。

---

# 三十、Workspace 文件写入

继续复用：

```text
_atomic_write_json
```

Windows PermissionError retry。

每个：

```text
workspace_id
```

一个：

```python
asyncio.Lock
```

避免：

```text
preview/apply/delete
```

同时写。

---

# 三十一、Impact Engine

新增独立：

```text
src/revision/impact.py
```

或者根据当前代码目录：

```text
src/core/revision/impact.py
```

不要把 Impact Rules 塞在 Vue。

它必须是确定性后端规则。

未来 Agent 只能：

```text
读取 Impact Report
```

不能自己决定：

> 哪些东西失效。

---

# 三十二、ImpactReport 建议结构

```python
class ImpactReport(BaseModel):

    affected_scene_ids: list[str]
    affected_shot_ids: list[str]

    production_plan_scene_ids: list[str]

    prompt_rebuild_shot_ids: list[str]
    frame_regeneration_shot_ids: list[str]
    video_regeneration_shot_ids: list[str]

    continuity_review_shot_ids: list[str]

    review_recommended_scene_ids: list[str]

    warnings: list[ImpactWarning]

    summary: ImpactSummary
```

---

# 三十三、ImpactSummary

例如：

```python
class ImpactSummary(BaseModel):
    scenes_changed: int = 0
    shots_added: int = 0
    shots_deleted: int = 0
    shots_modified: int = 0
    shots_moved: int = 0

    plans_to_rebuild: int = 0

    shots_recommended_for_regeneration: int = 0
```

未来 UI：

```text
影响：

故事段      2
镜头        7
制作规划    2
建议重制    5
```

---

# 三十四、最重要原则：Impact 不删除历史资产

永远不要：

```text
Shot description changed
↓
delete old image
delete old video
```

旧资产仍然是真实历史。

Impact Engine 只说：

```text
新的 Project Source 与旧生产结果不再完全匹配。
```

例如：

```text
建议重新生成
```

不是：

```text
旧视频无效并删除
```

这与现有 version/history 设计保持一致。

---

# 三十五、UpdateShot.description Impact

当前系统 Prompt Context 会读取：

```text
当前 Shot
上一 Shot
下一 Shot
```

因此修改：

```text
s03_c02.description
```

影响不只当前 prompt。

推荐规则：

```text
Production Plan:
整个 Scene 需要重新检查

Prompt Context:
s03_c01
s03_c02
s03_c03
受到影响

Generation:
s03_c02 建议重新生成

Continuity:
s03_c03 建议检查
```

原因：

```text
邻接上下文发生变化
```

但不要自动说：

```text
s03_c01 视频必须重做
```

它只是 prompt context 受到影响。

---

# 三十六、UpdateShot.characters Impact

修改本镜角色：

```text
Production Plan:
Scene 需要重新规划

Prompt:
当前 + 相邻上下文受到影响

Frames:
当前 Shot 建议重制

Video:
当前 Shot 建议重制

Continuity:
下一 Shot 建议检查
```

如果：

```text
characters_after
```

包含 Scene 外角色：

Hard validation error。

---

# 三十七、target_duration_s Impact

如果只改：

```text
8s → 5s
```

至少影响：

```text
Production Plan
video prompt
video generation
```

由于 Planner：

```text
i2v / keyframes
```

判断可能受到 duration 影响，

所以：

```text
production plan
```

必须重新检查。

第一版可以保守标：

```text
current Shot prompt bundle affected
```

不要为了微优化建立过度细碎规则。

---

# 三十八、InsertShot Impact

新增 Shot：

```text
Scene Production Plan
→ 必须重新规划

新 Shot
→ 必须生成

前一个 / 后一个 Shot
→ prompt context affected

从插入位置开始
→ continuity chain review
```

例如：

```text
A
B
C
```

变：

```text
A
D
B
C
```

原来：

```text
A → B
```

的 continuity 已经不再是相邻关系。

因此：

```text
D
B
```

至少需要新的 continuity planning。

---

# 三十九、DeleteShot Impact

```text
A
B
C
```

删除：

```text
B
```

变：

```text
A
C
```

则：

```text
Production Plan stale

A / C
prompt adjacency changed

C
continuity relationship changed
```

旧 B 的资产：

```text
仍然保存在 canonical/base history
```

Workspace working snapshot 里不再有这个 Shot。

Visual Diff 必须能看到：

```text
− Shot B
```

---

# 四十、Reorder Impact

例如：

```text
A B C D
```

变：

```text
A C B D
```

从第一个变化位置：

```text
B / C
```

之后：

```text
adjacency
continuity
prompt context
```

都可能变化。

Production Plan：

```text
整个 Scene
```

重新规划。

但：

```text
Shot B 自己过去的视频文件
```

仍然是历史资产。

不删除。

---

# 四十一、UpdateScene.summary Impact

Scene.summary 是所有 Shot 的上游故事上下文。

因此修改：

```text
Scene summary
```

至少：

```text
Production Plan stale

所有 Shot：
prompt context affected

所有 Shot：
generation review recommended
```

但是不要：

```text
自动删除 / 重新设计所有 Shot
```

Impact Report 可以：

```text
review_recommended_scene_ids
```

提示：

> 故事段内容变化较大，建议重新检查现有分镜是否仍然适合。

---

# 四十二、UpdateScene.characters Impact

如果角色集合改变：

```text
Production Plan stale
```

所有 Shot：

```text
role compatibility review
```

如果某个 Shot 使用已经从 Scene.characters 删除的角色：

Hard final validation 可以阻止这次 ChangeSet，

除非同一个 Changeset 中也修改这些 Shot。

---

# 四十三、Visual Diff 后端数据

不要让 Vue 自己比较两个巨大 JSON。

后端提供：

```python
build_workspace_diff(
    base_project,
    working_project,
)
```

返回结构化 Diff。

例如：

```python
class WorkspaceDiff(BaseModel):
    scenes: list[SceneDiff]
    summary: DiffSummary
```

---

# 四十四、SceneDiff

```python
class SceneDiff(BaseModel):
    scene_id: str

    state: str
    # unchanged / modified

    summary_change: FieldChange | None

    characters_change: FieldChange | None

    shots: list[ShotDiff]
```

第一版不做：

```text
Scene add / delete
```

所以 Scene state 只：

```text
unchanged
modified
```

---

# 四十五、ShotDiff

建议：

```python
class ShotDiff(BaseModel):
    shot_id: str

    state: str
    # added
    # deleted
    # modified
    # moved
    # unchanged

    before_index: int | None
    after_index: int | None

    description_change: FieldChange | None
    characters_change: FieldChange | None
    duration_change: FieldChange | None
```

如果 Shot：

既 moved 又 modified，

不要强行只能一个 state。

更好：

```python
change_types: list[str]
```

例如：

```json
{
  "shot_id": "s03_c02",
  "change_types": [
    "moved",
    "modified"
  ]
}
```

---

# 四十六、Visual Diff 必须使用稳定 ID 对齐

比较：

```text
base shots
working shots
```

用：

```text
shot_id
```

关联。

不要：

```text
index 1 对 index 1
```

否则 reorder 会被误判成：

> 两个 Shot 内容都变了。

---

# 四十七、Workspace aggregate diff 与单个 ChangeSet diff 分开

未来 UI 有两种 Diff。

### ChangeSet Preview

回答：

> “这次修改会改变什么？”

### Workspace Diff

回答：

> “这个实验版本相对于正式项目，总共改变了什么？”

两个 API / 数据对象最好区分。

例如：

```text
changeset.preview_diff
```

和：

```text
GET workspace/{id}/diff
```

---

# 四十八、Workspace aggregate impact

同样：

单 Changeset 有：

```text
impact_report
```

Workspace 整体还需要：

```text
aggregate impact
```

不要简单把所有历史 impact report 相加。

因为：

```text
change 1:
8s → 5s

change 2:
5s → 8s
```

最终 Workspace 相对 Base：

```text
无变化
```

所以 aggregate impact 应根据：

```text
base_project
vs
working_project
```

重新计算。

---

# 四十九、UI：入口

Storyboard 顶部增加一个较明确入口：

```text
[实验版本]
```

或者：

```text
[创作实验室]
```

我更推荐用户名称：

> **创作版本**

Workspace 这个技术名不要直接大量暴露给普通用户。

例如：

```text
创作版本

正式版本
实验：动作更紧凑
实验：增加悬疑感
```

---

# 五十、创建 Workspace

点击：

```text
[＋ 创建实验版本]
```

简单：

```text
新建实验版本

名称
[动作更紧凑版]

基于：
当前正式项目

本版本中的修改不会影响正式项目。

[取消]
[创建]
```

不需要 AI。

---

# 五十一、Workspace Studio

建议一个大型工作区。

例如：

```text
┌──────────────────────────────────────────────────────────┐
│ 创作版本 · 动作更紧凑版            基于正式项目          │
├───────────────┬──────────────────────┬───────────────────┤
│ Scene         │ Storyboard           │ 修改 / 影响       │
│               │                      │                   │
│ 故事段1       │ 镜头1                │ 当前差异          │
│ 故事段2       │ 镜头2                │                   │
│ 故事段3       │ 镜头3                │ 影响分析          │
│               │                      │                   │
│               │ [+ 镜头]             │ 修改历史          │
└───────────────┴──────────────────────┴───────────────────┘
```

不要重新复制当前完整 Shot Workbench。

AI-6A 只需要编辑：

```text
Scene summary
Scene characters

Shot intent
Shot characters
duration

insert
delete
reorder
```

不允许在 Workspace Studio 生成：

```text
首帧
尾帧
视频
```

那是 AI-6C。

---

# 五十二、Workspace 顶部必须非常明显

始终显示：

```text
实验版本

这里的修改不会影响正式项目。
```

避免用户误认为自己正在改 canonical。

可以用固定 banner。

---

# 五十三、修改最好采用 ChangeSet Preview

例如用户编辑 Shot：

```text
原：
赤焰守卫缓慢观察测试机。

新：
赤焰守卫短暂停顿后立即转向测试机。
```

用户点击：

```text
[预览修改]
```

右侧显示：

```text
本次修改

镜头 3 · 本镜意图

原
赤焰守卫缓慢观察测试机。

新
赤焰守卫短暂停顿后立即转向测试机。

影响
────────
制作规划需要重新检查
当前镜头建议重新生成
前后镜头提示词上下文发生变化

旧图片和视频不会删除。

[取消]
[应用到实验版本]
```

这就是未来 Agent proposal UI 的雏形。

---

# 五十四、批量 ChangeSet

必须允许一次 ChangeSet 包含多个 operation。

例如：

```text
“故事段3节奏更快”
```

未来 Agent 可以产生：

```text
UpdateShot c01
DeleteShot c02
UpdateShot c03
ReorderShots
```

一个：

```text
ChangeSet
```

Visual Diff 一次展示。

所以后端 API 不要设计成：

```text
PUT /workspace/shot
```

直接写。

应该：

```text
operations[]
```

提交。

---

# 五十五、手工 UI 也应该走同一个 ChangeSet API

不要：

```text
手工编辑 → PUT workspace shot

未来 Agent → ChangeSet
```

形成两套修改系统。

AI-6A 从第一天就：

```text
手工修改
        ↓
Semantic Operations
        ↓
ChangeSet Preview
        ↓
Apply
```

未来：

```text
Agent
        ↓
Semantic Operations
        ↓
同一个 ChangeSet Preview
        ↓
Apply
```

这是非常重要的统一。

---

# 五十六、Workspace Revision History

右侧：

```text
修改历史
```

例如：

```text
#4
调整故事段3节奏
3 个修改

#3
增加林夏反应镜头
1 个新增镜头

#2
缩短镜头2
8s → 5s

#1
创建实验版本
```

AI-6A 不需要实现 Undo。

先能追溯即可。

以后可以利用：

```text
before / after
```

创建 inverse ChangeSet。

---

# 五十七、Changeset 不能直接存 UI index

不要：

```text
shot_index = 2
```

使用：

```text
shot_id
```

Reorder 才使用：

```text
before_order
after_order
```

Agent 以后也只通过 stable IDs 操作。

---

# 五十八、Scene display index 同样与 scene_id 分开

UI：

```text
故事段 1
故事段 2
```

来自：

```text
Project.scenes index
```

内部引用继续：

```text
scene_id
```

不要把展示编号当身份。

---

# 五十九、Workspace delete / archive

建议：

```text
[归档]
[删除]
```

删除需要确认：

```text
删除实验版本不会影响正式项目，
但该实验版本中的修改记录将被删除。

[取消]
[删除]
```

由于 AI-6A 还不能生产：

不用考虑 Production Run 引用 Workspace。

AI-6C 后一旦存在 Production Run 引用：

就应该：

```text
RESTRICT delete
```

现在代码结构可以预留：

```python
can_delete_workspace()
```

---

# 六十、Project Source Signature

不要直接使用完整 Project JSON hash。

因为：

```text
JobQueue
generated assets
updated_at
```

可能不断变化。

Workspace base signature 应基于：

> Creative Source。

建议复用 / 扩展 AI-5A：

```python
build_project_source_signature()
```

包含：

```text
story_text
style

Characters:
name
description_en
views identity

Scenes:
scene_id
summary
characters

Shots:
shot_id
description
characters
target_duration_s
order
```

是否包含 generated assets：

不要。

Workspace 是创作版本。

---

# 六十一、Shot order 必须进入 signature

虽然：

```text
shot_id
```

保持稳定，

但：

```text
A B C
```

和：

```text
A C B
```

是完全不同的 Storyboard。

所以 signature 必须保存：

```text
shots:
[
 A,
 B,
 C
]
```

的数组顺序。

---

# 六十二、第一版不要克隆 AI Draft 文件

创建 Workspace 时不要复制：

```text
director_drafts.json
story_continuation_draft
character_designs
```

这些属于 canonical 工作过程。

Workspace 第一版只：

```text
Project snapshot
+
ChangeSets
```

以后 AI-6B 的 Workspace Director Draft：

要保存进：

```text
workspace 自己的目录
```

不能使用 canonical draft。

---

# 六十三、第一版也不要复制 ProductionPlan 文件

AI-6A 可以：

通过 Impact Report 告诉用户：

```text
Scene 3 的 Production Plan 需要重新生成
```

但暂时不需要把：

```text
canonical production_plans.json
```

复制到 workspace。

AI-6B / AI-6C 再正式建立：

```text
workspace production plans
```

避免这一阶段扩大太多。

---

# 六十四、但是需要预留 Workspace sidecar

建议 workspace metadata 中允许：

```python
artifacts: dict[str, str] = {}
```

或者不要固定 schema。

未来可以出现：

```text
director_drafts.json
production_plans.json
production_runs
```

但 AI-6A 不使用。

---

# 六十五、Workspace Impact 状态不要写进 Project model

不要给：

```python
Shot
```

新增：

```text
needs_regeneration
workspace_stale
impact
```

这些是 Workspace revision 层的信息。

保存在：

```text
ChangeSet / derived Impact
```

不要污染 canonical data model。

---

# 六十六、未来 Agent Constraints 的接口预留

AI-6A 不实现 Agent。

但 ChangeSet preview 可以提前支持：

```python
protected_entities: list[EntityRef] = []
```

不是必须暴露 UI。

如果实现很轻：

可以在 preview validator 内支持：

```python
if operation touches protected entity:
    reject
```

未来用户说：

```text
不要改林夏
不要改前三个故事段
```

Agent 就能转成真正的 deterministic constraints。

如果会明显扩大本阶段：

只设计接口，不实现 UI。

---

# 六十七、EntityRef

建议现在增加一个简单标准类型：

```python
class EntityRef(BaseModel):
    entity_type: str
    entity_id: str

    parent_id: str | None = None
```

例如：

```python
EntityRef(
    entity_type="scene",
    entity_id="s03",
)
```

```python
EntityRef(
    entity_type="shot",
    entity_id="s03_c02",
    parent_id="s03",
)
```

ImpactReport、ChangeSet、未来 Agent 都逐渐统一使用。

不要每层创造不同引用格式。

---

# 六十八、API 建议

根据当前 Router 风格调整。

---

## Workspace

```text
POST
/api/project/{p}/workspaces

GET
/api/project/{p}/workspaces

GET
/api/project/{p}/workspace/{workspace_id}

GET
/api/project/{p}/workspace/{workspace_id}/project

POST
/api/project/{p}/workspace/{workspace_id}/archive

DELETE
/api/project/{p}/workspace/{workspace_id}
```

---

## Diff

```text
GET
/api/project/{p}/workspace/{workspace_id}/diff

GET
/api/project/{p}/workspace/{workspace_id}/impact
```

---

## ChangeSets

```text
POST
/api/project/{p}/workspace/{workspace_id}/changeset/preview

POST
/api/project/{p}/workspace/{workspace_id}/changeset/{changeset_id}/apply

POST
/api/project/{p}/workspace/{workspace_id}/changeset/{changeset_id}/reject

GET
/api/project/{p}/workspace/{workspace_id}/changesets
```

不要让前端直接：

```text
PUT working_project.json
```

---

# 六十九、Preview Body

例如：

```json
{
  "title": "缩短战斗节奏",
  "intent": "减少中段停顿",

  "base_workspace_revision": 3,

  "operations": [
    {
      "type": "update_shot",
      "scene_id": "s03",
      "shot_id": "s03_c02",
      "target_duration_s": {
        "before": 8,
        "after": 5
      }
    }
  ]
}
```

后端：

不信任：

```text
before
revision
IDs
```

全部重新验证。

---

# 七十、Preview Response

应足够直接给 UI 画 Diff。

例如：

```json
{
  "changeset_id": "cs_xxx",

  "valid": true,

  "diff": {
    "...": "..."
  },

  "impact": {
    "affected_scene_ids": ["s03"],
    "affected_shot_ids": [
      "s03_c02"
    ],
    "production_plan_scene_ids": [
      "s03"
    ],
    "video_regeneration_shot_ids": [
      "s03_c02"
    ]
  },

  "warnings": []
}
```

未来 Agent 调：

```text
propose_changes
```

本质上就可以返回这个对象。

---

# 七十一、Apply API

Apply 不接受 operations 再传一遍。

前端只传：

```text
changeset_id
```

因为 operations 已经锁定在 preview。

流程：

```text
load changeset
↓
status == previewed
↓
workspace revision match
↓
before values match
↓
recreate after candidate
↓
signature == preview after_signature
↓
write-ahead
↓
atomic save working project
↓
increment revision
↓
applied
```

不要相信前端重新发送的 after value。

---

# 七十二、Apply 后重新计算 aggregate diff

Apply 成功：

返回：

```text
workspace_revision
workspace_diff_summary
aggregate_impact_summary
```

前端立即更新。

---

# 七十三、base canonical stale 检查不阻止 Apply

如果：

```text
Workspace 创建后 canonical changed
```

用户仍然应该可以继续实验。

所以：

```text
base_is_stale = true
```

只是 warning。

不要阻止：

```text
Workspace ChangeSet apply
```

因为 Workspace 已经有自己的独立 base。

真正未来想：

```text
promote → canonical
```

时才需要处理 merge/rebase。

---

# 七十四、Workspace UI 与 canonical 切换

不需要新增项目 dropdown。

这不是 Project switching。

Storyboard 中可以：

```text
正式版本
实验版本：动作更紧凑版
```

但 AI-6A 第一版最好不要改整个 ShotBoard 数据源。

先使用：

```text
CreativeWorkspaceStudio
```

单独工作区。

避免一下把：

```text
Workbench
Autopilot
Planner
```

全部变 source-aware。

AI-6B / 6C 再逐渐统一。

---

# 七十五、建议新增前端组件

例如：

```text
src/components/CreativeWorkspaceStudio.vue
```

内部：

```text
Workspace header

Scene list

WorkspaceShotEditor

DiffPanel

ImpactPanel

ChangeHistory
```

不必拆太多文件。

根据当前 Vue 文件大小合理选择。

---

# 七十六、Workspace Shot Editor

第一版不要复制复杂 Workbench。

Shot card：

```text
镜头 2

本镜意图
[...................]

角色
[赤焰守卫] [林夏]

时长
[5s]

[删除]
```

支持：

```text
拖拽 reorder
新增 Shot
```

修改后不是立即 save。

先进入：

```text
Pending Change
```

然后：

```text
[预览修改]
```

---

# 七十七、批量编辑体验

用户可以连续：

```text
改 Shot 1
删 Shot 2
改 Shot 3
```

这些操作可以暂存在前端 pending operations。

点击一次：

```text
[预览 3 项修改]
```

形成一个 Changeset。

不要每输入一个字符就创建 ChangeSet。

---

# 七十八、ChangeSet title

手工 UI 可以默认：

```text
修改故事段 3
```

用户可以改。

未来 Agent：

```text
title = "压缩中段战斗节奏"
intent = 用户自然语言要求
```

这会进入 Revision History。

---

# 七十九、Visual Diff 的颜色和视觉

遵循当前项目 UI 风格。

不用做 GitHub 代码 Diff。

面向创作用户。

例如：

```text
镜头 2 · 时长

8 秒
   ↓
5 秒
```

```text
− 镜头 3
赤焰守卫停下观察……
```

```text
＋ 新镜头
林夏从掩体后突然出现……
```

```text
↕ 镜头 4
从第 4 位移动到第 2 位
```

---

# 八十、Impact UI 与 Diff UI 分开

Diff 回答：

> 改了什么？

Impact 回答：

> 这些修改会让什么受到影响？

例如：

```text
修改
────────────────
镜头 2
8s → 5s

影响
────────────────
制作规划           需要重新检查
视频 Prompt         需要更新
当前镜头视频         建议重新制作
已有视频             保留
```

这会成为未来 Agent 最重要的解释层。

---

# 八十一、未来 Agent 不应该自己写 Impact 文案

后端：

```text
Impact Engine
```

返回：

```text
code
entity ref
reason code
```

前端本地映射用户文案。

Agent 可以：

```text
总结 Impact Report
```

但不能自己发明影响范围。

---

# 八十二、Impact Reason Code

例如：

```text
SCENE_SUMMARY_CHANGED
SCENE_CHARACTERS_CHANGED

SHOT_INTENT_CHANGED
SHOT_CHARACTERS_CHANGED
SHOT_DURATION_CHANGED

SHOT_INSERTED
SHOT_DELETED
SHOT_REORDERED

ADJACENCY_CHANGED
CONTINUITY_CHAIN_CHANGED
```

这样：

* UI
* Agent
* Debugging

都可以使用同一规则。

---

# 八十三、Impact 不需要做到完美编译器级精确

第一版应该：

> 保守但解释清楚。

宁可：

```text
这个 Scene Production Plan 建议重建
```

也不要因为规则过于聪明而漏掉真正依赖。

尤其我们后面生产成本低。

**可靠性比最小重算集合重要。**

---

# 八十四、代码目录建议

可以新增：

```text
src/revision/
    __init__.py

    models.py
    source.py
    workspace.py
    changeset.py
    diff.py
    impact.py
```

Router：

```text
routers/workspaces.py
```

如果当前项目不适合新增整个目录，也可以：

```text
src/core/revision/
```

但不要把所有逻辑塞：

```text
routers/workspaces.py
```

---

# 八十五、推荐职责

### `source.py`

```text
ProjectSourceRef
ProjectSourceStore
```

### `workspace.py`

```text
Workspace create/load/archive/delete
Working project read
reconciliation
```

### `changeset.py`

```text
Operation models
preview
apply
validation
```

### `diff.py`

```text
base ↔ working
before ↔ after
```

### `impact.py`

```text
deterministic dependency rules
```

---

# 八十六、不要建立通用 Event Sourcing Framework

虽然 ChangeSet 看起来像 event sourcing，

不要现在做：

```text
Generic EventBus
AggregateRoot
EventStore
CQRS
```

这些会让项目变得过度工程化。

我们需要的是：

```text
Project-specific semantic revision layer
```

明确、可读即可。

---

# 八十七、也不要使用 JSON Patch 库作为核心

内部如果某些 utility 想用 diff library：

可以。

但持久化业务格式仍然必须是：

```text
UpdateShot
InsertShot
DeleteShot
...
```

未来 Agent 必须理解这些语义。

---

# 八十八、第一版不要支持 Character 修改

虽然未来 Agent 肯定需要：

```text
UpdateCharacter
```

AI-6A 暂时不要实现。

原因：

Character：

```text
description
views
角色图生成历史
全项目引用
```

影响范围明显更大。

先把 Scene / Shot Revision 验证成功。

模型和 EntityRef 可以为：

```text
character
```

预留。

AI-7 再加。

---

# 八十九、第一版不要支持 Story Text 修改

同理。

不要：

```text
UpdateStoryText
```

AI-6A 是：

> Storyboard Revision Foundation。

未来全局 Producer 修改剧情时再增加。

先证明修改：

```text
Scene / Shot
```

可以安全追踪。

---

# 九十、第一版不要处理 Production Plan Edit

Workspace 里现在只计算：

```text
Production Plan affected
```

不要允许用户直接在 Workspace Studio 改：

```text
opening_source
link_mode
```

AI-6B / 6C 将让 Planner 针对 Workspace 重新生成。

不要提前把 Production Plan 变成两套。

---

# 九十一、本阶段不做这些能力

严格不要实现：

* Agnes 调用
* AI Director variant
* Agent chat
* Agent tools
* Workspace Production
* Workspace Production Plan
* Workspace 图片生成
* Workspace 视频生成
* Production Variant
* N 次完整循环
* Workspace → Canonical merge
* Rebase
* Undo
* Character revision
* Story revision
* AI 自动 Impact 判断
* 跨项目 Workspace

---

# 九十二、必要的简单验证

测试保持重点即可，不要为了覆盖率扩张本阶段。

至少确认：

### Case 1

创建 Workspace。

修改 Shot.description。

Apply 后：

```text
workspace changed
canonical project unchanged
```

---

### Case 2

一次 Changeset：

```text
UpdateShot
InsertShot
ReorderShots
```

Preview Diff 和 Apply 结果一致。

---

### Case 3

Insert Shot 后：

```text
原 Shot ID 不变
```

Reorder 后：

```text
Shot ID 仍不变
```

顺序只看数组。

---

### Case 4

旧 Changeset：

```text
base revision = 1
```

Workspace 已经 revision 2。

Apply：

```text
409 WORKSPACE_CHANGED
```

---

### Case 5

模拟：

```text
working_project write 成功
changeset status 未更新
crash
```

恢复：

通过：

```text
after_signature
```

认定 Changeset 已应用。

不能 apply 两次。

---

### Case 6

UpdateShot.description：

Impact 至少报告：

```text
Scene production plan affected
current Shot regeneration recommended
adjacent prompt context affected
```

---

### Case 7

Canonical project 在 Workspace 创建后改变。

Workspace：

```text
base_is_stale = true
```

但仍然可以正常编辑。

---

# 九十三、浏览器真正验收体验

最终应该做到：

```text
正式分镜：

故事段 3

镜头1
镜头2
镜头3
```

点击：

```text
[创作版本]
```

创建：

```text
动作更紧凑版
```

进入 Workspace：

```text
镜头1
镜头2
镜头3
```

修改：

```text
镜头2
8s → 5s

删除镜头3

新增：
林夏突然从掩体后出现
```

点击：

```text
预览修改
```

看到：

```text
修改
────────────────

镜头2
8s → 5s

− 镜头3

＋ 新镜头
林夏突然从掩体后出现


影响
────────────────

故事段        1
制作规划      需要重新规划
镜头          3 个受影响
视频          2 个建议重新制作

已有生成结果不会删除。
```

点击：

```text
[应用到实验版本]
```

Workspace：

改变。

返回正式 Storyboard：

仍然：

```text
镜头1
镜头2
镜头3
```

完全没变。

这就是 AI-6A 成功。

---

# 九十四、完成后重点汇报

完成以后重点告诉我：

1. Workspace 最终目录结构
2. `base_project` 和 `working_project` 分别承担什么
3. `ProjectSourceRef` 如何设计
4. 为什么现在只统一读取、不统一任意写入
5. Semantic ChangeSet 最终支持哪些 operation
6. Shot ID 如何保证 insert / reorder 后仍然稳定
7. 当前代码里是否发现依赖 Shot ID 排序的隐含假设，如何处理
8. Preview 如何做到完全不修改 Workspace
9. before-value + workspace revision 如何防止旧 ChangeSet 覆盖新修改
10. ChangeSet Apply 的 crash window 如何 reconciliation
11. Hard validation 与 soft warning 如何区分
12. Impact Engine 最终有哪些主要规则
13. 修改 Shot 为什么会影响相邻 Shot prompt context
14. Insert / Delete / Reorder 如何计算 continuity impact
15. 为什么历史图片和视频绝不自动删除
16. Workspace aggregate Diff 如何计算
17. 单 Changeset Diff 与整个 Workspace Diff 如何区分
18. Canonical 后来改变时 Workspace 如何表现
19. UI 如何明确告诉用户“实验版本不影响正式项目”
20. 实际浏览器是否做到：
    `正式 A → Workspace B → 修改 B → 正式 A 完全不变`
21. 哪些能力明确留给 AI-6B

````

---

## AI-6A 做完之后的下一步已经可以提前定死

AI-6B 就会第一次真正让 **AI Director 可以修改已有分镜，但只修改 Workspace**。

那时：

```text
Canonical
A B C

↓

Workspace

↓

AI Director：
KEEP A
UPDATE B
INSERT D
DELETE C

↓

ChangeSet

↓

Visual Diff

↓

Impact

↓

Apply Workspace
````

这就是我们过去一直故意不开放的：

> **“已有分镜时，AI 自主判断保留、修改、增加还是删除。”**

现在终于有了安全的地方可以做。

而未来 AI Producer 做所谓“全局针对性修改”，本质上也会使用完全相同的通道：

```text
自然语言要求
↓
理解项目
↓
Semantic ChangeSet
↓
Impact Engine
↓
Visual Diff
↓
Workspace
↓
Production
```

所以我认为 **AI-6A 是整个系统最后那个全局 Agent 的真正地基之一**。它不直接展示 AI 的聪明程度，但如果这一层做扎实，以后 Agent 不管改一个 Shot、一个 Scene，还是跨十个 Scene 做全局调整，都会是可查看、可追踪、可验证、不会直接破坏正式项目的。
