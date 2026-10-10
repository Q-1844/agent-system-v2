# -*- coding: utf-8 -*-
"""Phase51 C-T03：P51_C_W01 冻结分镜复验定义（从 W09r6 派生+B 批定版调整）。

调整项（C1：保存原计划并说明必要调整）：
1. n_dir_board：改为「核对冻结分镜+拍摄记录」——20 镜已在 canonical（P50_B 交付版），
   逐镜核对+双人站位核对（s04_c02 大副须在船上=用户 F2）+首帧核对+定点重建；
   completion shot minCount 6→20（真实镜数，修 P50 videoCount=6 缺陷）。
2. n_dir_video：require_new + video_version minCount 20（强制本轮独立生成全部
   20 镜新候选——C2：缓存复用只能证明恢复效率，不能称独立生产；A 批跨 attempt
   累计使 require_new 重试安全）。
3. skill 绑定 1.4.0 → 1.5.0（production-recipes §6 人数纪律/§7 首帧反例生效）。
4. 其余节点沿用 r6（allow_verified_reuse 核验复用语义；六员完整链保持同负荷）。
"""
import io
import json
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = "http://127.0.0.1:8000"
PROJ = "P50_B"
WID = "P51_C_W01"


def req(path, body=None, method="GET"):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data,
                               headers={"Content-Type": "application/json"},
                               method=method)
    with urllib.request.urlopen(r, timeout=60) as resp:
        return json.load(resp)


def main():
    src = json.load(open("projects/P50_B/output/workflows/definitions/P50_B_W09r6.json",
                         encoding="utf-8"))
    d = {k: v for k, v in src.items()
         if k not in ("workflowId", "name", "description", "notes", "contentHash",
                      "createdAt", "updatedAt", "revision", "status")}
    d["workflowId"] = WID
    d["name"] = "P51_C 冻结分镜复验"
    d["description"] = ("Phase51 C-T03：冻结分镜+资产复验（第 1 轮）——P50_B 交付版分镜"
                        "与 approved 资产全量复用，视频 20 镜本轮独立生成；验证修复后"
                        "引擎的可靠性与可测效率。")
    d["notes"] = ("冻结输入见 PHASE51_B_REPORT §7；Skill v1.5.0（人数纪律+首帧反例）；"
                  "n_dir_video=require_new×20（独立生成）；population_constraint 默认 on。")
    d["status"] = "draft"

    for n in d["nodes"]:
        cfg = n.get("config") or {}
        if n["id"] == "n_producer":
            cfg["objective"] = (
                "生产计划（冻结分镜复验）：按项目内已交付的《深水惊魂》冻结分镜（s01–s05 "
                "共 20 镜）与 approved 资产组织第 1 轮复验生产。资产不重新设计（资源阶段"
                "核验+定点修复）；视频全部本轮独立生成。目标成片约 60 秒，横屏 16:9 写实"
                "电影风格，无旁白/无主动配音/无对白，保留合格原生音效。")
        elif n["id"] == "n_dir_board":
            cfg["objective"] = (
                "【工作方法】开始执行前先 skill.load \"full-story-relay-calibration\" "
                "并严格遵循其制作方法（v1.5.0：注意 production-recipes §6 人数纪律、"
                "§7 首帧方法反例）；本轮不要加载其他技能。\n"
                "【任务】冻结分镜核对与拍摄记录：项目内 s01–s05 的 20 镜即冻结分镜"
                "（P50_B 交付版），本轮不改写分镜结构。逐镜完成：①用读工具核对 canonical "
                "描述/起止状态/机位/预计可见人物与分镜一致；②双人镜站位核对——每镜写明"
                "「船长在何处、大副在何处、各自做什么」（用户反馈实证大副站位错误风险，"
                "尤其 s04_c02 大副必须在船上而非礁石）；③逐镜核对已选首帧质量（含人物与"
                "场景融合度），不合格的先重建再继续；④产出每镜拍摄记录（叙事作用/主要动作"
                "与结果/起止状态/两人及怪物与船的位置/运动轴线/必要参考及版本/生成方式/"
                "预计可剪区间/关键动作不可删）。所有视频源素材至少 8 秒、默认 10 秒。")
            cfg["completion"] = {"strategy": "allow_verified_reuse",
                                 "outputs": [{"kind": "shot", "withImage": True,
                                              "minCount": 20}]}
        elif n["id"] == "n_dir_video":
            cfg["objective"] = (
                "【工作方法】开始执行前先 skill.load \"full-story-relay-calibration\" "
                "并严格遵循其制作方法（v1.5.0）；本轮不要加载其他技能。\n"
                "【任务】按核对后的冻结分镜真实生成本轮全部 20 镜的新视频候选（本轮独立"
                "生成——不把既有旧版本当作本轮产出）。逐镜方法自主决定（接力/双参考/正常"
                "切镜按分镜的接续方式字段），素材至少 8 秒、默认 10 秒。彼此独立的镜头立即"
                "连续提交（本地队列按在途上限自动排队）；以上游真实保留端点为输入的接力镜"
                "等上游完成后提交。完成后核对回执、文件真实时长与首中尾帧。人数纪律：系统"
                "默认已在生成请求注入确定性人数子句（population_constraint），你在逐镜"
                "文本中仍须写明谁在何处做什么。视频过程纪律：人物总数不得超过两人、不得"
                "出现分身；关键动作过程完整；怪物与船保持同一实体与合理尺度。")
            cfg["completion"] = {"strategy": "require_new",
                                 "outputs": [{"kind": "video_version", "playable": True,
                                              "minCount": 20}]}
        n["config"] = cfg
        if n.get("type") == "agent" and "objective" not in cfg:
            raise SystemExit(f"agent node {n['id']} missing objective")
    # skill 绑定版本 → 1.5.0
    for e in d["edges"]:
        if e.get("type") == "skill_binding":
            e["skillVersion"] = "1.5.0"

    out = req(f"/api/project/{PROJ}/workflow/definitions/{WID}", {"definition": d}, "PUT")
    print("saved:", out["workflowId"], "rev", out["revision"],
          "validation ok:", out["validation"]["ok"])
    if not out["validation"]["ok"]:
        print("ERRORS:", json.dumps(out["validation"]["errors"], ensure_ascii=False)[:800])
        return
    v = req(f"/api/project/{PROJ}/workflow/definitions/{WID}/validate", {}, "POST")
    print("validate:", v.get("ok"), json.dumps(v.get("errors", []), ensure_ascii=False)[:400])


if __name__ == "__main__":
    main()
