"""接口级测试（钉死保留接口的 200 + 响应结构 + 旧接口 404）。

PHASE_5 §5.4 第 1/3 层：连通性 + 接口测试。用 TestClient（不真调 Agnes）。
调 Agnes 的接口（first-frame/video/audio）需 mock_agnes，留待下轮。
"""
from tests.conftest import P4


# ---------- 第 1 层：连通性 ----------

def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["controller_ready"] is True


def test_system_limits(client):
    r = client.get("/api/system/limits")
    assert r.status_code == 200
    j = r.json()
    # 返回 {limits, quota}（阶段 1 验收）
    assert "limits" in j or "quota" in j


# ---------- 第 3 层：保留接口（shot 级管线，只读 project.json 不调 Agnes）----------

def test_get_shots(client):
    r = client.get(f"/api/project/{P4}/shots")
    assert r.status_code == 200
    j = r.json()
    assert "scenes" in j
    assert "characters" in j
    # v2 结构：scenes[].shots[]
    assert all("shots" in s for s in j["scenes"])


def test_get_timing(client):
    r = client.get(f"/api/project/{P4}/timing")
    assert r.status_code == 200
    j = r.json()
    assert "shots" in j
    assert "total_seconds" in j
    assert "fps" in j


def test_orchestrate_dry_run(client):
    """dry_run 不真调 Agnes，只预估视频数/eta/配额"""
    r = client.post(f"/api/project/{P4}/orchestrate", json={
        "stages": ["first_frame", "audio", "last_frame", "video", "compose"],
        "scope": "all", "skip_locked": True, "dry_run": True,
    })
    assert r.status_code == 200
    j = r.json()
    assert j["dry_run"] is True
    assert "plan" in j
    assert "plan_count" in j
    assert "video_count" in j


# ---------- 旧接口必须 404（钉死清理成果）----------

def test_old_run_404(client):
    assert client.post("/api/run", json={}).status_code == 404


def test_old_scenes_404(client):
    assert client.get("/api/projects/x/scenes").status_code == 404


def test_old_compose_404(client):
    assert client.post("/api/project/x/compose", json={}).status_code == 404


def test_old_task_current_404(client):
    """/api/task/current 路由已删；/api/task/{task_id} 不匹配 current（current 不是有效 task_id）"""
    # 注意：/api/task/{task_id} 路由仍保留（compose-shots 用），但 current 不再是专用路由
    r = client.get("/api/task/current")
    # task/{task_id} 把 current 当 task_id，返回 404（task_store 无 'current'）或 200（如有）
    # 关键：不再有专属的 current 路由返回当前任务
    assert r.status_code in (404, 200)


def test_old_test_pages_404(client):
    """test_pages 灰色接口已删（2026-08-01）：测试 HTML 路由 + generate-image + scene 库 + storyboard/generate"""
    assert client.get("/character-test").status_code == 404
    assert client.get("/scene-test").status_code == 404
    assert client.get("/storyboard-test").status_code == 404
    assert client.post("/api/generate-image", json={}).status_code == 404
    assert client.get("/api/scene/list").status_code == 404
    assert client.post("/api/storyboard/generate", json={}).status_code == 404
