"""SceneAgentRegistry 测试（mock，不联网）。

覆盖：入库规则（E2 accepted-only / volatile 不落）/ 前提失效（per-shot token +
scene_content 比对 → quarantined，SW 改写整体失效路径）/ 注入上限（K/M/notes）/
三节点惰性检测（派发 liveness+清扫 / commit / turn 收尾 sweep：retire + 孤儿清算）/
单段单飞句柄 / 压力计数 / 全局记忆（E3）。
"""
import pytest

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.subagent import registry as reg
from src.agents.subagent import fingerprints as fp


def make_proj(dirpath):
    s01 = [Shot(shot_id="s01_c01", scene_id="s01", index=1, description="外景"),
           Shot(shot_id="s01_c02", scene_id="s01", index=2, description="走廊",
                source_shot_id="s01_c01", opening_source="inherit_previous")]
    proj = Project(
        name="demo", style=StyleAnchor(), story_text="正文。",
        characters=[Character(name="林晚星", description_en="a keeper")],
        scenes=[Scene(scene_id="s01", summary="段一", characters=["林晚星"], shots=s01),
                Scene(scene_id="s02", summary="段二", characters=[], shots=[])],
        next_scene_seq=3)
    ProjectStore(dirpath).save(proj)
    return proj


@pytest.fixture()
def store(tmp_path):
    make_proj(tmp_path)
    return reg.SubagentStore(tmp_path, "demo"), tmp_path


def test_lazy_spawn_and_retire(store):
    st, tmp = store
    assert st.scene_record("s01") is None          # 不预建
    st.ensure_scene("s01")
    rec = st.scene_record("s01")
    assert rec["status"] == "active"
    assert st.retire_scene("s01") is True
    assert st.scene_record("s01")["status"] == "retired"
    assert st.retire_scene("s01") is False          # 幂等
    assert st.retire_scene("s99") is False          # 不存在


def test_memory_entry_rules(store):
    """E2：入库时点=commit 成功后；volatile 不落 memory；premises 带指纹（E6）。"""
    st, tmp = store
    st.ensure_scene("s01")
    proj = ProjectStore(tmp).load()
    scene = proj.scenes[0]
    basis = fp.scene_slice_fingerprint(scene)
    entries = reg.make_memory_entries(
        "s01", "dlg_x", "prop_x", basis,
        effective_end_state=[
            {"shot_id": "s01_c01", "fields": {},
             "rationale": "全景建立空间（依赖 canonical 角色）",
             "dependencies": [{"kind": "character_canonical", "ref": "林晚星"}]},
            {"shot_id": "s01_c02", "fields": {},
             "rationale": "接力走廊（依赖邻接）",
             "dependencies": [{"kind": "adjacency", "ref": "current"}]},
        ],
        design_notes="本段以暴雨压迫感为主线",
        scene=scene)
    # volatile 条目（adjacency 依赖）存在但不落盘
    assert any(e["volatile"] for e in entries)
    st.append_memory("s01", entries)
    on_disk = st.load_memory("s01")
    assert all(not e["volatile"] for e in on_disk)
    assert any("全景建立空间" in e["text"] for e in on_disk)
    # premises 指纹齐备（E6：shot_id + token）
    e0 = next(e for e in on_disk if "s01_c01" in e["text"])
    assert e0["premises"]["shots"][0]["shot_id"] == "s01_c01"
    assert len(e0["premises"]["shots"][0]["token"]) == 8
    assert e0["provenance"]["basis_fingerprint"] == basis


