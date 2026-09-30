# -*- coding: utf-8 -*-
"""Phase 43 §三步——盲测项目《晨露快递》夹具搭建（Claude Code 侧，正式入口）。

只搭【项目状态】：空项目 + approved 角色 Look + approved prop + 两个 location
（含 approved SIV）。剧本/镜头/场景实例/Binding 一律不建——那是受测 Agent 的
正式流程（预编剧冻结→场景绑定→生产）。

三个独立正式入口 turn（各自全新会话）：
  1. character_designer：建角色 榛果 + Look（approve_if_pass）
  2. character_designer：登记 prop 露珠信（参考图由本脚本先行直生成）
  3. scene_designer：草原晨光 / 老橡树 两 location + views + SIVs（approve_if_pass）

证据：_43_blind/ 下 events/result/资产快照。
"""
import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

PROJECT = "晨露快递"
EVROOT = BASE_DIR / "_43_blind"


def _load_env():
    env_file = BASE_DIR / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _bootstrap_deps():
    import deps
    from src.core.rate_limiter import RateLimiter
    from src.core.quota import QuotaLedger
    import yaml
    _APP_CFG = yaml.safe_load((BASE_DIR / "config" / "config.yaml").read_text(encoding="utf-8")) or {}
    _agnes_cfg = _APP_CFG.get("agnes") or {}
    RateLimiter.get(_agnes_cfg.get("rpm"))
    QuotaLedger.get_default(BASE_DIR / "cache" / "quota.json", limits=_APP_CFG.get("quota"))
    import routers.shots  # noqa: F401
    from src.pipeline.controller import PipelineController
    deps.pipeline_controller = PipelineController(str(BASE_DIR / "config" / "config.yaml"))
    deps.agnes_client = deps.pipeline_controller.agnes_client
    deps.project_manager = deps.pipeline_controller.project_manager
    from src.core.job_queue import JobQueue
    q = JobQueue(BASE_DIR / "cache" / "_43_blind_fixture_jobs.json", deps.JOB_HANDLERS)
    deps.job_queue = q
    return deps, q


