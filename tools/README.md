# tools/ — 执行文件专区（工作脚本暂存）

- 定位：诊断探针、评审服务器、一次性工具等**非核心代码**的工作脚本，与 src/ 核心严格分离
- 迁移纪律：累计 **50 份**或**战役收尾时（先到为准）**，整批迁移至 C 仓 archive/tools_batch_N/（迁移=git mv 语义的跨仓搬运+SHA256 记录）
- 永不迁移项：`.github/workflows/`、`requirements-ci.txt`（CI 活文件，平台要求必须在本仓）
