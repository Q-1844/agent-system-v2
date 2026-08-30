# Tavily Hub（tavily.sharyuke.com）国内链路 Probe 报告

日期：2026-08-30　端点：`POST https://tavily.sharyuke.com/api/proxy/search`（Bearer TAVILY_HUB_API_KEY）
固定参数：`max_results=3, include_answer=false, include_raw_content=false, include_images=false`；advanced 另带 `chunks_per_source=2`。
原始数据：`scripts/tavily_hub_probe_report.json`（pass1 延迟）、`scripts/tavily_hub_probe3_report.json`（pass3 质量明细）。

## 结论（闸门：通过，选择 basic）

| 指标 | basic | advanced |
|---|---|---|
| HTTP success | 14/14 = 100%（pass1） | 6/6 = 100%（pass1） |
| P50 | 1868ms（pass1）/ 1356ms（pass3） | 4187ms |
| P95 | 6403ms（pass1） | 4764ms |
| max | 6403ms | 4764ms |
| timeout/429/5xx | 0 | 0 |
| results 数 | 3/3 条（pass3 全新 query 5/5 满额） | — |

**选择 basic**：延迟约 advanced 的 1/3~1/2，snippet 已含可创作细节（建筑结构/价格数据/程序条文），符合"轻量受控"第一版定位。

## 中文质量抽样（pass3，全部高相关、权威中文来源）

- 中国空间站航天员作息 → 网信办/BBC中文/国防部（16 次日出日落、失重防护锻炼）
- 上海石库门结构格局 → 澎湃/文物报/维基（砖木结构、老虎窗、水磨石内墙）
- 民国上海黄包车夫收入 → 30 年代调查数据、2 公里 0.2 元价目
- 傣族泼水节 → 中国非遗网/维基（缅桂树枝蘸水、银碗点肩）
- 派出所报案流程 → 受案登记表/回执、《公安机关办理行政案件程序规定》条文

域名池：baike.baidu.com、cac.gov.cn、mod.gov.cn、nia.gov.cn、ihchina.cn、thepaper.cn、163.com、bbc.com、zh.wikipedia.org、zhuanlan.zhihu.com 等。

## 重要发现

1. **响应双层包装**：`{"code":0,"message":"success","data":{"data":{…Tavily 标准体…}}}`。Probe 前两轮"全空结果"是解包 bug 假象（`"results" in data["data"]` 在第一层恒假），修复后链路正常——这一坑已写进 Provider 的解包规则与单测。
2. **Hub 对完全相同 query 返回空结果**（108ms 秒回、疑似缓存去重缺陷）：同一 query 第二次搜索得 0 条。Runtime 侧无需处理（web.search 语义下 Agent 本应换词），但 zero_results obs 的 note 已提示 Agent 换更聚焦的查询。
3. **Credit 机制实测**：额度耗尽时 Hub 返回 HTTP 200 + `{"code":42901,"message":"Credit 不足，请等待恢复或升级套餐","data":null}`（非 4xx/5xx）。Provider 已映射为 `quota_exhausted` 并透出原始 message；Runtime 侧表现为结构化 tool error，Agent 优雅降级（真实验收 E/G 轮验证）。
4. Credit Pool=30、Recovery=5/h 与实测一致（当日 probe+验收共耗 ~30，出现 42901）。
