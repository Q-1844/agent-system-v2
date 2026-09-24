# STORY_FROZEN_38.md — 《狐狸进村》冻结预编剧（38.md §5 T2 第二轮完整故事）

日期：2026-09-25 冻结 ｜ 项目：Fable_Pilot_Fox_and_Grapes_V1
结构：9 镜 × 2 段（s11 雨林小径·晴晨 / s12 村庄村口·正午起）。**本文档与 `_38_story.py` 一字一致，生成开始前冻结，未改一字。**
链根：`ref_d27e1a01c36f`（2D 雨林 walk_side，沿用第一轮三重锚定链根——风格/身份锚不换）。
核心测试点（38.md T1 打点全部编入 + 新增跨 Location 接力）：

| 打点 | 对应镜 |
|---|---|
| **跨 Location 尾帧接力（新增，本轮主测）** | M3→M4：雨林边缘尾帧 → 乡村村口首帧 |
| 多体接触精度（落在具体部位） | M7（落叶停鼻尖） |
| 高运动后收势（吸引子高发） | M2（冲刺落地不停）、M4（急停刹住）、M7（扑空落地僵住） |
| 道具交互 + 道具链继承监控 | M5/M6（南瓜顶滚；观察南瓜是否被参考链带进后续镜） |
| 光照连续性（多镜渐进 日→暮） | M1-M3 晴晨 → M4-M6 正午 → M8 午后暖金 → M9 暮色 |
| 负面约束（unexpected_elements） | 全片无其他动物/人（M1 公鸡画外音只有声音；村庄空无一人） |
| 时长配方变量 | 5s×2 / 8s×4 / 10s×3 |

---

## 段 C（s11）雨林小径 · 晴晨

清晨，雨林雾气散尽，阳光比往常更亮。小狐狸沿小径晨跑时忽然听见远处传来
公鸡打鸣声（只有声音，画面中没有公鸡）——它从没听过这种声音，耳朵竖起，
朝着声音的方向越跑越快，穿过树干与蕨叶，跃过倒伏的树干，一路冲向小径
尽头越来越亮的光。树木渐稀，它在雨林边缘的阴影里刹住脚步，眯眼打量
外面刺眼的明亮空旷，前爪还踩在落叶上，迟疑又好奇。

## 段 D（s12）村庄村口 · 正午→暮

小径尽头连着一个安静的小村：土路、木栅栏、路边的南瓜地、干草垛和几座
茅草顶小屋，正午阳光把一切照得暖烘烘，村子里空无一人。狐狸深吸一口气
从雨林边缘一跃而出，冲进村口土路急停，回头看一眼来路的树林，才转回头
打量这个陌生世界。它压低身子挪到一只橙色大南瓜旁低头嗅闻，伸鼻一顶——
南瓜晃晃悠悠滚出半圈，吓得它弹开弓背。一阵风卷起金黄落叶绕它打转，它
追着落叶转圈跳跃、扑空落地，一片落叶悠悠飘下恰好停在它鼻尖上，它僵住、
眼睛斜向下瞪着鼻尖，忍不住打了个小喷嚏。午后暖光里它甩甩头坐下来舔爪子。
暮色降临，影子拉长，狐狸沿土路小跑回雨林边缘，在树影前停步回头望了一眼
安静的村庄。

---

## 逐镜预编剧（description/motion 为生成字段；expected_tail/story_type/duration_s 为校准元数据）

### M1 = s11_c01 ｜ 8s ｜ dynamic_establishing
- description：清晨放晴的雨林小径，阳光比清晨雾天更亮。狐狸沿小径向画面深处轻快奔跑，忽然远处传来公鸡打鸣声，它耳朵猛地竖起，脚步加快，穿行于树干与蕨叶之间，镜头侧面跟拍
- motion：Fox trots along the sunlit rainforest trail away from camera; a distant rooster crow makes its ears prick up and it quickens its pace, weaving between trunks and ferns; bright morning light, mist gone
- camera_angle: side tracking ｜ camera_movement: steady side tracking matching pace ｜ composition: 全景侧面跟拍，小径纵深，光斑比第一轮更亮更清晰
- expected_tail：狐狸在小径中段加速奔跑，身体朝向画面深处，耳朵前竖，四足离地或落地皆可
- 备注：链根参考直生（video_reference_asset_id=链根）；公鸡只闻其声不见其形（负面约束）

### M2 = s11_c02 ｜ 10s ｜ high_motion
- description：狐狸全力冲刺，纵身跃过倒伏树干，落地不停继续向前，冲向小径尽头越来越亮的开口，镜头从背后低机位跟随
- motion：Fox sprints at full speed, leaps over the fallen trunk, lands without stopping and keeps running toward the brightening opening at the end of the trail; low tracking camera behind
- action_sequence：sprint -> leap over fallen trunk -> land without stopping -> keep running toward the bright opening
- camera_angle: low angle from behind ｜ camera_movement: tracking behind, keeping pace
- expected_tail：狐狸落地后仍在向深处奔跑，背对镜头，前方树影渐稀透亮

### M3 = s11_c03 ｜ 8s ｜ environment_change
- description：小径尽头树木渐稀，前方透进刺眼的明亮。狐狸在雨林边缘的阴影里减速停步，眯起眼睛打量外面的亮光，前爪还踩在落叶上，尾巴微微摆动，迟疑又好奇
- motion：Trees thin out at the end of the trail with glaring brightness beyond; the fox slows to a stop inside the shadow of the forest edge, squints at the bright outside, front paws still on fallen leaves, tail swaying slightly, hesitant and curious
- camera_angle: eye level ｜ camera_movement: static after the fox settles ｜ motion_pace: decelerating to stillness
- expected_tail：狐狸站在雨林边缘树影里，面朝画面深处的明亮外界，姿态停定
- 备注：本镜尾帧是跨 Location 接力的出发状态

