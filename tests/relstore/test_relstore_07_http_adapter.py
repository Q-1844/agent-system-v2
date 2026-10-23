# tests/relstore/test_relstore_07_http_adapter.py
# G3-A：真实 GitHub REST 适配器的 MockTransport 行为测试（零 socket、零真实 token）。
import hashlib
import json

import pytest

from tools.relstore.errors import (AssetConflict, AuthError, NetworkInterrupt,
                                  RateLimited, RemoteWriteDisabled, ServerError)
from tools.relstore.github_release_adapter import (
    GitHubContentsIndex, GitHubReleaseAdapter, MockHttpTransport,
    UnprocessableEntity422, bootstrap_default_branch_plan, redact)
from tools.relstore.release_adapter import G2DisabledReleaseAdapter

O, R = "Q-1844", "agent-system-v2-rundata"
TOKEN = "ghs_G3A_TEST_TOKEN_NOT_REAL_1234567890"


def resp(status, obj=None, *, raw=None, headers=None):
    if raw is not None:
        return MockHttpTransport.bytes_response(status, raw, headers=headers)
    return MockHttpTransport.json_response(status, obj if obj is not None else {},
                                            headers=headers)


def make_adapter(handler, *, armed=False, index=None, token=None):
    t = MockHttpTransport(handler)
    kw = {}
    if index is not None:
        kw["index_client"] = index
    if armed:
        return GitHubReleaseAdapter.armed(O, R, token or TOKEN, t, **kw), t
    return GitHubReleaseAdapter(O, R, t, **kw), t


# ---------- 禁写门 ----------

def test_write_methods_disabled_by_default():
    a, t = make_adapter(lambda req: None)
    for m, args in [
        ("create_release", ("t",)),
        ("upload_asset", ("r", "n", b"x", "0" * 64)),
        ("publish_release", ("r",)),
    ]:
        with pytest.raises(RemoteWriteDisabled):
            getattr(a, m)(*args)
    assert t.requests == []  # 禁用时不得产生任何 HTTP 请求


def test_g2_disabled_still_hard_blocks():
    a = G2DisabledReleaseAdapter()
    with pytest.raises(RemoteWriteDisabled):
        a.upload_asset("r", "n", b"x", "0" * 64)


def test_armed_requires_token():
    with pytest.raises(RemoteWriteDisabled):
        GitHubReleaseAdapter.armed(O, R, "", MockHttpTransport(None))


# ---------- 只读 ----------

def test_reads_send_no_authorization_anonymous():
    def h(req):
        assert "Authorization" not in req["headers"]
        if "/repos/Q-1844/agent-system-v2-rundata" in req["url"] and \
                "/tags" not in req["url"] and "/immutable" not in req["url"]:
            return resp(200, {"default_branch": "main", "size": 1745751,
                               "private": False})
        if "/immutable-releases" in req["url"]:
            return resp(404, {"message": "Not Found"})
        if req["url"].endswith("/tags?per_page=100"):
            return resp(200, [{"name": "main-no-tag"}])
        return resp(404)
    a, t = make_adapter(h)
    info = a.get_repo_info()
    assert info == {"default_branch": "main", "size": 1745751,
                    "private": False, "has_pages": None, "archived": None}
    assert a.list_tags() == ["main-no-tag"]
    imm = a.get_immutable_releases()
    assert imm["state"] == "unknown" and imm["http"] == 404
    assert all(rq["method"] == "GET" for rq in t.requests)


def test_get_release_by_tag_200_and_404():
    seq = [resp(200, {"id": 417460021, "tag_name": "archive-projects-20261019"}),
           resp(404, {"message": "Not Found"})]
    a, _ = make_adapter(lambda req: seq.pop(0))
    h = a.get_release_by_tag("archive-projects-20261019")
    assert h.id == "417460021" and h.tag == "archive-projects-20261019"
    assert a.get_release_by_tag("nope") is None


def test_list_assets_parses_digest():
    a, _ = make_adapter(lambda req: resp(200, [
        {"id": 656674589, "name": "P55_SMOKE2B.tar", "size": 36116480, "state": "uploaded",
         "digest": "sha256:f18a80b3a2b60b4ed762ef9aec70edb85f9124147dd8cdc1f7f38779dd2b75ff"}]))
    assets = a.list_assets("417460021")
    assert assets[0].sha256 == "f18a80b3a2b60b4ed762ef9aec70edb85f9124147dd8cdc1f7f38779dd2b75ff"
    assert a.get_asset("417460021", "P55_SMOKE2B.tar").id == "656674589"
    assert a.get_asset("417460021", "missing") is None


