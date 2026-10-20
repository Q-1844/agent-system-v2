# STATE.md — 仓库状态说明

> 本文件由 2026-10-20 四仓迁移建立，面向协作者（含 GPT）快速了解基线、能力与测试入口。

## 基线（不可改写的锚点）

| 线 | 完整 SHA | 分支 |
|---|---|---|
| 生产基线 | `da123e22e0b413b5118c6d87632cda72204422d7` | `feat/phase55-collab-parallel` |
| R2 实验线 | `fecfd5f5bb90b59abb432abec4f5e166674205f2` | `feat/r2-collab-pool` |

- `main` 分支 = 生产基线的同名引用（同一哈希，非快照伪造）
- GPT 修复分支从上表两条基线分别新建（见协作方文档 `03_GitHub转接与后续任务.md` §2）
- 历史纪律：不重写历史、不 squash、不强推

## 系统是什么

AI 驱动的动画短片生产流水线：YAML 剧本 → Resource/Scene/Director Agent（资产/场景/分镜）→ Assembly Agent + Sequence 域（装配/拼接/混音）→ ffmpeg 成片。媒体生成走 Agnes API（文本/生图/生视频），密钥经环境变量引用（`config/config.yaml` 全占位符，真值在本地 `.env`，**永不入仓**）。

## 运行

- 后端：`api_server.py`（FastAPI，端口 8000）
- 前端：Vue/Vite（端口 7777，`npm run dev`）
- 图床：GitHub Pages（agnes-frame-host）+ imgbb 回退

## 测试入口

```bash
python -m pytest tests/ -q        # 全量 mock 回归：2847 项，零外部 API 配额，可离线
python -m pytest tests/ --collect-only -q   # 收集校验
```

- 本机环境：Python 3.13.0（Windows 11）
- CI 依赖锁：`requirements-ci.txt`（核心集；rembg/onnxruntime 等重依赖为惰性导入未含）
- 云端验证：`.github/workflows/ci.yml`（windows-latest + Python 3.13）
- 已知失败基线：约 291 项（GPT 交接包记录）；CI 初跑预期为红，按 **nodeid 身份**与基线差分，禁止以失败总数放行；身份对比器如需改核心代码 → 开 GPT 任务卡

## 协作分工

- **GPT**：核心代码修改（分支 + PR，`collab/` 前缀分支）
- **Claude Code（执行侧）**：验证/回归/迁移操作/归档（`ci/`、`docs/`、`chore/` 前缀）
- **用户**：看片验收、裁决

## 关联仓库

- B 运行数据：`Q-1844/agent-system-v2-rundata`（冻结批次，含 source SHA 链接）
- C 档案：`Q-1844/agent-system-v2-archive`（Phase 报告/交接/迁移记录）
- D 图床：`Q-1844/agnes-frame-host`（只读验收，不改动）
