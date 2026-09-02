# spike/subagent_diff_spike.py
# Director Sub-Agent Part 1 · diff feasibility spike（非生产代码，架构检查用）。
#
# 目的：验证 Part 1 §F/§11.6 的核心承诺——"desired scene end state（有序镜头集 +
# 接力图）→ 现有 shot.create/create_many/update/move/delete 原语"是否能稳定转换。
# 全部通过现有 TOOL_EXECUTORS 直接调用（模拟未来 Runtime 确定性 commit 路径：
# 不经 LLM tool-loop，指纹读时现算）。
#
# 运行：python spike/subagent_diff_spike.py（在仓库根目录）
# 输出：每个场景 PASS / FAIL / BLOCKER + 最终汇总。不修改任何生产代码。
import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime, AgentError
from src.agents.director import (
    TOOL_EXECUTORS, _shots_token, _shot_token,
)

RESULTS = []


def report(name, status, detail=""):
    RESULTS.append((name, status, detail))
    print(f"[{status:^7}] {name}" + (f" —— {detail}" if detail else ""))


# ============ 测试项目 ============
def make_proj(dirpath: Path):
    """s01：3 镜接力链 c1→c2→c3；s02：空段；s03：自由镜 a,b + 链 c1,c2（无段内接力）。"""
    s01 = [Shot(shot_id="s01_c01", scene_id="s01", index=1,
                description="雨夜灯塔外景", characters=["林晚星"]),
           Shot(shot_id="s01_c02", scene_id="s01", index=2,
                description="她冲进走廊", characters=["林晚星"],
                source_shot_id="s01_c01", opening_source="inherit_previous"),
           Shot(shot_id="s01_c03", scene_id="s01", index=3,
                description="楼梯间回望", characters=["林晚星"],
                source_shot_id="s01_c02", opening_source="inherit_previous")]
    s03 = [Shot(shot_id="s03_c01", scene_id="s03", index=1, description="码头全景"),
           Shot(shot_id="s03_c02", scene_id="s03", index=2, description="甲板特写",
                source_shot_id="s03_c01", opening_source="inherit_previous"),
           Shot(shot_id="s03_c03", scene_id="s03", index=3, description="缆绳松动"),
           Shot(shot_id="s03_c04", scene_id="s03", index=4, description="老水手抬头")]
    proj = Project(
        name="demo", style=StyleAnchor(), story_text="正文。",
        characters=[Character(name="林晚星", description_en="a keeper")],
        scenes=[Scene(scene_id="s01", summary="灯塔段", characters=["林晚星"], shots=s01),
                Scene(scene_id="s02", summary="空段", characters=[], shots=[]),
                Scene(scene_id="s03", summary="码头段", characters=[], shots=s03)],
        next_scene_seq=4)
    ProjectStore(dirpath).save(proj)
    return proj


def rt_for(path: Path) -> AgentRuntime:
    return AgentRuntime(project_dir_resolver=lambda name: path,
                        settings_path=path / "agents_data" / "settings.json",
                        client=None)


def load(path: Path):
    return ProjectStore(path).load()


def scene_of(proj, scene_id):
    return next((s for s in proj.scenes if s.scene_id == scene_id), None)


# ============ spike 版核心原语（Part 2 diff engine 的最小雏形）============

