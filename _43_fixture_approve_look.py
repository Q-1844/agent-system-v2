# -*- coding: utf-8 -*-
"""盲测夹具：按用户评审页裁定（FEEDBACK_fixture.json）走正式人评+批准路径。

用户裁定（2026-09-30 评审页，逐字在档）：
- 批准：榛果 Look 候选 1（gen_0675e0109d2f=ref_07918bd8f2ce）与
  候选 3（gen_240c2b373f57=ref_9fd8de53099c）；两场景视图与道具已批。
- 评语原文：「背包样式差不多就行了吗，重点是后续的视频中的人物道具场景要
  保持一致不能一直变换。只要能让不变的东西稳定，它什么样子倒不是最重要。」
- 候选 4 否决理由：「背包出现了两个」。

路径：POST /looks/{id}/review/human 语义（用户人评=最高审查层级，pass 即
满足 approve 门槛）→ approve_look_reference（正式审批，OCC+落盘校验）。
封面=候选 1（评审页首个批准项）。
"""
import asyncio
import io
import json
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

PROJ = "晨露快递"
NOTE = ("背包样式差不多就行了吗，重点是后续的视频中的人物道具场景要保持一致"
        "不能一直变换。只要能让不变的东西稳定，它什么样子倒不是最重要。"
        "（Phase 43 夹具评审页 2026-09-30：候选 1+3 显式批准；候选 4 否决理由=背包出现了两个）")


async def main():
    import deps
    from src.pipeline.controller import PipelineController
    deps.pipeline_controller = PipelineController(str(ROOT / "config" / "config.yaml"))
    deps.agnes_client = deps.pipeline_controller.agnes_client
    deps.project_manager = deps.pipeline_controller.project_manager

    pdir = deps.project_manager.get_project_dir(PROJ)
    from src.resources.look_review import record_human_review
    from src.resources.write_service import ResourceWriteService

    ws = ResourceWriteService(pdir, PROJ)

    # 1) 用户人评证据（append-only，reviewer=user）
    for rid in ("ref_07918bd8f2ce", "ref_9fd8de53099c"):
        r = record_human_review(pdir, "look_1a5486946002", rid,
                                overall="pass", note=NOTE, reviewer="user")
        print("human review:", rid, "ok=", r.get("ok"))
        if not r.get("ok"):
            print(json.dumps(r, ensure_ascii=True)[:300])
            return 1

    # 2) 正式批准（每次重读 revision 防 OCC 竞争）
    from src.resources.store import ResourceStore
    for i, rid in enumerate(("ref_07918bd8f2ce", "ref_9fd8de53099c")):
        doc = ResourceStore(pdir, PROJ).load()
        lk = doc.looks["look_1a5486946002"]
        from src.resources.models import look_revision
        r = await ws.approve_look_reference(
            "look_1a5486946002", rid, expected_revision=look_revision(lk),
            set_cover=(i == 0))
        print("approve:", rid, "ok=", r.get("ok"), "err=", r.get("error"))
        if not r.get("ok"):
            print(json.dumps(r, ensure_ascii=True)[:400])
            return 1

    # 3) 复核
    doc = ResourceStore(pdir, PROJ).load()
    lk = doc.looks["look_1a5486946002"]
    print("approved:", lk.approved_reference_asset_ids, "| cover:", lk.cover_asset_id,
          "| status:", lk.approval_status)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
