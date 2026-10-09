# -*- coding: utf-8 -*-
"""Phase50 C 比较包导出（PHASE50_C §3）：COMMON_INTENT + LOCKED_STORYBOARD。

- COMMON_INTENT：片名/故事/两人身份职能/船与怪物定义/关键动作与结果/写实风格/音频与成片目标。
  无逐镜技术答案（供豆包+Grok 原生方案对照）。
- LOCKED_STORYBOARD：冻结分镜（每镜功能/起止状态/必须保留事件/预计可见人物/机位关系/
  源与保留时长/合法素材路径及哈希）。供共同分镜对照。

用法：python _p50_c_export.py   （在 B 批分镜冻结后运行）
"""
import hashlib
import json
import sys
import io
import urllib.request
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000"
PROJ = "P50_B"
OUT = Path("_p50_c_package")


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as resp:
        return json.load(resp)


def sha_url(url):
    try:
        data = urllib.request.urlopen(url, timeout=60).read()
        return "sha256:" + hashlib.sha256(data).hexdigest()[:16], len(data)
    except Exception as e:
        return f"error:{e}", 0


def main():
    OUT.mkdir(exist_ok=True)
    p = json.load(open(f"projects/{PROJ}/output/project.json", encoding="utf-8"))
    r = json.load(open(f"projects/{PROJ}/output/resources.json", encoding="utf-8"))

    # ---------- COMMON_INTENT ----------
    looks = {}
    for k, lk in (r.get("looks") or {}).items():
        approved = lk.get("approved_reference_asset_ids") or []
        if approved:
            ra = (r.get("reference_assets") or {}).get(approved[0]) or {}
            h, sz = sha_url(ra.get("url") or "")
            looks[lk.get("character_name")] = {
                "look_id": lk.get("look_id"),
                "label": lk.get("label"),
                "approved_reference_asset_id": approved[0],
                "reference_url": ra.get("url"),
                "reference_sha256_16": h,
                "appearance_description": (lk.get("appearance_description") or "")[:800],
            }
    story_segments = []
    for sc in p.get("scenes", []):
        story_segments.append({
            "segment_id": sc.get("scene_id"),
            "title": sc.get("title"),
            "plot_task_summary": (sc.get("summary") or "")[:600],
        })
    intent = {
        "project": PROJ,
        "title_working": "深水惊魂（系统编剧可改名）",
        "format": {"aspect_ratio": "16:9", "target_duration_s": "50-70（完整叙事）",
                   "style": "写实电影（photorealistic；幻想怪物允许，但人物/船/材质/光照须像真实拍摄+VFX 电影）"},
        "audio_policy": "无旁白、无主动 TTS、首版无人物对白；保留合格原生环境声与同步音效",
        "cast_rule": "全片只有船长与大副两名人类；其余画面不得出现人形（含背景路人/分身）",
        "characters": {
            "captain": {"function": "操舵/决策",
                        "identity": looks.get("船长", {})},
            "first_mate": {"function": "配合/执行（甲板作业/缆绳/了望）",
                           "identity": looks.get("大副", {})},
        },
        "vessel": {"function": "两人活动的唯一空间；外景与甲板同一艘",
                   "identity": looks.get("渔船", {})},
        "monster": {"definition": "克苏鲁式海怪：头部触须/鳞甲体表/巨大体量；以水下阴影+触须拍击呈现；"
                                  "不能退化为泛化章鱼；不能上岸双足行走",
                    "identity": looks.get("海怪", {})},
        "story_arc_required": ["威胁被发现", "追赶压力上升", "两人有联系的行动（互相影响）",
                               "行动带来可观察结果（不能靠怪物突然消失/无原因跳场收尾）"],
        "story_segments_as_written": story_segments,
        "usage_note": "本包为共同故事意图（原生方案对照用）。豆包/Grok 据此独立规划分镜；"
                      "不要求采用 Agnes 系统的逐镜方法。",
    }
    (OUT / "COMMON_INTENT.json").write_text(
        json.dumps(intent, ensure_ascii=False, indent=1), encoding="utf-8")
    print("COMMON_INTENT written:", len(json.dumps(intent)), "bytes")

    # ---------- LOCKED_STORYBOARD ----------
    shots = []
    for sc in p.get("scenes", []):
        for sh in sc.get("shots", []):
            ff = sh.get("first_frame") or {}
            sel = (sh.get("selected_first_frame") or "")
            url = ff.get("url") or ""
            h, _ = sha_url(url) if url else ("", 0)
            vv = sh.get("video_versions") or []
            shots.append({
                "shot_id": sh.get("shot_id"),
                "scene_id": sh.get("scene_id"),
                "beat": sc.get("title"),
                "description": (sh.get("description") or "")[:500],
                "start_state": sh.get("opening_state"),
                "end_state": sh.get("end_state"),
                "camera": {"shot_size": sh.get("shot_size"), "angle": sh.get("camera_angle"),
                           "movement": sh.get("camera_movement")},
                "characters_expected": sh.get("characters"),
                "source_duration_target_s": sh.get("target_duration_s"),
                "link_mode": sh.get("link_mode"),
                "opening_source": sh.get("opening_source"),
                "first_frame_reference": {"url": url, "sha256_16": h} if url else None,
                "video_version_selected": sh.get("final_video"),
            })
    locked = {
        "project": PROJ,
        "frozen_from": "P50_B project.json（run_b25f5b900c3b 分镜审定通过版本）",
        "rules": "源素材默认 10s（8-12s 区间）；关键事件必须完整；人数纪律=仅船长与大副；"
                 "接力镜必须使用实际保留区间最后一张真实解码帧；正常切镜不强制接力",
        "shots": shots,
    }
    (OUT / "LOCKED_STORYBOARD.json").write_text(
        json.dumps(locked, ensure_ascii=False, indent=1), encoding="utf-8")
    print("LOCKED_STORYBOARD written:", len(shots), "shots")


if __name__ == "__main__":
    main()