def scene_slice_fingerprint(path: Path, scene_id: str) -> str:
    """per-scene 派生指纹（§7.5）：scene 内容字段（= SW story.update 白名单域）
    + 有序 shots（id + 可写内容 + 接力边）。读时现算，不动 canonical。
    与 SW 侧 _scene_token 同构（其只覆盖内容字段，这里扩展纳入 shots）。"""
    import hashlib
    scene = scene_of(load(path), scene_id)
    if scene is None:
        return ""
    payload = json.dumps({
        "content": {"title": scene.title, "location": scene.location,
                    "time_of_day": scene.time_of_day, "summary": scene.summary,
                    "characters": list(scene.characters or [])},
        "shots": [[sh.shot_id, sh.description, sh.source_shot_id]
                  for sh in scene.shots],
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def validate_end_state(end_state: list):
    """图完备校验（§11.6 第一道防线）：接力闭包 + 紧邻性。
    end_state 项：{shot_id?（已有 id=keep；缺省=create）, description, source_shot_id?}。
    返回错误列表（空 = 合法）。"""
    problems = []
    ids = [it.get("shot_id") for it in end_state]
    for i, it in enumerate(end_state):
        src = it.get("source_shot_id")
        if not src:
            continue
        if src not in ids:
            problems.append(f"{it.get('shot_id') or f'#{i}'} 接力的 {src} 不在目标镜头集")
        elif i == 0 or ids[i - 1] != src:
            problems.append(f"{it.get('shot_id') or f'#{i}'} 接力 {src} 但紧邻前驱是 "
                            f"{ids[i - 1] if i else '（无）'}——接力必须紧跟来源")
    return problems


def _exec(rt, project, tool, **args):
    obs = TOOL_EXECUTORS[tool](rt, project, json.dumps(args, ensure_ascii=False),
                               "spike-diff-commit", "msg_spike")
    if not obs.get("ok"):
        raise RuntimeError(f"{tool} 失败: {obs.get('error')}")
    return obs


def apply_end_state(rt, project: str, path: Path, scene_id: str,
                    end_state: list, *, basis: str = None,
                    skip_create_items: list = None):
    """确定性 diff + commit（模拟 Runtime 串行 commit 路径）。
    返回 (success, problems, ops_log)。
    策略：liveness → basis → 图完备 → delete → create(段尾) → reorder → update。"""
    problems, ops = [], []
    # 0. liveness（§8.2 commit 时复查）
    if scene_of(load(path), scene_id) is None:
        return False, [f"scene {scene_id} 已不存在（liveness 失败）"], ops
    # 1. basis（§F：proposal_basis 防语义 lost update）
    if basis is not None:
        cur = scene_slice_fingerprint(path, scene_id)
        if cur != basis:
            return False, [f"basis stale：proposal 基于指纹 {basis}，"
                           f"当前 {cur}——需 reconcile/rebase，不得直接套用"], ops
    # 2. 图完备（含 partial adoption 子集的重校验）
    eff = end_state if skip_create_items is None else \
        [it for it in end_state
         if it not in (skip_create_items or [])]
    problems += validate_end_state(eff)
    if problems:
        return False, problems, ops
    # 3. 分类
    cur_ids = [sh.shot_id for sh in scene_of(load(path), scene_id).shots]
    keep_ids = {it["shot_id"] for it in eff if it.get("shot_id") in cur_ids}
    del_ids = [i for i in cur_ids if i not in keep_ids]
    new_items = [it for it in eff if not it.get("shot_id") or it["shot_id"] not in cur_ids]
    # 4a. 预 detach（Part 2 §6.2 修订）：待删镜头的 keep 下游先解除接力——
    #     canonical 接力边在 delete 时仍存活（c3 锚 c2），不先解除会级联误删 keep 镜
    #     （合法端态"删中段重接下游"被误判为引擎缺陷）。解除后阶段 4b 再按端态改锚。
    del_set = set(del_ids)
    for sh in list(scene_of(load(path), scene_id).shots):
        if sh.source_shot_id in del_set and sh.shot_id in keep_ids:
            _exec(rt, project, "shot.update", shot_id=sh.shot_id,
                  expected_revision=_shot_token(load(path).get_shot(sh.shot_id)),
                  source_shot_id=None)
            ops.append(("pre-detach", sh.shot_id, f"was→{sh.source_shot_id}"))
    # 4b. delete（级联闭包意外 = 端态不可达信号；预 detach 后只剩引擎缺陷会触发）
    for sid in del_ids:
        casc = _exec(rt, project, "shot.delete", shot_id=sid)
        gone = casc.get("deleted", {}).get("shots", [])
        unexpected = [g for g in gone if g != sid and g in keep_ids]
        if unexpected:
            problems.append(f"delete {sid} 级联删掉了端态要保留的镜头: {unexpected}")
            return False, problems, ops
        ops.append(("delete", sid, {"cascaded": gone}))
    # 5. create（追加段尾，无锚 → 无 dependency_block 风险；顺序随后续 reorder 拉正）。
    #    spec 不带 source_shot_id——创建时新镜 id 未分配，接力在 update 阶段挂接（先建后锚）。
    if len(new_items) >= 2:
        specs = [{"description": it["description"]} for it in new_items]
        obs = _exec(rt, project, "shot.create_many", scene_id=scene_id, shots=specs,
                    expected_shots_revision=_shots_token(load(path).scenes))
        created = [c["shot_id"] for c in obs.get("created", [])]
        for c, it in zip(created, new_items):
            it["shot_id"] = c
        ops.append(("create_many", scene_id, created))
    elif new_items:
        it = new_items[0]
        obs = _exec(rt, project, "shot.create", scene_id=scene_id,
                    description=it["description"],
                    expected_shots_revision=_shots_token(load(path).scenes))
        it["shot_id"] = obs["created"][0]["shot_id"]
        ops.append(("create", scene_id, it["shot_id"]))
    # 6. reorder（selection 式；每步用最新结构指纹）
    desired = [it["shot_id"] for it in eff]
    for i in range(len(desired)):
        cur = [sh.shot_id for sh in scene_of(load(path), scene_id).shots]
        if cur[i] == desired[i]:
            continue
        if i == 0:
            _exec(rt, project, "shot.move", shot_id=desired[0], before=cur[0],
                  expected_shots_revision=_shots_token(load(path).scenes))
        else:
            _exec(rt, project, "shot.move", shot_id=desired[i], after=desired[i - 1],
                  expected_shots_revision=_shots_token(load(path).scenes))
        ops.append(("move", desired[i], f"→pos {i}"))
    # 7. update 内容 + 接力挂接（每镜最新内容指纹；reorder 已保证相邻性）
    for it in eff:
        if it.get("shot_id") not in keep_ids and it.get("shot_id") not in \
                [c.get("shot_id") for c in new_items]:
            continue
        fields = {k: v for k, v in it.items()
                  if k not in ("shot_id", "source_shot_id")}
        if it.get("source_shot_id") is not None:
            fields["source_shot_id"] = it["source_shot_id"]
        if not fields:
            continue
        _exec(rt, project, "shot.update", shot_id=it["shot_id"],
              expected_revision=_shot_token(load(path).get_shot(it["shot_id"])),
              **fields)
        ops.append(("update", it["shot_id"], list(fields.keys())))
    # 8. 终态断言：canonical 顺序 == desired
    final = [sh.shot_id for sh in scene_of(load(path), scene_id).shots]
    if final != desired:
        problems.append(f"终态顺序不符：{final} != {desired}")
        return False, problems, ops
    return True, problems, ops


# ============ 场景 ============

def s1_create(tmp):
    """空段从无到有：3 镜含接力链。"""
    path = tmp / "s1"; path.mkdir()
    make_proj(path)
    rt = rt_for(path)
    end = [{"description": "教室晨光"}, {"description": "翻开课本",
            "source_shot_id": None},
           {"description": "窗外鸟飞过"}]
    # 注：source_shot_id=None → 不带接力；这里测纯 create
    ok, probs, ops = apply_end_state(rt, "demo", path, "s02", end)
    scene = scene_of(load(path), "s02")
    assert ok and len(scene.shots) == 3, probs
    report("S1 create（空段→3镜）", "PASS", f"ops={len(ops)}")


def s2_update(tmp):
    """纯内容 update。"""
    path = tmp / "s2"; path.mkdir()
    make_proj(path)
    rt = rt_for(path)
    end = [{"shot_id": "s01_c01", "description": "雨夜灯塔外景（大远景）"},
           {"shot_id": "s01_c02", "description": "她冲进走廊"},
           {"shot_id": "s01_c03", "description": "楼梯间回望"}]
    ok, probs, ops = apply_end_state(rt, "demo", path, "s01", end)
    assert ok, probs
    assert load(path).get_shot("s01_c01").description.endswith("大远景）")
    report("S2 update（改描述）", "PASS")


def s3_delete(tmp):
    """删尾镜（无下游）。"""
    path = tmp / "s3"; path.mkdir()
    make_proj(path)
    rt = rt_for(path)
    end = [{"shot_id": "s01_c01", "description": "雨夜灯塔外景"},
           {"shot_id": "s01_c02", "description": "她冲进走廊"}]
    ok, probs, ops = apply_end_state(rt, "demo", path, "s01", end)
    assert ok, probs
    assert load(path).get_shot("s01_c03") is None
    report("S3 delete（删尾镜）", "PASS")


def s4_reorder(tmp):
    """自由镜重排（保持接力链紧邻）。"""
    path = tmp / "s4"; path.mkdir()
    make_proj(path)
    rt = rt_for(path)
    end = [{"shot_id": "s03_c01", "description": "码头全景"},
           {"shot_id": "s03_c02", "description": "甲板特写",
            "source_shot_id": "s03_c01"},
           {"shot_id": "s03_c03", "description": "缆绳松动"},
           {"shot_id": "s03_c04", "description": "老水手抬头"}]
    # 目标：把 c04（老水手抬头）移到最前，链 c01→c02 保持紧邻
    end = [end[3], end[0], end[1], end[2]]
    end[1], end[2] = end[2], end[1]  # [c04, c01, c02, c03]
    # 修正引用：直接重写
    end = [{"shot_id": "s03_c04", "description": "老水手抬头"},
           {"shot_id": "s03_c01", "description": "码头全景"},
           {"shot_id": "s03_c02", "description": "甲板特写",
            "source_shot_id": "s03_c01"},
           {"shot_id": "s03_c03", "description": "缆绳松动"}]
    ok, probs, ops = apply_end_state(rt, "demo", path, "s03", end)
    final = [sh.shot_id for sh in scene_of(load(path), "s03").shots]
    assert ok and final == ["s03_c04", "s03_c01", "s03_c02", "s03_c03"], (probs, final)
    report("S4 reorder（move 拉正，链保持紧邻）", "PASS", f"ops={len(ops)}")


def s4b_reorder_invalid(tmp):
    """非法端态：接力倒序 → 图完备校验拒绝（打回，零副作用）。"""
    path = tmp / "s4b"; path.mkdir()
    make_proj(path)
    rt = rt_for(path)
    end = [{"shot_id": "s03_c02", "description": "甲板特写",
            "source_shot_id": "s03_c01"},   # c2 接力 c01 但 c01 不在它前面
           {"shot_id": "s03_c01", "description": "码头全景"}]
    ok, probs, ops = apply_end_state(rt, "demo", path, "s03", end)
    assert not ok and any("紧邻" in p for p in probs), probs
    assert len(scene_of(load(path), "s03").shots) == 4  # 零副作用
    report("S4b 非法端态（接力倒序）被图完备校验拦截", "PASS")


def s5_mixed(tmp):
    """混合：删 1 + 增 2 + 重排 + 改内容（自由镜段）。"""
    path = tmp / "s5"; path.mkdir()
    make_proj(path)
    rt = rt_for(path)
    end = [
        {"shot_id": "s03_c01", "description": "码头全景（改：黄昏）"},
        {"description": "海鸥掠过桅杆", "source_shot_id": "s03_c01"},
        {"shot_id": "s03_c02", "description": "甲板特写",
         "source_shot_id": "s03_c01"},   # 注意：c02 与新镜头都锚 c01 —— 非法（紧邻）
    ]
    # 修正为合法混合：删 c03/c04，链后追加新镜
    end = [
        {"shot_id": "s03_c01", "description": "码头全景（改：黄昏）"},
        {"shot_id": "s03_c02", "description": "甲板特写",
         "source_shot_id": "s03_c01"},
        {"description": "海鸥掠过桅杆", "source_shot_id": "s03_c02"},
        {"description": "远处的风暴云"},
    ]
    ok, probs, ops = apply_end_state(rt, "demo", path, "s03", end)
    final = [sh.shot_id for sh in scene_of(load(path), "s03").shots]
    assert ok and len(final) == 4, (probs, final)
    # 内容按端态落位（id 可能复用刚删除的序号：分配规则=段内现存最大+1）
    descs = [sh.description for sh in scene_of(load(path), "s03").shots]
    assert descs == ["码头全景（改：黄昏）", "甲板特写", "海鸥掠过桅杆", "远处的风暴云"], descs
    # 新镜头的接力挂接：create spec 里的 source_shot_id 被白名单静默剔除（同 S10）
    relay_ok = scene_of(load(path), "s03").shots[2].source_shot_id == "s03_c02"
    detail = (f"结构/内容全部按端态落位；id 复用了刚删除的序号 {final}"
              + ("；接力挂接成功" if relay_ok
                 else "；⚠ 新镜头 source_shot_id 在 create spec 中被白名单静默剔除（S10 同根因）"))
    report("S5 混合（delete+create+reorder+update）", "PASS", detail)


def s6_detach_before_delete(tmp):
    """§11.6 的核心场景：删 c1 但保留接力它的 c2（先 detach 再 delete）。
    修复后（update_shot_relay + shot.update 的 source_shot_id）应完整可达。"""
    path = tmp / "s6"; path.mkdir()
    make_proj(path)
    rt = rt_for(path)
    # detach：update 传 source_shot_id=None
    obs = TOOL_EXECUTORS["shot.update"](
        rt, "demo", json.dumps({"shot_id": "s01_c02",
                                "expected_revision":
                                    _shot_token(load(path).get_shot("s01_c02")),
                                "source_shot_id": None}, ensure_ascii=False),
        "spike", "msg")
    detached = obs.get("ok") and "source_shot_id" in (obs.get("changed") or [])
    assert detached, f"detach 失败: {obs}"
    # delete 来源 → 下游存活
    obs2 = TOOL_EXECUTORS["shot.delete"](
        rt, "demo", json.dumps({"shot_id": "s01_c01"}), "spike", "msg")
    gone = obs2.get("deleted", {}).get("shots", [])
    c02 = load(path).get_shot("s01_c02")
    c03 = load(path).get_shot("s01_c03")
    if (c02 is not None and c02.source_shot_id is None
            and c03 is not None and c03.source_shot_id == "s01_c02"
            and gone == ["s01_c01"]):
        report("S6 detach-before-delete", "PASS",
               "c02 detach 后幸存（旧路径会被级联删除）；c03 仍接力 c02 不受影响；"
               "仅 c01 被删（无意外级联）")
    else:
        report("S6 detach-before-delete", "FAIL", f"gone={gone} c02={c02} c03={c03}")


def s6b_workaround_costs(tmp):
    """变通路径的代价度量：删链重建 → shot_id 漂移 + 资产丢失。"""
    path = tmp / "s6b"; path.mkdir()
    make_proj(path)
    rt = rt_for(path)
    before = "s01_c02"
    TOOL_EXECUTORS["shot.delete"](rt, "demo",
                                  json.dumps({"shot_id": "s01_c01"}), "spike", "msg")
    obs = TOOL_EXECUTORS["shot.create"](
        rt, "demo", json.dumps({"scene_id": "s01", "description": "她冲进走廊",
                                "expected_shots_revision":
                                    _shots_token(load(path).scenes)},
                               ensure_ascii=False), "spike", "msg")
    after = obs["created"][0]["shot_id"]
    report("S6b 变通（删链重建）可行但 id 漂移", "PASS" if after != before else "FAIL",
           f"{before} → {after}（若 c02 有已生成图/视频资产将随删除丢失）")


def s7_basis_stale(tmp):
    """proposal_basis 防语义 lost update：basis 指纹 vs 当前指纹。"""
    path = tmp / "s7"; path.mkdir()
    make_proj(path)
    rt = rt_for(path)
    basis = scene_slice_fingerprint(path, "s01")
    # 模拟 delegation 飞行期间 canonical 被他方改动（Main Director 自己改了 c1）
    TOOL_EXECUTORS["shot.update"](
        rt, "demo", json.dumps({
            "shot_id": "s01_c01",
            "expected_revision": _shot_token(load(path).get_shot("s01_c01")),
            "description": "雨夜灯塔外景（已被他方修改）"}, ensure_ascii=False),
        "spike", "msg")
    end = [{"shot_id": "s01_c01", "description": "雨夜灯塔外景"},
           {"shot_id": "s01_c02", "description": "她冲进走廊"},
           {"shot_id": "s01_c03", "description": "楼梯间回望"}]
    ok, probs, ops = apply_end_state(rt, "demo", path, "s01", end, basis=basis)
    assert not ok and any("basis stale" in p for p in probs), probs
    assert load(path).get_shot("s01_c01").description.endswith("他方修改）")  # 未被旧端态覆盖
    report("S7 basis stale（commit 前拒绝，无 lost update）", "PASS")


def s8_scene_deleted(tmp):
    """delegation 飞行中 scene 被 SW 删除 → commit 时 liveness 拒绝。"""
    path = tmp / "s8"; path.mkdir()
    make_proj(path)
    rt = rt_for(path)
    basis = scene_slice_fingerprint(path, "s02")
    # 模拟 SW story.delete(s02)：直接操作 canonical（spike 只模拟结果态）
    proj = load(path)
    proj.scenes = [s for s in proj.scenes if s.scene_id != "s02"]
    ProjectStore(path).save(proj)
    end = [{"description": "教室晨光"}]
    ok, probs, ops = apply_end_state(rt, "demo", path, "s02", end, basis=basis)
    assert not ok and any("liveness" in p for p in probs), probs
    report("S8 scene 已删除（commit 时 liveness 拒绝）", "PASS")


def s9_partial_adoption(tmp):
    """partial adoption：采纳端态子集 → 必须重校验接力闭包。"""
    path = tmp / "s9"; path.mkdir()
    make_proj(path)
    rt = rt_for(path)
    # 合法 partial：端态 = [c1(改), c2, NEW(锚c2)]，只采纳 [c1(改), c2]（剔除 NEW）
    end = [{"shot_id": "s01_c01", "description": "雨夜灯塔外景（改）"},
           {"shot_id": "s01_c02", "description": "她冲进走廊"},
           {"shot_id": "s01_c03", "description": "楼梯间回望"},
           {"description": "新结尾镜", "source_shot_id": "s01_c03"}]
    skipped = [end[3]]
    ok, probs, ops = apply_end_state(rt, "demo", path, "s01", end,
                                     skip_create_items=skipped)
    assert ok and len(scene_of(load(path), "s01").shots) == 3, probs
    report("S9a partial adoption（合法子集）", "PASS")
    # 非法 partial：端态里 c2 改锚到 NEW，但 NEW 被剔除 → 闭包破坏 → 拒绝
    path2 = tmp / "s9b"; path2.mkdir()
    make_proj(path2)
    rt2 = rt_for(path2)
    end2 = [{"shot_id": "s03_c01", "description": "码头全景"},
            {"description": "新插入镜"},
            {"shot_id": "s03_c02", "description": "甲板特写",
             "source_shot_id": "s03_c01"}]
    # 构造依赖 NEW 的端态：c02 改锚到新镜
    end2 = [{"shot_id": "s03_c01", "description": "码头全景"},
            {"shot_id": "s03_c03", "description": "新中段（承担接力）"},
            {"shot_id": "s03_c02", "description": "甲板特写",
             "source_shot_id": "s03_c03"}]
    # partial 剔除 c03：c02 的锚不在集合 → 闭包破坏
    ok2, probs2, _ = apply_end_state(rt2, "demo", path2, "s03", end2,
                                     skip_create_items=[end2[1]])
    assert not ok2 and any("不在目标镜头集" in p for p in probs2), probs2
    report("S9b partial adoption（闭包破坏被拒）", "PASS")


def s10_create_relay_in_new_shots(tmp):
    """新镜头之间的接力链在 create_many spec 中是否被接受（端态接力图的新边）。"""
    path = tmp / "s10"; path.mkdir()
    make_proj(path)
    rt = rt_for(path)
    end = [{"description": "起手"},
           {"description": "承接", "source_shot_id": "__NEW_0__"},
           {"description": "收束"}]
    # diff engine 必须把"锚到同批新镜头"翻译为 create 后的真实 id + update 挂接
    specs = [{"description": "起手"},
             {"description": "承接"},   # create 时不带锚
             {"description": "收束"}]
    obs = TOOL_EXECUTORS["shot.create_many"](
        rt, "demo", json.dumps({"scene_id": "s02", "shots": specs,
                                "expected_shots_revision":
                                    _shots_token(load(path).scenes)},
                               ensure_ascii=False), "spike", "msg")
    created = [c["shot_id"] for c in obs.get("created", [])]
    # 尝试挂接：c_new2 锚 c_new1 —— 同样撞 source_shot_id 白名单
    obs2 = TOOL_EXECUTORS["shot.update"](
        rt, "demo", json.dumps({"shot_id": created[1],
                                "expected_revision":
                                    _shot_token(load(path).get_shot(created[1])),
                                "source_shot_id": created[0]}, ensure_ascii=False),
        "spike", "msg")
    linked = obs2.get("ok") and "source_shot_id" in (obs2.get("changed") or [])
    if linked:
        got = load(path).get_shot(created[1]).source_shot_id
        report("S10 新镜头间接力挂接", "PASS",
               f"created={created}；{created[1]} 接力 {got}（先建后锚模式验证）")
    else:
        report("S10 新镜头间接力挂接", "BLOCKER",
               f"created={created}；update 挂接 changed={obs2.get('changed')}")


def s11_delete_mid_chain_relink(tmp):
    """Part 2 §6.2 拓扑序验证：删接力链中段、保留并重接下游。
    canonical s01 = [c1, c2(锚c1), c3(锚c2)]；端态 = [c1, c3(重接 c1)]（删 c2）。
    合法端态（图完备：c3 的锚 c1 在集合且紧邻前驱）。若 delete 先行，
    c3 在 canonical 的接力边（锚 c2）会触发级联误删 → 违反"保留 c3"。"""
    path = tmp / "s11"; path.mkdir()
    make_proj(path)
    rt = rt_for(path)
    end = [{"shot_id": "s01_c01", "description": "雨夜灯塔外景"},
           {"shot_id": "s01_c03", "description": "楼梯间回望",
            "source_shot_id": "s01_c01"}]
    ok, probs, ops = apply_end_state(rt, "demo", path, "s01", end)
    proj = load(path)
    c3 = proj.get_shot("s01_c03")
    if ok and c3 is not None and c3.source_shot_id == "s01_c01":
        final = [sh.shot_id for sh in scene_of(proj, "s01").shots]
        assert final == ["s01_c01", "s01_c03"], final
        report("S11 删接力链中段+重接下游", "PASS",
               f"ops={len(ops)}；c3 重接 c1，级联零误删")
    else:
        report("S11 删接力链中段+重接下游", "FAIL",
               f"ok={ok} probs={probs} c3={c3 and c3.source_shot_id}")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="subagent_diff_spike_"))
    try:
        for fn in (s1_create, s2_update, s3_delete, s4_reorder, s4b_reorder_invalid,
                   s5_mixed, s6_detach_before_delete, s6b_workaround_costs,
                   s7_basis_stale, s8_scene_deleted, s9_partial_adoption,
                   s10_create_relay_in_new_shots, s11_delete_mid_chain_relink):
            try:
                fn(tmp)
            except Exception as e:
                report(fn.__name__, "ERROR", f"{type(e).__name__}: {e}")
                traceback.print_exc()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    n_pass = sum(1 for _, s, _ in RESULTS if s == "PASS")
    n_block = sum(1 for _, s, _ in RESULTS if s == "BLOCKER")
    n_err = sum(1 for _, s, _ in RESULTS if s == "ERROR")
    print(f"\n===== 汇总：{n_pass} PASS / {n_block} BLOCKER / {n_err} ERROR =====")
    return 1 if (n_block or n_err) else 0


if __name__ == "__main__":
    sys.exit(main())