async def agent_turn(agent_id: str, task: str, tag: str) -> dict:
    """一个全新会话的正式入口 turn。"""
    import deps
    from src.agents.runtime import get_agent_runtime
    if agent_id == "scene_designer":
        from src.agents.scene_designer import scene_designer_turn as turn_fn
    elif agent_id == "character_designer":
        from src.agents.character_designer import character_designer_turn as turn_fn
    else:
        raise ValueError(agent_id)
    rt = get_agent_runtime()
    store = rt._session_store(PROJECT)
    s = store.get_or_create(agent_id, "scene", f"p43_fixture_{tag}_{int(time.time())}")
    assert not s.messages
    sid = s.session_id
    EVROOT.mkdir(parents=True, exist_ok=True)
    ev_path = EVROOT / f"events_fixture_{tag}.jsonl"
    t0 = time.time()

    def cb(ev):
        try:
            with ev_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
            if str(ev.get("type")) in ("tool.started", "tool.completed", "tool.failed"):
                print(f"  [{int(time.time()-t0):>4}s] {ev.get('type')} {ev.get('name')} "
                      f"ok={ev.get('ok')} {str(ev.get('summary') or ev.get('error') or '')[:80]}",
                      flush=True)
        except Exception:
            pass

    result = await turn_fn(rt, PROJECT, sid, task, event_cb=cb)
    (EVROOT / f"result_fixture_{tag}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"=== {tag} done in {int(time.time()-t0)}s ===")
    print((result.get("assistant_message") or "")[:600])
    return result


async def main():
    import deps
    deps, q = _bootstrap_deps()
    await q.start()
    try:
        pm = deps.project_manager
        try:
            pdir = pm.get_project_dir(PROJECT)
            print("project exists:", pdir)
        except FileNotFoundError:
            pdir = pm.create_project(PROJECT, render_family="2d_animation",
                                     art_direction="二维手绘童话绘本风，笔触柔软，清晨草原的露水光感，暖绿与金色")
            print("project created:", pdir)

        # ---- 1) prop 参考图直生成（资源道具无生成端点，Claude Code 侧备图）----
        prop_local = pdir / "input" / "prop_dew_letter.png"
        prop_local.parent.mkdir(parents=True, exist_ok=True)
        if not prop_local.exists():
            client = deps.agnes_client
            prompt = ("A tiny letter wrapped in a fresh green leaf, sealed with a glowing "
                      "dew drop, resting on a mossy stone. 2D hand-drawn storybook style, "
                      "soft watercolor, warm morning light, clean composition, single "
                      "object centered, no characters.")
            r = await client.generate_image(prompt=prompt, size_tier="1K", ratio="1:1")
            data = r.get("b64")
            if data:
                import base64
                prop_local.write_bytes(base64.b64decode(data))
            else:
                await client.download(r["url"], prop_local)
            print("prop image saved:", prop_local)

        # ---- 2) 角色 + Look ----
        task_char = (
            "项目：晨露快递。你是 Resource Agent，为本项目搭建角色资产（只做资源，不碰镜头）：\n"
            "1. character.create 新建角色「榛果」：一只圆滚滚的小刺猬，棕色短刺像小灌木，"
            "奶白色的肚皮和脸颊，黑豆眼，粉色小鼻头，背着一个小小的褐色邮差布包；"
            "appearance 描述写可见视觉特征（体型比例/毛色/标志细节），方便后续画像与视频锚定。\n"
            "2. resource.look.prepare 为榛果生成 hero Look（approve_if_pass）："
            "全身立绘、清晨柔光、干净浅背景，2D 手绘绘本水彩风，形象必须小巧可爱"
            "（比例圆润、头身比约 1:1.2、不写实不尖锐）。\n"
            "3. resource.prop.register 登记道具「露珠信」：reference_local="
            "input/prop_dew_letter.png（已备好的参考图），描述=一片新鲜绿叶包裹的小信件，"
            "封口处有一颗发亮的露珠（approve_if_pass）。\n"
            "完成后 character.read / resource.inventory 复核三者为 approved，把 id 写进回复。"
            "审查不过按真实原因调整后重试（≤3 次），仍不过如实报告。")
        await agent_turn("character_designer", task_char, "char")

        # ---- 3) 两个 location + hero views（SIV 挂故事段实例，由受测 Agent 建完剧本后走正式流程生成）----
        task_scene = (
            "项目：晨露快递。你是 Scene Agent，为本项目搭建场景资产（只做场景，不碰镜头/实例）：\n"
            "1. location A「晨雾草原」：清晨的开阔草原，低矮软草挂露珠，远处几棵野花，"
            "薄雾未散，天光暖金；identity_description 写【可见视觉特征】（地形/植被/光线/远景），"
            "style：2D hand-drawn storybook, soft watercolor。\n"
            "2. location B「老橡树」：草原尽头的一棵巨大老橡树，粗壮树干有树洞，"
            "低处一个圆形小木门（信箱洞口），枝叶垂下晨露，树根盘起；同样只写可见特征。\n"
            "3. 各建 1 个 hero LocationView（camera_zone 必填，如 meadow_path / oak_front；"
            "approve_if_pass）。（SceneInstanceView 需要挂在故事段实例上，本项目还没有"
            "故事段——留给后续实例绑定流程，不要强行创建。）\n"
            "完成后 scene.inventory 复核：两个 location + 两个 approved view，把 id 写进回复。\n"
            "纪律：一次 prepare=一次生成尝试；不动其它项目。")
        await agent_turn("scene_designer", task_scene, "scene")
    finally:
        await q.stop()


if __name__ == "__main__":
    _load_env()
    asyncio.run(main())
