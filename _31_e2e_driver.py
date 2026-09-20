# -*- coding: utf-8 -*-
"""31.md Phase E — 真机 E2E driver（Agnes only，禁 SenseNova/fallback）。

子命令：
  fixture   构建最小 fixture 项目 Golden_31_Integration_Fixture（真实跨域 canonical，
            参考资产=Golden 真实 approved 图片字节）+ 31.md §14 Cases A/B/C 真实生成
            （A 静态人物 / B 人物+道具 / C pick_up 首尾帧+keyframes 视频）
  mitigate  Golden_Full_Production_V1 紧景别代表镜缓解变体矩阵：
            s16_c02(CU)/s17_c03(CU)/s17_c02(ECU) × {auto, crop, crop+pop} × 2
            s09_c01(EWS 人群案例) × {auto, auto+pop} × 2
  review    对 _31_integration_e2e/ 下全部候选跑 review_shot_core（真机 Vision）
            + 生成 GALLERY.html + REVIEW.json

纪律（D2 运维教训）：
  - 串行 + PROVIDER_INTERVAL=75s（429 风暴自愈口径）；失败等待后整 handler 重试一次
  - 候选文件逐批拷贝到证据目录（handler 候选文件名跨批复用会覆盖）
  - 429/失败不伪造 PASS；所有结果落 _31_integration_e2e/*.json

构建顺序敏感（fingerprint 输入）：
  profile 更新与 prop 真实字节必须在 _build_scene（SIV 审批）**之前**——
  SIV basis 含可见 prop approved 参考的内容 sha256 与 profile 维度。
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

GOLDEN = ROOT / "projects" / "Golden_Full_Production_V1"
FIXTURE_NAME = "Golden_31_Integration_Fixture"
FIXTURE = ROOT / "projects" / FIXTURE_NAME
BUILD_TMP = ROOT / "_31_fixture_build"
E2E = ROOT / "_31_integration_e2e"
PROVIDER_INTERVAL = 75.0
RETRY_WAIT = 90.0


def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        import os
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def setup_clients():
    import deps
    from src.pipeline.controller import PipelineController
    ctrl = PipelineController(str(ROOT / "config" / "config.yaml"))
    deps.agnes_client = ctrl.agnes_client
    return ctrl.agnes_client


def bind_pm(pdir: Path):
    import deps
    deps.project_manager = SimpleNamespace(get_project_dir=lambda name: pdir)


def _load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _save_json(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


async def _emit(sink: list, stage, msg, pct=None):
    sink.append({"stage": stage, "msg": msg})
    print(f"    [{stage}] {msg}", flush=True)


def _manifest_append(entries: list):
    m = E2E / "MANIFEST.json"
    cur = _load_json(m) if m.exists() else []
    cur.extend(entries)
    _save_json(m, cur)
    print(f"  manifest +{len(entries)} (total {len(cur)})", flush=True)


def _collect_batch(pdir: Path, shot_id: str, slot: str, group: str, variant: str,
                   params: dict, sink: list) -> list:
    """取最新批次候选，拷贝到证据目录（防跨批覆盖），返回 manifest 条目。"""
    from src.core.store import ProjectStore
    shot = ProjectStore(pdir).load().get_shot(shot_id)
    batches = shot.first_frame_batches if slot == "first" else shot.last_frame_batches
    batch = batches[-1]
    out_dir = E2E / group
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for i, cand in enumerate(batch.candidates or []):
        tag = f"{shot_id}_{variant}_c{i + 1}"
        dest = out_dir / f"{tag}.png"
        if cand.local and Path(cand.local).exists():
            shutil.copyfile(cand.local, dest)
        entries.append({
            "group": group, "shot_id": shot_id, "variant": variant,
            "cand_index": i + 1, "tag": tag, "frame": slot,
            "file": str(dest) if dest.exists() else None,
            "provider_url": cand.url,
            "candidate_id": cand.candidate_id, "batch_id": batch.batch_id,
            "params": params,
            "generation_basis": {k: v for k, v in (batch.generation_basis or {}).items()
                                 if k not in ("dependency_fingerprints",)},
            "prompt": batch.prompt_snapshot,
            "emit_log": sink[-6:],
        })
    return entries


async def _run_handler(handler, pdir: Path, shot_id: str, params: dict, label: str):
    """串行真机调用 + 失败重试一次（429 自愈口径）。返回 emit 日志。"""
    sink: list = []
    job = SimpleNamespace(project=pdir.name, shot_id=shot_id,
                          params=dict(params), kind=label)
    print(f"  >> {label} {shot_id} {params}", flush=True)
    for attempt in (1, 2):
        try:
            await handler(job, lambda *a, **k: _emit(sink, *a, **k))
            return sink
        except Exception as e:   # noqa: BLE001
            print(f"  !! attempt {attempt} failed: {type(e).__name__}: {str(e)[:300]}",
                  flush=True)
            if attempt == 1:
                await asyncio.sleep(RETRY_WAIT)
            else:
                raise


async def _gap():
    print(f"  .. cool down {PROVIDER_INTERVAL:.0f}s", flush=True)
    await asyncio.sleep(PROVIDER_INTERVAL)


# ============ fixture 项目构建（真实跨域 canonical + 真实参考字节）============

def _golden_asset_files() -> dict:
    gres = _load_json(GOLDEN / "output" / "resources.json")
    gsys = _load_json(GOLDEN / "output" / "scene_system.json")
    gbinds = _load_json(GOLDEN / "output" / "shot_production_bindings.json")
    out = {}
    for lk in gres["looks"].values():
        if lk.get("approval_status") == "approved" and lk.get("approved_reference_asset_ids"):
            a = gres["reference_assets"].get(lk["approved_reference_asset_ids"][0], {})
            if a.get("local"):
                out["look"] = GOLDEN / a["local"]
                out["look_char"] = lk.get("character_name")
                break
    b = gbinds["bindings"].get("s16_c02") or {}
    siv_id = (b.get("scene_binding") or {}).get("pinned_scene_instance_view_id")
    sciv = gsys.get("scene_instance_views", {}).get(siv_id) if siv_id else None
    if sciv is None:
        for v in gsys.get("scene_instance_views", {}).values():
            if v.get("approval_status") == "approved" and v.get("reference_asset_id"):
                sciv = v
                break
    if sciv:
        sa = gres["reference_assets"].get(sciv.get("reference_asset_id"), {})
        if sa.get("local"):
            out["siv"] = GOLDEN / sa["local"]
    for pr in gres["props"].values():
        if pr.get("approval_status") == "approved" and pr.get("approved_reference_asset_ids"):
            a = gres["reference_assets"].get(pr["approved_reference_asset_ids"][0], {})
            if a.get("local"):
                out["prop"] = GOLDEN / a["local"]
                out["prop_name"] = pr.get("name")
                break
    return out


def _golden_profile_values() -> dict:
    gp = _load_json(GOLDEN / "output" / "project_profile.json")
    return {k: gp[k] for k in ("render_family", "art_direction", "style_tags",
                               "negative_style_constraints") if gp.get(k)}


def build_fixture_project() -> dict:
    """跨域真实骨架 + 真实参考字节 + profile + 三镜 binding。幂等（重建）。
    顺序敏感：profile / prop 真实字节在 _build_scene 之前（SIV 指纹输入）。"""
    from src.core.models import Project, Shot
    from src.core.store import ProjectStore
    from src.resources.store import ResourceStore
    from src.shot_binding import ShotBindingService
    from src.visual_profile import VisualProfileService
    from test_shot_binding_s3b1 import LOOK, PROP, make_pdir
    from test_shot_binding_s3b2 import _bind, _build_scene

    if BUILD_TMP.exists():
        shutil.rmtree(BUILD_TMP)
    BUILD_TMP.mkdir(parents=True)
    pdir = make_pdir(BUILD_TMP)               # → BUILD_TMP/_31_fixture_build
    shutil.rmtree(FIXTURE, ignore_errors=True)
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(pdir), str(FIXTURE))
    shutil.rmtree(BUILD_TMP)

    store = ProjectStore(FIXTURE)
    proj = store.load()
    proj.name = FIXTURE_NAME
    proj.scenes[0].shots.append(Shot(shot_id="s01_c03", scene_id="s01", index=3,
                                     characters=["Alice"]))
    store.save(proj)

    def _recast(s):
        s.characters = ["Alice"]
    store.update_shot("s01_c02", _recast)

    def _kf(s):
        s.link_mode = "keyframes"
        s.video.num_frames = 73
    store.update_shot("s01_c03", _kf)

    # ---- profile（Golden 同值；必须在 SIV 审批前）----
    VisualProfileService(FIXTURE, FIXTURE_NAME).update(
        expected_revision="", **_golden_profile_values())

    # ---- 真实参考字节（look / prop；prop 进 SIV 指纹 → 必须在 _build_scene 前）----
    ga = _golden_asset_files()
    assets_dir = FIXTURE / "output" / "assets_31"
    assets_dir.mkdir(parents=True, exist_ok=True)
    look_png = assets_dir / "look_hero.png"
    prop_png = assets_dir / "prop_hero.png"
    shutil.copyfile(ga["look"], look_png)
    shutil.copyfile(ga["prop"], prop_png)
    rdoc = ResourceStore(FIXTURE, FIXTURE_NAME).load()
    for aid, f in (("ref_alice000001", look_png), ("ref_sword000001", prop_png)):
        rdoc.reference_assets[aid].local = \
            str(f.relative_to(FIXTURE)).replace("\\", "/")
        rdoc.reference_assets[aid].url = None
    ResourceStore(FIXTURE, FIXTURE_NAME).save(rdoc)

    # ---- 场景链（真实 store 写入；SIV approved）----
    (FIXTURE / "output" / "scene_system.json").unlink()
    ids = asyncio.run(_build_scene(FIXTURE))
    # SIV 自身资产文件内容 → 真实场景字节（输出侧，非指纹输入，审批后覆盖安全）
    if ga.get("siv"):
        rdoc = ResourceStore(FIXTURE, FIXTURE_NAME).load()
        from src.scene_system.store import SceneSystemStore
        sdoc = SceneSystemStore(FIXTURE, FIXTURE_NAME).load()
        sciv = sdoc.scene_instance_views[ids["sciv_a"]]
        siv_asset = rdoc.reference_assets[sciv.reference_asset_id]
        if siv_asset.local:
            siv_file = FIXTURE / siv_asset.local
            if siv_file.exists():
                shutil.copyfile(ga["siv"], siv_file)
                print(f"  siv asset ← {ga['siv'].name}", flush=True)

    # ---- 三镜 binding（静态 / 人物+道具 / pick_up）----
    svc = ShotBindingService(FIXTURE)
    _bind(svc, shot_id="s01_c01", inst=ids["inst"], view=ids["view_a"])
    _bind(svc, shot_id="s01_c02", inst=ids["inst"], view=ids["view_a"],
          props=[{"prop_id": PROP, "relation": "held", "anchor": "right_hand"}])
    _bind(svc, shot_id="s01_c03", inst=ids["inst"], view=ids["view_a"],
          action={"kind": "pick_up", "subject_character": "Alice",
                  "prop_id": PROP, "start_state": "right hand empty",
                  "end_state": "right hand holding the item"})
    for sid in ("s01_c01", "s01_c02", "s01_c03"):
        v = svc.read_validity(sid)
        assert v["readiness"] == "READY", (sid, v)
    print(f"  fixture built at {FIXTURE} (look={ga['look'].name}, "
          f"prop={ga['prop'].name}, siv={'real' if ga.get('siv') else 'placeholder'})",
          flush=True)
    return ids


async def run_fixture_cases():
    from routers.shots import (_handler_first_frame, _handler_last_frame,
                               _handler_video, choose_first_frame_business)
    from src.core.store import ProjectStore
    bind_pm(FIXTURE)
    manifest = []

    # Case A — 静态人物
    sink = await _run_handler(_handler_first_frame, FIXTURE, "s01_c01",
                              {"variants": 2, "origin_run_id": "e31_caseA"},
                              "first_frame")
    manifest += _collect_batch(FIXTURE, "s01_c01", "first", "fixture",
                               "caseA_static", {"variants": 2}, sink)
    _manifest_append(manifest)
    manifest = []
    await _gap()

    # Case B — 人物+道具
    sink = await _run_handler(_handler_first_frame, FIXTURE, "s01_c02",
                              {"variants": 2, "origin_run_id": "e31_caseB"},
                              "first_frame")
    manifest += _collect_batch(FIXTURE, "s01_c02", "first", "fixture",
                               "caseB_prop", {"variants": 2}, sink)
    _manifest_append(manifest)
    manifest = []
    await _gap()

    # Case C — pick_up：首帧（不得持物）→ 选定 → 尾帧（应持物）→ keyframes 视频
    sink = await _run_handler(_handler_first_frame, FIXTURE, "s01_c03",
                              {"variants": 2, "origin_run_id": "e31_caseC_ff"},
                              "first_frame")
    manifest += _collect_batch(FIXTURE, "s01_c03", "first", "fixture",
                               "caseC_pickup_first", {"variants": 2}, sink)
    _manifest_append(manifest)
    manifest = []
    store = ProjectStore(FIXTURE)
    shot = store.load().get_shot("s01_c03")
    first_cand = shot.first_frame_batches[-1].candidates[0].candidate_id
    choose_first_frame_business(store, "s01_c03", first_cand)
    print(f"  selected first frame {first_cand}", flush=True)
    await _gap()

    sink = await _run_handler(_handler_last_frame, FIXTURE, "s01_c03",
                              {"origin_run_id": "e31_caseC_lf"}, "last_frame")
    manifest += _collect_batch(FIXTURE, "s01_c03", "last", "fixture",
                               "caseC_pickup_last", {}, sink)
    _manifest_append(manifest)
    manifest = []
    await _gap()

    sink = await _run_handler(_handler_video, FIXTURE, "s01_c03",
                              {"origin_run_id": "e31_caseC_vid"}, "video")
    shot = ProjectStore(FIXTURE).load().get_shot("s01_c03")
    vv = shot.video_versions[-1]
    dest = E2E / "fixture" / "caseC_video.mp4"
    if vv.local and Path(vv.local).exists():
        shutil.copyfile(vv.local, dest)
    _manifest_append([{
        "group": "fixture", "shot_id": "s01_c03", "variant": "caseC_video",
        "frame": "video", "file": str(dest) if dest.exists() else None,
        "provider_url": vv.url, "generation_basis": dict(vv.generation_basis or {}),
        "prompt": vv.prompt_snapshot, "params": {},
    }])


# ============ 缓解变体矩阵（Golden 真实镜）============

MITIGATE_PLAN = {
    "s16_c02": [("auto", {}), ("crop", {"human_reference_mode": "crop"}),
                ("crop_pop", {"human_reference_mode": "crop",
                              "population_constraint": True})],
    "s17_c03": [("auto", {}), ("crop", {"human_reference_mode": "crop"}),
                ("crop_pop", {"human_reference_mode": "crop",
                              "population_constraint": True})],
    "s17_c02": [("auto", {}), ("crop", {"human_reference_mode": "crop"}),
                ("crop_pop", {"human_reference_mode": "crop",
                              "population_constraint": True})],
    "s09_c01": [("auto", {}), ("auto_pop", {"population_constraint": True})],
}


async def run_mitigate():
    from routers.shots import _handler_first_frame
    bind_pm(GOLDEN)
    for shot_id, variants in MITIGATE_PLAN.items():
        for variant, extra in variants:
            params = {"variants": 2, "origin_run_id": f"e31_{shot_id}_{variant}",
                      **extra}
            try:
                sink = await _run_handler(_handler_first_frame, GOLDEN, shot_id,
                                          params, "first_frame")
            except Exception as e:   # noqa: BLE001 —— 单镜失败不拖垮矩阵（诚实记录）
                _manifest_append([{"group": "mitigate", "shot_id": shot_id,
                                   "variant": variant, "error": str(e)[:400],
                                   "params": params}])
                await _gap()
                continue
            entries = _collect_batch(GOLDEN, shot_id, "first", "mitigate",
                                     variant, params, sink)
            _manifest_append(entries)
            await _gap()


# ============ Review（真机 Vision）+ Gallery ============

def review_line(rv) -> str:
    if rv is None:
        return "<i>（未审查）</i>"
    if "error" in rv:
        return f"<i>{rv['error']}</i>"
    d = rv["derived"]
    r = rv["review"]
    hard = "、".join(d["hard_fails"]) or "—"
    people = r["observed"].get("people_count")
    size_obs = r["observed"].get("shot_size_observed")
    return (f"<b>{d['overall']}</b> | 人形={people} | 实际景别={size_obs}<br>"
            f"hard: {hard}")


def run_review():
    from src.agents.shot_review import review_shot_core
    from src.core.store import ProjectStore

    client = setup_clients()
    manifest = _load_json(E2E / "MANIFEST.json")
    review_path = E2E / "REVIEW.json"
    results = _load_json(review_path) if review_path.exists() else {}
    projects = {}

    async def run_all():
        sem = asyncio.Semaphore(2)
        for entry in manifest:
            if entry.get("frame") == "video" or entry.get("error"):
                continue
            key = (f"{entry['group']}||{entry['shot_id']}||{entry['variant']}"
                   f"||c{entry.get('cand_index', 0)}")
            if key in results and isinstance(results[key], dict) \
                    and "error" not in results[key]:
                continue
            if not entry.get("file") or not Path(entry["file"]).exists():
                continue
            pdir = FIXTURE if entry["group"] == "fixture" else GOLDEN
            if pdir.name not in projects:
                projects[pdir.name] = ProjectStore(pdir).load()
            proj = projects[pdir.name]
            shot = proj.get_shot(entry["shot_id"])
            async with sem:
                try:
                    rv = await review_shot_core(client, project_dir=pdir, proj=proj,
                                                shot=shot, frame=entry.get("frame", "first"),
                                                image_local=entry["file"], persist=False)
                except Exception as e:   # noqa: BLE001
                    rv = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
            results[key] = rv
            overall = (rv or {}).get("derived", {}).get("overall",
                                                        (rv or {}).get("error", "skip"))
            print(f"  review {key}: {overall}", flush=True)
            _save_json(review_path, results)
    asyncio.run(run_all())

    def esc(s):
        return (str(s or "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    rows = {}
    for entry in manifest:
        rows.setdefault((entry["group"], entry["shot_id"]), []).append(entry)
    html = ["<html><head><meta charset='utf-8'><style>",
            "body{font-family:sans-serif;background:#111;color:#ddd}",
            "table{border-collapse:collapse}td{border:1px solid #444;padding:6px;"
            "vertical-align:top}",
            "img{width:320px}video{width:320px}h3{margin:10px 0 4px}",
            ".legend{background:#222;padding:10px;border-radius:6px;font-size:14px;"
            "line-height:1.6;max-width:1100px}",
            "button.sel{padding:4px 10px;border:0;border-radius:4px;cursor:pointer;"
            "font-size:14px}",
            "img.pickable{cursor:zoom-in}",
            "input.note{background:#1c1c1c;color:#ddd;border:1px solid #444;"
            "padding:4px 8px;margin:2px 0 6px}",
            "h3{margin:14px 0 2px}",
            "</style></head><body><h1>31.md Integration E2E Gallery</h1>",
            "<div class='legend'>"
            "<b>变体图例</b>：auto=现状默认（紧景别人形参考全退/宽景全发）· "
            "crop=look hero 头肩/面部裁剪参考（v1 旧 bbox）· "
            "<b>crop_v2=修复 bbox 后的裁剪参考（人物紧框）</b> · "
            "crop_pop/auto_pop=裁剪/默认 + 人数排除子句<br>"
            "<b>评审行</b>：overall | 人形=实到人数 | 实际景别 | hard fails"
            "（自动 Vision 初筛口径——<b>最终以人评为准</b>）。"
            "fixture 组镜头无 creative 描述，人群幻觉属预期，不作为质量证据。</div>"]
    for (group, shot_id), entries in sorted(rows.items()):
        html.append(f"<h3>{esc(group)} / {esc(shot_id)}</h3>")
        html.append(f"<input class='note' data-note-key='{esc(group)}/{esc(shot_id)}' "
                    f"placeholder='本镜备注（自动保存）' style='width:600px'>")
        html.append("<table><tr>")
        for e in entries:
            key = (f"{e['group']}||{e['shot_id']}||{e['variant']}"
                   f"||c{e.get('cand_index', 0)}")
            rv = results.get(key)
            # 相对路径（Gallery 与图片同目录树——HTTP/file 双协议可用）
            rel = Path(e["file"]).relative_to(E2E).as_posix() if e.get("file") else None
            if e.get("frame") == "video":
                media = (f"<video src='{rel}' controls></video>"
                         if rel else "<i>missing</i>")
            else:
                media = (f"<img src='{rel}' class='pickable'>" if rel
                         else f"<i>{esc(e.get('error', 'missing'))}</i>")
            gb = e.get("generation_basis") or {}
            meta = (f"scene={esc(gb.get('scene_reference_mode'))} "
                    f"human={esc(gb.get('human_reference_mode'))} "
                    f"pop={esc(gb.get('population_constraint'))}")
            sel_btn = (f"<button class='sel' data-key='{esc(key)}'>未评</button>"
                       if e.get("frame") != "video" else "")
            html.append(f"<td>{media}<br>{esc(e['variant'])} c{e.get('cand_index', '')}"
                        f"<br><small>{meta}</small><br>{review_line(rv)}"
                        f"<br>{sel_btn}</td>")
        html.append("</tr>")
    html.append("</table>")
    html.append("""
