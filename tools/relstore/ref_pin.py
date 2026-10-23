"""R-016 修复：GitHub Contents API 分支钉 ref（纯字符串函数）。

背景（B2.2 真实远端首试 R-016，冻结 SHA256 ``19b6b72a...``）：
  GitHub Contents API 的 GET 请求若不带 ``?ref=<branch>``，默认读取仓库默认分支
  （本仓为 ``chore/migration-readiness``）。在操作非默认分支（B1 测试分支、B 归档
  分支等）的 state.json / asset 时，必须显式钉住目标分支，否则读到默认分支的空 /
  陈旧状态 → 404 或 stale。B2.2 首试因执行器 ``RealContentsCAS`` 的 contents GET
  未带 ``?ref=`` 而失败；修复在 ``report3/tools/g4b22_real_executor.py:316-320``。

本模块把该修复抽出为不依赖 httpx / 真实网络的纯函数，便于：
  - 在干净环境（离线、无第三方传输库）下回归测试；
  - 供未来 transport wrapper / 执行器复用，确保分支语义不丢。

契约：transport 在发出 contents GET 前应调用 ``pin_ref_on_contents_get`` 改写 path。
该函数本身不发起任何网络请求，零副作用。
"""
from __future__ import annotations


def pin_ref_on_contents_get(method: str, path: str, branch: str) -> str:
    """对 GitHub Contents API 的分支作用域 GET 钉 ``?ref=<branch>``。

    - 仅作用于 ``GET`` 且 path 以 ``contents/`` 开头；
    - 若 path 已含 ``?ref=`` 则幂等返回原值（不重复追加、不覆盖既有 ref）；
    - 非 contents 路径 / 非 GET 一律原样返回。

    Args:
        method: HTTP 方法（约定大写，与 GitHub REST 一致）。
        path: Contents API 路径，如 ``contents/b2_2_test/0cd4d7/state.json``。
        branch: 目标分支名，可含斜杠，如 ``test/g4b22-20261023-0cd4d7``。
            调用方应保证非空；为空时仍追加 ``?ref=``（不擅自跳过，暴露调用错误）。

    Returns:
        改写后的 path，保证分支作用域 GET 读到目标分支而非默认分支。
    """
    if method == "GET" and path.startswith("contents/") and "?ref=" not in path:
        return f"{path}?ref={branch}"
    return path
