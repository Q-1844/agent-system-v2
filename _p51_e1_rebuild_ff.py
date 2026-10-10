# Phase51-E E1：s04_c04/s05_c03 首帧重建（selected 指针切换到 _p51_imgtest 合格候选）
# 执行侧操作（用户已批准的修复备料，§8.1 候选库；本轮目检亲证 V2_s51/V1_s51 合格）。
# 写路径=ProjectStore.update_shot（CLAUDE.md 认可唯一写回通道）；落账 ledger。
import json, sys, io, time, uuid
sys.path.insert(0, '.')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from pathlib import Path

pdir = Path('projects/P50_B')
from src.core.store import ProjectStore
from src.core.models import ImageBatch, Candidate, ShotStatus

PICKS = {
    's04_c04': {
        'local': str(Path('_p51_imgtest/s04c04_V2_船尾回望_s51.png').as_posix()),
        'url': 'https://cos-platform-outputs.agnes-ai.cn/images/t2i/task_aYOcgri6nD8XVYljfY7Rs13kP75ud8y9/output_89ff1eec419c4068bc8f8e38a69944ed.png',
        'variant': 'V2_船尾回望（船尾前景+触须浅水受阻+无沙滩；目检合格）',
    },
    's05_c03': {
        'local': str(Path('_p51_imgtest/s05c03_V1_直出_s51.png').as_posix()),
        'url': 'https://cos-platform-outputs.agnes-ai.cn/images/t2i/task_YVrGyiqAD7hxMo39HjM2RAO2azCBdDNw/output_f91e036abf7d45a1bf62e53d8bc5a50f.png',
        'variant': 'V1_直出（开阔海面驶向港口灯，双人可见，无码头结构；目检合格）',
    },
}

store = ProjectStore(pdir)
for sid, p in PICKS.items():
    now = time.time()
    batch_id = f"batch_ffe1_{uuid.uuid4().hex[:6]}"
    cand_id = f"cand_ffe1_{uuid.uuid4().hex[:6]}"
    local_abs = str((pdir.parent / p['local']).resolve())
    def _mut(s):
        s.first_frame_batches.append(ImageBatch(
            batch_id=batch_id, kind="first_frame", created_at=now, status="ready",
            prompt_snapshot=f"Phase51-E 首帧重建：{p['variant']}",
            generation_basis={
                "slot_semantics": "hard_boundary_first_frame",
                "source": "phase51_e_first_frame_rebuild",
                "variant": p['variant'],
                "candidate_library": "_p51_imgtest（2026-10-10 §8.1 战役产物）"},
            candidates=[Candidate(candidate_id=cand_id, local=local_abs, url=p['url'],
                                  status=ShotStatus.DONE.value, created_at=now)]))
        s.selected_first_frame = cand_id
        s.first_frame.mark_done(local=local_abs, url=p['url'])
        s.first_frame.status = ShotStatus.DONE.value
    store.update_shot(sid, _mut)
    print(f"{sid}: batch={batch_id} cand={cand_id} -> selected 切换完成")

# 复核
proj = store.load()
for sid in PICKS:
    sh = proj.get_shot(sid)
    sel = sh.first_frame.selected
    print(f"复核 {sid}: selected={sh.selected_first_frame} url_ok={bool(sel and sel.url)} local_ok={bool(sel and sel.local)}")

# 落账
led = Path('cache/p50_timing_ledger.jsonl')
with led.open('a', encoding='utf-8') as f:
    for sid, p in PICKS.items():
        f.write(json.dumps({"event": "first_frame_rebuild", "utc": time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime()),
                            "project": "P50_B", "shot_id": sid, "variant": p['variant'],
                            "url": p['url'], "local": p['local'],
                            "note": "phase51-E E1 首帧重建（attractor 根因修复前置）"}, ensure_ascii=False) + "\n")
print("账本已记录")
