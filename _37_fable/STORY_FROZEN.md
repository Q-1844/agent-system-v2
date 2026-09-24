# 《雨林奇遇》完整故事校准战役 — Phase 37A 第一组（37.md §4）

日期：2026-09-24 ｜ 项目：Fable_Pilot_Fox_and_Grapes_V1（37.md §15 长期 Calibration Project）
状态：**生成开始前冻结**（story_frozen=true；只能经 §5 校准协议修正后续镜头，
不得根据生成结果偷偷重写原始故事）

## 故事全文（起承转合）

> 清晨的雨林覆盖着薄雾。小狐狸沿雾中小径晨跑（起）。它在倒伏树干后发现一朵
> 巨型发光蘑菇，好奇触碰，蘑菇亮起、洒出光尘（承）。光尘引来一群萤火虫，
> 狐狸兴奋追逐，跃过倒木，穿过雨林到达一处豁然开朗的空地水塘（转）。水塘
> 惊起一只蓝闪蝶，绕它一圈后停在它的尾巴尖上；狐狸僵住、慢慢回头看这位
> 小客人。蝴蝶最终飞落在塘边红花上，狐狸趴在石上安睡，暮色里萤火虫三两
> 点缀（合）。

## 结构：2 个正式故事段（37.md §9 语义边界）

- **段 A《晨雾小径》** = scene `s09`（4 镜 N1-N4）— Location `loc_04970479321e`，view_70e07a5c7211（trail main），状态=晨雾白昼
- **段 B《空地水塘》** = scene `s10`（5 镜 N5-N9）— 同 Location，新 LocationView（clearing pond），状态=午后暖光

跨段接力：s10_c01 以 prev_tail 接 s09_c04 末帧（展平顺序，34.md V1C 已实证跨段衔接）。

## 镜头表（预编剧冻结）