def test_quarantine_on_premise_stale(store):
    """前提失效：SW 改写段内容（story.update 域）→ scene_content_token 变 →
    全量隔离（宁可错杀）；镜内容变 → 引用该镜的条目隔离。"""
    st, tmp = store
    st.ensure_scene("s01")
    proj = ProjectStore(tmp).load()
    scene = proj.scenes[0]
    basis = fp.scene_slice_fingerprint(scene)
    entries = reg.make_memory_entries(
        "s01", "dlg_x", "prop_x", basis,
        effective_end_state=[
            {"shot_id": "s01_c01", "fields": {},
             "rationale": "决策A（锚定 c01）", "dependencies": []},
            {"shot_id": "s01_c02", "fields": {},
             "rationale": "决策B（锚定 c02）", "dependencies": []},
        ],
        design_notes="段级说明", scene=scene)
    st.append_memory("s01", entries)
    # 场景 1：只改 c02 内容 → 只有锚定 c02 的条目隔离（per-shot 粒度）
    proj = ProjectStore(tmp).load()
    proj.get_shot("s01_c02").description = "被改写的走廊"
    ProjectStore(tmp).save(proj)
    swept = st.sweep_memory(proj.scenes[0])
    quarantined_texts = [e["text"] for e in st.load_memory("s01")
                         if e["status"] == "quarantined"]
    assert any("决策B" in t for t in quarantined_texts)
    assert not any("决策A" in t for t in quarantined_texts)
    assert swept["quarantined_count"] >= 1
    # 场景 2：SW 改段 summary → scene_content_token 变 → 剩余全隔离
    proj = ProjectStore(tmp).load()
    proj.scenes[0].summary = "彻底改写的剧情"
    ProjectStore(tmp).save(proj)
    swept2 = st.sweep_memory(proj.scenes[0])
    remaining_active = [e for e in st.load_memory("s01")
                        if e["status"] == "active"]
    assert remaining_active == []
    # 隔离条目保留供审计（不删）
    assert len(st.load_memory("s01")) >= 3


def test_injection_caps(store):
    """注入上限：K=12 / per-shot M=3 / 段级 notes 4。"""
    st, tmp = store
    st.ensure_scene("s01")
    proj = ProjectStore(tmp).load()
    scene = proj.scenes[0]
    basis = fp.scene_slice_fingerprint(scene)
    end_state = []
    # c01 挂 5 条（应只保留最新 3）+ c02 挂 2 条 + 5 条段级 notes（应只保留 4）
    for i in range(5):
        end_state.append({"shot_id": "s01_c01", "fields": {},
                          "rationale": f"c01 决策第{i}代", "dependencies": []})
    for i in range(2):
        end_state.append({"shot_id": "s01_c02", "fields": {},
                          "rationale": f"c02 决策第{i}代", "dependencies": []})
    entries = []
    for i in range(5):
        entries += reg.make_memory_entries(
            "s01", f"dlg_{i}", f"prop_{i}", basis, end_state,
            design_notes=f"段级说明第{i}版", scene=scene,
            created_at=1000.0 + i)
    st.append_memory("s01", entries)
    text, n_active, _ = st.render_memory_for_injection(scene)
    assert "c01 决策第4代" in text and "c01 决策第0代" not in text   # M=3
    assert "段级说明第4版" in text and "段级说明第0版" not in text   # notes=4
    assert n_active <= reg.MEMORY_INJECTION_CAP                     # K=12


def test_turn_tail_sweep_retire_and_orphan(store):
    """节点③：registry−canonical → retire；孤儿 delegation 清算。"""
    st, tmp = store
    st.ensure_scene("s01")
    st.ensure_scene("s02")
    # 孤儿 delegation：dispatched、无句柄、无 proposal
    st.save_delegation({"delegation_id": "dlg_orphan", "scene_id": "s02",
                        "status": "dispatched", "proposal_id": None})
    # 活跃句柄的 delegation：不清算
    reg.acquire_handle("s01", "dlg_live")
    st.save_delegation({"delegation_id": "dlg_live", "scene_id": "s01",
                        "status": "dispatched", "proposal_id": None})
    # SW 删掉 s02
    proj = ProjectStore(tmp).load()
    proj.scenes = [s for s in proj.scenes if s.scene_id != "s02"]
    ProjectStore(tmp).save(proj)
    result = st.sweep([s.scene_id for s in proj.scenes])
    assert result["retired"] == ["s02"]
    assert result["aborted"] == ["dlg_orphan"]
    assert st.load_delegation("dlg_live")["status"] == "dispatched"
    reg.release_handle("s01", "dlg_live")
    # 下一轮 sweep：live 变孤儿（句柄已释放）
    result2 = st.sweep([s.scene_id for s in proj.scenes])
    assert "dlg_live" in result2["aborted"]


