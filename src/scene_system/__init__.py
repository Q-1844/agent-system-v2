# src/scene_system/
# Scene System V1 — Phase S1：空间与场景事实系统基础（Location / SpatialAnchor /
# SetLayout / LocationView / SceneInstance / SceneState）。
#
# 🔴 独立 canonical companion：projects/{project}/output/scene_system.json。
#    不修改 src/core/models.py（Project/Scene/Shot）、project.json schema、
#    src/resources/*（Production Resources V1 已冻结）、src/agents/*（本轮禁止 Agent 接入）。
#
# 与现有 Story Scene 的关系（Phase S1 审计结论，无 BLOCKER）：
#   - 现有 Scene（"s01"）= 故事结构段（summary/location 自由文本/characters/shots）。
#     scene_id 是身份证号，永不变名。本系统不替换、不重命名、不改其 schema。
#   - SceneInstance = "某个 Story Scene 在某个 Location 中发生时的生产空间状态"，
#     通过 story_scene_id 单向引用现有 Scene。二者有意不同名不同物。
#   - 现有 Scene.location / time_of_day（创作层自由文本）保持原样；
#     SceneState.time_of_day 等是生产时刻结构化状态（另一层真相），不迁移不覆盖。
