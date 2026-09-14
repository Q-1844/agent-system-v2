# tests/test_voice_a1_generation_freshness.py
# Audio V1 Phase A1 §28：Generation artifact（§17/§18）+ Freshness（§21/§22）+
# 显式再生成（§23）+ Race protection（§24）+ Canonical Mutation Audit（§42）+
# Selection / Approval（§20）。
import asyncio
import hashlib
import io
import json
import struct
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.models import Character, Project, Scene, Shot
from src.core.store import ProjectStore
from src.audio.service import VoiceService, VoiceServiceError


# ============ fixtures / helpers ============

def make_svc(tmp_path: Path) -> VoiceService:
    p = Project(name="voice_a1_gen",
                characters=[Character(name="小明"), Character(name="小红")])
    p.scenes.append(Scene(scene_id="s01", characters=["小明", "小红"], shots=[
        Shot(shot_id="s01_c01", scene_id="s01", index=1,
             characters=["小明"], dialogue="我们现在出发。"),
        Shot(shot_id="s01_c02", scene_id="s01", index=2,
             characters=["小红"], dialogue="等一下。"),
        Shot(shot_id="s01_c03", scene_id="s01", index=3,
             characters=["小明"], dialogue="门锁住了。"),
    ]))
    ProjectStore(tmp_path).save(p)
    return VoiceService(tmp_path)


def approve(svc: VoiceService, character: str, voice_id: str) -> str:
    r = svc.create_profile(character, voice_id)
    assert r["ok"], r
    r2 = svc.approve_profile(f"vp_{character}", r["revision"])
    assert r2["ok"], r2
    return r2["revision"]


