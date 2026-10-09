# -*- coding: utf-8 -*-
"""Phase50 B 批 setup：写实《克苏鲁追船》W09 完整生产链（PHASE50_B §1-§3）。

新项目 P50_B + 新 W09 定义：
- 目标级 brief（无逐镜答案）：威胁被发现→追赶压力上升→两人有联系的行动→可观察结果；
- 写实资产从写实素材开始（船长/大副/船/克苏鲁 参考由资源 Agent 真实新建）；
- 正式 Skill 绑定：full-story-relay-calibration v1.4.0 同时绑 n_dir_board 与 n_dir_video
  （Phase49 人评：正式包在一致性/运镜/衔接胜出；候选包未转正）；
- 视频节点：素材 ≥8s 默认 10s、无旁白无主动 TTS 无对白、两人世界（population 纪律）。

用法：python _p50_b_setup.py
"""
import json
import sys
import urllib.request

sys.path.insert(0, ".")

BASE = "http://127.0.0.1:8000"
PROJ = "P50_B"
WID = "P50_B_W09"
RELAY_SKILL = "full-story-relay-calibration"


def req(path, body=None, method="GET"):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data,
                               headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(r, timeout=60) as resp:
        return json.load(resp)


def main():
    # 1) 建项目
    try:
        req(f"/api/projects/create?name={PROJ}&render_family=photorealistic"
            "&art_direction=%E5%86%99%E5%AE%9E%E6%B5%B7%E4%B8%8A%E8%BF%BD%E9%80%90"
            "%E7%9F%AD%E7%89%87", None, "POST")
        print("project P50_B created")
    except Exception as e:
        print("create:", e)

    # 2) W09 定义：目标级 brief，逐镜方法完全留给系统 Agent
    skill_hint = (f'【工作方法】开始执行前先 skill.load "{RELAY_SKILL}" 并严格遵循其制作方法；'
                  "本轮不要加载其他技能。\n")
    params = {
        "producerObjective": (
            "生产计划：写实电影短片《深水惊魂》（暂名，可由编剧定名）——海上，一艘船受到"
            "巨型海怪（克苏鲁式触须怪物）追赶，船上只有船长与大副两名人类。两人的应对推动剧情，"
            "观众能看懂威胁、行动选择与结果。目标成片约 60 秒（50–70 秒完整叙事），横屏 16:9，"
            "写实电影风格（允许幻想怪物，但人物/船/材质/光照要像真实拍摄+视觉特效的电影）。"
            "首版无旁白、无主动配音、无人物对白，保留合格原生环境声与同步音效。"
            "资产全部新建（写实参考），逐镜方法由各专业 Agent 自主决定。"),
        "writerObjective": (
            "【作品目标】写实海难追逐短片：威胁被发现（远处的怪物/异动）→追赶压力上升"
            "（船全速逃跑，怪物逼近）→两人采取有联系的行动（船长的操纵与大副的配合/应对，"
            "行动影响结果）→行动带来可观察结果（摆脱/代价/开放结尾均可，但不能靠怪物"
            "突然消失或无原因跳场收尾）。"
            "要求：无对白、无旁白——纯视觉叙事；人物功能清楚（船长=操舵/决策，大副=配合/执行）；"
            "每段写清剧情任务、起始状态、主动作、结果状态。镜头数由导演阶段按剧情决定，"
            "你不预先规定分镜。"),
        "characterObjective": (
            "新建写实资产（全部真实生成参考图）："
            "①船长——身份/面部/体型/服装（防水外套等海上作业装束）/显著识别点，写实皮肤质感；"
            "②大副——与船长可区分的另一名人类（面部/发型/服装不同）；"
            "③船——同一艘渔船/小型机动船的整体外观参考（船体轮廓、驾驶区域、甲板），"
            "外景与甲板必须是同一艘船；"
            "④克苏鲁式海怪——同一怪物的参考：头部触须、体表质感、体量巨大（相对船的尺度基准）；"
            "表现角度可变化但必须可识别为同一实体，不能退化为泛化章鱼。"
            "参考图必须是写实电影质感（不许插画风/游戏质感）。缺项如实列出，不伪造。"),
        "sceneObjective": (
            "建立场景：开阔海域（追船发生地）与渔船甲板（人物活动区）。写实海况："
            "为追赶段落维护简明空间定义（时间/天气/光源/波浪状态/追赶方向）。"
            "每个故事段的具体状态用 scene.state_view.prepare 生成 SceneInstanceView "
            "并绑定正确 SceneInstance——后续分镜首帧与视频以状态图为空间依据。"),
        "boardObjective": (
            skill_hint
            + "【任务】按已写好的故事自主设计分镜：镜头清单、资源绑定、分镜图（首帧候选）。"
              "每镜规划含：功能与主动作、起止状态（人物位置/持物/船航向/威胁位置）、机位与"
              "空间关系、预计可见人物（两人/一人/无人）、必须保留的关键事件、源时长与预计"
              "保留时长。所有视频源素材至少 8 秒、默认 10 秒（常规 8–12 秒内安排）。"
              "追赶关系按世界空间与相对位置定义（不要求每镜同向）；跨轴线需建立机位关系。"
              "两人身份镜用已批准 Look 参考；人物总数纪律：全片只有船长与大副两名人类。"),
        "videoObjective": (
            skill_hint
            + "【任务】按已选首帧与镜头清单真实生成全部视频：逐镜自主决定模式与接续方式"
              "（素材至少 8 秒，默认 10 秒），完成后核对回执、文件真实时长与首中尾帧。"
              "生成前先完整审看各镜已选首帧；首帧不合格的镜头先修首帧再提交。"
              "视频过程纪律：人物总数不得超过两人、不得出现分身；关键动作（操舵/配合/接触）"
              "过程完整；怪物与船保持同一实体与合理尺度。"),
        "videoCount": 6,
        "bindRelaySkill": True,
    }
    r = req(f"/api/project/{PROJ}/workflow/templates/W09/create",
            {"workflowId": WID, "params": params}, "POST")
    print("create rev", r.get("revision"), "ok:", r.get("validation", {}).get("ok"))

    # 3) 追加正式 Skill 绑定到 n_dir_board（模板默认只绑 n_dir_video）
    dpath = f"projects/{PROJ}/output/workflow/definitions/{WID}.json"
    import pathlib
    for cand in (f"projects/{PROJ}/output/workflows/definitions/{WID}.json", dpath):
        p = pathlib.Path(cand)
        if p.exists():
            dpath = cand
            break
    d = json.load(open(dpath, encoding="utf-8"))
    skill_nodes = [n for n in d["nodes"] if n.get("type") == "skill"]
    if skill_nodes:
        sn = skill_nodes[0]
        targets = {e.get("target") for e in d.get("edges", [])
                   if e.get("type") == "skill_binding"}
        if "n_dir_board" not in targets:
            d["edges"].append({"id": "e_skill_board", "type": "skill_binding",
                               "source": sn["id"], "target": "n_dir_board",
                               "skillVersion": "1.4.0"})
            print("skill binding added -> n_dir_board")
    r = req(f"/api/project/{PROJ}/workflow/definitions/{WID}",
            {"definition": d, "expectedRevision": d["revision"]}, "PUT")
    print("PUT ok:", r.get("validation", {}).get("ok"))
    r = req(f"/api/project/{PROJ}/workflow/definitions/{WID}/compile", {}, "POST")
    print("compile:", r.get("ok"))

    # 4) 启动 run
    r = req(f"/api/project/{PROJ}/workflow/definitions/{WID}/runs",
            {"idempotencyKey": "p50_b_run1"}, "POST")
    run = r.get("run") or {}
    print("run:", run.get("runId"), "status:", run.get("status"))


if __name__ == "__main__":
    main()
