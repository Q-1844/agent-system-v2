# -*- coding: utf-8 -*-
"""Phase D2 (30.md §3) — Shot Image Generation reference behavior 全量审计。

对 52 镜逐镜只读编译 ShotReferencePack（validity gate → compile，不调 provider、
不写任何 canonical），输出每镜：
- references 按 source_type 计数与发送顺序（前 6 = 真实 image_refs 预演）
- 场景参考是否存在 + 位置
- 人形参考计数（每角色 look + legacy identity views）
- first-frame temporal 过滤后的 eligible 数 / cap(6) 是否触发 / 触发后保留下什么
- 按 shot_size 分组统计（CU/ECU 是否收到完整场景图）
输出 _phase_d2_audit/pack_ref_inventory.json + PACK_REF_AUDIT.md。
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJ = ROOT / "projects" / "Golden_Full_Production_V1"
OUT = ROOT / "_phase_d2_audit"


def main() -> int:
    t0 = time.time()
    sys.path.insert(0, str(ROOT))
    from src.core.store import ProjectStore
    from src.shot_binding import ShotBindingService
    from src.generation.image_adapter import (
        filter_first_frame_references, _cap_provider_input_images)

    OUT.mkdir(parents=True, exist_ok=True)
    project = ProjectStore(PROJ).load()
    svc = ShotBindingService(PROJ, PROJ.name)

    rows = []
    for shot in sorted(project.all_shots(), key=lambda s: (s.scene_id, s.index)):
        sid = shot.shot_id
        row = {"shot_id": sid, "scene_id": shot.scene_id, "shot_size": shot.shot_size,
               "camera_angle": shot.camera_angle, "characters": list(shot.characters or []),
               "link_mode": shot.link_mode}
        validity = svc.read_validity(sid)
        row["readiness"] = validity["readiness"]
        if validity["readiness"] != "READY":
            row["skip_reason"] = validity["readiness"]
            rows.append(row)
            continue
        try:
            compiled = svc.compile_reference_pack(sid)
        except Exception as e:  # PackBlockedError 等 —— 结构化记录，不中断
            row["skip_reason"] = f"compile_error: {e}"
            rows.append(row)
            continue
        if compiled.get("status") != "COMPILED":
            row["skip_reason"] = "compile: " + ",".join(
                sorted({str(i.get("reason")) for i in compiled.get("issues") or []}))
            rows.append(row)
            continue
        pack = compiled["pack"]
        refs = pack.get("references") or []

        type_seq = [r.get("source_type") for r in refs]
        row["ref_sequence"] = type_seq
        row["ref_counts"] = dict(Counter(type_seq))
        row["total_refs"] = len(refs)
        row["scene_ref"] = {
            "present": "scene_instance_view" in type_seq,
            "position": type_seq.index("scene_instance_view") if "scene_instance_view" in type_seq else None,
            "priority": next((r.get("priority") for r in refs
                              if r.get("source_type") == "scene_instance_view"), None),
        }
        # 人形参考（character_look + character_identity）
        human = [r for r in refs if r.get("source_type") in ("character_look", "character_identity")]
        per_char = {}
        for r in human:
            per_char.setdefault(r.get("subject_character") or "?", []).append(
                f"{r.get('source_type')}:{r.get('semantic_note') or ''}")
        row["human_refs_per_character"] = {k: len(v) for k, v in per_char.items()}
        row["human_refs_total"] = len(human)
        row["human_detail"] = per_char

        # first-frame 演练：temporal 过滤 → cap
        eligible, excluded = filter_first_frame_references(refs)
        row["first_eligible"] = len(eligible)
        row["first_excluded"] = len(excluded)
        dropped = []
        kept = _cap_provider_input_images(eligible, reserved_slots=0, dropped=dropped)
        row["cap_fired"] = bool(dropped)
        row["first_sent_sequence"] = [r.get("source_type") for r in kept]
        if dropped:
            row["cap_dropped"] = [d.get("source_id") for d in dropped]
        # 演练请求里人形参考最终几张
        row["first_sent_human"] = sum(
            1 for r in kept if r.get("source_type") in ("character_look", "character_identity"))
        rows.append(row)

    (OUT / "pack_ref_inventory.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 汇总 ----
    ok = [r for r in rows if r.get("readiness") == "READY" and "ref_sequence" in r]
    by_size = {}
    for r in ok:
        by_size.setdefault(r["shot_size"], []).append(r)
    lines = ["# Phase D2 §3 — Pack reference 全量审计（52 镜，read-only）", ""]
    lines.append(f"- READY 且编译成功：{len(ok)} / {len(rows)}")
    lines.append(f"- 场景参考存在：{sum(1 for r in ok if r['scene_ref']['present'])} / {len(ok)}")
    lines.append(f"- 场景参考位置=refs[0]：{sum(1 for r in ok if r['scene_ref'].get('position') == 0)}")
    lines.append(f"- cap(6) 触发：{sum(1 for r in ok if r['cap_fired'])}")
    lines.append(f"- first 实际发送含 ≥2 张人形：{sum(1 for r in ok if r['first_sent_human'] >= 2)}")
    lines.append(f"- first 实际发送含 ≥4 张人形：{sum(1 for r in ok if r['first_sent_human'] >= 4)}")
    lines.append("")
    lines.append("## 按 shot_size 分组（首帧实际发送序列 = temporal 过滤 + cap 后）")
    lines.append("")
    for size, group in sorted(by_size.items()):
        lines.append(f"### {size}（{len(group)} 镜）")
        lines.append("")
        lines.append("| shot | refs(pack) | scene@pos | first 发送序列 | 人形(发送) |")
        lines.append("|---|---|---|---|---|")
        for r in group:
            seq = "→".join(s.replace("scene_instance_view", "SCENE").replace(
                "character_look", "LOOK").replace("character_identity", "IDENT")
                .replace("prop", "PROP").replace("composition", "COMP")
                for s in r["first_sent_sequence"])
            lines.append(f"| {r['shot_id']} | {r['total_refs']} | "
                         f"{r['scene_ref'].get('position') if r['scene_ref']['present'] else '-'} "
                         f"| {seq} | {r['first_sent_human']} |")
        lines.append("")
    (OUT / "PACK_REF_AUDIT.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"audited={len(rows)} ready_compiled={len(ok)} "
          f"scene_ref={sum(1 for r in ok if r['scene_ref']['present'])} "
          f"cap_fired={sum(1 for r in ok if r['cap_fired'])} "
          f"human>=4={sum(1 for r in ok if r['first_sent_human'] >= 4)} elapsed={time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
