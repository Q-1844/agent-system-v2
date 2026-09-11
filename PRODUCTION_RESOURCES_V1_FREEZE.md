# PRODUCTION_RESOURCES_V1_FREEZE.md

**Production Resources V1 — 功能冻结**

- **freeze commit candidate**：`feat/resources-v1-phase1c1` @ 本报告提交（子模块）；父仓库指针更新另计
- **date**：2026-09-11
- **完整报告**：`PRODUCTION_RESOURCES_V1_FINAL_REPORT.md`（22 节）

## Supported capabilities（冻结面）

| 能力 | 落点 |
|---|---|
| Character（legacy canonical，name 即身份） | 零改动 |
| Clothing CRUD + references + publish | 1C1/1C3 |
| CharacterLook：create/generate（三阶段协议+GenerationBasis+stale 保护）/review/approve | 1C1/1C1.1/1C2 |
| Prop CRUD + references + **review/approve**（1C5 补齐） | 1C4/1C5 |
| **CharacterReferenceComposition**：CRUD / 生成（专属 basis+预算）/ 六维度审查（逐 binding）/ 审批 / Shared publish+import | **1C5** |
| Generation Recovery：cancel cleanup + startup orphan sweep（look+composition） | 1C1.2/1C5 |
| Shared Production Library：copy-on-publish/import、版本、lineage、隔离 | 1C3/1C5 |
| Resources UI：/resources 页（Look + Prop 审批 + Compositions + 共享库） | 1C4/1C5 |

## Frozen APIs / schema

- **resources.json / shared_library/resources.json：schema_version=2**
  （v1 惰性可读：纯读零写回、首写升级、未知 fail closed——迁移契约有测试固化）
- **REST 全集**（`routers/resources.py`，白名单契约测试固化）：
  - GET：/resources · /resources/{id}（comp_ 前缀 dispatch）· /resources/legacy-character-references/{name}
    · /looks · /looks/{id} · /clothings · /clothings/{id} · /props · /props/{id}
    · /compositions · /compositions/{id} · /api/shared/resources[/{id}]
  - 写：clothings CRUD×3 + references×2 · reference-assets 注册 · looks create/patch/generate
    · looks review/approve/revoke-approval · props CRUD×3 + references×2 + review/approve/revoke-approval
    · compositions create/patch/generate/review/approve/revoke-approval
    · resources publish / publish-character / import
- **canonical 不变量**：approved ⊆ candidates（detach/导入侧强制）；review pass ≠ approved、
  无 pass 证据不能 approve（双向硬门槛）；生成输入只用 approved refs；generation 与 approval
  状态独立；无 base64 入 canonical/records；project.json 零 schema 变化（T36 字节级）。

## Known limitations（冻结时已知，见 Final Report §18）

孤儿 generating 无运行中自愈（startup sweep 兜底，无 daemon）· Composition/Look 无 delete API ·
Character delete 不级联资源（legacy 既有债）· composition 本体重复导入不查重（依赖侧已查重）·
审查者 run-to-run 波动（人工终审架构消化）。

## Next system

**Scene System V1**（Scene / Location / Scene State / Spatial Anchor / Shot binding /
story continuity）。Resources V1 冻结后**不**再扩展任何资源功能；Scene 侧通过只读 API /
未来 additive Agent 工具消费 approved references。

## 验证快照（冻结依据）

资源八套件 144 passed · 关键 Agent 14 文件 223 passed+1 skipped（2 例已知自扰单跑复绿）·
真机 E2E：双道具组合六维度一次 PASS + 单道具审批正链 + Shared remap 全链 9/9 ·
vite build ✓ · 触碰文件 typecheck 零错误 · 全量回归见 Final Report §16。
