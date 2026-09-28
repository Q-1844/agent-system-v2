# 实验 B 报告：s02_c02 融合首帧起步 vs 裸尾帧接力基线（2026-09-27）

约束遵守：未改任何 Skill；未继续旧桥接任务；未动 s03_c01；**原项目 `Crane_Lake_P40A` canonical 零改动**（实验在字节级克隆 `Crane_Lake_P40A_expB` 中执行，原因是视频 handler 完成后会自动 set-final + 首帧槽同步 + 末帧重提取，无法满足"不自动替换 canonical 已选视频"）。

## 0. 上游端点复核（s02_c01 真实尾帧）

目检 `output/frames/s02_c01_video_end.jpg`（vv_97699959）：extreme wide shot，鹤为湖面上方小剪影（身份特征不可读），但湖泊镜面反光/森林/逆光/下降滑翔动作清晰。**判定：非"身份明显错误"，属"远景身份信息不足但场景动作可用" → 按规则继续实验。**

## 1. 候选 A（视频双参考）＝本地不可执行，记录缺口

- v2.0 服务端 ti2vid 单图上限：2 张参考 400 `ti2vid supports at most 1 image`（32.md X005 真机实证；AGNES_API_FACTS §3.2 D）。
- 本地编译层同样强制恰好 1 张（`compile_agnes_video_call` multi_reference_slot_invalid，agnes_adapter.py:116-123）。
- 结论：**未调用**（不凑数）。若未来要真双参考，路径 = 2.5 契约 `mode=reference` 的形状验证（当前"未验证勿发"+适配器未实现，agnes_client.py:544-568）。

## 2. 实际调用清单（全部付费调用，共 2 次）

| # | 调用 | 模型 | 模式 | 图片语义 | 实际消耗 |
|---|---|---|---|---|---|
| ① | 首帧合成 ×2 候选 | agnes-image-2.1-flash（1K/16:9） | BOUND first_frame + reference_previous，ref_layout=continuity_tail | refs=[场景 SIV（已批准），approved Look ref_310e243e4f14，s02_c01 真实尾帧（接力参考）]；legacy 身份视图按预算裁剪（receipt dropped_optional 可证） | 2 张图 |
| ② | 视频生成 | agnes-video-v2.0（URL 路径佐证；未显式选 2.5） | i2v（首槽硬边界） | 首槽=融合首帧 ff_f38c1865_1（Look+尾帧合成，目检选定）；prompt=存量校准俯冲语义+Pack 生产上下文+风格 | 8.04s 视频（193f@24fps，1280x704） |

配额账本：`cache/quota.json`（注意：该账本按服务进程内存记账，本实验确认消耗=1 视频 8.04s + 2 图）。

## 3. Receipts（真实落库，位置 `projects/Crane_Lake_P40A_expB/output/project.json`）

- 首帧批次 `ff_f38c1865`：generation_basis={binding_revision f28fc0dc, READY, scene=full, human=keep, ref_layout=continuity_tail, sent=[scene_instance_view, character_look], used=[ref_cd859377bdc8, ref_310e243e4f14]}；候选 v1/v2（cos 图床 URL 各自持久化）。
- 视频版本 `vv_6e26ffbc`：input_visuals=[{slot:first, semantic_role:hard_start_boundary, url=融合首帧, candidate_id, batch_id}]；Native AV generation_basis（provider/model/provider_mode 等）完整；prompt_snapshot=俯冲语义。
- 克隆内字段翻转（实验必需，可逆）：s02_c02 opening_source independent→reference_previous（source_version=vv_97699959，continuity=ready）、link_mode multi→i2v。
- 旧资产保护：生成前已备份克隆内将被管线同路径覆盖的文件（s02_c02.mp4=vv_11e01881 字节）至 `_tmp/expB/baseline_current_vv11e01881.mp4`；基线 v1 从 URL 归档为 `baseline_v1_vve716ddfe.mp4`。原项目文件零触碰。

## 4. 并排比较（3×3：行=v1基线/现行基线/候选B，列=首0.3s/中4s/末7.7s）

对比图：`_tmp/expB/comparison_3x3.jpg`；单帧与三段视频均在 `_tmp/expB/`。

| 检查维度 | 基线 v1（vv_e716ddfe 裸尾帧接力） | 基线现行（vv_11e01881 裸尾帧接力重试） | 候选 B（融合首帧起步，vv_6e26ffbc） |
|---|---|---|---|
| 与上镜剪辑（开场） | EWS≈尾帧，剪辑近不可见 | 同左（剪辑最平滑） | EWS→MS 景别跳变（符合分镜目标），同湖面/逆光/倒影语境，读作正常切换 |
| 角色身份特征 | **不合格**：全白颈、红眉状冠、白翅无黑 | **不合格**：红冠+粉喙、无黑颈、翅全白 | **合格**：黑颈+红冠+黑次级飞羽+黑腿（与 Look 一致） |
| 身体和翅膀 | 天鹅状体态 | 天鹅状浮水体态 | 鹤形正确，翅展/收合自然 |
| 动作 | 俯冲→浮水拍翼 | 俯冲→低浮水面 | 俯冲→贴水俯冲→**触水站立** |
| 剧情（俯冲确认湖泊） | 前 2/3 命中，结尾变形 | 同左 | 命中；但结尾**提前完成落水**（expected_tail=空中减速） |
| 景别目标（medium shot） | EWS 起步→CU 过冲 | 同左 | MS 起步→俯冲近景→MS 落点，全程贴近目标 |
| 末帧可用性（供下镜接力） | 漂移尾帧（禁用） | 漂移尾帧（禁用） | 身份合格，但状态=已触水（若选用需按此重定 s03_c01 开场意图） |

候选 B 已知瑕疵（如实记录）：末帧颈前羽色偏粉（光照染色或轻度漂移，起始/中段黑颈明确）；结尾比剧本预期多做了半拍（已落到水面站立）。

## 5. 推荐

**推荐候选 B（融合首帧起步，vv_6e26ffbc）**：身份维度压倒性胜出（两条裸尾帧基线全程无黑颈/黑飞羽，是被机制注定的失败——远景尾帧不含身份信息，multi 单参考槽装不下第二张图）；景别全程贴合 medium shot 目标；与上镜的语境连续性由融合首帧承载。代价是结尾动作提前半拍 + 末帧颈前偏粉。

供你裁决的三选：
- **选 B 收编**：我把克隆内该配方应用到原项目（生成同样的候选并人工选定，原 canonical 版本保留在 video_versions 历史）。
- **接受基线**：维持 vv_11e01881（不建议——身份链已断，s03_c01 之前必须解决）。
- **再迭代 B**：针对末段提前落水，改写 prompt 收束句（"ends airborne, wings decelerating, feet just above surface"）后重生成一条（+1 视频配额）。

未做的事：s03_c01 未生成（等 s02_c02 定版后再决策）；桥接未续（端点不合格维持暂停）；Skill/正式代码零改动。
