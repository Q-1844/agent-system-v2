# _37_fable/_38_story.py — 《狐狸进村》冻结预编剧（38.md §5 T2 第二轮；与 STORY_FROZEN_38.md 一致）。
# 唯一事实源：生成开始前冻结；修正只能走 38.md §2 校准协议（下镜过渡/重试/keyframes 桥）。
# 段 C=s11（雨林小径·晴晨，复用雨林 Location+trail view）；段 D=s12（村庄村口，新建乡村 Location）。
CHAIN_ROOT_REF = "ref_d27e1a01c36f"      # 2D 雨林 walk_side（沿用第一轮链根）
FOX_LOOK = "look_2c6a01d45db5"           # 狐狸 approved look
FOREST_LOCATION_ID = "loc_04970479321e"  # 雨林 Location（复用）
TRAIL_VIEW = "view_70e07a5c7211"         # 已批准 trail main base view（复用）

SEGMENTS = [
    {
        "key": "C", "title": "雨林小径·晴晨", "scene_hint": None,
        "summary": "清晨雾散的雨林，狐狸沿小径晨跑时听见远处公鸡打鸣声，耳朵竖起越跑越快，"
                   "跃过倒木冲向小径尽头越来越亮的光，在雨林边缘的阴影里刹住，眯眼打量"
                   "外面刺眼的明亮空旷。",
        "state": {"time_of_day": "early morning", "weather": "clear, mist gone",
                  "lighting": "Bright slanting morning light through the canopy, "
                              "crisp green shadows, sun higher than dawn",
                  "environment_conditions": "Dry trail, crisp air, a few dry leaves "
                                            "drifting between trunks"},
    },
    {
        "key": "D", "title": "村庄村口", "scene_hint": None,
        "summary": "小径尽头连着一个空无一人的安静小村：土路、木栅栏、南瓜地、干草垛与茅草顶"
                   "小屋。狐狸从雨林边缘跃出冲进村口急停；它嗅闻大南瓜、把南瓜顶得打滚吓得"
                   "弹开；追着风卷的落叶转圈跳跃扑空，一片落叶恰好落在鼻尖上；打完喷嚏坐下来"
                   "舔爪子；暮色降临时沿土路跑回雨林边缘，回头望了一眼安静的村庄。",
        "state": {"time_of_day": "midday", "weather": "clear",
                  "lighting": "Bright warm midday sun, short shadows, warm ochre and "
                              "wood tones on fences and dirt road",
                  "environment_conditions": "Dry dirt road, warm still air, faint warm "
                                            "breeze stirring dust and dry leaves"},
    },
]

