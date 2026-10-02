# -*- coding: utf-8 -*-
"""Phase 45.1b — 音频资产身份档一次性确定性登记。

把 output/audio_imports/ 现有测试垫乐的身份（origin/license/生成批次/实测哈希）
登记进 assets_meta.json，使 assembly.audio_assets_read 能透出身份。**只登记既有
本地合成测试素材，不虚称授权音乐**；正式授权音乐由用户提供后另行登记。
身份档是只读元数据（非 canonical）：丢失/损坏只降级为 unregistered，不影响渲染。
"""
import hashlib
import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
LIB = BASE / "projects" / "雨灯归航" / "output" / "audio_imports"
PARAMS = BASE / "_44c" / "music_scenes_params.json"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    params = json.loads(PARAMS.read_text(encoding="utf-8")) if PARAMS.is_file() else {}
    assets: dict = {}

    for key in ("s1_riverside", "s2_workshop", "s3_tower"):
        seg = params.get(key) or {}
        src = Path(seg.get("file") or "")
        p = LIB / "music" / src.name if src.name else None
        if p is None or not p.is_file():
            print(f"[skip] {key}: wav 不存在")
            continue
        rel = "audio_imports/music/" + p.name
        assets[rel] = {
            "origin": params.get("origin", "locally_composed_synthesized_test_asset"),
            "license": params.get("license", "self_composed_no_third_party_rights"),
            "created_by": "phase44c batch script _44c9_music_scenes.py",
            "registered_at": time.time(),
            "sha256": _sha(p),
            "params_sha256_declared": seg.get("sha256"),
            "duration_s_declared": seg.get("duration_s"),
            "note": f"测试垫乐（{seg.get('structure', '')}）；正式音乐待用户提供/授权",
        }
        print(f"[ok] {rel} sha={assets[rel]['sha256'][:12]}")

    # 早期整曲垫乐（44C 历史 v1/v2，当前 plan 未引用；保留身份便于回看）
    for name, note in (("bed_44c_v1.wav", "44C 首轮正弦环境垫（已被 v2 替换）"),
                       ("bgm_44c_v2.wav", "44C 程序化作曲 v2 单段（已被分场景三段替换）")):
        p = LIB / "music" / name
        if p.is_file():
            rel = "audio_imports/music/" + name
            assets[rel] = {
                "origin": "locally_composed_synthesized_test_asset",
                "license": "self_composed_no_third_party_rights",
                "created_by": "phase44c batch script",
                "registered_at": time.time(),
                "sha256": _sha(p),
                "note": f"{note}；当前正式 plan 未引用",
            }
            print(f"[ok] {rel} sha={assets[rel]['sha256'][:12]}")

    out = LIB / "assets_meta.json"
    out.write_text(json.dumps({"schema": "audio_assets_meta_v1", "assets": assets,
                               "updated_at": time.time()},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"written: {out} ({len(assets)} assets)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
