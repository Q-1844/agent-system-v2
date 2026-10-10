# Phase51-E：派生 P51_E_W01 definition（基于 P51_C_W01）
# 变更：workflowId / n_dir_video.objective（11 镜清单+批量提交纪律）/ minCount 20→11
import json, sys, io
sys.path.insert(0, '.')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from pathlib import Path

SHOTS_11 = ['s01_c01', 's01_c02', 's01_c03', 's01_c04', 's01_c05',
            's02_c01', 's02_c02', 's02_c03', 's02_c04', 's04_c04', 's05_c03']

src = json.load(open('projects/P50_B/output/workflows/definitions/P51_C_W01.json',
                     encoding='utf-8'))
d = json.loads(json.dumps(src))  # deep copy
d['workflowId'] = 'P51_E_W01'
d['name'] = 'P51-E 效率验证+修复轮（11 镜重生成）'
d['description'] = ('Phase51-E：E1（s04_c04/s05_c03 首帧重建修复）+E2（9 镜效率主测试）。'
                    '启动级生成范围闸只放行 11 镜；n_dir_video require_new×11。')
for k in ('revision', 'updatedAt', 'createdAt', 'contentHash', 'status'):
    d.pop(k, None)

for n in d['nodes']:
    if n['id'] == 'n_dir_video':
        n['config']['objective'] = (
            '【工作方法】开始执行前先 skill.load "full-story-relay-calibration" 并严格遵循其制作方法'
            '（v1.5.0）；本轮不要加载其他技能。\n'
            '【任务】对指定 11 镜真实生成本轮新视频候选（本轮独立生成——不把既有旧版本当作本轮产出）。'
            '镜头清单（仅此 11 镜；服务端生成范围闸只放行清单内镜头，清单外提交会被预检拒绝）：'
            's01_c01、s01_c02、s01_c03、s01_c04、s01_c05、s02_c01、s02_c02、s02_c03、s02_c04、'
            's04_c04、s05_c03。'
            '其中 s04_c04/s05_c03 为首帧重建修复镜（新首帧已重建并选用：s04_c04=船尾回望触须浅水受阻、'
            's05_c03=开阔海面驶向港口灯；按新首帧+分镜描述正常生成，叙事不得虚构沙滩/码头/到港）；'
            's01_c01–s02_c04 为既有分镜重生成（首帧沿用当前选用，接力镜按接续方式执行）。'
            '逐镜方法自主决定（接力/双参考/正常切镜按分镜的接续方式字段），素材至少 8 秒、默认 10 秒。'
            '彼此独立的镜头立即连续提交（本地队列按在途上限自动排队），'
            '不要"提交一镜→等结算→再提交下一镜"；以上游真实保留端点为输入的接力镜等上游完成后提交。'
            '完成后核对回执、文件真实时长与首中尾帧。'
            '人数纪律：系统默认已在生成请求注入确定性人数子句（population_constraint），'
            '你在逐镜文本中仍须写明谁在何处做什么。'
            '视频过程纪律：人物总数不得超过两人、不得出现分身；关键动作过程完整；'
            '怪物与船保持同一实体与合理尺度。')
        comp = n['config']['completion']
        comp['outputs'][0]['minCount'] = 11

out = Path('projects/P50_B/output/workflows/definitions/P51_E_W01.json')
out.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')
print('saved', out)
print('minCount =', [n['config']['completion']['outputs'][0]['minCount']
                     for n in d['nodes'] if n['id'] == 'n_dir_video'])
print('objective len =', len([n for n in d['nodes'] if n['id'] == 'n_dir_video'][0]['config']['objective']))
