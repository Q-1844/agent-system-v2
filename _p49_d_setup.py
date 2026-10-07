# -*- coding: utf-8 -*-
"""Phase49 D 批:新会话完整故事(候选 Skill 自主制作)。
输入只给作品目标、候选 Skill、合法资源和项目事实;不提前给逐镜方法答案(D1)。
用法: python _p49_d_setup.py
"""
import json
import sys
import urllib.request

sys.path.insert(0, ".")

BASE = "http://127.0.0.1:8000"


def req(path, body=None, method="GET"):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data,
                               headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(r, timeout=60) as resp:
        return json.load(resp)


def main():
    # 1) 建项目
    try:
        req("/api/projects/create?name=P49_D_NEW&render_family=photorealistic"
            "&art_direction=%E5%86%99%E5%AE%9E%E5%84%BF%E7%AB%A5%E5%8F%8B%E6%83%85%E7%9F%AD%E7%89%87",
            None, "POST")
        print("project P49_D_NEW created")
    except Exception as e:
        print("create:", e)

    # 2) 资源同源复制
    import shutil
    shutil.copy("projects_archive_20261007/P48_D_Cast/output/resources.json",
                "projects/P49_D_NEW/output/resources.json")
    shutil.rmtree("projects/P49_D_NEW/output/resources", ignore_errors=True)
    shutil.copytree("projects_archive_20261007/P48_D_Cast/output/resources/looks",
                    "projects/P49_D_NEW/output/resources/looks")
    print("resources copied")

    # 3) W09 定义:目标级 brief,不给逐镜答案
    skill_id = "agnes-production-craft"
    load_hint = (f'【工作方法】开始执行前先 skill.load "{skill_id}" 并严格遵循其制作方法；'
                 "本轮试验不要加载其他技能。\n")
    params = {
        "producerObjective": (
            "生产计划：本项目是自主制作测试《追风筝》。已有资产：两个 approved Look"
            "（小满=9岁女孩黄雨衣、阿岩=10岁男孩条纹T恤工装裤，resources.json 内含参考图）。"
            "角色阶段必须复用既有 Look，不新建。"),
        "writerObjective": (
            "【作品目标】《追风筝》：雨后放晴的傍晚，小满和阿岩在庭院放风筝；"
            "风筝线断/被树枝挂住（阻碍）；两人想办法把风筝取回来（动作带来状态变化）；"
            "风筝重新飞起来（可理解的结果）。"
            "要求：完整故事包含目标、阻碍、状态变化和结果；镜头数由剧情需要决定，"
            "不为凑数加镜；每段写清剧情任务、起始状态、主动作、结果状态。"
            "不预先指定分镜——分镜由导演阶段按你加载的技能自主设计。"),
        "characterObjective": (
            "项目已有两个 approved Look（小满/阿岩，resources.json 含参考图），"
            "本轮只读取复用，不新建角色与 Look；缺道具参考如实列为缺项，不伪造。"),
        "sceneObjective": (
            "建立场景：老宅庭院（雨后傍晚）Location 与 SceneInstance。写实风格。"),
        "boardObjective": (
            load_hint
            + "【任务】按已写好的故事自主设计分镜：建立镜头清单、绑定资源、生成分镜图"
              "（首帧候选）。每镜规划含起态/主动作/结果/时长/剪辑留量/衔接要求；"
              "所有视频源素材至少 8 秒、默认 10 秒。身份镜用已批准 Look 参考。"),
        "videoObjective": (
            load_hint
            + "【任务】按已选首帧与镜头清单真实生成全部视频：逐镜自主决定模式与接续方式"
              "（素材至少 8 秒，默认 10 秒），完成后核对回执、文件真实时长与首中尾帧。"
              "生成前先完整审看各镜已选首帧；首帧不合格的镜头按你加载的技能处理。"),
        "videoCount": 6,
        "bindRelaySkill": False,
    }
    r = req("/api/project/P49_D_NEW/workflow/templates/W09/create",
            {"workflowId": "P49_D_NEW_W09", "params": params}, "POST")
    print("create rev", r.get("revision"), "ok:", r["validation"]["ok"])

    # 4) 注入候选 Skill 绑定 + 复用型节点策略修正
    wid = "P49_D_NEW_W09"
    p = f"projects/P49_D_NEW/output/workflows/definitions/{wid}.json"
    d = json.load(open(p, encoding="utf-8"))
    ncd = next(n for n in d["nodes"] if n["id"] == "n_cd")
    ncd["config"]["completion"]["strategy"] = "allow_verified_reuse"
    if not any(n.get("type") == "skill" for n in d["nodes"]):
        d["nodes"].append({"id": "n_skill_0", "type": "skill", "refId": skill_id,
                           "refVersion": "0.9.0", "label": "候选制作 Skill",
                           "config": {}, "position": {"x": 0, "y": 0}})
        sn_id = "n_skill_0"
    for e in [e for e in d["edges"] if e.get("type") == "skill_binding"]:
        d["edges"].remove(e)
    for target in ("n_dir_board", "n_dir_video"):
        d["edges"].append({"id": f"e_skill_{target}", "type": "skill_binding",
                           "source": sn_id, "target": target, "skillVersion": "0.9.0"})
    r = req(f"/api/project/P49_D_NEW/workflow/definitions/{wid}",
            {"definition": d, "expectedRevision": d["revision"]}, "PUT")
    print("inject PUT ok:", r.get("validation", {}).get("ok"))
    r = req(f"/api/project/P49_D_NEW/workflow/definitions/{wid}/compile", {}, "POST")
    print("compile:", r.get("ok"))


if __name__ == "__main__":
    main()
