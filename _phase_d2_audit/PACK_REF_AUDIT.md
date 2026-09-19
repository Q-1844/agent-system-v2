# Phase D2 §3 — Pack reference 全量审计（52 镜，read-only）

- READY 且编译成功：47 / 52
- 场景参考存在：47 / 47
- 场景参考位置=refs[0]：47
- cap(6) 触发：30
- first 实际发送含 ≥2 张人形：47
- first 实际发送含 ≥4 张人形：18

## 按 shot_size 分组（首帧实际发送序列 = temporal 过滤 + cap 后）

### close-up（16 镜）

| shot | refs(pack) | scene@pos | first 发送序列 | 人形(发送) |
|---|---|---|---|---|
| s09_c03 | 9 | 0 | SCENE→LOOK→LOOK | 2 |
| s09_c08 | 9 | 0 | SCENE→LOOK→LOOK | 2 |
| s10_c02 | 9 | 0 | SCENE→LOOK→LOOK | 2 |
| s10_c03 | 5 | 0 | SCENE→IDENT→IDENT→IDENT→LOOK | 4 |
| s10_c06 | 5 | 0 | SCENE→IDENT→IDENT→IDENT→LOOK | 4 |
| s11_c02 | 9 | 0 | SCENE→LOOK→LOOK | 2 |
| s11_c03 | 5 | 0 | SCENE→IDENT→IDENT→IDENT→LOOK | 4 |
| s13_c06 | 9 | 0 | SCENE→LOOK→LOOK | 2 |
| s15_c04 | 5 | 0 | SCENE→IDENT→IDENT→IDENT→LOOK | 4 |
| s16_c02 | 9 | 0 | SCENE→LOOK→LOOK | 2 |
| s16_c05 | 9 | 0 | SCENE→LOOK→LOOK | 2 |
| s16_c07 | 9 | 0 | SCENE→LOOK→LOOK | 2 |
| s17_c03 | 5 | 0 | SCENE→IDENT→IDENT→IDENT→LOOK | 4 |
| s17_c06 | 5 | 0 | SCENE→IDENT→IDENT→IDENT→LOOK | 4 |
| s18_c02 | 13 | 0 | SCENE→LOOK→LOOK→LOOK | 3 |
| s18_c07 | 9 | 0 | SCENE→LOOK→LOOK | 2 |

### extreme close-up（3 镜）

| shot | refs(pack) | scene@pos | first 发送序列 | 人形(发送) |
|---|---|---|---|---|
| s11_c05 | 5 | 0 | SCENE→IDENT→IDENT→IDENT→LOOK | 4 |
| s17_c02 | 5 | 0 | SCENE→IDENT→IDENT→IDENT→LOOK | 4 |
| s18_c06 | 5 | 0 | SCENE→IDENT→IDENT→IDENT→LOOK | 4 |

### extreme wide shot（3 镜）

| shot | refs(pack) | scene@pos | first 发送序列 | 人形(发送) |
|---|---|---|---|---|
| s09_c01 | 5 | 0 | SCENE→IDENT→IDENT→IDENT→LOOK | 4 |
| s13_c01 | 13 | 0 | SCENE→LOOK→LOOK→LOOK | 3 |
| s17_c07 | 9 | 0 | SCENE→LOOK→LOOK | 2 |

### medium close-up（2 镜）

| shot | refs(pack) | scene@pos | first 发送序列 | 人形(发送) |
|---|---|---|---|---|
| s09_c07 | 9 | 0 | SCENE→LOOK→LOOK | 2 |
| s14_c05 | 9 | 0 | SCENE→LOOK→LOOK | 2 |

### medium shot（20 镜）

| shot | refs(pack) | scene@pos | first 发送序列 | 人形(发送) |
|---|---|---|---|---|
| s09_c02 | 5 | 0 | SCENE→IDENT→IDENT→IDENT→LOOK | 4 |
| s09_c05 | 5 | 0 | SCENE→IDENT→IDENT→IDENT→LOOK | 4 |
| s10_c01 | 13 | 0 | SCENE→LOOK→LOOK→LOOK | 3 |
| s11_c01 | 13 | 0 | SCENE→LOOK→LOOK→LOOK | 3 |
| s11_c04 | 9 | 0 | SCENE→LOOK→LOOK | 2 |
| s11_c06 | 9 | 0 | SCENE→LOOK→LOOK | 2 |
| s13_c02 | 13 | 0 | SCENE→LOOK→LOOK→LOOK | 3 |
| s13_c03 | 5 | 0 | SCENE→IDENT→IDENT→IDENT→LOOK | 4 |
| s13_c04 | 9 | 0 | SCENE→LOOK→LOOK | 2 |
| s13_c05 | 5 | 0 | SCENE→IDENT→IDENT→IDENT→LOOK | 4 |
| s14_c03 | 5 | 0 | SCENE→IDENT→IDENT→IDENT→LOOK | 4 |
| s15_c01 | 17 | 0 | SCENE→LOOK→LOOK→LOOK→LOOK | 4 |
| s15_c03 | 5 | 0 | SCENE→IDENT→IDENT→IDENT→LOOK | 4 |
| s15_c05 | 9 | 0 | SCENE→LOOK→LOOK | 2 |
| s15_c06 | 13 | 0 | SCENE→LOOK→LOOK→LOOK | 3 |
| s16_c01 | 13 | 0 | SCENE→LOOK→LOOK→LOOK | 3 |
| s16_c06 | 13 | 0 | SCENE→LOOK→LOOK→LOOK | 3 |
| s17_c01 | 9 | 0 | SCENE→LOOK→LOOK | 2 |
| s18_c03 | 13 | 0 | SCENE→LOOK→LOOK→LOOK | 3 |
| s18_c05 | 5 | 0 | SCENE→IDENT→IDENT→IDENT→LOOK | 4 |

### wide shot（3 镜）

| shot | refs(pack) | scene@pos | first 发送序列 | 人形(发送) |
|---|---|---|---|---|
| s09_c06 | 9 | 0 | SCENE→LOOK→LOOK | 2 |
| s14_c01 | 9 | 0 | SCENE→LOOK→LOOK | 2 |
| s18_c08 | 13 | 0 | SCENE→LOOK→LOOK→LOOK | 3 |
