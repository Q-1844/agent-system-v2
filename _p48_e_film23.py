# -*- coding: utf-8 -*-
"""Phase48 E：风格片 2/3 与说明片的项目/定义创建（可选启动）。

用法：
  python _p48_e_film23.py create            # 建项目+定义（不启动 run）
  python _p48_e_film23.py start watercolor  # 启动风格片2 run
  python _p48_e_film23.py start realistic   # 启动风格片3 run
  python _p48_e_film23.py start explainer   # 启动说明片 run

纪律：风格片 1（run_e9484ba7ad72）出成片候选前不 start；仅在余额可用时启动。
定义参数与风格片 1 完全一致（同 brief 同参数），仅项目 render_family/art_direction 不同。
"""
import json
import sys
import urllib.request

BASE = "http://localhost:8000"

# 与 _p48_e_evidence.json styles 段一致
FILMS = {
    "watercolor": {
        "project": "P48_E_Watercolor",
        "workflowId": "wf_e_film_watercolor",
        "render_family": "2d_animation",
        "art_direction": "soft watercolor storybook illustration, visible paper grain, gentle wash edges, hand-painted feel",
    },
    "realistic": {
        "project": "P48_E_Realistic",
        "workflowId": "wf_e_film_realistic",
        "render_family": "photorealistic",
        "art_direction": "cinematic photorealistic rendering, natural lighting, shallow depth of field, filmic grade",
    },
    "explainer": {
        "project": "P48_E_Explainer",
        "workflowId": "wf_e_explainer",
        "render_family": "2d_animation",
        "art_direction": "clean technical 2D animation, crisp outlines, flat colors, diagram-friendly clear shapes",
    },
}

# 风格片 1（wf_e_film_2d）逐节点 objective 原文（_p48_e_film1_objectives.json 提取）
BRIEF_OBJ = (
    "按以下故事骨架真实新写全片故事段（3-4 个场景段落，不扩写流水账）："
    "《风筝与竹竿》：小女孩小满（双马尾辫，黄雨衣）的风筝挂在了村口老槐树的高枝上。"
    "她的朋友阿岩（乱翘短发，蓝白条纹T恤，深绿背带工装裤）想用长竹竿把风筝捅下来，"
    "第一次尝试失败（竹竿太短够不着）。小满面临选择：爬上摇摇晃晃的树杈（有跌落风险），"
    "或回家求助（放弃今天放飞）。她选择小心爬树，在树杈间意外发现阿岩前几天丢失的"
    "红色手电筒（回收此前道具伏笔）。她不动手电筒，改用手电筒玻璃反光引来路人的注意，"
    "借来长梯取下风筝。两人傍晚一起放飞风筝。")
CHAR_OBJ = ("生产小满与阿岩两名角色及各自 1 套 Look（外观：小满=双马尾辫/明黄色雨衣/"
            "蓝背带裤；阿岩=乱翘短发/蓝白条纹T恤/深绿背带工装裤），红色手电筒作为道具"
            "如实生产参考。")
SCENE_OBJ = ("建立全部场景 Location/SceneInstance；每个故事段涉及的具体状态（时段/天气/"
             "门已开等）用 scene.state_view.prepare 生成 SceneInstanceView 状态图并绑定"
             "正确 SceneInstance——后续分镜的首帧与视频以状态图为空间依据；仅补地点资料时"
             "才用 LocationView。")
VIDEO_OBJ = ("按批准首帧与镜头清单真实生成全部视频候选：逐镜决定接续方式"
             "（按 relay skill v1.4.0 路径表与 Phase48 受控对照结论选择），完成后核对"
             "首中尾帧与剧情尾态。全片无旁白。")
ASM_OBJ = ("接收精确选片范围装配成片：禁止自动旁白；默认保留合格原生环境声与动作声"
           "（逐镜先分类再决定）；音乐仅按作品政策（本片不加）。完成渲染并核验可播放。")

FILM_PARAMS = {
    "writerObjective": BRIEF_OBJ,
    "characterObjective": CHAR_OBJ,
    "sceneObjective": SCENE_OBJ,
    "videoObjective": VIDEO_OBJ,
    "videoCount": 4,
    "assemblyObjective": ASM_OBJ,
}

