# _p48_a11_skill_snapshot_drill.py — Phase48 A11：半途改 Skill 的快照验证（直接演练）。
# 隔离方式：SkillRegistry 注入临时 source_dirs（仓库支持的测试注入点，正式 Skill 零污染）；
# 真实引擎/校验/编译/RunStore 持久化。内容更新 = 仓库支持的方式（编辑包内文件 + 版本号）。
# 通过标准：run1 冻结 H1 → 内容更新 → run2 快照 H2≠H1 → run1 从磁盘重读仍 H1（不改写历史）。
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

ROOT = Path("_p48_a11_root")
PKG = "p48-skill-snapshot-test"
ev = {"events": []}


def _note(event, **kw):
    ev["events"].append({"ts": time.time(), "event": event, **kw})


def _build_definition():
    nodes = [
        {"id": "nst", "type": "boundary_start", "refId": None, "refVersion": None,
         "label": None, "config": {}, "position": {"x": 0, "y": 0}},
        {"id": "n_dir", "type": "agent", "refId": "director", "refVersion": None,
         "label": None, "config": {"objective": "测试 Skill 快照绑定（不派发）"},
         "position": {"x": 0, "y": 0}},
        {"id": "nen", "type": "boundary_end", "refId": None, "refVersion": None,
         "label": None, "config": {}, "position": {"x": 0, "y": 0}},
        {"id": "n_skill", "type": "skill", "refId": PKG, "refVersion": None,
         "label": PKG, "config": {}, "position": {"x": 0, "y": 0}},
    ]
    edges = [
        {"id": "e1", "type": "execution", "source": "nst", "target": "n_dir",
         "sourcePort": "out", "targetPort": "in"},
        {"id": "e2", "type": "execution", "source": "n_dir", "target": "nen",
         "sourcePort": "out", "targetPort": "in"},
        {"id": "eb1", "type": "skill_binding", "source": "n_skill", "target": "n_dir",
         "skillVersion": "1.4.0", "sourcePort": "out", "targetPort": "in"},
    ]
    return {"schemaVersion": "workflow_draft_v1", "projectId": "P48_A11",
            "workflowId": "wf_a11", "name": "A11 Skill 快照演练链",
            "status": "draft", "nodes": nodes, "edges": edges}


