# Production Resources V1 — Phase 1C3 报告（Shared Production Library 最小闭环）

> 阶段：**Phase 1C3 — Shared Production Library 最小闭环**（封版前四件事之三：跨项目复用）
> 分支：`feat/resources-v1-phase1c1`（追加 commits，未 push）
> 铁律：**Project A 的修改绝不污染 Project B / Shared**（构造保证，测试字节级断言）；`saved_characters/` 零触碰。

---

## 1. 存储形状

```
shared_library/                      # 仓库根（与 saved_characters/ 平级，全新目录）
  resources.json                     # SharedDocument canonical（schema_version=1）
  media/{shared_asset_id}.{ext}      # copy-on-publish 的媒体副本
```

`SharedDocument`：`{schema_version, characters{schar_id→SharedCharacter}, looks/clothings/props（复用 1B typed models，scope=shared）, reference_assets, meta{shared_id→SharedEntryMeta}}`。

- **SharedEntryMeta（version/origin 不侵入 typed models）**：`{resource_id, kind, version, origin_project, original_id, published_at, asset_map(项目 asset_id→shared asset_id), character_snapshot(look 发布时角色身份快照), history[{version,published_at}]}`——version/origin 全部住在 shared 侧 meta 段，**项目 resources.json 与 typed models 形状零变化**（1B 的 T17 形状断言原样通过）。
- SharedCharacter：`{schar_id, name, description_en, view_asset_ids{view→shared asset_id}}`。

## 2. Publish（project → shared，只读项目 canonical）

- `publish(resource_id)`：look/clothing/prop；**幂等 republish=同 shared id、version+1**（按 `origin_project+original_id+kind` 找回条目；asset_map 复用——同项目 asset 的 shared 副本 id 稳定，内容/checksum 刷新）。
- **媒体 copy-on-publish**：项目文件 → `shared_library/media/{shared_asset_id}{ext}`（真实 sha256 checksum；扩展名白名单校验）。
- **look 发布的显式依赖链**：`clothing_id` 非空时该 Clothing 必须**已发布**（查 meta origin 指针），否则 `clothing_not_published`(409)——不隐式捆绑发布，与模块"一切跨域流转皆显式动作"的哲学一致。
- look 发布时快照角色身份（`character_snapshot={name, description_en}`）进 meta。
- `publish_character(character_name)`：三视图文件复制进 shared media（`view_asset_ids` 映射）；无可发布视图 → `missing_character_reference` 拒绝。
- 项目侧零写入（publish 后项目 clothing.origin 仍 None——有测试断言）。

## 3. Import（shared → project snapshot，copy-on-import）

- **新 id + 媒体复制**：`output/resources/imported/{new_asset_id}{ext}`；ReferenceAsset/Clothing/Look/Prop 全部新 id、`scope=project`、`origin=ResourceOrigin(source=shared_library, shared_resource_id, shared_version, imported_at=ISO)`。
- **写安全**：项目锁（`project_write_lock(project)`）+ **document revision OCC**（import 写项目 canonical，必须接 `expected_document_revision`；stale→409）。
- **look 导入**：前置校验目标项目存在同名 Character（`character_not_found`）；自动捆绑其共享 clothing——**带查重**：项目内已有 origin 指向同一共享 clothing 的副本则不再复制，FK 直连既有副本（防重复膨胀）。
- **character 导入**：视图文件复制进 `output/characters/{name}_{view}_seg.png` + `Character.views`（走 ProjectStore 正常写入——legacy 通道立即可用，不注册项目 ReferenceAsset）；**项目内已有同名 → `character_exists` 拒绝（不覆盖）**。
- shared 侧在 import 中零改动（字节不变，测试断言）。

## 4. API

| 端点 | 说明 |
|---|---|
| `POST /resources/publish` | `{resource_id}`（look/clothing/prop） |
| `POST /resources/publish-character` | `{character_name}` |
| `POST /resources/import` | `{shared_resource_id, expected_document_revision}` |
| `GET /api/shared/resources?type=` | 共享库轻量列表（全局命名空间） |
| `GET /api/shared/resources/{id}` | 共享资源读（含 version/origin/关系 resolve） |

并发：shared 文档写走 `project_write_lock("__shared_library__")` 全局串行化；与项目锁分层（publish=shared 锁、import=项目锁内做 dedupe+写入）。

## 5. tests（`tests/test_resources_phase1c3.py`，10 条全绿）

| # | 覆盖 |
|---|---|
| T1 | publish clothing：媒体真实复制+checksum、scope=shared、**项目 canonical 零改动** |
| T2 | republish：同 shared id、version 2、history 2 条、origin 记录 |
| T3 | look 发布依赖：未发布 clothing→409；发布后 FK 接共享 clothing、候选媒体进库、character_snapshot |
| T4 | publish character：视图复制+checksum；无视图/不存在角色拒绝 |
| T5 | import clothing：新 id、媒体复制进项目、origin 三元组、A/shared 零改动 |
| T6 | import look：捆绑查重（FK 直连已有副本、无第二份 clothing）、缺角色拒绝 |
| T7 | import character：视图进 legacy 通道可读回、同名冲突拒绝、之后 look 可导入 |
| T8 | **隔离**：A 改原版 + B 改副本 → shared 字节不变 + C 副本仍原名 + A/B 各自生效 |
| T9 | import OCC stale / 共享 id 不存在 |
| HTTP | publish→shared list/read→import 回 p2（patch default_shared_dir 到 tmp，**绝不写真实 shared_library/**）+ OCC 409 |

回归：资源五套件 **89 passed**（1B 22 + 1C1 39 + hardening 9 + 1C2 9 + 1C3 10；白名单契约扩展 3 个新端点）。

## 6. 与 saved_characters/ 的关系

零改动、零读取、零写入（本轮代码不 import 该目录任何内容）。旧角色库继续以原 API/原 UI 入口工作；Shared Library 是**新增**的、带 version/origin 的生产资料复用通道。二者的合并（如果需要）留待封版后讨论。

## 7. deviations / blockers

1. **版本语义简化**：re-publish 原地更新条目 + version 递增 + history 追加；**不保留旧版本快照**（Phase 1A 的 immutable-per-version 只落地"版本号可追溯"，不落地多版本存储——用户明示"不需要现在做复杂的 content-addressed storage"，多版本存储属于同类复杂度，如实记录为简化）。
2. **publish 无项目 OCC**：publish 只读项目 canonical（零项目写入），无 OCC 对象可守；并发发布同一资源由 shared 全局锁串行化，后写版本+1。import 写项目 canonical → 必须接 document OCC。
3. **character 导入走 legacy views 通道**（不注册项目 ReferenceAsset）：导入的角色立即可被既有管线消费；ReferenceAsset 化属于"显式 Base Look"场景，非本轮。
4. **import 不做重复导入检测**（除 look→clothing 查重外）：同一条目可多次导入产生多份独立副本——copy-on-import 语义下这是合法操作（用户可能想基于同一母版开两个分支），UI 层可后续加提示。
5. **blockers：无。**

**Phase 1C3 完成。进入 Phase 1C4（最小 Resources UI + Prop CRUD）。**
