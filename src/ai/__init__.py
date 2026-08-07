# src/ai - AI 工作流（Phase AI-1 起新增）
#
# 模型职责窄：每个 workflow 只调文本模型理解和写文本，不做项目写入/建 Scene/生成图。
# 项目操作全部由 routers/ 的普通代码完成（确定性）。