# 每镜字段（update_shot_fields 白名单内）。expected_tail 与 story_type 是
# 校准元数据（不进生成字段，进 Calibration Trace）。
SHOTS = [
    {"shot": "M1", "segment": "C",
     "fields": {
         "description": "清晨放晴的雨林小径，阳光比清晨雾天更亮。狐狸沿小径向画面深处轻快奔跑，"
                        "忽然远处传来公鸡打鸣声，它耳朵猛地竖起，脚步加快，穿行于树干与蕨叶之间，"
                        "镜头侧面跟拍",
         "motion": "Fox trots along the sunlit rainforest trail away from camera; a distant "
                   "rooster crow makes its ears prick up and it quickens its pace, weaving "
                   "between trunks and ferns; bright morning light, mist gone",
         "camera_angle": "side tracking",
         "camera_movement": "steady side tracking matching the fox pace",
         "composition": "全景侧面跟拍，小径纵深，树干蕨叶层次，光斑清晰明亮",
         "environment_motion": "bright light patches shifting, a few dry leaves drifting",
         "motion_pace": "brisk, quickening",
         "target_duration_s": 8.0,
         "video_reference_asset_id": CHAIN_ROOT_REF,
     },
     "expected_tail": "狐狸在小径中段加速奔跑，身体朝向画面深处，耳朵前竖，四足离地或落地皆可",
     "story_type": "dynamic_establishing", "duration_s": 8.0},

    {"shot": "M2", "segment": "C",
     "fields": {
         "description": "狐狸全力冲刺，纵身跃过倒伏树干，落地不停继续向前，冲向小径尽头越来越亮"
                        "的开口，镜头从背后低机位跟随",
         "motion": "Fox sprints at full speed, leaps over the fallen trunk, lands without "
                   "stopping and keeps running toward the brightening opening at the end of "
                   "the trail",
         "action_sequence": "sprint -> leap over the fallen trunk -> land without stopping -> "
                            "keep running toward the bright opening",
         "camera_angle": "low angle from behind",
         "camera_movement": "tracking behind the fox, keeping pace",
         "composition": "中景背面跟随，小径纵深，倒木为跳跃障碍，尽头透亮",
         "motion_pace": "explosive, sustained",
         "target_duration_s": 10.0,
         "video_reference_asset_id": "prev_tail",
     },
     "expected_tail": "狐狸落地后仍在向深处奔跑，背对镜头，前方树影渐稀透亮",
     "story_type": "high_motion", "duration_s": 10.0},

    {"shot": "M3", "segment": "C",
     "fields": {
         "description": "小径尽头树木渐稀，前方透进刺眼的明亮。狐狸在雨林边缘的阴影里减速停步，"
                        "眯起眼睛打量外面的亮光，前爪还踩在落叶上，尾巴微微摆动，迟疑又好奇",
         "motion": "Trees thin out at the end of the trail with glaring brightness beyond; "
                   "the fox slows to a stop inside the shadow of the forest edge, squints at "
                   "the bright outside, front paws still on fallen leaves, tail swaying "
                   "slightly, hesitant and curious",
         "camera_angle": "eye level",
         "camera_movement": "static after the fox settles",
         "composition": "中景，狐狸在前景树影中面朝深处亮部，明暗对比",
         "motion_pace": "decelerating to stillness",
         "target_duration_s": 8.0,
         "video_reference_asset_id": "prev_tail",
     },
     "expected_tail": "狐狸站在雨林边缘树影里，面朝画面深处的明亮外界，姿态停定",
     "story_type": "environment_change", "duration_s": 8.0},

    {"shot": "M4", "segment": "D",
     "fields": {
         "description": "狐狸深吸一口气，从雨林边缘一跃而出，冲进正午阳光下的小村村口：脚下变成"
                        "土路，两侧是木栅栏，路边一片橙色南瓜地，远处干草垛和茅草顶小屋。它冲过"
                        "村口土路猛地急停，爪下扬起一小片尘土，回头看一眼来路的树林，再转回头"
                        "打量村庄，村子空无一人",
         "motion": "The fox takes a breath and bursts out of the forest edge into a sunlit "
                   "village outskirts at midday: dirt road underfoot, wooden fences on both "
                   "sides, an orange pumpkin patch by the road, haystacks and thatched cottages "
                   "beyond; it dashes along the road and skids to a sudden stop, dust puffing "
                   "up, glances back at the treeline it came from, then turns to take in the "
                   "empty village",
         "action_sequence": "burst out of the treeline into sunlight -> dash along the dirt "
                            "road -> skid to a sudden stop with a puff of dust -> glance back "
                            "at the forest -> turn head forward and survey the village",
         "camera_angle": "wide establishing, settling to eye level",
         "camera_movement": "brief tracking on the dash, settling as the fox skids to a stop",
         "composition": "远景开场收到中景，土路纵深，雨林树影在画面一缘，栅栏与南瓜地在另一侧",
         "environment_motion": "dust puff settling at the skid, warm still air",
         "motion_pace": "explosive start, hard stop",
         "target_duration_s": 10.0,
         "video_reference_asset_id": "prev_tail",
     },
     "expected_tail": "狐狸站在村口土路中央，身后是雨林边缘树影，面前是木栅栏与南瓜地，"
                      "正午阳光，尘土未定",
     "story_type": "cross_location_transition", "duration_s": 10.0},

    {"shot": "M5", "segment": "D",
     "fields": {
         "description": "狐狸压低身子，沿着木栅栏小心挪近南瓜地，在一只有它半个身子大的橙色大南瓜"
                        "旁停下，伸头低头嗅闻，尾巴平伸保持平衡",
         "motion": "The fox creeps low along the wooden fence toward the pumpkin patch, stops "
                   "beside a big orange pumpkin half its size, lowers its head to sniff it, "
                   "tail stretched out for balance",
         "camera_angle": "low angle, ground level",
         "camera_movement": "slow creep track, then static",
         "composition": "中景低机位，狐狸与大南瓜同框，栅栏为背景",
         "motion_pace": "slow, careful",
         "target_duration_s": 5.0,
         "video_reference_asset_id": "prev_tail",
     },
     "expected_tail": "狐狸在大南瓜旁低头嗅闻，姿态压低，尾巴平伸",
     "story_type": "static_reaction", "duration_s": 5.0},

    {"shot": "M6", "segment": "D",
     "fields": {
         "description": "狐狸伸鼻顶了顶南瓜，南瓜晃了晃、滚出小半圈停住。狐狸吓得弹开半步，"
                        "弓背压低盯住南瓜，尾巴炸起，南瓜旁扬起一点尘土",
         "motion": "The fox nudges the pumpkin with its nose; the pumpkin wobbles and rolls "
                   "a half turn before stopping; the fox springs back half a step, back "
                   "arched low, staring at it, tail bristled",
         "action_sequence": "nose nudges the pumpkin -> pumpkin wobbles and rolls a half turn "
                            "-> fox springs back half a step -> back arched, staring, tail "
                            "bristled -> pumpkin stops",
         "camera_angle": "eye level",
         "camera_movement": "static",
         "composition": "中景，狐狸弓背与大南瓜相对",
         "motion_pace": "sudden burst then freeze",
         "target_duration_s": 8.0,
         "video_reference_asset_id": "prev_tail",
     },
     "expected_tail": "狐狸弓身后退半步盯着静止的南瓜，尾巴垂落，尘土微扬",
     "story_type": "prop_interaction", "duration_s": 8.0},

    {"shot": "M7", "segment": "D",
     "fields": {
         "description": "一阵风卷起几片金黄落叶绕着狐狸打转，狐狸兴奋地追着落叶原地转圈跳跃，"
                        "最后一跃扑空落地，落叶悠悠飘散，其中一片恰好落在它的鼻尖上。狐狸猛地"
                        "僵住，眼睛斜向下盯着鼻尖上的叶子，一动不动",
         "motion": "A gust of wind swirls golden leaves around the fox; it excitedly chases "
                   "them in a spinning leap, misses on the last jump and lands; one leaf "
                   "drifts down and settles right on its nose tip; the fox freezes, eyes "
                   "crossing down at the leaf on its nose, motionless",
         "action_sequence": "wind swirls golden leaves -> fox chases spinning and leaping -> "
                            "final leap misses -> lands -> one leaf settles on its nose tip -> "
                            "freeze, eyes down at the leaf",
         "camera_angle": "eye level, arcing slightly with the spin",
         "camera_movement": "follow the spin, then static hold on the frozen pose",
         "composition": "中景，狐狸居中，落叶纷飞，尾帧一片叶停在鼻尖",
         "motion_pace": "playful burst, held stillness",
         "target_duration_s": 10.0,
         "video_reference_asset_id": "prev_tail",
     },
     "expected_tail": "狐狸四足站定，一片黄叶停在鼻尖上，眼睛斜向下看，姿态僵住",
     "story_type": "multi_body_contact", "duration_s": 10.0},

    {"shot": "M8", "segment": "D",
     "fields": {
         "description": "狐狸打了个小喷嚏，鼻尖的叶子飘落。它甩甩头，端坐下来舔了舔前爪，"
                        "午后暖光把它的毛染成暖金色，村子安安静静",
         "motion": "The fox sneezes softly and the leaf flutters down; it shakes its head, "
                   "sits upright and licks a front paw; warm afternoon light gilds its fur, "
                   "the village quiet around",
         "camera_angle": "eye level",
         "camera_movement": "static",
         "composition": "中景偏近，狐狸端坐，暖金色调",
         "motion_pace": "slow, calm",
         "target_duration_s": 5.0,
         "video_reference_asset_id": "prev_tail",
     },
     "expected_tail": "狐狸端坐在土路上，尾巴环过前爪，神态放松，午后暖光",
     "story_type": "static_reaction", "duration_s": 5.0},

    {"shot": "M9", "segment": "D",
     "fields": {
         "description": "暮色降临，影子拉长。狐狸沿土路小跑回雨林边缘，在树影前停步，回头望了"
                        "一眼沉入黄昏的安静村庄，剪影小小的停在明暗交界处",
         "motion": "Dusk settles and shadows stretch; the fox trots back along the dirt road "
                   "toward the forest edge, stops at the treeline shadow and looks back once "
                   "at the quiet village sinking into dusk, a small silhouette at the light "
                   "boundary",
         "camera_angle": "high wide angle",
         "camera_movement": "static wide, hold",
         "composition": "大远景，土路纵深，狐狸小剪影停在雨林边缘树影前，村庄在身后",
         "environment_motion": "dusk light fading gradually, long shadows",
         "target_duration_s": 8.0,
         "video_reference_asset_id": "prev_tail",
     },
     "expected_tail": "暮色大远景：狐狸小剪影停在雨林边缘树影前，身后村庄沉入黄昏",
     "story_type": "static_final", "duration_s": 8.0},
]