def main():
    from src.agents.skill_system.registry import SkillRegistry
    from src.agents.runtime import AgentRuntime
    from src.workflow import catalog as wf_catalog
    from src.workflow.validate import validate_definition
    from src.workflow.plan import compile_plan, build_start_snapshot
    from src.workflow.executor import WorkflowEngine
    from src.workflow.store import RunStore

    # ① 隔离 Skill 包（复制真实 bridge 包，改 id/版本；正式 builtin 目录零写入）
    src_pkg = Path("src/agents/skill_system/builtin/keyframes-bridge-repair")
    tmp_skills = ROOT / "_skills"
    pkg_dir = tmp_skills / PKG
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)
    shutil.copytree(src_pkg, pkg_dir)
    mj = pkg_dir / "skill.json"
    m = json.loads(mj.read_text(encoding="utf-8"))
    m["skill_id"] = PKG
    m["version"] = "1.4.0"
    mj.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")

    rt = AgentRuntime(project_dir_resolver=lambda name: ROOT / name,
                      settings_path=ROOT / "settings.json")
    reg = SkillRegistry(source_dirs=[tmp_skills])
    assert reg.get(PKG) is not None, "隔离包应注册成功"
    # 注入 catalog 的 registry 来源（正式全局 registry 不受影响）
    wf_catalog.get_skill_registry = lambda: reg

    def _start_run(tag):
        d = _build_definition()
        v = validate_definition(d, rt, "P48_A11")
        assert v["ok"], v["errors"]
        plan = compile_plan(d, v, v["catalog"])
        snap = build_start_snapshot(d, rt, "P48_A11", v, plan)
        eng = WorkflowEngine()
        out = eng.start_run(ROOT / "P48_A11", "P48_A11", d, v, plan, snap,
                            f"key-a11-{tag}")
        return out["run"], snap

    # ② run1：内容版本 V1 冻结
    run1, snap1 = _start_run("r1")
    h1 = snap1["skillVersions"][PKG]["contentHash"]
    v1 = snap1["skillVersions"][PKG]["version"]
    _note("run1_started", runId=run1["runId"], contentHash=h1, version=v1)

    # ③ 半途内容更新（仓库支持的方式：编辑包文件 + 版本号）→ registry 重载
    time.sleep(1.1)   # 确保 mtime 变化可被签名捕捉
    (pkg_dir / "SKILL.md").write_text(
        (pkg_dir / "SKILL.md").read_text(encoding="utf-8")
        + "\n\n## A11 演练追加：内容版本 V2（隔离副本）\n", encoding="utf-8")
    m2 = json.loads(mj.read_text(encoding="utf-8"))
    m2["version"] = "1.4.1"
    t_edit = time.time()
    mj.write_text(json.dumps(m2, ensure_ascii=False, indent=2), encoding="utf-8")
    _note("content_updated", version="1.4.1", edit_ts=t_edit)
    reg.reload()   # 显式重载（生产路径由 mtime 签名触发；演练显式化窗口）

    # ④ run2：新 run 读到新内容
    run2, snap2 = _start_run("r2")
    h2 = snap2["skillVersions"][PKG]["contentHash"]
    v2 = snap2["skillVersions"][PKG]["version"]
    _note("run2_started", runId=run2["runId"], contentHash=h2, version=v2)

    # ⑤ run1 从磁盘重读：仍绑定 H1（历史不改写）
    run1_disk = RunStore(ROOT / "P48_A11", "P48_A11").load(run1["runId"])
    h1_disk = run1_disk["snapshot"]["skillVersions"][PKG]["contentHash"]
    _note("run1_reloaded_from_disk", contentHash=h1_disk)

    # ⑥ 生产交叉核对：F3 快照 hash == 当前正式 relay 包 hash（冻结后零漂移）
    f3 = json.loads(Path("projects/山雀夜航/output/workflows/runs/run_fb5e2c27eac8.json")
                    .read_text(encoding="utf-8"))
    h_f3 = f3["snapshot"]["skillVersions"]["full-story-relay-calibration"]["contentHash"]
    from src.workflow.catalog import _skill_content_hash
    h_cur = _skill_content_hash(reg.get(PKG))   # 隔离包当前 hash（V2）
    relay_reg = SkillRegistry()                 # 正式 builtin registry（未受注入影响）
    h_relay_now = _skill_content_hash(relay_reg.get("full-story-relay-calibration"))
    _note("cross_check_f3", f3_snapshot=h_f3, relay_current=h_relay_now)

    ev["results"] = {
        "run1_hash": h1, "run1_version": v1,
        "run2_hash": h2, "run2_version": v2,
        "run1_hash_unchanged_on_disk": (h1_disk == h1),
        "new_run_reads_new_content": (h2 != h1 and h2 == h_cur),
        "note": "快照 version=定义边请求版本（定义层冻结）；contentHash=实际包内容快照——V2 内容由 hash 变化与 h_cur 等值证明",
        "f3_snapshot_matches_current_builtin": (h_f3 == h_relay_now),
        "isolated_pkg_v2_hash": h_cur,
    }
    ok = (ev["results"]["run1_hash_unchanged_on_disk"]
          and ev["results"]["new_run_reads_new_content"]
          and ev["results"]["f3_snapshot_matches_current_builtin"])
    ev["pass"] = ok
    Path("_p48_a11_drill_evidence.json").write_text(
        json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(ev["results"], ensure_ascii=False, indent=1))
    print("A11 PASS" if ok else "A11 FAIL")


if __name__ == "__main__":
    main()
