# Phase51-E：s02_c04 首帧切换（N_lock 重建候选 s37——旧首帧帆船时代错误+海怪巨大
# 具象立起与分镜"水下暗影"严重不符；用户 §七.3 明日一并重生成的执行）
import json, sys, io, time, uuid
sys.path.insert(0, '.')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from pathlib import Path

pdir = Path('projects/P50_B')
from src.core.store import ProjectStore
from src.core.models import ImageBatch, Candidate, ShotStatus

PICK = {
    'local': str(Path('_p51_s02c04_ff/s02c04_nlock_s37.png').resolve()),
    'url': 'https://cos-platform-outputs.agnes-ai.cn/images/t2i/task_7qtOZbydQ5rVCKezx8yUEg9xG09wqLYk/output_0a904ec4193c49e1bb275cc29173fd8f.png',
    'variant': 'N_lock 无参考详细锁定 s37（WS 低机位偏俯、水下暗影隐约触须、单船无帆船、乌云黄昏；目检合格）',
}

store = ProjectStore(pdir)
now = time.time()
batch_id = f"batch_ffe1_{uuid.uuid4().hex[:6]}"
cand_id = f"cand_ffe1_{uuid.uuid4().hex[:6]}"
def _mut(s):
    s.first_frame_batches.append(ImageBatch(
        batch_id=batch_id, kind="first_frame", created_at=now, status="ready",
        prompt_snapshot=f"Phase51-E s02_c04 首帧重建：{PICK['variant']}",
        generation_basis={
            "slot_semantics": "hard_boundary_first_frame",
            "source": "phase51_e_first_frame_rebuild",
            "recipe": "N_lock 无参考+详细身份锁定（§11 实证复杂叙事镜最优）",
            "replaces": "旧首帧 i2i/task_kzDupX7Y5thJXgDtB2WbOI6eQZ1xAkyt（帆船+具象怪物，与分镜不符）"},
        candidates=[Candidate(candidate_id=cand_id, local=PICK['local'], url=PICK['url'],
                              status=ShotStatus.DONE.value, created_at=now)]))
    s.selected_first_frame = cand_id
    s.first_frame.mark_done(local=PICK['local'], url=PICK['url'])
    s.first_frame.status = ShotStatus.DONE.value
store.update_shot('s02_c04', _mut)
proj = store.load()
sh = proj.get_shot('s02_c04')
print(f"s02_c04: selected={sh.selected_first_frame} url_ok={bool(sh.first_frame.selected and sh.first_frame.selected.url)}")
with Path('cache/p50_timing_ledger.jsonl').open('a', encoding='utf-8') as f:
    f.write(json.dumps({"event": "first_frame_rebuild", "utc": time.strftime(
        '%Y-%m-%dT%H:%M:%S+00:00', time.gmtime()), "project": "P50_B", "shot_id": "s02_c04",
        "variant": PICK['variant'], "url": PICK['url'],
        "note": "phase51-E s02_c04 首帧重建（帆船时代错误+怪物形态不符分镜的根因修复）"}, ensure_ascii=False) + "\n")
print("账本已记录")
