# src/sequence/__init__.py
# Sequence S1 — Assembly Foundation（24.md）。
# 独立 backend domain：把多个已完成的有声 Shot / VideoVersion 按顺序组合成
# 可播放、可导出的连续 AV 片段（24.md §1）。
#
# 🔴 边界（24.md §3）：Sequence 是 Shot Production 的消费者，不是它的第二个入口。
#    本 domain 只读 Project/Shot.final_video truth（src/core/store.py），绝不修改
#    Shot canonical / VideoVersion 生成 / NativeAV pack / compiler / Agnes adapter。
# 🔴 S1 只支持 Hard Cut（§11）；不做 music/narration/mixing（§27）；不做 UI（§26）。
