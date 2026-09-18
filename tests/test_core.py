"""core 纯函数单元测试（钉死当前行为，为下轮 ③ 拆分提供安全网）。

对应 PHASE_5 §5.4 骨架：snap_num_frames / plan_shot_timing / rate_limiter。
所有用例不依赖外部（不调 Agnes、不读项目），可独立快速跑。
"""
from src.api.agnes_client import snap_num_frames, max_frames_for, resolve_size
from src.core.timing import snap_8n1, plan_shot_timing, split_dialogue, ShotTiming


# ---------- snap_num_frames / max_frames_for / resolve_size ----------

def test_snap_num_frames_global_cap():
    """不给 size 时仅用全局上限 441（AGNES_API_FACTS §3.4）"""
    assert snap_num_frames(120) == 121   # 8*15+1
    assert snap_num_frames(500) == 441   # 夹到全局上限
    assert snap_num_frames(10) == 81     # 夹到下限 MIN_FRAMES


def test_snap_num_frames_size_aware():
    """给 size 时再夹到 max_frames_for(size)"""
    assert snap_num_frames(500, 1280, 704) == 441   # 720p 接受 441
    assert snap_num_frames(500, 1920, 1080) == 169  # 1080p 上限 169


def test_max_frames_for():
    assert max_frames_for(1280, 704) == 441   # 720p 档
    assert max_frames_for(1920, 1080) == 169  # 1080p 档
    assert max_frames_for(640, 480) == 961    # 480p 档


def test_resolve_size():
    assert resolve_size("1K", "16:9") == "1312x736"
    assert resolve_size("unknown", "16:9") == "1312x736"  # 兜底 1K


# ---------- snap_8n1 ----------

def test_snap_8n1():
    assert snap_8n1(120) == 121
    assert snap_8n1(500) == 441
    assert snap_8n1(10) == 81


# ---------- plan_shot_timing ----------

def test_plan_shot_timing_short():
    """短台词塞得进单镜，不拆"""
    t = plan_shot_timing(4.2, 24)
    assert isinstance(t, ShotTiming)
    assert not t.needs_split
    assert t.suggested_splits == 1
    # num_frames 必须 8n+1 且在 [81, 441]
    assert (t.num_frames - 1) % 8 == 0
    assert 81 <= t.num_frames <= 441


def test_plan_shot_timing_needs_split():
    """超长台词必须拆（PHASE_5 §5.4 骨架断言）"""
    t = plan_shot_timing(25.0, 24)
    assert t.needs_split
    assert t.suggested_splits == 2


def test_plan_shot_timing_silent():
    """无台词镜头默认 3.0s -> 81 帧"""
    t = plan_shot_timing(0, 24)
    assert not t.needs_split
    assert t.num_frames == 81
    assert t.audio_seconds == 0


# ---------- split_dialogue ----------

def test_split_dialogue_returns_list():
    parts = split_dialogue("第一句。第二句。第三句。", 24)
    assert isinstance(parts, list)
    assert len(parts) >= 1


def test_split_dialogue_empty():
    assert split_dialogue("", 24) == [""]


# ---------- Phase D：2K 档限速实测校准（29.md §35 运行中系统修复）----------
def test_rate_limiter_2k_calibrated():
    """image_2k 文档 10 RPM 在持续批量下实测不可持续（Phase D Turn 1：95×429），
    DEFAULT_RPM 校准为 4；桶容量仍为 1（不突发）。"""
    from src.core.rate_limiter import DEFAULT_RPM
    assert DEFAULT_RPM["image_2k"] == 4
    assert DEFAULT_RPM["image_1k"] == 20
    assert DEFAULT_RPM["video"] == 1
