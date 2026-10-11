# 协作流程 v2：派生 P51_F_W01（明天测试用）——前置 4 道审核卡 autoApprove
# （资产冻结复用场景自动放行），视频终审/选用/成片三道保留人工。
import json, sys, io
sys.path.insert(0, '.')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from pathlib import Path

AUTO_NODES = {'n_rv_sw', 'n_rv_cd', 'n_rv_sc', 'n_rv_board'}
src = json.load(open('projects/P50_B/output/workflows/definitions/P51_E_W01.json',
                     encoding='utf-8'))
d = json.loads(json.dumps(src))
d['workflowId'] = 'P51_F_W01'
d['name'] = 'P51-F 协作流程 v2 测试（前置卡自动放行）'
d['description'] = ('协作流程 v2：n_rv_sw/n_rv_cd/n_rv_sc/n_rv_board 四道前置卡'
                    ' autoApprove 自动放行（资产冻结复用）；视频一次终审+修复 3 次豁免闸；'
                    '审核三查判据（Skill v1.6.0）。')
for k in ('revision', 'updatedAt', 'createdAt', 'contentHash', 'status'):
    d.pop(k, None)
for n in d['nodes']:
    if n['id'] in AUTO_NODES:
        n['config']['autoApprove'] = True
out = Path('projects/P50_B/output/workflows/definitions/P51_F_W01.json')
out.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')
print('saved', out)
print('autoApprove nodes:', [n['id'] for n in d['nodes'] if (n.get('config') or {}).get('autoApprove')])