# 说明片：手摇手电筒组装与工作原理。无旁白；核心因果靠动作与音效。
# 目标只给主题与表达约束，镜头数/步骤拆解由 Agent 依主题决定（E 批说明片要求）。
EXPLAINER_PARAMS = {
    "writerObjective": (
        "为无旁白说明片真实新写分镜故事段（不写台词、不写旁白文本）：主题="
        "手摇式手电筒的组装与工作原理。观众须仅靠画面动作、画面安排与合格音效理解："
        "①手摇发电的因果（摇柄转动→灯亮）；②组装顺序（外壳/齿轮组/发电机/灯头）；"
        "③摇动停止后灯渐暗（能量耗尽）。禁止以长字幕或人物逐句解说替代核心视觉因果。"),
    "characterObjective": (
        "如需出现手部/人物则生产对应 Look，无对白角色；生产手摇式手电筒的道具参考"
        "（外壳/摇柄/齿轮组/灯头分件与整体外观）。"),
    "sceneObjective": SCENE_OBJ,
    "videoObjective": (
        "按批准首帧与镜头清单真实生成全部视频候选：逐镜决定接续方式"
        "（按 relay skill v1.4.0 路径表与 Phase48 受控对照结论选择），完成后核对"
        "首中尾帧与剧情尾态。全片无旁白；动作声（摇柄咔哒/齿轮咬合/电流声）是理解"
        "因果的主要声音信息，不得静音化处理。"),
    "videoCount": 4,
    "assemblyObjective": (
        "接收精确选片范围装配成片：禁止自动旁白；默认保留合格原生环境声与动作声"
        "（逐镜先分类再决定，动作声是本片理解主通道）；音乐仅按作品政策（本片不加）。"
        "完成渲染并核验可播放。"),
}


def api(method, path, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            body = r.read().decode("utf-8")
        return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} {method} {path} =>", e.read().decode()[:400])
        raise


def _definition_exists(project: str, workflow_id: str) -> bool:
    from pathlib import Path
    p = (Path("projects") / project / "output" / "workflows" / "definitions"
         / f"{workflow_id}.json")
    return p.exists()


def create_film(key):
    f = FILMS[key]
    # 1) 项目（已存在则跳过）
    projs = api("GET", "/api/projects")
    names = projs.get("projects") or []
    if f["project"] in names:
        print(f"[skip] project {f['project']} exists")
    else:
        api("POST", "/api/projects/create", {
            "name": f["project"], "render_family": f["render_family"],
            "art_direction": f["art_direction"]})
        print(f"[ok] project created {f['project']} ({f['render_family']})")
    prof = api("GET", f"/api/project/{f['project']}/visual-profile")
    print("[profile]", json.dumps(prof.get("profile") or prof, ensure_ascii=False)[:200])
    # 2) 定义（模板 create 非幂等，重跑前查磁盘）
    params = dict(EXPLAINER_PARAMS if key == "explainer" else FILM_PARAMS)
    if _definition_exists(f["project"], f["workflowId"]):
        print(f"[skip] definition {f['workflowId']} exists")
    else:
        out = api("POST", f"/api/project/{f['project']}/workflow/templates/W09/create",
                  {"workflowId": f["workflowId"], "params": params})
        v = out.get("validation") or {}
        print(f"[ok] definition created {f['workflowId']} rev={out.get('revision')} "
              f"valid={v.get('ok')} {v.get('errors') or ''}")
    return f


def start_film(key):
    f = create_film(key)
    out = api("POST", f"/api/project/{f['project']}/workflow/definitions/"
                      f"{f['workflowId']}/runs",
              {"idempotencyKey": f"p48e_{key}_v1"})
    run = out.get("run") or {}
    print(f"[started] {f['project']} run={run.get('runId')} status={run.get('status')} "
          f"reused={out.get('reused')}")
    return run.get("runId")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "create"
    if cmd == "create":
        for k in FILMS:
            create_film(k)
    elif cmd == "start" and len(sys.argv) > 2:
        start_film(sys.argv[2])
    else:
        print(__doc__)