def test_single_flight_handle():
    reg.reset_turn_budget()
    assert reg.acquire_handle("s05", "dlg_1") is True
    assert reg.acquire_handle("s05", "dlg_2") is False   # 单段单飞
    assert reg.active_handle("s05") == "dlg_1"
    reg.release_handle("s05", "dlg_1")
    assert reg.acquire_handle("s05", "dlg_3") is True
    reg.release_handle("s05", "dlg_3")


def test_pressure_and_structural_accounting(store):
    """structural_since_delegation 聚合 + pressure 阈值。"""
    st, tmp = store
    st.ensure_scene("s01")
    receipts = [
        {"ok": True, "verified": True, "unchanged": False,
         "operation": "shot.create", "scene_id": "s01"},
        {"ok": True, "verified": True, "unchanged": False,
         "operation": "shot.delete", "scene_id": "s01"},
        {"ok": True, "verified": True, "unchanged": False,
         "operation": "shot.update", "scene_id": "s01"},   # 非结构写：不计
        {"ok": False, "verified": True, "unchanged": False,
         "operation": "shot.move", "scene_id": "s01"},     # 失败：不计
    ]
    st.add_structural_writes(receipts)
    assert st.scene_record("s01")["structural_since_delegation"] == 2
    assert st.pressure_ready("s01") is False
    for _ in range(4):
        st.add_structural_writes([receipts[0]])
    assert st.pressure_ready("s01") is True   # 6 ≥ SPAWN_PRESSURE_THRESHOLD
    # 新 delegation 起算清零
    st.bump_delegation_count("s01")
    assert st.scene_record("s01")["structural_since_delegation"] == 0
    assert st.scene_record("s01")["delegation_count"] == 1


def test_global_memory(store):
    """E3：全局决策记忆 + 注入渲染（空 = 空串，reground bit-for-bit 保护）。"""
    st, tmp = store
    assert st.render_global_memory_for_injection() == ""
    st.append_global_memory(reg.make_global_entry(
        "s05 提案 accept：保 B 弃 A，因为全片节奏曲线在中段需要收紧",
        proposal_id="prop_x"))
    st.append_global_memory(reg.make_global_entry(
        "接受 s05/s06 边界改为夜戏衔接", proposal_id="prop_y",
        kind="bcr_decision"))
    text = st.render_global_memory_for_injection()
    assert "全局导演决策" in text
    assert "prop_x" in text and "夜戏衔接" in text
    # 上限 K_g=8：注入只取最新 8 条
    for i in range(10):
        st.append_global_memory(reg.make_global_entry(f"决策{i}"))
    assert st.render_global_memory_for_injection().count("- ") <= reg.GLOBAL_MEMORY_CAP


def test_atomic_and_versioned(store):
    """存储原子 + schema version（硬约束 6）。"""
    st, tmp = store
    st.ensure_scene("s01")
    st.save_delegation({"delegation_id": "dlg_v", "scene_id": "s01",
                        "status": "dispatched"})
    st.save_proposal({"proposal_id": "prop_v", "scene_id": "s01",
                      "end_state": []})
    import json
    root = tmp / "output" / "agents" / "scene_agents"
    assert json.loads((root / "registry.json").read_text(encoding="utf-8"))["version"] == 1
    assert json.loads((root / "delegations" / "dlg_v.json").read_text(encoding="utf-8"))["version"] == 1
    assert json.loads((root / "proposals" / "prop_v.json").read_text(encoding="utf-8"))["version"] == 1
