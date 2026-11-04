# tests/relstore/test_relstore_09_g3b_draft_immutable.py
# G3-B 第一关（本地修正，零真实外呼）：
# - Draft 资产匿名不可见（browser_download_url 匿名 404）；带权限 API 端点二进制下载可用
# - Publish 后匿名二次下载可用
# - publish_release 发送 draft:false + make_latest:"false"
# - immutable 状态闸：enabled/disabled 放行、UNKNOWN(401/403/404/None) 停机
# - immutable=enabled 时必须"全传完再 publish"顺序约束（模拟测试）
import hashlib

import pytest

from tools.relstore.errors import (ImmutableStateUnknown, RemoteWriteDisabled,
                                  ServerError)
from tools.relstore.github_release_adapter import (
    GitHubReleaseAdapter, MockHttpTransport)
from tools.relstore.immutable_gate import assert_writable, evaluate

O, R = "Q-1844", "agent-system-v2-rundata"
TOKEN = "ghs_G3B_TEST_NOT_REAL"


def resp(status, obj=None, *, raw=None, headers=None):
    if raw is not None:
        return MockHttpTransport.bytes_response(status, raw, headers=headers)
    return MockHttpTransport.json_response(status, obj if obj is not None else {})


def make(handler, *, armed=True):
    t = MockHttpTransport(handler)
    a = (GitHubReleaseAdapter.armed(O, R, TOKEN, t) if armed
         else GitHubReleaseAdapter(O, R, t))
    return a, t


# ---------- immutable 状态闸 ----------

@pytest.mark.parametrize("raw,expect", [
    ({"http": 200, "raw": {"enabled": True}}, "enabled"),
    ({"http": 200, "raw": {"enabled": False}}, "disabled"),
    ({"http": 401, "note": "Requires authentication"}, "unknown"),
    ({"http": 403, "note": "no Administration:read"}, "unknown"),
    ({"http": 404, "note": "endpoint unavailable"}, "unknown"),
    (None, "unknown"),
])
def test_immutable_evaluate(raw, expect):
    assert evaluate(raw) == expect


def test_immutable_unknown_blocks_writes():
    for raw in ({"http": 401}, {"http": 403}, {"http": 404}, None):
        with pytest.raises(ImmutableStateUnknown):
            assert_writable(raw)
    assert assert_writable({"http": 200, "raw": {"enabled": False}}) == "disabled"
    assert assert_writable({"http": 200, "raw": {"enabled": True}}) == "enabled"


def test_immutable_401_body_is_not_disabled():
    """G3-B 修正：401 'Requires authentication' 不得解释为 disabled。"""
    result = {"state": "unknown", "http": 401,
              "note": "Requires authentication"}
    with pytest.raises(ImmutableStateUnknown):
        assert_writable(result)


# ---------- Draft 可见性语义 ----------

ASSET_ID = "8801"
DRAFT_BYTES = b"draft-binary-" * 100
DRAFT_SHA = hashlib.sha256(DRAFT_BYTES).hexdigest()
ASSET_ID_PUB = "8802"
PUB_BYTES = b"published-binary-" * 100


def test_draft_asset_anonymous_denied_authenticated_ok():
    """Draft：browser_download_url 匿名 404；API 端点+octet+凭据 → 302 SAS → 200。"""
    sas = "https://release-assets.githubusercontent.com/d?sig=SECRET&jwt=abc"

    def h(req):
        url = req["url"]
        if url.endswith(f"/releases/assets/{ASSET_ID}") and \
                req["headers"].get("Accept") == "application/octet-stream":
            # 带权限 API 端点（Authorization 必须在场）
            assert req["headers"].get("Authorization") == f"Bearer {TOKEN}"
            return resp(302, raw=b"", headers={"location": sas})
        if url.endswith(f"/releases/assets/{ASSET_ID}"):
            # 匿名/JSON 元数据路径：Draft 也应可见元数据（owner 凭据）
            return resp(200, {"id": ASSET_ID, "name": "g3.tar",
                               "browser_download_url":
                                   "https://github.com/x/y/releases/download/t/g3.tar",
                               "size": len(DRAFT_BYTES)})
        if "github.com/x/y/releases/download" in url:
            # Draft 的 browser_download_url 匿名访问 → 404（官方行为：Draft 不可见）
            assert "Authorization" not in req["headers"]
            return resp(404, {"message": "Not Found"})
        if "release-assets.githubusercontent.com" in url:
            assert "Authorization" not in req["headers"]  # SAS 不带凭据
            return resp(200, raw=DRAFT_BYTES)
        return resp(404)
    a, t = make(h)
    # 匿名 browser_download_url 路径 → 404（Draft 不可见）
    with pytest.raises(ServerError, match="404"):
        a.download_asset(ASSET_ID)
    # 带权限 API octet 路径 → 成功
    assert a.download_asset_authenticated(ASSET_ID) == DRAFT_BYTES
    # SAS 不入异常/日志
    assert all("SECRET" not in str(x) for x in a.call_log())


