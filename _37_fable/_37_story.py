# _37_fable/_37_story.py — 《雨林奇遇》冻结预编剧（37.md §4；与 STORY_FROZEN.md 一致）。
# 唯一事实源：生成开始前冻结；修正只能走 37.md §5 校准协议（下镜过渡/插情节）。
CHAIN_ROOT_REF = "ref_d27e1a01c36f"      # 2D 雨林 walk_side 变体（三重锚定链根）
FOX_LOOK = "look_2c6a01d45db5"           # 狐狸 approved look
LOCATION_ID = "loc_04970479321e"         # 雨林 Location（36.md §6）
TRAIL_VIEW = "view_70e07a5c7211"         # 已批准 trail main base view

SEGMENTS = [
    {
        "key": "A", "title": "晨雾小径", "scene_hint": None,
        "summary": "清晨的雨林覆盖着薄雾。小狐狸沿雾中小径晨跑，发现一朵巨型发光蘑菇，"
                   "好奇触碰，蘑菇亮起、洒出光尘，光尘中腾起萤火虫，狐狸追着萤火虫群"
                   "跃过倒木奔向雨林深处。",
        "state": {"time_of_day": "early morning", "weather": "thin mist",
                  "lighting": "Soft morning light shafts cutting through mist, "
                              "cool green shadows",
                  "environment_conditions": "Humid air, faint mist between trunks, "
                                            "dripping leaves"},
    },
    {
        "key": "B", "title": "空地水塘", "scene_hint": None,
        "summary": "小径尽头豁然开朗，狐狸来到空地水塘边喝水。对岸蕨丛晃动惊起一只蓝闪"
                   "蝶，绕它一圈停在尾巴尖上；蝴蝶最终飞落塘边红花，狐狸趴在石上安睡，"
                   "暮色里萤火虫点缀。",
        "state": {"time_of_day": "late afternoon", "weather": "clear, thinning mist",
                  "lighting": "Warm slanting afternoon light on the pond, long "
                              "soft shadows",
                  "environment_conditions": "Calm air over the pond, cicada hum, "
                                            "light motes above water"},
    },
]

