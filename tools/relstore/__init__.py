# tools/relstore/__init__.py
"""Release 归档/恢复核心（G2 本地隔离开发）。

子模块：
- manifest：包内 payload manifest v2（严格 JSON、无自哈希）与包外 receipt
- safe_tar：安全检视/解包/确定性打包/分卷重组
- path_rebase：改名恢复的白名单 canonical JSON 重定基
- release_adapter / fake_release_adapter：存储接口（G2 真实写硬禁用 + 内存 Fake）
- archive_campaign：plan→package→verify 状态机
- restore_campaign：整包对账→安全解包→payload/legacy 双模式恢复
"""
