"""
动画分镜工作流 - 主入口
"""

import argparse
import asyncio
import os
import sys

from src.pipeline.controller import PipelineController
from src.utils.logger import setup_logger

logger = setup_logger("main")


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="动画分镜工作流 - AI驱动的动画创作工具"
    )

    parser.add_argument(
        "--script",
        type=str,
        default="input/script.yaml",
        help="剧本文件路径 (默认: input/script.yaml)",
    )

    parser.add_argument(
        "--mode",
        type=str,
        choices=["interactive", "auto"],
        default="interactive",
        help="运行模式 (默认: interactive)",
    )

    parser.add_argument(
        "--name",
        type=str,
        default=None,
        help="项目名称 (可选，默认从剧本读取)",
    )

    parser.add_argument(
        "--config",
        type=str,
        default="config/config.yaml",
        help="配置文件路径 (默认: config/config.yaml)",
    )

    return parser.parse_args()


async def main():
    """主函数"""
    args = parse_args()

    # 检查剧本文件
    if not os.path.exists(args.script):
        logger.error(f"剧本文件不存在: {args.script}")
        print(f"错误: 剧本文件不存在: {args.script}")
        print("请创建剧本文件或使用 --script 参数指定路径")
        sys.exit(1)

    # 检查配置文件
    if not os.path.exists(args.config):
        logger.error(f"配置文件不存在: {args.config}")
        print(f"错误: 配置文件不存在: {args.config}")
        sys.exit(1)

    # 检查API密钥
    if not os.environ.get("AGNES_API_KEY"):
        logger.warning("未设置AGNES_API_KEY环境变量，请在配置文件中设置或设置环境变量")
        print("警告: 未设置AGNES_API_KEY环境变量")
        print("请设置环境变量: export AGNES_API_KEY=your_api_key")

    if not os.environ.get("MIMO_API_KEY"):
        logger.warning("未设置MIMO_API_KEY环境变量，请在配置文件中设置或设置环境变量")
        print("警告: 未设置MIMO_API_KEY环境变量")
        print("请设置环境变量: export MIMO_API_KEY=your_api_key")

    # 创建控制器
    controller = PipelineController(args.config)

    # 运行工作流
    try:
        await controller.run(
            script_path=args.script,
            project_name=args.name,
            mode=args.mode,
        )
    except KeyboardInterrupt:
        logger.info("用户中断")
        print("\n用户中断，进度已保存")
    except Exception as e:
        logger.error(f"工作流执行失败: {e}", exc_info=True)
        print(f"\n错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
