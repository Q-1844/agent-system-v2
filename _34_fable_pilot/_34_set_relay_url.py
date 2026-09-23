# _34_fable_pilot/_34_set_relay_url.py — 把某镜的接力参考 URL 指向 Pages 图床（临时手术工具）。
# 用法: python _34_set_relay_url.py <shot_id> <pages_url>
# 只改 video_end_frame.url（local/status/source_version 不动）→ relay freshness 指纹不变。
# 走 ProjectStore.update_shot 原子写盘（后端每请求从盘加载，进程外写可见）。
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.models import Asset, ShotStatus
from src.core.store import ProjectStore

PROJ_DIR = Path(__file__).resolve().parent.parent / "projects" / "Fable_Pilot_Fox_and_Grapes_V1"


def main():
    shot_id, url = sys.argv[1], sys.argv[2]
    assert url.startswith("https://"), "URL 必须是 https 公网地址"
    store = ProjectStore(PROJ_DIR)

    def show(tag, sh):
        ef = sh.video_end_frame
        print(f"[{tag}] {shot_id}: url={str(ef.url)[:80]} local={ef.local} "
              f"status={ef.status} end_ver={sh.video_end_source_version}")

    before = {}
    shot = store.update_shot(shot_id, lambda s: before.update(
        local=s.video_end_frame.local, ver=s.video_end_source_version))
    show("before", shot)
    assert before["ver"], "该镜无末帧记录，拒绝盲改"
    store.update_shot(shot_id, lambda s: setattr(
        s, "video_end_frame",
        Asset(local=before["local"], url=url, status=ShotStatus.DONE.value)))
    after = store.update_shot(shot_id, lambda s: None)
    show("after ", after)


if __name__ == "__main__":
    main()
