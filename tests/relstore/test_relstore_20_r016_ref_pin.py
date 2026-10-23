"""R-016 回归：Contents GET 必须钉 ``?ref=<branch>``。

复刻 B2.2 真实远端首试发现的默认分支语义缺陷（详见 ref_pin 模块文档），
确保修复以纯函数形式留存于 relstore 库，且在干净环境（无 httpx / 无网络）下可回归。
"""
import pytest

from tools.relstore.ref_pin import pin_ref_on_contents_get


class TestPinRefOnContentsGet:
    def test_appends_ref_to_contents_get(self):
        out = pin_ref_on_contents_get(
            "GET", "contents/b2_2_test/0cd4d7/state.json",
            "test/g4b22-20261023-0cd4d7",
        )
        assert out == (
            "contents/b2_2_test/0cd4d7/state.json?ref=test/g4b22-20261023-0cd4d7"
        )

    def test_idempotent_existing_ref_preserved(self):
        # 已带 ?ref= 的路径不被覆盖、不重复追加
        path = "contents/b2_2_test/0cd4d7/state.json?ref=other/branch"
        assert pin_ref_on_contents_get("GET", path, "test/x") == path

    def test_non_contents_path_untouched(self):
        # git/ref/heads/... 等元数据路径不钉 ref
        assert pin_ref_on_contents_get(
            "GET", "git/ref/heads/main", "test/x") == "git/ref/heads/main"

    def test_put_not_pinned(self):
        # 仅 GET 钉 ref；PUT/POST 由 CAS/sha 语义负责
        path = "contents/b2_2_test/0cd4d7/state.json"
        assert pin_ref_on_contents_get("PUT", path, "test/x") == path

    def test_query_string_branch_not_double_pinned(self):
        path = "contents/x/y?ref=test/a&other=1"
        assert pin_ref_on_contents_get("GET", path, "test/b") == path

    def test_branch_with_slash_supported(self):
        out = pin_ref_on_contents_get("GET", "contents/x/state.json", "feat/a-b")
        assert out == "contents/x/state.json?ref=feat/a-b"