@pytest.mark.parametrize("status,state,note_part",
                         [(200, "enabled", "200"), (200, "disabled", "200"),
                          (404, "unknown", "404"), (403, "unknown", "40")])
def test_immutable_status_interpretation(status, state, note_part):
    body = {"enabled": state == "enabled"} if status == 200 else {"message": "x"}
    if status == 200 and state == "disabled":
        body = {"enabled": False}
    a, _ = make_adapter(lambda req: resp(status, body))
    out = a.get_immutable_releases()
    assert out["state"] == state
    assert str(out["http"]).startswith(note_part[:2])


# ---------- 写：请求格式与状态映射 ----------

def test_create_release_draft_201_request_format():
    def handler(req):
        if req["method"] == "GET" and "/releases/tags/" in req["url"]:
            return resp(404, {"message": "Not Found"})  # 同 tag 幂等预检：不存在
        return resp(201, {"id": 9001, "tag_name": "test-relstore/g3-abcd/r1",
                           "draft": True})
    a, t = make_adapter(handler, armed=True)
    h = a.create_release("test-relstore/g3-abcd/r1", "g3", "body",
                          target_commitish="349b6aa39d17216898044d6a267c0067e6997fe1",
                          draft=True)
    req = t.requests[1]  # [0]=get_release_by_tag 幂等预检
    assert req["method"] == "POST" and req["url"].endswith("/releases")
    assert req["json"]["tag_name"] == "test-relstore/g3-abcd/r1"
    assert req["json"]["draft"] is True
    assert req["json"]["target_commitish"] == "349b6aa39d17216898044d6a267c0067e6997fe1"
    assert req["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in req["url"]
    assert h.id == "9001"


def test_upload_asset_content_type_octet_accept_json():
    """真实 GitHub 实测：uploads 端点 Accept 必须为 JSON，Content-Type 才是 octet-stream。"""
    data = b"ct-check"
    seen = {}

    def handler(req):
        if req["method"] == "GET" and "/releases/tags/" in req["url"]:
            return resp(404, {"message": "Not Found"})
        seen.update(req["headers"])
        return resp(201, {"id": 7010, "name": "x.tar", "size": len(data)})
    a, _ = make_adapter(handler, armed=True)
    a.upload_asset("9001", "x.tar", data, hashlib.sha256(data).hexdigest())
    assert seen["Content-Type"] == "application/octet-stream"
    assert seen["Accept"] == "application/vnd.github+json"


def test_upload_asset_201_endpoint_and_name_param():
    data = b"hello-asset" * 10
    sha = hashlib.sha256(data).hexdigest()

    def h(req):
        assert req["url"].startswith("https://uploads.github.com/")
        assert req["params"] == {"name": "g3-test-abcd.tar"}
        assert req["headers"]["Content-Type"] == "application/octet-stream"
        assert req["data"] == data
        return resp(201, {"id": 7001, "name": "g3-test-abcd.tar",
                           "size": len(data), "state": "uploaded"})
    a, _ = make_adapter(h, armed=True)
    meta = a.upload_asset("9001", "g3-test-abcd.tar", data, sha)
    assert meta.id == "7001" and meta.size == len(data) and meta.sha256 == sha


def test_upload_422_same_sha_reconciles_without_second_post():
    data = b"same-bytes"
    sha = hashlib.sha256(data).hexdigest()
    calls = iter([
        resp(422, {"message": "Validation Failed",
                    "errors": [{"code": "already_exists"}]}),
        resp(200, [{"id": 7002, "name": "g3.tar", "size": len(data),
                     "state": "uploaded",
                     "digest": f"sha256:{sha}"}]),
    ])
    a, t = make_adapter(lambda req: next(calls), armed=True)
    meta = a.upload_asset("9001", "g3.tar", data, sha)
    assert meta.id == "7002"  # 幂等复用
    methods = [r["method"] for r in t.requests]
    assert methods == ["POST", "GET"]  # 绝不第二次 POST


def test_upload_422_different_sha_conflicts_no_overwrite():
    data = b"new-bytes"
    a, _ = make_adapter(lambda req: (
        resp(422, {"message": "already_exists"}) if req["method"] == "POST"
        else resp(200, [{"id": 7003, "name": "g3.tar", "size": 999,
                          "digest": "sha256:" + "1" * 64}])), armed=True)
    with pytest.raises(AssetConflict, match="同名异 sha"):
        a.upload_asset("9001", "g3.tar", data, hashlib.sha256(data).hexdigest())


def test_upload_422_asset_missing_raises_422():
    a, _ = make_adapter(lambda req: (
        resp(422, {"message": "already_exists"}) if req["method"] == "POST"
        else resp(200, [])), armed=True)
    with pytest.raises(UnprocessableEntity422):
        a.upload_asset("9001", "g3.tar", b"x", hashlib.sha256(b"x").hexdigest())


def test_download_follows_302_sas_and_never_logs_it():
    sas = ("https://release-assets.githubusercontent.com/x?sp=r&sig=SECRETSIG&"
           "jwt=eyJsecret&se=2026-10-22")

    def h(req):
        if req["url"].endswith("/releases/assets/7001"):
            return resp(302, raw=b"", headers={"location": sas})
        if "release-assets.githubusercontent.com" in req["url"]:
            # 第二次访问 SAS：模拟 500，错误信息必须遮蔽 sig
            return resp(500, {"message": "boom"})
        return resp(404)
    a, t = make_adapter(h, armed=True)
    with pytest.raises(ServerError) as ei:
        a.download_asset("7001")
    assert "SECRETSIG" not in str(ei.value)
    assert "eyJsecret" not in str(ei.value)
    # 访问 SAS 不带 Authorization（匿名下载）
    sas_req = [r for r in t.requests if "release-assets" in r["url"]][0]
    assert "Authorization" not in sas_req["headers"]


def test_download_200_bytes():
    blob = b"\x00\x00\x00\x18ftypmp42" + b"z" * 100

    def h(req):
        if req["url"].endswith("/releases/assets/7001"):
            return resp(302, raw=b"",
                         headers={"location": "https://release-assets/x"})
        return resp(200, raw=blob, headers={"content-type": "application/octet-stream"})
    a, _ = make_adapter(h)
    assert a.download_asset("7001") == blob


@pytest.mark.parametrize("status,exc", [(401, AuthError), (403, AuthError),
                                         (409, AssetConflict), (429, RateLimited),
                                         (502, ServerError)])
def test_error_status_mapping_on_get(status, exc):
    a, _ = make_adapter(lambda req: resp(status, {"message": f"m{status}"}))
    with pytest.raises(exc):
        a.get_release_by_tag("t")


def test_401_no_retry_contract_at_adapter_level():
    # 适配器本身不重试（重试在状态机层）；401 直接抛 AuthError
    a, t = make_adapter(lambda req: resp(401, {"message": "bad credentials"}),
                          armed=True)
    with pytest.raises(AuthError):
        a.create_release("t", "n")
    assert len(t.requests) == 1


class _ReadTimeout(Exception):
    pass


def test_transport_timeout_mapped_network_interrupt():
    from tools.relstore.github_release_adapter import _map_transport_error
    e = _map_transport_error(_ReadTimeout(), url="https://api.github.com/x")
    assert isinstance(e, NetworkInterrupt)


def test_receipt_lost_recovery_finds_existing_no_repost():
    data = b"committed-bytes"
    sha = hashlib.sha256(data).hexdigest()

    def h(req):
        if req["method"] == "POST":
            raise NetworkInterrupt("模拟上传响应丢失（黑窗）")
        if req["method"] == "GET" and "/assets" in req["url"]:
            return resp(200, [{"id": 7009, "name": "g3.tar", "size": len(data),
                                "state": "uploaded", "digest": f"sha256:{sha}"}])
        return resp(404)
    a, t = make_adapter(h, armed=True)
    meta = a.upload_asset_with_recovery("9001", "g3.tar", data, sha)
    assert meta.id == "7009"
    assert [r["method"] for r in t.requests] == ["POST", "GET"]


# ---------- 遮蔽 ----------

def test_redact_authorization_sas_token():
    s = ("GET https://h/x?sp=r&sig=TOPSECRET&jwt=AAA Authorization: Bearer "
         + TOKEN)
    out = redact(s, token=TOKEN)
    assert "TOPSECRET" not in out and "AAA" not in out and TOKEN not in out
    assert "<REDACTED>" in out


# ---------- B1 bootstrap 计划（只设计不执行） ----------

def test_bootstrap_plan_readonly_first_single_init():
    plan = bootstrap_default_branch_plan(owner=O, repo="agent-system-v2-rundata-1",
                                           readme_text="x")
    assert plan[0]["method"] == "GET"
    assert sum(1 for s in plan if s["method"] == "PUT") == 1
    assert "README" in plan[1]["path"]
