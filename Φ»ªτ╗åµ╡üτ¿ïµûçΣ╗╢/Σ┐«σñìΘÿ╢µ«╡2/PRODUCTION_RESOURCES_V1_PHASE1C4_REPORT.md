# Production Resources V1 — Phase 1C4 报告（最小可用 Resources UI + Prop CRUD）

> 阶段：**Phase 1C4 — 最小可用 Resources UI + Prop CRUD**（封版前四件事之四）
> 分支：`feat/resources-v1-phase1c1`（追加 commits，未 push）
> 纪律：Location 不做（归 Scene System）；不重写 Agent Center；角色页/Shot/Director 零触碰。

---

## 1. Prop CRUD（后端）

`write_service` 五个 mutation（全部短锁 + OCC + 中性 receipt + save 后重读 verified）：

| 操作 | 说明 |
|---|---|
| `create_prop(name, description?, tags?, expected_document_revision)` | approval_status=unreviewed |
| `update_prop(prop_id, patch{name?,description?,tags?}, expected_revision)` | 白名单严格拒绝未知字段；rename 只改 name |
| `delete_prop(prop_id, expected_revision)` | 当前模型无资源引用 Prop（Look 无 prop FK）→ 无 resource_in_use 检查；**docstring 注明：未来 Look 加 prop FK 必须补 reference sweep** |
| `attach_prop_reference` / `detach_prop_reference` | 幂等；asset 必须存在；与 clothing 同语义 |

HTTP：`POST/PATCH/DELETE /props[/{id}]`、`POST/DELETE /props/{id}/references[/{asset_id}]`（31 routes）。Phase 1B 白名单契约 +5 端点。

## 2. 共享库静态服务

`api_server.py` +1 mount：`/shared_library` → StaticFiles（目录不存在则创建空挂载）。共享媒体 URL = `/shared_library/media/{id}.png`（项目媒体沿用既有 `/projects` 挂载）。前端零代理、零 base64。

## 3. 前端（`/resources` 新页面，未触碰现有角色页）

| 文件 | 内容 |
|---|---|
| `src/views/resources/index.vue`（新，~640 行） | 见下 |
| `src/api/index.ts` | ApiClient +29 个资源域方法（read/create/update/delete/reference/generate/review/approve/publish/import/shared）+ `resourcesRequest` 包装（把 FastAPI `detail=receipt` 结构化错误还原为对象供页面判 error 码） |
| `src/router/index.ts` | `/resources` 路由（meta: 生产资源） |
| `src/layouts/MainLayout.vue` | 侧栏菜单 +「生产资源」项（icon 内联 svg） |

### 页面结构（最小可用闭环）
- **Scope 切换**：项目资源 | 共享库。
- **项目·角色/Look tab**：左侧角色列表（`/characters/full`，点击「发布此角色」→ publish-character）→ 右侧该角色的 Look 卡片（封面/生成状态/审批状态徽标）→ Look 详情：
  - 候选图网格（`/projects/{p}/{local}` 直出；缺失标红）；每候选：审查徽标（pass/needs_fix/uncertain，来自 read_look 的 latest_reviews）+ 审查摘要（1 行）+ **批准/撤销批准**按钮（hover 提示"需审查通过"——门槛在服务端，前端只是提示）+ 封面星标。
  - 操作：生成候选（1-4 张选择）、视觉审查、发布到共享库（`clothing_not_published` 时引导"先发布服装再发布 Look"两步链）。
  - 新建 Look 表单（label/服装下拉/外观差异）。
- **项目·服装 tab**：列表 + 新建/重命名/删除（被引用→展示服务端 409 resource_in_use）+ 详情（refs 网格 + 注册并绑定参考图：项目相对路径或 URL + 解绑 + used_by + 发布）。
- **项目·其他（道具）tab**：Prop CRUD 全闭环（新建/重命名/删除/参考图绑定解绑）。
- **共享库 scope**：卡片（类型徽标/名称/version/来源项目/角色快照）+ **导入到项目**（document OCC 从 resources envelope 取）。

## 4. 验证

- 后端：`tests/test_resources_phase1c4.py` 2 条（service 全生命周期 + HTTP 契约）全绿；资源六套件 **91 passed**（1B 22 + 1C1 39 + hardening 9 + 1C2 9 + 1C3 10 + 1C4 2）。
- 前端：**`npx vite build` ✓**（28.25s，生产 bundle 成功）；`vue-tsc --noEmit` 下**本轮触碰文件（api/index.ts / views/resources / router / MainLayout）零类型错误**——现存 6 个错误全部位于本轮未触碰文件（store/index.ts ×4 = CLAUDE.md 既有债；components/CreativeWorkspaceStudio.vue ×1、ShotCard.vue ×1，git status 可证不在本轮改动清单）。
- 浏览器人工验收（页面交互/真实生成/审查/批准链路的视觉体验）留给你按 §122 惯例验收——功能面已由 API 契约测试 + 真机 E2E（1C1/1C2）覆盖。

## 5. 改动文件

| 文件 | 改动 |
|---|---|
| `src/resources/write_service.py` | +Prop CRUD 5 mutation |
| `routers/resources.py` | +5 Prop 端点（31 routes） |
| `api_server.py` | +1 行 mount `/shared_library`（UI 展示共享媒体所需） |
| `src/api/index.ts` | +29 资源 API 方法 + resourcesRequest |
| `src/views/resources/index.vue` | 新页面 |
| `src/router/index.ts` / `src/layouts/MainLayout.vue` | 路由 + 菜单 |
| `tests/test_resources_phase1c4.py` / `tests/test_resources_phase1b.py` | 新测试 / 白名单 +5 |

## 6. deviations / blockers

1. **`npm run build`（含 vue-tsc）当前不能整体通过**——6 个既有类型错（store 4 + components 2）是历史债（CLAUDE.md 只记录了 store 的 4 个，components 的 2 个为**本轮新发现并如实补充记录**；均不在本轮触碰文件中）。按项目既有惯例以 `npx vite build`（esbuild）作为构建门禁。
2. Look 详情的候选图用 `/projects` 静态挂载直出（项目相对路径）；**绝对路径的 legacy 视图文件无法经该挂载展示**——本页角色列表刻意为文字列表（与角色页列表同哲学），不展示三视图，不受影响。
3. Prop delete 无 resource_in_use 检查——当前模型无任何资源引用 Prop，检查无对象；已在代码 docstring 与本报告双处注明未来约束。
4. 浏览器交互细节（布局/审美）按"最小可用"标准实现，未做视觉打磨。
5. **blockers：无。**

**Phase 1C4 完成 → 四件事全部完成，等待你确认后封版 Production Resources V1。**
