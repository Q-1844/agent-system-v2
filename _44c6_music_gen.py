# -*- coding: utf-8 -*-
"""Phase 44C+ — MiniMax 音乐生成探测（用户询问"背景音乐不能生成嘛"，真机验证）。

端点：POST https://api.minimax.cn/v1/music_generation（与 TTS 同域国内站）
模型：music-3.0-free（免费档全账户可用，RPM 3）；付费 music-3.0 不可用则降级记录。
器乐模式：is_instrumental=true + prompt（风格/情绪/场景）。
响应：data.audio hex（output_format=hex）；base_resp.status_code=0 成功；
      extra_info.music_duration 实测毫秒。
产物：受管资产 output/audio_imports/music/gen_44c_v1.mp3 + sha256 + 参数/回执入档。
用法：python _44c6_music_gen.py [--model music-3.0-free] [--prompt-file F]
"""
import argparse
import binascii
import hashlib
import json
import os
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
PROJECT = "雨灯归航"

DEFAULT_PROMPT = (
    "Gentle ambient orchestral score for a short animated film. Warm soft piano "
    "melody over subtle string pad, light harp arpeggio, faint rain atmosphere "
    "at the beginning turning serene and hopeful. Slow tempo around 66 BPM, "
    "melancholic opening blossoming into warm hope, smooth seamless texture "
    "suitable as continuous background music under narration. No drums, no "
    "sudden dynamics, gentle ending that fades naturally.")

GEN_ENDPOINT = "/v1/music_generation"
DEFAULT_BASE = "https://api.minimax.cn"


def _load_env():
    env = BASE / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main() -> int:
    import httpx
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="music-3.0-free")
    ap.add_argument("--prompt-file", default=None)
    a = ap.parse_args()
    _load_env()
    key = os.environ.get("MINIMAX_API_KEY", "")
    if not key:
        print("PROBE_FAIL: MINIMAX_API_KEY 未配置")
        return 1
    prompt = (Path(a.prompt_file).read_text(encoding="utf-8")
              if a.prompt_file else DEFAULT_PROMPT)
    request = {
        "model": a.model,
        "prompt": prompt,
        "lyrics": "",
        "is_instrumental": True,
        "stream": False,
        "output_format": "hex",
        "audio_setting": {"sample_rate": 44100, "bitrate": 256000,
                          "format": "mp3"},
    }
    url = DEFAULT_BASE + GEN_ENDPOINT
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    t0 = time.monotonic()
    try:
        resp = httpx.post(url, json=request, headers=headers, timeout=300)
    except Exception as e:   # noqa: BLE001
        print(f"PROBE_FAIL: 请求异常 {type(e).__name__}: {str(e)[:200]}")
        return 1
    latency_ms = int((time.monotonic() - t0) * 1000)
    try:
        payload = resp.json()
    except Exception:
        print(f"PROBE_FAIL: HTTP {resp.status_code} 非 JSON：{resp.text[:200]}")
        return 1
    base = payload.get("base_resp") or {}
    sc = base.get("status_code")
    ev = {"model": a.model, "http_status": resp.status_code,
          "base_resp": {"status_code": sc,
                        "status_msg": str(base.get("status_msg", ""))[:200]},
          "trace_id": str(payload.get("trace_id") or ""),
          "latency_ms": latency_ms, "endpoint": url}
    if sc != 0:
        ev["result"] = "provider_error"
        (BASE / "_44c" / "music_gen_probe.json").write_text(
            json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"PROBE_PROVIDER_ERROR: {json.dumps(ev, ensure_ascii=False)}")
        return 1
    data = payload.get("data") or {}
    audio_hex = data.get("audio")
    if not audio_hex or not isinstance(audio_hex, str):
        ev["result"] = "no_audio"
        ev["data_keys"] = sorted(k for k in data.keys() if k != "audio")
        (BASE / "_44c" / "music_gen_probe.json").write_text(
            json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"PROBE_NO_AUDIO: data 无 audio（keys={ev['data_keys']}）——"
              f"可能 data.status={data.get('status')}（1=生成中）")
        return 1
    audio = binascii.unhexlify(audio_hex)
    extra = {k: v for k, v in (payload.get("extra_info") or {}).items()
             if not any(m in str(k).lower() for m in ("key", "token", "secret"))}
    out = BASE / "projects" / PROJECT / "output" / "audio_imports" / "music" / \
        f"gen_44c_{a.model.replace('.', '_')}_v1.mp3"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(audio)
    ev.update({
        "result": "ok",
        "audio_bytes": len(audio),
        "music_duration_ms": extra.get("music_duration"),
        "extra_info": {k: v for k, v in extra.items() if k != "audio_duration"},
        "asset": str(out), "sha256": sha256_file(out),
        "prompt_chars": len(prompt),
    })
    (BASE / "_44c" / "music_gen_probe.json").write_text(
        json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in ev.items() if k != "extra_info"},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