| # | shot | 时长 | 类型 | description（中文，校准载体） | motion（英文，编译承载） | 预期尾帧 expected_tail |
|---|------|------|------|------------------------------|--------------------------|------------------------|
| N1 | s09_c01 | 8s | 动态建立 | 晨雾弥漫的热带雨林小径，狐狸从画面左侧入画，沿小径向右侧轻快奔跑，穿行于树干与蕨叶之间，晨光斜射穿过雾气，镜头侧面跟拍 | Fox trots into frame from the left along a misty rainforest trail, weaving between trunks and ferns; side tracking camera follows; morning light shafts through mist | 狐狸在小径中段减速站定，四足着地，身体朝向画面右侧，耳朵竖起 |
| N2 | s09_c02 | 10s | 道具交互+转向 | 狐狸忽然停下，发现前方倒伏树干后一朵巨型发光蘑菇（蓝绿色荧光）。它绕过倒木走近，低头伸鼻尖轻触蘑菇帽——蘑菇被碰后亮起，洒出一圈细小光尘 | Fox stops, notices a giant glowing mushroom behind a fallen trunk, circles around it, leans in and touches the cap with its nose; the mushroom brightens and releases a ring of tiny light motes | 狐狸鼻尖仍贴近蘑菇帽，蘑菇亮起，光尘悬浮在两者之间 |
| N3 | s09_c03 | 5s | 静态反应 | 光尘缓缓飘散，狐狸后退半步蹲坐下来，歪着头打量发光的蘑菇，尾巴环过前爪 | Light motes drift away; the fox sits back on its haunches, head tilted, studying the glowing mushroom, tail curled around its front paws | 狐狸坐姿稳定，面朝右侧蘑菇，头微歪 |
| N4 | s09_c04 | 10s | 高运动+收势 | 蘑菇光尘中腾起一群萤火虫。狐狸兴奋起身，追着萤火虫群沿小径向画面深处奔跑，加速后纵身跃过倒伏树干，落地后减速站稳，背对镜头，萤火虫在前方林间盘旋 | A swarm of fireflies rises from the light motes; the fox springs up and chases them down the trail away from camera, accelerating, leaps over the fallen trunk, lands and slows to a stand; fireflies swirl ahead | 狐狸从倒木落地后减速站稳，背对镜头面向深处，萤火虫在前方盘旋 |
| N5 | s10_c01 | 8s | 环境变化 | 雨林豁然开朗：小径尽头是一片空地，中央一汪浅水塘，午后暖光斜照水面。狐狸走出小径来到塘边，低头喝水，水面映出倒影 | The trail opens into a clearing with a shallow pond; the fox steps out, walks to the pond edge and lowers its head to drink, reflection on the water | 狐狸立在水塘边缘低头饮水，水面有倒影，身后是雨林 |
| N6 | s10_c02 | 8s | 惊变+状态转换 | 对岸蕨丛忽然晃动。狐狸惊得抬头后退一步，前爪踩进浅水，溅起一圈水花，耳朵压后，紧盯蕨丛 | The ferns across the pond rustle; the fox startles, rears back a step, front paws splashing into shallow water, ears pinned, staring at the ferns | 狐狸站在浅水中抬头盯向对岸蕨丛，水花尚未落尽 |
| N7 | s10_c03 | 10s | 复杂交互 | 一只蓝闪蝶从蕨丛飞出，绕狐狸头顶一圈，轻轻落在它翘起的尾巴尖上。狐狸僵住，眼睛慢慢斜向下看尾巴，随后小步转身回头，鼻尖凑近尾巴尖上的蝴蝶，一动不动 | A blue morpho butterfly flies out, circles the fox once, and lands on its raised tail tip; the fox freezes, eyes sliding down, then turns its head back slowly toward the butterfly, nose close, motionless | 狐狸回头面向尾尖，蝴蝶停在尾尖毛上，姿态静止 |
| N8 | s10_c04 | 8s | 静态互动收势 | 蝴蝶腾起，缓缓落在塘边一朵红花上，翅膀开合。狐狸小心翼翼绕到花旁，趴卧在旁边的平石上，下巴搁在前爪上，安静地看着蝴蝶 | The butterfly lifts and settles on a red flower by the pond; the fox carefully circles close and lies down on a flat stone, chin on paws, watching it quietly | 狐狸趴在石上，蝴蝶停在红花上，两者相对，画面安稳 |
| N9 | s10_c05 | 5s | 静态远景收势 | 暮色降临，空地全景：狐狸蜷在石上闭眼入睡，尾巴盖住鼻尖，萤火虫三两点缀林间，镜头缓缓拉远定格 | Dusk settles; wide view of the clearing, the fox curled asleep on the stone, tail over its nose, a few fireflies dotting the trees; the camera slowly pulls back and holds | 大远景：狐狸安睡石上，塘面映暮色，林间萤光点点 |

合计 72s 视频（Token Plan 500s/日配额充裕）。

## §4 要求覆盖对照

- 起承转合 ✓（N1 起 / N2-N3 承 / N4-N6 转 / N7-N9 合）
- 静态+动态 ✓（静 N3/N8/N9；动 N1/N2/N4/N5/N6/N7）
- 环境变化 ✓（段边界小径→空地水塘 + N9 暮色）
- 人物方向变化 ✓（N2 绕行转向 / N4 背对镜头 / N7 回头）
- 道具/环境交互 ✓（N2 触蘑菇发光 / N6 踩水溅花）
- 高运动 ✓（N4 追逐跳跃）
- 稳定收势 ✓（N4 落地收势 / N9 静态收尾）
- 易偏离状态转换 ✓（N2 接触发光 / N4 落地点 / N6 惊跳 / N7 蝴蝶落尾）
- expected tail 每镜预写 ✓ ｜ 时长变量 5/8/10 ✓

## 链根与参考（§8 三重锚定）

- chain_root_reference_id = `ref_d27e1a01c36f`（2D 雨林 walk_side 变体，已批准；
  identity/costume/style/环境四维过审，36.md §1.2.1）
- N1 = 链根参考直生（video_reference_asset_id=ref_d27e1a01c36f）
- N2-N9 = prev_tail 尾帧接力（video_reference_asset_id='prev_tail'）
- 身份 look = look_2c6a01d45db5（狐狸，approved）
