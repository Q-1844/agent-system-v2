# Phase51-E：s02_c04 装配前置修复——版本 local 别名缺陷绕过
# 背景：修复轮新版本（怪物实体化失败）覆盖了共用 local 路径的字节；
# final_video 指针=att2 失败版。装配读 local → 必须恢复 C-T03 att4 通过版字节+指针。
import json, sys, io, time, hashlib, urllib.request
sys.path.insert(0, '.')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from pathlib import Path
from src.core.store import ProjectStore

GOOD_VID = 'vv_d3a0bf21'   # C-T03 att4（一审通过版；帆船背景为已知瑕疵=用户待定项）
proj = ProjectStore(Path('projects/P50_B')).load()
sh = proj.get_shot('s02_c04')
vv = next((v for v in sh.video_versions if v.version_id == GOOD_VID), None)
assert vv and vv.url, '目标版本不存在或无 URL'
dest = Path(str(vv.local))
print('目标 local:', dest)
print('恢复前 sha256:', hashlib.sha256(dest.read_bytes()).hexdigest()[:16])

data = None
for i in range(6):
    try:
        data = urllib.request.urlopen(vv.url, timeout=120).read()
        break
    except Exception as e:
        print(f'下载重试 {i+1}/6: {type(e).__name__}: {str(e)[:60]}')
        time.sleep(15)
assert data, 'DNS/下载持续失败'
dest.write_bytes(data)
new_hash = hashlib.sha256(data).hexdigest()
print('恢复后 sha256:', new_hash[:16], f'({len(data)/1e6:.1f} MB)')

def _mut(s):
    s.final_video = GOOD_VID
    s.video_end_source_version = GOOD_VID
store.update_shot('s02_c04', _mut)

proj2 = ProjectStore(Path('projects/P50_B')).load()
sh2 = proj2.get_shot('s02_c04')
cur = next(v for v in sh2.video_versions if v.version_id == sh2.final_video)
ok = cur.local and Path(str(cur.local)).exists() and hashlib.sha256(Path(str(cur.local)).read_bytes()).hexdigest() == new_hash
print('final_video ->', sh2.final_video, '| 字节一致性校验:', 'OK' if ok else 'FAIL')

with Path('cache/p50_timing_ledger.jsonl').open('a', encoding='utf-8') as f:
    f.write(json.dumps({"event": "version_bytes_restore", "utc": time.strftime(
        '%Y-%m-%dT%H:%M:%S+00:00', time.gmtime()), "project": "P50_B", "shot_id": "s02_c04",
        "version_id": GOOD_VID, "sha256": new_hash,
        "note": "版本local别名缺陷绕过：从 provider URL 恢复 C-T03 att4 通过版字节+指针切换（装配前置）"},
        ensure_ascii=False) + "\n")