### M4 = s12_c01 ｜ 10s ｜ cross_location_transition（本轮主测点）
- description：狐狸深吸一口气，从雨林边缘一跃而出，冲进正午阳光下的小村村口：脚下变成土路，两侧是木栅栏，路边一片橙色南瓜地，远处干草垛和茅草顶小屋。它冲过村口土路猛地急停，爪下扬起一小片尘土，回头看一眼来路的树林，再转回头打量村庄，村子空无一人
- motion：The fox takes a breath and bursts out of the forest edge into a sunlit village outskirts at midday: dirt road underfoot, wooden fences on both sides, an orange pumpkin patch by the road, haystacks and thatched cottages beyond; it dashes along the road and skids to a sudden stop, dust puffing up, glances back at the treeline it came from, then turns to take in the empty village
- action_sequence：burst out of treeline into sunlight -> dash along dirt road -> skid to a stop, dust puff -> glance back at the forest -> turn head forward and survey the village
- camera_angle: wide establishing settling to eye level ｜ camera_movement: brief tracking on the dash, settling as the fox skids to a stop
- composition：远景开场收到中景，土路纵深，雨林树影在画面一缘，栅栏与南瓜地在另一侧
- expected_tail：狐狸站在村口土路中央，身后是雨林边缘树影，面前是木栅栏与南瓜地，正午阳光，尘土未定
- 备注：首镜跨 Location 接力（prev_tail 来自雨林 M3 尾帧）；监控森林背景是否渗入、村庄 identity 是否被 relay 压住、急停后是否触发特写吸引子

### M5 = s12_c02 ｜ 5s ｜ static_reaction
- description：狐狸压低身子，沿着木栅栏小心挪近南瓜地，在一只有它半个身子大的橙色大南瓜旁停下，伸头低头嗅闻，尾巴平伸保持平衡
- motion：The fox creeps low along the wooden fence toward the pumpkin patch, stops beside a big orange pumpkin half its size, lowers its head to sniff it, tail stretched out for balance
- camera_angle: low angle, ground level ｜ camera_movement: slow creep track, then static
- expected_tail：狐狸在大南瓜旁低头嗅闻，姿态压低，尾巴平伸

### M6 = s12_c03 ｜ 8s ｜ prop_interaction
- description：狐狸伸鼻顶了顶南瓜，南瓜晃了晃、滚出小半圈停住。狐狸吓得弹开半步，弓背压低盯住南瓜，尾巴炸起，南瓜旁扬起一点尘土
- motion：The fox nudges the pumpkin with its nose; the pumpkin wobbles and rolls a half turn before stopping; the fox springs back half a step, back arched low, staring at it, tail bristled
- camera_angle: eye level ｜ camera_movement: static
- expected_tail：狐狸弓身后退半步盯着静止的南瓜，尾巴垂落，尘土微扬
- 备注：监控道具继承效应（南瓜是否被参考链带进 M7-M9）

### M7 = s12_c04 ｜ 10s ｜ high_motion + multi_body_contact（打点）
- description：一阵风卷起几片金黄落叶绕着狐狸打转，狐狸兴奋地追着落叶原地转圈跳跃，最后一跃扑空落地，落叶悠悠飘散，其中一片恰好落在它的鼻尖上。狐狸猛地僵住，眼睛斜向下盯着鼻尖上的叶子，一动不动
- motion：A gust of wind swirls golden leaves around the fox; it excitedly chases them in a spinning leap, misses on the last jump and lands; one leaf drifts down and settles right on its nose tip; the fox freezes, eyes crossing down at the leaf on its nose, motionless
- action_sequence：wind swirls leaves -> fox chases spinning and leaping -> final leap misses -> lands -> a leaf settles on its nose tip -> freeze, eyes down at the leaf
- camera_angle: eye level, arcing slightly with the spin ｜ camera_movement: follow the spin, then static hold on the frozen pose
- expected_tail：狐狸四足站定，一片黄叶停在鼻尖上，眼睛斜向下看，姿态僵住
- 备注：多体接触精度打点（第一轮实证：接触面易滑到邻近物体）

### M8 = s12_c05 ｜ 5s ｜ static_comedy
- description：狐狸打了个小喷嚏，鼻尖的叶子飘落。它甩甩头，端坐下来舔了舔前爪，午后暖光把它的毛染成暖金色，村子安安静静
- motion：The fox sneezes softly and the leaf flutters down; it shakes its head, sits upright and licks a front paw; warm afternoon light gilds its fur, the village quiet around
- camera_angle: eye level ｜ camera_movement: static
- expected_tail：狐狸端坐在土路上，尾巴环过前爪，神态放松，午后暖光

### M9 = s12_c06 ｜ 8s ｜ static_final
- description：暮色降临，影子拉长。狐狸沿土路小跑回雨林边缘，在树影前停步，回头望了一眼沉入黄昏的安静村庄，剪影小小的停在明暗交界处
- motion：Dusk settles and shadows stretch; the fox trots back along the dirt road toward the forest edge, stops at the treeline shadow and looks back once at the quiet village sinking into dusk, a small silhouette at the light boundary
- camera_angle: high wide angle ｜ camera_movement: static wide, hold
- composition：大远景，土路纵深，狐狸小剪影停在雨林边缘树影前，村庄在身后
- expected_tail：暮色大远景：狐狸小剪影停在雨林边缘树影前，身后村庄沉入黄昏
- 备注：收束镜；光照渐进终点（正午→午后→暮）
