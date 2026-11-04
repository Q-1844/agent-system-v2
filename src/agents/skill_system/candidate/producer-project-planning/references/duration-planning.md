# 时长规划（duration-planning）

## 四种模式

| 模式 | 用户说了什么 | 契约字段 | 语义 |
|---|---|---|---|
| exact | "必须正好 120 秒" | target_seconds | 硬边界，验收按精确值 |
| range | "100 到 140 秒之间" | range_seconds | 硬边界，窗口内自由 |
| approximate | "大概两分钟" | target_seconds + 策略容差 | 软目标；窗口 = target*(1±tol)，容差写入证据 |
| unspecified | "做个卫星视频" | 全空 | 内容驱动；付费前必须先形成有限的内部执行估计 |

**铁律**：approximate 的窗口是策略推导，不是用户精度；unspecified 不得自造
target_seconds。窗口来源必须写进 evidence（`user_explicit` vs
`policy_derived_not_user_precision`）。

## SceneBudget 设计算法

每个故事段一份预算：

```
target_s        本场目标秒数（可选）
min_s / max_s   本场可接受窗口
shot_min / shot_max   本场镜头数范围
per_shot 4-11 秒（与生产端校验窗口一致）
```

确定性可行性（校验不过就不许进媒体阶段）：

1. `shot_min * 4 <= max_s`（最少镜头用最短秒数也塞得进窗口上界）
2. `shot_max * 11 >= min_s`（最多镜头用最长秒数也能覆盖窗口下界）
3. `target_s` 在 `[min_s, max_s]` 内
4. 全片：各场窗口之和与整体软/硬目标相容；付费上限 `video_seconds` 覆盖
   `sum(target_s)`

**不要机械均分**「每段 4 镜×10 秒」——按叙事节奏分配（高潮段短镜密切、陈述段长镜），
这是本技能存在的意义。

## 时长从哪来（unspecified 时）

由内容驱动：先让 Script Writer 定故事与场次，再按每场戏的内容量推导窗口，
最后合成全片内部执行估计（producer_inferred）。这个估计付费前要接受与
exact 相同的确定性校验，但不对外宣称"用户要求了 N 秒"。