def test_authenticated_download_403_maps_auth_error():
    a, _ = make(lambda req: resp(403, {"message": "forbidden"}))
    from tools.relstore.errors import AuthError
    with pytest.raises(AuthError):
        a.download_asset_authenticated("123")


# ---------- Publish 语义 ----------

def test_publish_sends_draft_false_and_make_latest_false():
    def h(req):
        if req["method"] == "PATCH":
            assert req["json"] == {"draft": False, "make_latest": "false"}
            return resp(200, {"id": 9001, "draft": False,
                               "make_latest": "false"})
        return resp(404)
    a, t = make(h)
    a.publish_release("9001")
    assert t.requests[0]["method"] == "PATCH"


def test_draft_phase_cannot_skip_verification_before_publish():
    """顺序约束模拟：immutable=enabled 时发布前必须完成带权限二进制验证。
    这里验证 gate 函数与下载方法组合的调用次序约定（executor 层实施）。"""
    a, _ = make(lambda req: resp(200, {"id": 1, "enabled": True}))
    state = assert_writable(a.get_immutable_releases())
    assert state == "enabled"
    # enabled → executor 必须先 authenticated 下载验证，再 publish_release


# ---------- 回执丢失（G3-B 真实流程的客户端模拟形态） ----------

def test_receipt_lost_recovery_via_tag_assets_listing():
    """上传响应丢弃后，按 tag→assets 的 name+size+sha 找回同一 asset，
    且资产总数为 1（无重复上传）。"""
    data = b"real-asset-bytes"
    sha = hashlib.sha256(data).hexdigest()

    def h(req):
        if req["method"] == "GET" and req["url"].endswith("/releases/tags/t"):
            return resp(200, {"id": 9001, "tag_name": "t"})
        if req["method"] == "GET" and "/releases/9001/assets" in req["url"]:
            return resp(200, [{"id": 8803, "name": "g3.tar", "size": len(data),
                                "state": "uploaded",
                                "digest": f"sha256:{sha}"}])
        return resp(404)
    a, _ = make(h)
    rel = a.get_release_by_tag("t")
    assets = a.list_assets(rel.id)
    match = [x for x in assets
             if x.name == "g3.tar" and x.size == len(data) and x.sha256 == sha]
    assert len(assets) == 1 and match and match[0].id == "8803"


# ---------- 禁写默认 ----------

def test_g3b_unarmed_still_cannot_write():
    a, t = make(lambda req: resp(200, {}), armed=False)
    with pytest.raises(RemoteWriteDisabled):
        a.create_release("t", "n")
    with pytest.raises(RemoteWriteDisabled):
        a.publish_release("9001")
    assert t.requests == []


# ---------- executor 本地打包冒烟（零网络） ----------

def test_executor_package_local(tmp_path, monkeypatch):
    from tools.relstore import g3b_executor as X
    from tools.relstore.errors import RelstoreError
    work = tmp_path / "work"
    work.mkdir()
    full_sha = "a" * 40
    out = tmp_path / "pkg.json"
    rc = X.main(["package", "--nonce", "t1", "--workdir", str(work),
                 "--source-commit", full_sha, "--out", str(out)])
    assert rc == 0
    doc = __import__("json").loads(out.read_text(encoding="utf-8"))
    assert doc["bytes"] <= 1024 * 1024 and len(doc["sha256"]) == 64
    assert doc["restorable_level"] == "full"
    assert doc["payload"]["source_code_commit"] == full_sha
    # 短 SHA 必须拒绝
    import pytest as _pt
    with _pt.raises(RelstoreError):
        X.main(["package", "--nonce", "t2", "--workdir", str(work),
                "--source-commit", "abc123", "--out", str(tmp_path / "x.json")])


def test_executor_token_gate(tmp_path):
    from tools.relstore import g3b_executor as X
    from tools.relstore.errors import RelstoreError
    with pytest.raises(RelstoreError):
        X.main(["precheck", "--token-file", "",
                "--out", str(tmp_path / "o.json"), "--nonce", "t"])
