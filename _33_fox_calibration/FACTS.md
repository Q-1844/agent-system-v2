# 33.md Animal Character Creative Calibration V1 — Preflight 与固定常量

日期：2026-09-21 ｜ 分支 `feat/agent-v2-phase-d-golden-run` @ b72eb30 ｜ 性质：Creative Calibration（小规模、强控制变量）

## 一、Preflight 审计（§二 要求，代码路径实证）

| 确认项 | 结论 | 证据 |
|---|---|---|
| 当前视频模型 | agnes-video-v2.0（legacy 契约：num_frames 8n+1≤441、seed/negative 可传、multi/keyframes 可用；2.5 拒 multi/keyframes） | `src/api/agnes_client.py:528-532`、FACTS §3.2 |
| 真正支持的 reference 数量 | **1 张**（multi 形态服务端按 ti2vid 处理，`ti2vid supports at most 1 image`，32.md 7 次 400 实证） | FACTS §3.2D :193 |
| reference 输入形式 | multi：`extra_body.image=[URL]`（公网 URL；data URI 视频拒绝→需公网托管）；i2v：顶层 `image=URL` | `agnes_client.py:570-624` |
| 是否支持 subject/image reference | 支持（=multi 单图）；无独立 subject-reference 概念 | 同上 |
| **是否会把 reference 当 first frame** | **强首帧化条件**：32.md mS 样本实证「场景板复刻前缀 ~0.7-2.8s 近冻结」——reference 会以首帧相似方式强条件化开场。→ 本轮 §九 reference-leakage 检查的核心假设：R1 白底板/R2 中性板/R3 电影感图的泄漏形态不同 | 32.md `_patterns.ms_prefix` |
| 隐藏 fallback | 无；fail-closed；唯一自动重试=v2.0 提交 400 含 num_frames 降帧 | 31.md/32.md 审计在档 |
| 当前真实 prompt | 实验直调 client 层，prompt 逐条记录进 EXPERIMENTS.jsonl（同 32.md 模式） | 本轮 §六 基础 prompt |
| 当前 provider payload | `request_kwargs` 全字段快照进记录 | 同 32.md |
| 生成产物与 provenance | video_id/task_id/provider url/本地 mp4/ffprobe 全记录；refs 记 role+url+来源 | 同 32.md |
| 配额 | Token Plan：video 500s/日、images 4000/日（config/config.yaml:75-78 校准值）；独立进程必须先 `QuotaLedger.get_default(config)` 再建 client（32.md 教训） | 本轮驱动已按此写 |
| 本轮预算 | 核心 12 条（3 Shot × R0-R3）×5.04s ≈ 60.5s + 提示词密度 2-3 条 ≈ 15s + §八 例外重跑 ≤4 条 ≈ 20s；总计 ≤100s ≪ 500s/日 | §八 不许大量 reroll |

## 二、固定常量（§一/§三/§四——本轮全程不变）

### 风格行（§四裁定）

```
Cinematic storybook animation, stylized 2D animated look. Do not render as live-action photography or 3D rendered CGI.
```

**裁定依据**：规格建议 cinematic storybook / stylized 2D；32.md 实证「2D animated image + 禁真人/禁 3D」风格行表现最好（X033/X022/X028 best 样本均该风格行）。二者合并。风格不是本轮变量。

### 狐狸设定（§三——quadruped，非人形化）

```
A natural quadruped red fox with red-orange fur, white muzzle and chest, dark
brown-black lower legs, large expressive pointed ears, amber eyes, and a full
bushy tail with a pale tip. Realistic stable fox body proportions. A small
distinctive notch in its left ear. No clothing, no accessories.
```

- 稳定辨识特征 = **左耳小缺口**（§三建议项之一）。
- 无帽子/披风/项链/武器/多层服装/大量饰品（§三禁止项全部遵守）。

### 场景事实（固定，供 R0 编译与 R3 参考）

```
A quiet vineyard: rows of grapevines with hanging clusters of purple grapes,
a dirt path running between the rows, soft warm daylight.
```

### 视频参数（全部样本一致）

model=agnes-video-v2.0；num_frames=121（5.04s）；frame_rate=24；1280x704（16:9）；1 RPM 串行 75s 间隔。

### 编译规则（关键设计，保证变量单一）

- **R1/R2/R3 prompt = §六基础 prompt 原文 + 风格行**（不加任何毛色/耳朵/尾巴/背景文字描述——身份由 reference 提供，§六明文）；
- **R0 prompt = §六基础 prompt 原文 + 狐狸设定段 + 场景事实段 + 风格行**（无视觉 reference 时身份/场景由 production facts 文本提供，§七「No visual reference ≠ 没有 Production Truth」）；
- R1/R2/R3 不加狐狸文本描述的理由：把「参考图格式」做成唯一身份来源变量，测试才锐利；若 R1-R3 也带文本锚，参考图变量被稀释。
- seed：每 Shot 配对（A/B/C 各一个稳定 seed，同 Shot 的 R0-R3 共用）→ paired comparison（§一要求）。

## 三、三种 Character Reference（§五——同一只狐狸，只变格式）

| 格式 | 内容 | 生成方式 |
|---|---|---|
| R1 Existing Character Plate | 当前系统标准角色板：白底、全身、居中、占画面 40-50%（`routers/characters.py:111-112` 原格式） | t2i |
| R2 Neutral Video Identity Reference | 单狐、无文字、无三视图拼版、柔和中性背景、占画面大比例、自然站立 3/4 角度、光线均匀、无强动作 | i2i（R1 为 ref，锁同一只狐狸） |
| R3 Cinematic Identity Reference | 同狐、简化葡萄园自然环境、电影光线、完整可见、无其他主体、环境不抢主体 | i2i（R1 为 ref，锁同一只狐狸） |

R2/R3 用 i2i 从 R1 派生=**构造性保证三张是同一只狐狸**（否则三张独立 t2i 会引入「三只不同狐狸」混淆变量）。生成后逐张人工目检；若 i2i 身份漂移，最多重试 2 次，仍失败则回退纯 t2i 并如实记录。

## 四、实验矩阵

- 核心 12 条：Shot A(CU 表演) / B(全身走) / C(单次跳) × R0(t2v 无参考) / R1 / R2 / R3。
- §八 纪律：每组合先 1 条；仅当两种 reference 表现非常接近/明显随机异常/无法判断时对该组合补 1-2 条。
- 提示词密度（§十一）：Reference Winner 出来后，选 Shot B 或 C × P1/P2（P3 仅当观察到重复失败才加一条约束）。
- 评价维度（§九）：Character(identity/face/fur/proportion/tail/species) + Motion(locomotion/anatomy/fluidity/start/landing/temporal) + Camera(framing/shot-size/smoothness/scale) + **Reference Leakage**（白底入镜/板构图复制/贴图感/姿态锁死/背景污染）+ Hard failures（duplicate fox/extra limbs/species transformation/tail multiplication/消失等，单独记录）。

## 五、交付物（§十五）

`ANIMAL_CHARACTER_CALIBRATION_V1_REPORT.md` + EXPERIMENTS.jsonl + 全部真实 MP4 + R1/R2/R3 及 provenance + payload 快照 + 逐条评价 + HTML Gallery（行=Shot A/B/C，列=R0-R3，肉眼连续对比）。PASS 判据按 §十六；通过后**停止**，不自行开始《狐狸和葡萄》Pilot。