def wav_bytes(seconds: float = 0.25, rate: int = 16000) -> bytes:
    """stdlib 合成合法 PCM WAV（fake provider 的音频载荷；无需网络/预置文件）。"""
    buf = io.BytesIO()
    n = int(seconds * rate)
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(
            struct.pack("<h", 6000 if ((i // 40) % 2) == 0 else 0) for i in range(n)))
    return buf.getvalue()


class FakeAdapter:
    """Fake provider：计数调用、回放固定载荷；可注入 call_hook（dispatch 前触发）。"""

    def __init__(self, payload: bytes | None = None, call_hook=None):
        self.calls = []                # 每次 dispatch 记录 pack
        self.payload = payload or wav_bytes()
        self.call_hook = call_hook

    async def synthesize(self, pack):
        self.calls.append(dict(pack.to_dict()))
        if self.call_hook is not None:
            await self.call_hook(pack)
        return {"audio": self.payload,
                "provider_extra_info": {"usage_characters": 7,
                                        "audio_length": 1000,
                                        "audio_sample_rate": 32000},
                "request_snapshot": {"model": pack.provider_binding["default_model"],
                                     "text": pack.text},
                "latency_ms": 5, "word_timestamps": [],
                "word_timestamps_note": "word_timestamps_unavailable"}


# ============ Generation artifact（§17/§18/§45）============

def test_generate_creates_artifact_raw_and_production_wav(tmp_path):
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    out = asyncio.run(svc.generate("s01_c01", FakeAdapter()))
    assert out["ok"] is True
    av = out["audio_version"]
    assert av["audio_version_id"] == "s01_c01_av01"
    raw = tmp_path / "output" / "dialogue_audio" / "raw" / "s01_c01_av01.mp3"
    prod = tmp_path / "output" / "dialogue_audio" / "production" / "s01_c01_av01.wav"
    assert raw.exists() and raw.stat().st_size > 0              # §17A raw 保留
    assert prod.exists() and prod.stat().st_size > 0            # §17B production WAV
    probe = av["production_wav"]["ffprobe"]
    assert probe["sample_rate"] == 48000 and probe["channels"] == 1
    assert probe["codec"] == "pcm_s16le"                        # 内部统一 48k/mono/PCM


def test_generate_records_usage_and_provider_metadata(tmp_path):
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    out = asyncio.run(svc.generate("s01_c01", FakeAdapter()))
    av = out["audio_version"]
    assert out["usage_characters"] == 7                         # §26 usage_characters 持久化
    assert av["provider_extra_info"]["usage_characters"] == 7
    assert av["provider"] == "minimax" and av["model"] == "speech-2.8-turbo"
    assert av["provider_voice_id"] == "male-qn-qingse"
    assert av["provider_request_snapshot"] == {"model": "speech-2.8-turbo",
                                               "text": "我们现在出发。"}
    assert av["generation_basis"]["dialogue_id"] == "s01_c01"


def test_generate_persists_store_and_selection_pointer(tmp_path):
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    asyncio.run(svc.generate("s01_c01", FakeAdapter()))
    doc = json.loads((tmp_path / "output" / "dialogue_audio.json")
                     .read_text(encoding="utf-8"))
    entry = doc["dialogues"]["s01_c01"]
    assert entry["selected_audio_version_id"] == "s01_c01_av01"  # 显式 artifact 指针
    assert len(entry["audio_versions"]) == 1


def test_generate_av_id_monotonic_no_reuse(tmp_path):
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    a = asyncio.run(svc.generate("s01_c01", FakeAdapter()))["audio_version_id"]
    b = asyncio.run(svc.generate("s01_c01", FakeAdapter()))["audio_version_id"]
    assert (a, b) == ("s01_c01_av01", "s01_c01_av02")


def test_generate_not_ready_zero_provider_calls(tmp_path):
    """未 READY → voice_not_ready，provider 调用 = 0（§8 门）。"""
    svc = make_svc(tmp_path)
    adapter = FakeAdapter()
    with pytest.raises(VoiceServiceError) as e:
        asyncio.run(svc.generate("s01_c01", adapter))
    assert e.value.code == "voice_not_ready"
    assert adapter.calls == []


def test_generate_secret_never_persisted(tmp_path):
    """§13/§45：secret 绝不进 JSON / artifact（fake adapter 不经手 key，
    但该测试钉死 store 落盘内容全量扫描）。"""
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    asyncio.run(svc.generate("s01_c01", FakeAdapter()))
    for f in (tmp_path / "output").glob("*.json"):
        blob = f.read_text(encoding="utf-8")
        for mark in ("MINIMAX_API_KEY", "Authorization", "Bearer"):
            assert mark not in blob, f"{f.name} 含 {mark}"


# ============ Canonical Mutation Audit（§42/§45：Visual V1 不受影响）============

FROZEN_STORES = ("project.json", "resources.json", "scene_system.json",
                 "project_profile.json", "shot_production_bindings.json")

VOICE_OWN_FILES = {"voice_profiles.json", "dialogue_audio.json"}
VOICE_OWN_DIRS = {"dialogue_audio"}


def _hash_output_dir(pdir: Path) -> dict:
    """output/ 下全部文件的 sha256（含 voice 自身文件；由 caller 过滤）。"""
    out = {}
    root = pdir / "output"
    for f in sorted(root.rglob("*")):
        if f.is_file():
            out[str(f.relative_to(root))] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def test_generate_does_not_touch_visual_canonical(tmp_path):
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    # 审计覆盖面：五冻结 store 全部落盘占位（真实文件参与字节审计）
    for name in FROZEN_STORES:
        f = tmp_path / "output" / name
        if not f.exists():
            f.write_text("{}", encoding="utf-8")
    before = _hash_output_dir(tmp_path)
    asyncio.run(svc.generate("s01_c01", FakeAdapter()))
    after = _hash_output_dir(tmp_path)
    # 1) 既有文件字节恒等（Visual 冻结域一个字节都不许变）
    for k, v in before.items():
        assert after.get(k) == v, f"既有 canonical 被修改: {k}"
    # 2) 新增/消失的只能是 Voice 自己的 store 与音频文件
    added = set(after) - set(before)
    removed = set(before) - set(after)
    assert all(a in VOICE_OWN_FILES or a.split("\\")[0].split("/")[0] in VOICE_OWN_DIRS
               for a in added), f"新增了非 Voice 文件: {added}"
    assert removed == set()


def test_voice_module_writes_only_voice_stores(tmp_path):
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    asyncio.run(svc.generate("s01_c01", FakeAdapter()))
    created = {f.name for f in (tmp_path / "output").iterdir() if f.is_file()}
    voice_own = {"voice_profiles.json", "dialogue_audio.json"}
    # project.json 是 make_svc 造项目时的合法写入；voice 生成后不得再出现其它新 store
    assert created - {"project.json"} == voice_own


# ============ Freshness（§21/§22/§45）============

def test_freshness_missing_when_no_audio(tmp_path):
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    r = svc.freshness("s01_c01")
    assert r["status"] == "missing" and r["code"] == "no_selection"


def test_freshness_current_after_generate(tmp_path):
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    out = asyncio.run(svc.generate("s01_c01", FakeAdapter()))
    assert out["freshness"]["status"] == "current"


def test_freshness_text_change_stale_and_no_auto_regen(tmp_path):
    """§21/§23：Dialogue text 变 → STALE；read/list/freshness 绝不自动 regenerate。"""
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    asyncio.run(svc.generate("s01_c01", FakeAdapter()))
    ProjectStore(tmp_path).update_shot(
        "s01_c01", lambda sh: setattr(sh, "dialogue", "我们现在马上出发。"))
    adapter = FakeAdapter()
    # 连续只读操作（§23：read 不得自动 regenerate）
    for _ in range(3):
        assert svc.freshness("s01_c01")["status"] == "stale"
        svc.list_audio("s01_c01")
    assert adapter.calls == []                                   # 0 provider call


def test_freshness_voice_profile_change_stale(tmp_path):
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    asyncio.run(svc.generate("s01_c01", FakeAdapter()))
    cur = svc.get_profile("vp_小明").revision()
    assert svc.update_profile("vp_小明", cur, speed=1.3)["ok"]
    assert svc.freshness("s01_c01")["status"] == "stale"


def test_freshness_unrelated_profile_change_no_false_stale(tmp_path):
    """§22 核心：Bob 的 VoiceProfile 变化 → Alice 音频保持 CURRENT。"""
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    approve(svc, "小红", "female-shaonv")
    asyncio.run(svc.generate("s01_c01", FakeAdapter()))          # Alice 的 s01_c01
    assert svc.freshness("s01_c01")["status"] == "current"
    cur = svc.get_profile("vp_小红").revision()
    svc.update_profile("vp_小红", cur, speed=1.4)                # 无关角色变化
    assert svc.freshness("s01_c01")["status"] == "current"       # 不 false-stale
    # 同理：Alice 变化不波及 Bob 的 s01_c02（未生成 → missing，也不误报 stale）
    assert svc.freshness("s01_c02")["status"] == "missing"


def test_freshness_unrelated_dialogue_change_no_false_stale(tmp_path):
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    asyncio.run(svc.generate("s01_c01", FakeAdapter()))
    ProjectStore(tmp_path).update_shot(
        "s01_c03", lambda sh: setattr(sh, "dialogue", "风越来越大。"))   # 别的镜
    assert svc.freshness("s01_c01")["status"] == "current"


def test_freshness_speaker_change_stale(tmp_path):
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    asyncio.run(svc.generate("s01_c01", FakeAdapter()))
    ProjectStore(tmp_path).update_shot(
        "s01_c01", lambda sh: setattr(sh, "characters", ["小红"]))       # 换说话人
    r = svc.freshness("s01_c01")
    # stored speaker（小明）已不在出场名单 → 解析失败 → STALE（code=invalid_speaker）
    assert r["status"] == "stale" and r["code"] == "invalid_speaker"


def test_explicit_regenerate_restores_current(tmp_path):
    """§45：STALE → 显式 regenerate → CURRENT（新版本指针）。"""
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    asyncio.run(svc.generate("s01_c01", FakeAdapter()))
    ProjectStore(tmp_path).update_shot(
        "s01_c01", lambda sh: setattr(sh, "dialogue", "我们现在马上出发。"))
    assert svc.freshness("s01_c01")["status"] == "stale"
    out = asyncio.run(svc.generate("s01_c01", FakeAdapter()))    # 显式
    assert out["freshness"]["status"] == "current"
    assert out["audio_version_id"] == "s01_c01_av02"


# ============ Race protection（§24/§28）============

def test_race_related_dialogue_change_zero_provider_calls(tmp_path):
    """生成窗口内 dialogue 变化 → dependency_changed_before_voice_generation，
    provider 调用必须 0。"""
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    adapter = FakeAdapter()

    async def inject_change(pack):                               # 测试 seam：
        ProjectStore(tmp_path).update_shot(                      # pack 后、校验前改
            "s01_c01", lambda sh: setattr(sh, "dialogue", "我们现在出发！"))

    with pytest.raises(VoiceServiceError) as e:
        asyncio.run(svc.generate("s01_c01", adapter,
                                 before_redispatch_hook=inject_change))
    assert e.value.code == "dependency_changed_before_voice_generation"
    assert adapter.calls == []                                   # 0 provider call
    # canonical 未被本次失败生成污染（store 无该 dialogue 条目）
    assert svc.list_audio("s01_c01")["audio_versions"] == []


def test_race_related_voice_change_zero_provider_calls(tmp_path):
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    adapter = FakeAdapter()

    async def inject_change(pack):
        cur = svc.get_profile("vp_小明").revision()
        svc.update_profile("vp_小明", cur, speed=1.5)            # 相关 voice 变化

    with pytest.raises(VoiceServiceError) as e:
        asyncio.run(svc.generate("s01_c01", adapter,
                                 before_redispatch_hook=inject_change))
    assert e.value.code == "dependency_changed_before_voice_generation"
    assert adapter.calls == []


def test_race_unrelated_bob_change_alice_proceeds(tmp_path):
    """§24：无关 Bob VoiceProfile 变化不得阻断 Alice（0 误杀）。"""
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    approve(svc, "小红", "female-shaonv")
    adapter = FakeAdapter()

    async def inject_change(pack):
        cur = svc.get_profile("vp_小红").revision()
        svc.update_profile("vp_小红", cur, speed=1.4)            # 无关角色变化

    out = asyncio.run(svc.generate("s01_c01", adapter,
                                   before_redispatch_hook=inject_change))
    assert out["ok"] is True
    assert len(adapter.calls) == 1                               # Alice 正常 dispatch


# ============ Selection / Approval（§20/§45）============

def test_select_and_approve_semantics(tmp_path):
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    v1 = asyncio.run(svc.generate("s01_c01", FakeAdapter()))["audio_version_id"]
    v2 = asyncio.run(svc.generate("s01_c01", FakeAdapter()))["audio_version_id"]
    listing = svc.list_audio("s01_c01")
    assert [v["audio_version_id"] for v in listing["audio_versions"]] == [v1, v2]
    assert listing["selected_audio_version_id"] == v2            # 生成指向新版本
    statuses = {v["audio_version_id"]: v["review_status"]
                for v in listing["audio_versions"]}
    assert statuses == {v1: "unselected", v2: "selected"}
    assert svc.approve_selected("s01_c01")["approved"] is True   # 人类门
    # 换选择 → 回未批准（approved 绑定所选版本）
    r = svc.select_audio("s01_c01", v1)
    assert r["ok"] is True and r["approved"] is False
    assert svc.list_audio("s01_c01")["approved"] is False
    # 不存在的版本拒绝
    assert svc.select_audio("s01_c01", "s01_c01_av99")["error"] == "audio_version_not_found"
    # 批准后仍消费显式 artifact identity（不是 mtime/glob）
    assert svc.list_audio("s01_c01")["selected_audio_version_id"] == v1


def test_approve_selected_without_selection_rejected(tmp_path):
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    r = svc.approve_selected("s01_c01")
    assert r["ok"] is False and r["error"] == "no_selection"


def test_freshness_selected_version_dangling_missing(tmp_path):
    """选择指针悬空（手改 JSON）→ MISSING，绝不 glob 最新文件顶替。"""
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    asyncio.run(svc.generate("s01_c01", FakeAdapter()))
    f = tmp_path / "output" / "dialogue_audio.json"
    doc = json.loads(f.read_text(encoding="utf-8"))
    doc["dialogues"]["s01_c01"]["selected_audio_version_id"] = "s01_c01_av99"
    f.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    r = svc.freshness("s01_c01")
    assert r["status"] == "missing" and r["code"] == "selected_version_not_found"
