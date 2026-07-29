# 动画分镜工作流

AI驱动的动画创作工具，基于 Vue3 + Vite + TypeScript + NaiveUI 构建。

## 功能特性

- 🎬 **分镜生成** - AI自动生成分镜关键帧
- 🎭 **角色管理** - 管理角色参考图和一致性
- 🎥 **动画预览** - 预览过渡动画效果
- 🎵 **音频合成** - TTS语音合成和字幕生成
- 📊 **质量评估** - AI自动评估生成质量
- ⚙️ **灵活配置** - 自定义各种生成参数

## 技术栈

- **前端框架**: Vue 3.5
- **构建工具**: Vite 5
- **类型系统**: TypeScript 5
- **UI组件库**: Naive UI 2.4
- **状态管理**: Pinia 2
- **路由**: Vue Router 4
- **CSS工具**: UnoCSS

## 快速开始

### 安装依赖

```bash
cd animation-storyboard
npm install
```

### 启动开发服务器

```bash
npm run dev
```

访问 http://localhost:3000

### 构建生产版本

```bash
npm run build
```

## 项目结构

```
src/
├── assets/          # 静态资源
├── components/      # 公共组件
│   ├── common/      # 通用组件
│   ├── layout/      # 布局组件
│   └── storyboard/  # 分镜相关组件
├── constants/       # 常量定义
├── hooks/           # 组合式函数
├── layouts/         # 布局文件
├── router/          # 路由配置
├── store/           # 状态管理
├── styles/          # 样式文件
├── typings/         # 类型定义
├── utils/           # 工具函数
└── views/           # 页面视图
    ├── dashboard/   # 仪表盘
    ├── characters/  # 角色管理
    ├── storyboard/  # 分镜画廊
    ├── animation/   # 动画预览
    ├── audio/       # 音频管理
    └── settings/    # 设置
```

## API配置

在设置页面配置以下API：

- **Agnes API** - 用于文本理解、图片生成、视频生成
- **Mimo TTS API** - 用于语音合成

## 使用流程

1. 准备剧本YAML文件
2. 运行Python后端生成分镜
3. 在前端界面查看和确认结果
4. 导出最终视频

## 许可证

MIT