<script>
const STATES = ["", "ok", "best", "bad"];
const LABELS = {"": "未评", "ok": "✅ 可用", "best": "⭐ 首选", "bad": "❌ 不可用"};
const COLORS = {"": "#555", "ok": "#2a7", "best": "#fa0", "bad": "#c33"};
function paint(btn, st){ btn.textContent = LABELS[st];
  btn.style.background = COLORS[st]; btn.style.color = st ? "#111" : "#ddd"; }
function post(body){ fetch('/api/feedback', {method:'POST',
  headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)}); }
document.querySelectorAll('button.sel').forEach(btn => {
  btn.onclick = () => {
    const cur = btn.dataset.state || "";
    const next = STATES[(STATES.indexOf(cur) + 1) % STATES.length];
    btn.dataset.state = next; paint(btn, next);
    post({key: btn.dataset.key, state: next, at: Date.now()});
  };
});
document.querySelectorAll('input.note').forEach(inp => {
  inp.onchange = () => post({note_key: inp.dataset.noteKey, note: inp.value, at: Date.now()});
});
fetch('/api/feedback').then(r => r.json()).then(fb => {
  const sel = (fb || {}).selections || {};
  document.querySelectorAll('button.sel').forEach(btn => {
    const s = sel[btn.dataset.key];
    if (s && s.state) { btn.dataset.state = s.state; paint(btn, s.state); }
  });
  const notes = (fb || {}).notes || {};
  document.querySelectorAll('input.note').forEach(inp => {
    if (notes[inp.dataset.noteKey]) inp.value = notes[inp.dataset.noteKey];
  });
});
</script>
</body></html>""")
    (E2E / "GALLERY.html").write_text("\n".join(html), encoding="utf-8")
    print(f"gallery → {E2E / 'GALLERY.html'}", flush=True)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    load_env()
    t0 = time.time()
    if cmd == "fixture":
        setup_clients()
        build_fixture_project()
        asyncio.run(run_fixture_cases())
    elif cmd == "mitigate":
        setup_clients()
        asyncio.run(run_mitigate())
    elif cmd == "review":
        run_review()
    else:
        raise SystemExit(__doc__)
    print(f"done {cmd} in {(time.time() - t0) / 60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
