# Phase D2 §18 — 52 镜 Before 质量矩阵（review_shot 真机）

审查时间：2026-09-19 18:01；对象=各镜当前 selected first frame

- **PASS**: 1 — s09_c06
- **NEEDS_REFRAME**: 24 — s09_c05 s09_c08 s09_c07 s10_c01 s10_c02 s10_c06 s11_c06 s13_c01 s13_c02 s13_c03 s13_c04 s13_c06 s14_c03 s14_c04 s14_c05 s16_c03 s16_c06 s16_c07 s17_c01 s17_c07 s18_c02 s18_c03 s18_c05 s18_c07
- **NEEDS_CAST_FIX**: 23 — s09_c01 s09_c02 s09_c03 s11_c01 s11_c02 s11_c03 s11_c04 s11_c05 s14_c01 s15_c01 s15_c02 s15_c03 s15_c04 s15_c05 s15_c06 s16_c01 s16_c02 s16_c05 s17_c02 s17_c03 s17_c06 s18_c01 s18_c06
- **NEEDS_REVIEW**: 3 — s13_c05 s16_c08 s18_c08
- **REVIEW_ERROR**: 1 — s10_c03

| shot | size | 类别 | hard fail | soft fail | 反馈摘要 |
|---|---|---|---|---|---|
| s09_c01 | extreme wide shot | NEEDS_CAST_FIX | unexpected_people, shot_size_correctness, framing_and_composition | cast_cardinality, subject_scale | 改景别为EWS：把沈夜+老马压到画面下1/3以内，让驿站与雨幕成为视觉主体。 |
| s09_c02 | medium shot | NEEDS_CAST_FIX | duplicate_identity, unexpected_people, shot_size_correctness, framing_and_composition | cast_cardinality | Hard fail：画面将主角复制成 2 人（正背相对），须修复模型生成，确保只有 1 个沈夜。 |
| s09_c03 | close-up | NEEDS_CAST_FIX | duplicate_identity, shot_size_correctness, framing_and_composition | subject_scale, scene_scope_correctness, identity_look | 镜头需重新生成为CU特写，聚焦手部、信件及沈夜脸部表情，背景需虚化并移至室内。 |
| s09_c05 | medium shot | NEEDS_REFRAME | missing_character, shot_size_correctness | subject_scale, prop_fidelity | 景别需改为MS（中景），让人物占据画面更大比例，便于展现面部表情和眼神细节。 |
| s09_c08 | close-up | NEEDS_REFRAME | shot_size_correctness | subject_scale | 将景别从 WS 改为 CU：裁剪至二人上半身或面部特写，确保眼神交汇成为视觉中心。 |
| s09_c06 | wide shot | PASS | - | - |  |
| s09_c07 | medium close-up | NEEDS_REFRAME | shot_size_correctness | prop_fidelity | 重生成：必须将景别从WS推近至MCU（Medium Close-Up），重点展示柳衣在窗边的上半身及面部神态。 |
| s10_c01 | medium shot | NEEDS_REFRAME | shot_size_correctness | subject_scale | 景别错误：实际为EWS而非中景，需放大至中景(MS)以聚焦人物。 |
| s10_c02 | close-up | NEEDS_REFRAME | shot_size_correctness, framing_and_composition | subject_scale, camera_semantics, prop_fidelity | 景别错误：必须生成近景或特写，目前画面过远（EWS），需推近至主体。 |
| s10_c03 | close-up | REVIEW_ERROR | - | - |  |
| s10_c06 | close-up | NEEDS_REFRAME | shot_size_correctness | cast_cardinality | 景别从WS改为CU，聚焦周伯面部表情和含泪双眼 |
| s11_c01 | medium shot | NEEDS_CAST_FIX | duplicate_identity, unexpected_people, shot_size_correctness | cast_cardinality, prop_fidelity | 纠正景别：从广角（WS）改为中景（MS），让人物主体占据画面主要空间。 |
| s11_c02 | close-up | NEEDS_CAST_FIX | unexpected_people, shot_size_correctness | cast_cardinality, subject_scale | 核心问题：镜头是CU近景，但生成了WS全景，需重生成并裁剪至两人面部特写。 |
| s11_c03 | close-up | NEEDS_CAST_FIX | duplicate_identity, unexpected_people, shot_size_correctness, framing_and_composition | cast_cardinality, subject_scale | 必须修正景别：重生成 close-up，聚焦沈夜的面部表情和手中的信件，去掉背景中的其他人物。 |
| s11_c04 | medium shot | NEEDS_CAST_FIX | duplicate_identity, shot_size_correctness | cast_cardinality | Remove duplicate character: The figure on the left is an exa |
| s11_c05 | extreme close-up | NEEDS_CAST_FIX | duplicate_identity, unexpected_people, shot_size_correctness | cast_cardinality | 必须切换至ECU景别，仅保留信纸局部与沈夜手指，删除所有背景人物与街道元素。 |
| s11_c06 | medium shot | NEEDS_REFRAME | shot_size_correctness | subject_scale, prop_fidelity | 景别过大（WS），请重生成Medium Shot，需切至膝盖以上，放大人物主体。 |
| s13_c01 | extreme wide shot | NEEDS_REFRAME | shot_size_correctness | subject_scale, identity_look, prop_fidelity | 必须重生成Extreme Wide Shot，人物应缩小至剪影，确保远山和晨雾成为画面上2/3的绝对主体。 |
| s13_c02 | medium shot | NEEDS_REFRAME | missing_character, shot_size_correctness | cast_cardinality, subject_scale, identity_look, prop_fidelity | Correct shot size to Medium Shot; characters must be larger  |
| s13_c03 | medium shot | NEEDS_REFRAME | shot_size_correctness, framing_and_composition | subject_scale, prop_fidelity | 景别错误：当前为全景，必须改为中景（MS），聚焦躯干与面部。 |
| s13_c04 | medium shot | NEEDS_REFRAME | shot_size_correctness | prop_fidelity | 调整景别：将镜头推近至Medium Shot（膝上/半身），聚焦人物情绪。 |
| s13_c06 | close-up | NEEDS_REFRAME | shot_size_correctness, framing_and_composition | subject_scale, prop_fidelity | 画面是广角双人镜头，必须重生成为手部特写以聚焦断剑与手势。 |
| s13_c05 | medium shot | NEEDS_REVIEW | - | prop_fidelity | 检查周伯肩膀处是否需要添加绷带细节以符合镜头意图。 |
| s14_c01 | wide shot | NEEDS_CAST_FIX | unexpected_people, missing_character | cast_cardinality, subject_scale | 核心叙事错误：本镜应为空镜（环境音+马蹄声），不应直接展示柳衣和周伯的人像。 |
| s14_c03 | medium shot | NEEDS_REFRAME | shot_size_correctness | cast_cardinality, subject_scale, scene_scope_correctness, scene_identity | 景别问题：镜头要求Medium Shot，实际是Extreme Wide Shot，需重新生成或裁剪至人物半身。 |
| s14_c04 | extreme close-up | NEEDS_REFRAME | shot_size_correctness | - | 当前画面为 MS，需裁剪或重新生成以突出屋檐滴水细节，达到 ECU 的视觉强度。 |
| s14_c05 | medium close-up | NEEDS_REFRAME | shot_size_correctness | scene_scope_correctness, identity_look, prop_fidelity | 修正景别：从全景切换为中近景（MCU/CU），聚焦于柳衣的面部表情与眼神。 |
| s15_c01 | medium shot | NEEDS_CAST_FIX | unexpected_people, shot_size_correctness, framing_and_composition | cast_cardinality, subject_scale, scene_scope_correctness, scene_identity | 重生成medium shot，人物占比需放大至画面主体，当前EWS无法体现对峙张力。 |
| s15_c02 | wide shot | NEEDS_CAST_FIX | duplicate_identity, missing_character, framing_and_composition | camera_semantics | 萧寒未在画面中出现，需补入出场。 |
| s15_c03 | medium shot | NEEDS_CAST_FIX | unexpected_people, shot_size_correctness | cast_cardinality, subject_scale, prop_fidelity | 严重错景：必须改为MS（中景），目前EWS导致人物太小，无法看清面部表情和情绪。 |
| s15_c04 | close-up | NEEDS_CAST_FIX | duplicate_identity, unexpected_people, shot_size_correctness, framing_and_composition | cast_cardinality, subject_scale | 重生成：必须删除画面右侧或左侧的一位老者，只保留单一人物。 |
| s15_c05 | medium shot | NEEDS_CAST_FIX | duplicate_identity, unexpected_people, shot_size_correctness | cast_cardinality | Shot size mismatch: Director asked for MS but generated a WS |
| s15_c06 | medium shot | NEEDS_CAST_FIX | unexpected_people, shot_size_correctness | cast_cardinality, scene_identity | 场景错误：画面处于室外院落，必须改为落雁驿内景大堂（背景应为墙壁、门窗、蜡烛而非天空和雨）。 |
| s16_c01 | medium shot | NEEDS_CAST_FIX | duplicate_identity, unexpected_people, shot_size_correctness | cast_cardinality, subject_scale, scene_scope_correctness, prop_fidelity, scene_identity | 重生成MS/室内近景：三人应占据画面2/3以上高度，去除远景建筑/远山/天空。 |
| s16_c02 | close-up | NEEDS_CAST_FIX | unexpected_people, shot_size_correctness, framing_and_composition | cast_cardinality, subject_scale, camera_semantics, scene_scope_correctness, scene_identity | 景别严重错误：需从全景强制裁剪或重生成至特写 (CU) 范围。 |
| s16_c03 | close-up | NEEDS_REFRAME | missing_character, shot_size_correctness, framing_and_composition | cast_cardinality, subject_scale | 镜头要求CU近景但画面实际WS全景;需重生成近景构图聚焦周伯挡暗器动作 |
| s16_c05 | close-up | NEEDS_CAST_FIX | unexpected_people, missing_character, shot_size_correctness | cast_cardinality, subject_scale, identity_look, prop_fidelity | 景别错误：要求CU近景，实际EWS极远景；需重新生成以特写聚焦断剑瞬间与人物表情。 |
| s16_c08 | close-up | NEEDS_REVIEW | - | - | 当前画面为静态烛火，如需表现掌风扫灭瞬间，建议补充火焰熄灭动态帧。 |
| s16_c06 | medium shot | NEEDS_REFRAME | shot_size_correctness, framing_and_composition | camera_semantics, scene_scope_correctness, prop_fidelity, scene_identity | 景别严重错误：目前为建场用远景（WS），需改为Medium Shot聚焦人物近景以传达受伤情节。 |
| s16_c07 | close-up | NEEDS_REFRAME | shot_size_correctness | subject_scale | 景别严重错误：expected CU/MS用于传达面部情绪爆发，observed WS全身远景无法建立情感连接，必须重生 |
| s17_c01 | medium shot | NEEDS_REFRAME | shot_size_correctness, framing_and_composition | subject_scale, scene_scope_correctness, prop_fidelity, scene_identity | 必须重新生成；当前是极远全景，而非中景（Medium Shot）。 |
| s17_c02 | extreme close-up | NEEDS_CAST_FIX | duplicate_identity, unexpected_people, shot_size_correctness, framing_and_composition | cast_cardinality, subject_scale, camera_semantics, prop_fidelity, scene_identity | 景别严重错误：由ECU/CU错生成为EWS，需重新生成极特写镜头。 |
| s17_c03 | close-up | NEEDS_CAST_FIX | duplicate_identity, unexpected_people, shot_size_correctness, framing_and_composition | cast_cardinality, subject_scale, scene_identity | 镜头完全跑偏，请重置为面部特写构图。 |
| s17_c06 | close-up | NEEDS_CAST_FIX | unexpected_people, shot_size_correctness | cast_cardinality | 严重失配：镜头要求 Close-up（特写），画面实为 WS（全景），必须重选景。 |
| s17_c07 | extreme wide shot | NEEDS_REFRAME | shot_size_correctness, framing_and_composition | subject_scale, camera_semantics | 严重偏离景别要求：需要重新生成为极远景(Extreme Wide Shot)，让人物成为环境中的微小剪影。 |
| s18_c01 | medium shot | NEEDS_CAST_FIX | unexpected_people, shot_size_correctness, framing_and_composition | cast_cardinality, scene_scope_correctness, prop_fidelity, scene_identity | Expected MS, Observed WS: 需重新生成以符合中景要求，确保人物膝上可见。 |
| s18_c02 | close-up | NEEDS_REFRAME | shot_size_correctness | subject_scale, prop_fidelity | 必须将景别从WS调整为CU，聚焦沈夜面部以体现眼神决绝。 |
| s18_c03 | medium shot | NEEDS_REFRAME | shot_size_correctness | subject_scale, scene_scope_correctness, identity_look, prop_fidelity | Shot size mismatch: generate as MS (medium shot) to fill fra |
| s18_c05 | medium shot | NEEDS_REFRAME | missing_character, shot_size_correctness | scene_scope_correctness, prop_fidelity | 修改镜头为内景柴房门口站位，而非庭院中央 |
| s18_c06 | extreme close-up | NEEDS_CAST_FIX | duplicate_identity, unexpected_people, shot_size_correctness | cast_cardinality, subject_scale | 镜头id s18_c06 要求ECU玉佩特写，当前为WS全景，需重新生成聚焦沈夜手中玉佩的特写画面。 |
| s18_c07 | close-up | NEEDS_REFRAME | shot_size_correctness | camera_semantics, identity_look, prop_fidelity | 需要修改为近景特写，聚焦柳衣面部表情变化。 |
| s18_c08 | wide shot | NEEDS_REVIEW | - | scene_scope_correctness, identity_look, prop_fidelity, scene_identity | 重绘场景：必须将背景改为室内大堂，增加摇曳烛光与雨夜氛围，移除外部河流与远山。 |