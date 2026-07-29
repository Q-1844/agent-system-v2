"""
合成输出阶段
生成分镜板HTML和最终视频
"""

from pathlib import Path
from typing import Dict, Any, List
from datetime import datetime

from jinja2 import Template

from ..utils.logger import setup_logger
from ..utils.file_saver import FileSaver

logger = setup_logger("composer")


# 分镜板HTML模板
STORYBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>动画分镜板 - {{ project_name }}</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Inter', 'Microsoft YaHei', sans-serif;
            padding: 24px;
            background-color: #111;
            color: #e5e5e5;
        }

        .header {
            text-align: center;
            margin-bottom: 32px;
        }

        .header h1 {
            font-size: 28px;
            font-weight: 600;
            margin-bottom: 8px;
        }

        .header p {
            color: #888;
            font-size: 14px;
        }

        .storyboard-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
            gap: 20px;
            max-width: 1200px;
            margin: 0 auto;
        }

        .scene-card {
            background-color: #1a1a1a;
            border: 1px solid #222;
            border-radius: 10px;
            overflow: hidden;
            transition: border-color 0.2s ease;
        }

        .scene-card:hover {
            border-color: #444;
        }

        .scene-card img {
            width: 100%;
            display: block;
        }

        .scene-info {
            padding: 16px;
        }

        .scene-info h3 {
            margin: 0 0 8px 0;
            font-size: 16px;
            font-weight: 600;
        }

        .scene-info p {
            margin: 0 0 8px 0;
            color: #999;
            font-size: 13px;
            line-height: 1.5;
        }

        .scene-meta {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 12px;
        }

        .tag {
            font-size: 11px;
            color: #888;
            background-color: #222;
            padding: 3px 8px;
            border-radius: 4px;
        }

        .video-link {
            display: inline-block;
            color: #e5e5e5;
            font-size: 13px;
            text-decoration: none;
            padding: 8px 16px;
            background-color: #222;
            border-radius: 6px;
            transition: background-color 0.2s ease;
        }

        .video-link:hover {
            background-color: #333;
        }

        .footer {
            text-align: center;
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid #222;
            color: #666;
            font-size: 12px;
        }

        @media (max-width: 768px) {
            body {
                padding: 16px;
            }
            .header h1 {
                font-size: 22px;
            }
            .storyboard-grid {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🎬 {{ project_name }}</h1>
        <p>生成时间: {{ timestamp }}</p>
    </div>

    <div class="storyboard-grid">
        {% for scene in scenes %}
        <div class="scene-card">
            {% if scene.image_path %}
            <img src="{{ scene.image_path }}" alt="{{ scene.description }}">
            {% else %}
            <div style="height: 200px; background: #222; display: flex; align-items: center; justify-content: center;">
                <span style="color: #666;">无图片</span>
            </div>
            {% endif %}
            <div class="scene-info">
                <h3>场景 {{ scene.id }}</h3>
                <p>{{ scene.description }}</p>
                <div class="scene-meta">
                    {% for char in scene.characters %}
                    <span class="tag">{{ char }}</span>
                    {% endfor %}
                </div>
                {% if scene.video_path %}
                <a class="video-link" href="{{ scene.video_path }}">▶ 观看动画</a>
                {% endif %}
            </div>
        </div>
        {% endfor %}
    </div>

    <div class="footer">
        <p>由动画分镜工作流自动生成</p>
    </div>
</body>
</html>"""


class Composer:
    """合成输出器"""

    def __init__(self, file_saver: FileSaver):
        self.file_saver = file_saver

    def compose(
        self,
        project_name: str,
        scenes: List[Dict[str, Any]],
        characters: List[Dict[str, Any]],
    ) -> Dict[str, str]:
        """
        生成最终输出

        Args:
            project_name: 项目名称
            scenes: 场景列表
            characters: 角色列表

        Returns:
            输出文件路径字典
        """
        logger.info("开始合成输出...")

        outputs = {}

        # 生成分镜板HTML
        html_path = self._generate_storyboard_html(project_name, scenes)
        outputs["storyboard_html"] = str(html_path)
        logger.info(f"分镜板HTML已生成: {html_path}")

        # 生成项目报告
        report_path = self._generate_report(project_name, scenes, characters)
        outputs["project_report"] = str(report_path)
        logger.info(f"项目报告已生成: {report_path}")

        logger.info("合成输出完成")
        return outputs

    def _generate_storyboard_html(
        self, project_name: str, scenes: List[Dict[str, Any]]
    ) -> Path:
        """
        生成分镜板HTML

        Args:
            project_name: 项目名称
            scenes: 场景列表

        Returns:
            HTML文件路径
        """
        template = Template(STORYBOARD_TEMPLATE)

        # 准备模板数据
        template_scenes = []
        for scene in scenes:
            template_scenes.append(
                {
                    "id": scene["id"],
                    "description": scene["description"],
                    "characters": scene.get("characters", []),
                    "image_path": scene.get("image_path"),
                    "video_path": scene.get("transitions", {}).get("to_next"),
                }
            )

        html_content = template.render(
            project_name=project_name,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            scenes=template_scenes,
        )

        # 保存HTML
        html_path = self.file_saver.output_dir / "storyboard.html"
        html_path.write_text(html_content, encoding="utf-8")

        return html_path

    def _generate_report(
        self,
        project_name: str,
        scenes: List[Dict[str, Any]],
        characters: List[Dict[str, Any]],
    ) -> Path:
        """
        生成项目报告

        Args:
            project_name: 项目名称
            scenes: 场景列表
            characters: 角色列表

        Returns:
            报告文件路径
        """
        # 统计信息
        total_scenes = len(scenes)
        completed_scenes = len([s for s in scenes if s.get("image_path")])
        total_transitions = len(
            [s for s in scenes if s.get("transitions", {}).get("to_next")]
        )
        total_audio = len([s for s in scenes if s.get("audio_path")])

        # 计算平均分数
        scores = [s.get("score", {}).get("total", 0) for s in scenes if s.get("score")]
        avg_score = sum(scores) / len(scores) if scores else 0

        report = {
            "project_name": project_name,
            "created_at": datetime.now().isoformat(),
            "stages": [
                {
                    "name": "script_parser",
                    "status": "completed",
                    "output_files": ["parsed_script.json"],
                },
                {
                    "name": "character_gen",
                    "status": "completed",
                    "output_files": [
                        f"characters/{c['name']}_front.png"
                        for c in characters
                        if c.get("reference_images", {}).get("front")
                    ],
                },
                {
                    "name": "storyboard_gen",
                    "status": "completed",
                    "output_files": [
                        f"storyboards/scene_{s['id']:02d}.png"
                        for s in scenes
                        if s.get("image_path")
                    ],
                },
                {
                    "name": "animation_gen",
                    "status": "completed",
                    "output_files": [
                        f"videos/transition_{s['id']:02d}_{s['id']+1:02d}.mp4"
                        for s in scenes
                        if s.get("transitions", {}).get("to_next")
                    ],
                },
                {
                    "name": "tts_gen",
                    "status": "completed",
                    "output_files": [
                        f"audio/dialog_{s['id']:02d}.mp3"
                        for s in scenes
                        if s.get("audio_path")
                    ],
                },
            ],
            "statistics": {
                "total_scenes": total_scenes,
                "completed_scenes": completed_scenes,
                "total_transitions": total_transitions,
                "total_audio": total_audio,
                "average_score": round(avg_score, 1),
            },
        }

        return self.file_saver.save_json("project_report.json", report)
