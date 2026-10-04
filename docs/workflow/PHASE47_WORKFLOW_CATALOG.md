# Phase 47 工作流目录（W00-W09）

> 模板版本：`p47_templates_v1`（src/workflow/templates.py）· 定义格式：workflow_draft_v1
> 每模板 = 可复制的工作流定义 + 默认目标 + completion 契约；复制产生新工作流身份
> （definition.template 记录来源）；创建路径：`POST /api/project/{p}/workflow/templates/{tid}/create`
> 或页面「从模板新建」。Skill 绑定用真实 skill_binding 边（v1.3.1，名册内）。

## 模板清单与真实运行索引

| ID | 执行链 | Skill | completion 契约（默认） | 真实运行证据 |
| --- | --- | --- | --- | --- |
| W00 | producer 计划 → 审核 | — | 计划节点（无写域） | P47_C_Templates: run_a1a30e4c581a completed（浏览器亦验证） |
| W01 | 编剧新写/修订 → 审核 | — | `{kind:"scene", minCount:N}` require_new | run_12faea654aef completed（2 场景 created）；浏览器创建 run_fe2fea（诚实失败展示） |
| W02 | 角色资源 → 审核（可批准 Look） | — | character×1 + look×1(withImage) require_new | run_43f241b4c3aa completed（真实三视图+Look 图+批准选用） |
| W03 | 场景设计 → 审核 | — | location×1 require_new | 首跑暴露 outputs.py bug→修复；恢复轮 PASS（2 Location+3 视图图像可读） |
| W04 | 导演分镜准备 → 审核（可选首帧选用） | — | shot×N(withImage) require_new | 首跑 3 镜 created（图像后补）；恢复轮 run_2fe33eae9111 outputs_verified |
| W05 | 导演视频制作 → 审核（选用视频版本） | relay-calibration | video_version×N(playable) require_new | run_ff815a61a1dc completed（真实视频+Skill load+选片指针） |
| W06 | 导演诊断修复 → 审核 | — | video_version×1 allow_verified_reuse | C3: 修复候选真实生成（s01_c01 原+修=2 版本） |
| W07 | 导演端点桥接 → 审核 | keyframes-bridge-repair | video_version×1 allow_verified_reuse | C3: 桥接候选真实生成（s01_c02→s02_c01） |
| W08 | 装配（素材确认审核可选）→ 装配 → 成片审核 | —（不可绑） | sequence×1(withCompletedRender) require_new | run_a34b64fad732 completed（快照冻结→对齐门→render 文件落盘） |
| W09 | 六员完整生产（7 道审核，视频节点绑 relay） | relay-calibration | 全节点契约 | D 批 run_22b54d2261b9（进行中） |

## 前置条件与所需输入（§5.2 对齐）

| ID | 必需输入 | 预期产物 |
| --- | --- | --- |
| W00 | 故事目标、项目状态、任务范围 | 持久化计划与待决定项（无越权委派，WF_DELEGATE_GUARD 服务端拦截） |
| W01 | 创作简报、新写/修订范围 | 故事段或正文版本+回执（scene created+hash） |
| W02 | 角色/Look 目标（新项目需 ProjectVisualProfile——正式建项目 API 自带） | 角色+Look 参考图候选（真实图像文件） |
| W03 | 故事段、地点、时段空间目标 | Location/SceneInstance + 环境视图图像 |
| W04 | 故事/资源/场景引用 | 镜头清单+资源绑定+分镜图候选 |
| W05 | 批准首帧或合法参考、镜头范围 | 真视频版本（playable）+首中尾帧 |
| W06 | 已知问题视频/根帧、反馈范围 | 修复候选+前后对比（原版保留） |
| W07 | 两个合格端点+真实接缝 | 桥接候选+双端依据+拼接预览 |
| W08 | 已选视频版本（scope.videoVersions 或审核选用）+音频政策 | Sequence+render（项目本地正式快照冻结为前置） |
| W09 | 新故事目标、资产审批授权 | 从新故事到可审成片的完整证据链 |

## 复用与恢复语义（模板可改）

- 任何节点 completion 可改 `strategy: allow_verified_reuse`（服务端核验既有产物：
  存在+内容 hash+文件可读+审批状态；reusedRefs 与 changedRefs 分开记录）。
- 恢复轮/续做轮建议改用 allow_verified_reuse（C 批 W03/W04 恢复轮的真实用法）。
- 审核 selection 支持 kind: video_version / sequence_render / first_frame /
  look_reference / prop_reference / composition_candidate；versionId 可缺省
  （运行期在审核卡片从真实候选中选定）；items 多选用（二审多镜一次选用）。

## 修改记录

- v1（2026-10-04）：初版 10 模板；真机排障后 W08 增快照冻结前置说明（模板 objective 已含）。
