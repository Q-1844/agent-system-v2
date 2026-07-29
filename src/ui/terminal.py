"""
终端用户界面
提供进度显示、图片预览、用户交互等功能
"""

from pathlib import Path
from typing import List, Dict, Any

from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.prompt import Prompt
from rich.panel import Panel
from rich.columns import Columns

from ..utils.logger import setup_logger

logger = setup_logger("terminal_ui")


class TerminalUI:
    """终端用户界面"""

    def __init__(self):
        # force_terminal=True 确保rich正确处理编码
        self.console = Console(force_terminal=True)

    def show_banner(self):
        """显示启动横幅"""
        self.console.print()
        self.console.print(
            "[bold cyan]🎬 动画分镜工作流[/bold cyan]", justify="center"
        )
        self.console.print(
            "[dim]AI驱动的动画创作工具[/dim]", justify="center"
        )
        self.console.print()

    def show_stage_header(self, stage_name: str, current: int, total: int):
        """
        显示阶段标题

        Args:
            stage_name: 阶段名称
            current: 当前阶段编号
            total: 总阶段数
        """
        self.console.print()
        self.console.print(
            f"[bold cyan]{'=' * 60}[/bold cyan]"
        )
        self.console.print(
            f"[bold]🎬 阶段 {current}/{total}: {stage_name}[/bold]"
        )
        self.console.print(
            f"[bold cyan]{'=' * 60}[/bold cyan]"
        )
        self.console.print()

    def show_progress(self, description: str, total: int):
        """
        显示进度条

        Args:
            description: 进度描述
            total: 总数

        Returns:
            Progress对象和task_id
        """
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            console=self.console,
        )
        task = progress.add_task(description, total=total)
        return progress, task

    def show_image_preview(self, image_path: str, scores: Dict[str, Any]):
        """
        显示图片预览和评分

        Args:
            image_path: 图片路径
            scores: 评分字典
        """
        self.console.print()
        self.console.print("[bold green]📸 生成结果预览[/bold green]")
        self.console.print(f"文件: {image_path}")

        # 显示评分表格
        table = Table(title="质量评估")
        table.add_column("维度", style="cyan")
        table.add_column("得分", style="magenta")

        dimension_names = {
            "character": "角色一致性",
            "style": "风格匹配度",
            "composition": "构图质量",
            "clarity": "清晰度",
        }

        for key, name in dimension_names.items():
            if key in scores:
                score = scores[key]
                # 根据分数设置颜色
                if score >= 8:
                    color = "green"
                elif score >= 6:
                    color = "yellow"
                else:
                    color = "red"
                table.add_row(name, f"[{color}]{score}/10[/{color}]")

        if "total" in scores:
            table.add_row(
                "[bold]综合[/bold]",
                f"[bold cyan]{scores['total']}/10[/bold cyan]",
            )

        self.console.print(table)

        if "suggestions" in scores and scores["suggestions"]:
            self.console.print(f"[dim]建议: {scores['suggestions']}[/dim]")

        self.console.print()

    def ask_user_choice(self, options: List[str]) -> int:
        """
        等待用户选择

        Args:
            options: 选项列表

        Returns:
            用户选择的索引 (从1开始)
        """
        self.console.print("[bold yellow]请选择操作:[/bold yellow]")
        for i, option in enumerate(options, 1):
            self.console.print(f"  [{i}] {option}")

        choice = Prompt.ask(
            "请输入选项",
            choices=[str(i) for i in range(1, len(options) + 1)],
            default="1",
        )
        return int(choice)

    def ask_yes_no(self, question: str, default: bool = True) -> bool:
        """
        等待用户确认

        Args:
            question: 问题
            default: 默认值

        Returns:
            用户选择
        """
        default_str = "Y/n" if default else "y/N"
        response = Prompt.ask(
            f"{question} [{default_str}]", default="y" if default else "n"
        )
        return response.lower() in ("y", "yes", "是")

    def show_success(self, message: str):
        """显示成功消息"""
        self.console.print(f"[bold green]✅ {message}[/bold green]")

    def show_warning(self, message: str):
        """显示警告消息"""
        self.console.print(f"[bold yellow]⚠️ {message}[/bold yellow]")

    def show_error(self, message: str):
        """显示错误消息"""
        self.console.print(f"[bold red]❌ {message}[/bold red]")

    def show_info(self, message: str):
        """显示信息消息"""
        self.console.print(f"[cyan]ℹ️ {message}[/cyan]")

    def show_character_info(self, characters: List[Dict[str, Any]]):
        """
        显示角色信息

        Args:
            characters: 角色列表
        """
        table = Table(title="角色列表")
        table.add_column("名称", style="cyan")
        table.add_column("描述", style="white")
        table.add_column("参考图", style="green")

        for char in characters:
            refs = char.get("reference_images", {})
            ref_count = len([v for v in refs.values() if v])
            table.add_row(
                char["name"],
                char["description"][:50] + "..." if len(char["description"]) > 50 else char["description"],
                f"{ref_count}张",
            )

        self.console.print(table)

    def show_scene_info(self, scenes: List[Dict[str, Any]]):
        """
        显示场景信息

        Args:
            scenes: 场景列表
        """
        table = Table(title="场景列表")
        table.add_column("ID", style="cyan")
        table.add_column("描述", style="white")
        table.add_column("角色", style="green")
        table.add_column("状态", style="magenta")

        for scene in scenes:
            status = scene.get("status", "pending")
            status_colors = {
                "pending": "yellow",
                "in_progress": "blue",
                "completed": "green",
                "failed": "red",
            }
            status_color = status_colors.get(status, "white")
            table.add_row(
                str(scene["id"]),
                scene["description"][:30] + "..." if len(scene["description"]) > 30 else scene["description"],
                ", ".join(scene.get("characters", [])),
                f"[{status_color}]{status}[/{status_color}]",
            )

        self.console.print(table)

    def open_image(self, image_path: str):
        """
        打开图片查看器

        Args:
            image_path: 图片路径
        """
        import subprocess
        import platform

        try:
            if platform.system() == "Windows":
                subprocess.run(["start", image_path], shell=True, check=False)
            elif platform.system() == "Darwin":
                subprocess.run(["open", image_path], check=False)
            else:
                subprocess.run(["xdg-open", image_path], check=False)
        except Exception as e:
            logger.warning(f"无法打开图片: {e}")

    def clear(self):
        """清屏"""
        self.console.clear()
