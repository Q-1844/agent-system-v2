# 动画分镜工作流 - 后端

AI驱动的动画创作工具，支持从剧本自动生成动画分镜。

## 功能特性

- 🎬 **剧本解析** - 自动解析YAML剧本，提取角色和场景
- 🎭 **角色生成** - 为每个角色生成多角度参考图
- 🖼️ **分镜生成** - 为每个场景生成关键帧图片
- 🎥 **动画生成** - 在相邻分镜之间生成过渡动画
- 🎵 **语音合成** - 为对话生成语音和字幕
- 📊 **质量评估** - AI自动评估生成质量
- 💾 **断点恢复** - 支持暂停和继续

## 快速开始

### 1. 安装依赖

```bash
cd animation-storyboard
pip install -r requirements.txt
```

### 2. 设置API密钥

```bash
# Windows
set AGNES_API_KEY=<你的-AGNES_API_KEY>
set MIMO_API_KEY=<你的-MIMO_API_KEY>

# Linux/Mac
export AGNES_API_KEY=<你的-AGNES_API_KEY>
export MIMO_API_KEY=<你的-MIMO_API_KEY>
```

或者在 `config/config.yaml` 中直接填写。

### 3. 准备剧本

编辑 `input/script.yaml`，参考示例格式。

### 4. 运行工作流

```bash
# 交互模式（默认）
python main.py

# 自动模式
python main.py --mode auto

# 指定剧本文件
python main.py --script input/my_script.yaml

# 指定项目名称
python main.py --name "我的动画项目"
```

## 项目结构

```
animation-storyboard/
├── config/
│   └── config.yaml              # 全局配置
├── input/
│   └── script.yaml              # 输入剧本
├── projects/                    # 项目目录（自动创建）
│   └── my-project/
│       ├── output/
│       │   ├── characters/      # 角色参考图
│       │   ├── storyboards/     # 分镜关键帧
│       │   ├── videos/          # 过渡动画
│       │   ├── audio/           # 对话音频
│       │   ├── subtitles/       # 字幕文件
│       │   └── storyboard.html  # 分镜板预览
│       └── progress.json        # 项目进度
├── src/
│   ├── api/
│   │   ├── agnes_client.py      # Agnes API客户端
│   │   └── mimo_client.py       # Mimo TTS客户端
│   ├── pipeline/
│   │   ├── controller.py        # 流水线控制器
│   │   ├── script_parser.py     # 剧本解析
│   │   ├── character_gen.py     # 角色生成
│   │   ├── storyboard_gen.py    # 分镜生成
│   │   ├── animation_gen.py     # 动画生成
│   │   ├── tts_gen.py           # 语音合成
│   │   └── composer.py          # 合成输出
│   ├── utils/
│   │   ├── evaluator.py         # 图片质量评估
│   │   ├── file_saver.py        # 文件保存器
│   │   ├── logger.py            # 日志工具
│   │   ├── progress.py          # 进度追踪
│   │   └── project_manager.py   # 项目管理器
│   └── ui/
│       └── terminal.py          # 终端UI
├── main.py                      # 主入口
└── requirements.txt             # 依赖列表
```

## 交互模式说明

在交互模式下，每个阶段完成后会暂停，等待用户确认：

```
请选择操作:
  [1] 继续下一阶段
  [2] 重新执行当前阶段
  [3] 暂停保存
```

- **继续下一阶段** - 接受当前结果，继续执行
- **重新执行当前阶段** - 重新生成当前阶段的结果
- **暂停保存** - 保存进度，下次运行从断点继续

## 配置说明

`config/config.yaml` 主要配置：

```yaml
agnes:
  settings:
    image:
      num_candidates: 3          # 每步生成候选图数量
      quality_threshold: 7.0     # 质量阈值（0-10）
      max_retries: 3             # 最大重试次数
      timeout: 60                # API超时时间（秒）

    video:
      num_frames: 121            # 视频帧数
      frame_rate: 24             # 帧率
      polling_interval: 5        # 轮询间隔（秒）
      max_polling_retries: 180   # 最大轮询次数（15分钟）
```

## 输出说明

运行完成后，在 `projects/{项目名}/output/` 目录下：

- `characters/` - 角色参考图（正面、侧面、全身）
- `storyboards/` - 分镜关键帧图片
- `videos/` - 过渡动画视频
- `audio/` - 对话音频文件
- `subtitles/` - SRT字幕文件
- `storyboard.html` - 分镜板预览（可在浏览器打开）
- `project_report.json` - 项目报告

## 注意事项

1. 视频生成是异步任务，需要等待10-15分钟
2. 每步会生成3个候选，自动选择最佳结果
3. 质量评分低于阈值时会警告，但仍会继续
4. 支持断点恢复，中断后重新运行会从上次位置继续
5. 日志文件保存在 `logs/` 目录下

## 错误处理

- API调用失败会自动重试3次
- 速率限制时会自动等待
- 视频生成超时会提示（最长15分钟）
- 任何阶段失败都可以选择重试或跳过