# 每镜字段（update_shot_fields 白名单内）。expected_tail 与 story_type/notes 是
# 校准元数据（不进生成字段，进 Calibration Trace）。
SHOTS = [
    {"shot": "N1", "segment": "A",
     "fields": {
         "description": "晨雾弥漫的热带雨林小径，狐狸从画面左侧入画，沿小径向右侧轻快奔跑，"
                        "穿行于树干与蕨叶之间，晨光斜射穿过雾气，镜头侧面跟拍",
         "motion": "Fox trots into frame from the left along a misty rainforest trail, "
                   "weaving between trunks and ferns; morning light shafts cut through mist",
         "camera_angle": "side tracking",
         "camera_movement": "steady side tracking, matching the fox pace",
         "composition": "全景侧面跟拍，小径纵深，树干蕨叶层次",
         "environment_motion": "mist drifting between trunks, light shafts shimmering",
         "motion_pace": "brisk, steady",
         "target_duration_s": 8.0,
         "video_reference_asset_id": CHAIN_ROOT_REF,
     },
     "expected_tail": "狐狸在小径中段减速站定，四足着地，身体朝向画面右侧，耳朵竖起",
     "story_type": "dynamic_establishing", "duration_s": 8.0},

    {"shot": "N2", "segment": "A",
     "fields": {
         "description": "狐狸忽然停下，发现前方倒伏树干后一朵巨型发光蘑菇（蓝绿色荧光）。"
                        "它绕过倒木走近，低头伸鼻尖轻触蘑菇帽——蘑菇被碰后亮起，洒出一圈细小光尘",
         "motion": "Fox stops, notices a giant glowing mushroom behind a fallen trunk, "
                   "circles around it, leans in and touches the cap with its nose; "
                   "the mushroom brightens and releases a ring of tiny light motes",
         "action_sequence": "stop and notice the glowing mushroom -> circle around the "
                            "fallen trunk -> lean in -> nose touches the cap -> mushroom "
                            "brightens, light motes drift out",
         "camera_angle": "eye level",
         "camera_movement": "slow push in as the fox approaches",
         "composition": "中景，倒木前景，蘑菇与狐狸同框",
         "target_duration_s": 10.0,
         "video_reference_asset_id": "prev_tail",
     },
     "expected_tail": "狐狸鼻尖仍贴近蘑菇帽，蘑菇亮起，光尘悬浮在两者之间",
     "story_type": "prop_interaction", "duration_s": 10.0},

    {"shot": "N3", "segment": "A",
     "fields": {
         "description": "光尘缓缓飘散，狐狸后退半步蹲坐下来，歪着头打量发光的蘑菇，尾巴环过前爪",
         "motion": "Light motes drift away; the fox sits back on its haunches, head "
                   "tilted, studying the glowing mushroom, tail curled around its front paws",
         "camera_angle": "eye level",
         "camera_movement": "static",
         "composition": "中景偏近，狐狸与蘑菇相对",
         "motion_pace": "slow, gentle",
         "target_duration_s": 5.0,
         "video_reference_asset_id": "prev_tail",
     },
     "expected_tail": "狐狸坐姿稳定，面朝右侧蘑菇，头微歪",
     "story_type": "static_reaction", "duration_s": 5.0},

    {"shot": "N4", "segment": "A",
     "fields": {
         "description": "蘑菇光尘中腾起一群萤火虫。狐狸兴奋起身，追着萤火虫群沿小径向画面深处"
                        "奔跑，加速后纵身跃过倒伏树干，落地后减速站稳，背对镜头，萤火虫在前方林间盘旋",
         "motion": "A swarm of fireflies rises from the light motes; the fox springs up and "
                   "chases them down the trail away from camera, accelerating, leaps over the "
                   "fallen trunk, lands and slows to a stand; fireflies swirl ahead",
         "action_sequence": "fireflies rise -> fox springs up and chases -> accelerating run "
                            "away from camera -> leap over the fallen trunk -> land, decelerate, "
                            "stand still",
         "camera_angle": "low angle from behind",
         "camera_movement": "tracking behind the fox, settling as it lands",
         "composition": "中景背面跟随，小径纵深，倒木为跳跃障碍",
         "motion_pace": "explosive start, soft landing",
         "target_duration_s": 10.0,
         "video_reference_asset_id": "prev_tail",
     },
     "expected_tail": "狐狸从倒木落地后减速站稳，背对镜头面向深处，萤火虫在前方盘旋",
     "story_type": "high_motion", "duration_s": 10.0},

    {"shot": "N5", "segment": "B",
     "fields": {
         "description": "雨林豁然开朗：小径尽头是一片空地，中央一汪浅水塘，午后暖光斜照水面。"
                        "狐狸走出小径来到塘边，低头喝水，水面映出倒影",
         "motion": "The trail opens into a clearing with a shallow pond; the fox steps out, "
                   "walks to the pond edge and lowers its head to drink, reflection on the water",
         "camera_angle": "wide establishing, then eye level",
         "camera_movement": "gentle push in as the fox reaches the pond",
         "composition": "远景开场收到中景，水塘居中，雨林环抱",
         "environment_motion": "calm water, faint reflections, warm light on the surface",
         "target_duration_s": 8.0,
         "video_reference_asset_id": "prev_tail",
     },
     "expected_tail": "狐狸立在水塘边缘低头饮水，水面有倒影，身后是雨林",
     "story_type": "environment_change", "duration_s": 8.0},

    {"shot": "N6", "segment": "B",
     "fields": {
         "description": "对岸蕨丛忽然晃动。狐狸惊得抬头后退一步，前爪踩进浅水，溅起一圈水花，"
                        "耳朵压后，紧盯蕨丛",
         "motion": "The ferns across the pond rustle; the fox startles, rears back a step, "
                   "front paws splashing into shallow water, ears pinned, staring at the ferns",
         "camera_angle": "eye level",
         "camera_movement": "slight shake on the startle, then steady",
         "composition": "中景正面偏侧，水花与倒影可见",
         "motion_pace": "sudden burst then freeze",
         "target_duration_s": 8.0,
         "video_reference_asset_id": "prev_tail",
     },
     "expected_tail": "狐狸站在浅水中抬头盯向对岸蕨丛，水花尚未落尽",
     "story_type": "startle_transition", "duration_s": 8.0},

    {"shot": "N7", "segment": "B",
     "fields": {
         "description": "一只蓝闪蝶从蕨丛飞出，绕狐狸头顶一圈，轻轻落在它翘起的尾巴尖上。"
                        "狐狸僵住，眼睛慢慢斜向下看尾巴，随后小步转身回头，鼻尖凑近尾巴尖上的蝴蝶，一动不动",
         "motion": "A blue morpho butterfly flies out, circles the fox once, and lands on its "
                   "raised tail tip; the fox freezes, eyes sliding down, then turns its head "
                   "back slowly toward the butterfly, nose close, motionless",
         "action_sequence": "butterfly circles once -> lands on the raised tail tip -> fox "
                            "freezes -> eyes slide down -> slow turn of head back toward tail "
                            "-> hold still, nose close to butterfly",
         "camera_angle": "eye level, close on the fox",
         "camera_movement": "static, hold on the frozen pose",
         "composition": "近景，狐狸回头与尾尖蝴蝶同框",
         "motion_pace": "very slow, held stillness",
         "target_duration_s": 10.0,
         "video_reference_asset_id": "prev_tail",
     },
     "expected_tail": "狐狸回头面向尾尖，蝴蝶停在尾尖毛上，姿态静止",
     "story_type": "complex_interaction", "duration_s": 10.0},

    {"shot": "N8", "segment": "B",
     "fields": {
         "description": "蝴蝶腾起，缓缓落在塘边一朵红花上，翅膀开合。狐狸小心翼翼绕到花旁，"
                        "趴卧在旁边的平石上，下巴搁在前爪上，安静地看着蝴蝶",
         "motion": "The butterfly lifts and settles on a red flower by the pond; the fox "
                   "carefully circles close and lies down on a flat stone, chin on paws, "
                   "watching it quietly",
         "camera_angle": "low angle, ground level",
         "camera_movement": "static",
         "composition": "近景低机位，狐狸趴石与红花同框",
         "motion_pace": "slow, calm",
         "target_duration_s": 8.0,
         "video_reference_asset_id": "prev_tail",
     },
     "expected_tail": "狐狸趴在石上，蝴蝶停在红花上，两者相对，画面安稳",
     "story_type": "static_closing_interaction", "duration_s": 8.0},

    {"shot": "N9", "segment": "B",
     "fields": {
         "description": "暮色降临，空地全景：狐狸蜷在石上闭眼入睡，尾巴盖住鼻尖，萤火虫三两"
                        "点缀林间，镜头缓缓拉远定格",
         "motion": "Dusk settles; wide view of the clearing, the fox curled asleep on the "
                   "stone, tail over its nose, a few fireflies dotting the trees; the camera "
                   "slowly pulls back and holds",
         "camera_angle": "high wide angle",
         "camera_movement": "slow pull back, then hold",
         "composition": "大远景，空地全景，石与狐狸居中偏左",
         "environment_motion": "fireflies drifting, dusk light fading gradually",
         "target_duration_s": 5.0,
         "video_reference_asset_id": "prev_tail",
     },
     "expected_tail": "大远景：狐狸安睡石上，塘面映暮色，林间萤光点点",
     "story_type": "static_final", "duration_s": 5.0},
]
