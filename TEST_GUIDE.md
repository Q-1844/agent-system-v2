# 测试指南

## 启动步骤

### 1. 启动后端API

```bash
cd animation-storyboard
python api_server.py
```

后端将在 http://localhost:8000 启动

### 2. 启动前端

```bash
npm run dev
```

前端将在 http://localhost:7777 (或类似端口) 启动

### 3. 打开浏览器

访问前端地址，你应该能看到：

- ✅ 左侧导航栏（Dashboard, Characters, Storyboard, Animation, Audio, Settings）
- ✅ 右侧仪表盘内容
- ✅ 统计卡片（Total: 3, Done: 2, Active: 1, Pending: 0）
- ✅ 进度条（67%）
- ✅ 3个场景卡片

## 测试API

### 获取项目列表
```bash
curl http://localhost:8000/api/projects
```

### 获取角色列表
```bash
curl http://localhost:8000/api/projects/我的动画短片/characters
```

### 获取场景列表
```bash
curl http://localhost:8000/api/projects/我的动画短片/scenes
```

### 获取项目进度
```bash
curl http://localhost:8000/api/projects/我的动画短片/progress
```

## 示例数据

项目 `我的动画短片` 已包含示例数据：

### 角色
- 小星 - 银发蓝眼的女孩
- 暗影 - 黑色盔甲的反派

### 场景
1. ✅ 小星站在山顶（已完成，8.5分）
2. ✅ 暗影从云层浮现（已完成，8.2分）
3. 🔄 两人对峙（进行中，8.5分）

## 下一步

1. 点击左侧导航栏切换页面
2. 点击"开始生成"按钮测试工作流
3. 查看角色管理页面
4. 查看分镜画廊页面
