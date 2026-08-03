<template>
  <div class="wb-overlay" @click.self="$emit('close')">
    <div class="wb">
      <!-- 顶部：镜头标识 + 模式切换 + 状态 -->
      <header class="wb-head">
        <span class="wb-title">{{ sceneId }} · {{ shot.shot_id }}</span>
        <span v-if="projectStyleLabel" class="wb-badge">{{ projectStyleLabel }}</span>
        <span v-if="projectRatio" class="wb-badge">{{ projectRatio }}</span>
        <div class="mode-switch">
          <button :class="{ on: mode === 'standard' }" @click="setMode('standard')">标准模式</button>
          <button :class="{ on: mode === 'professional' }" @click="setMode('professional')">专业模式</button>
        </div>
        <span v-if="mode === 'standard' && hasProfessionalSettings" class="prof-hint" @click="setMode('professional')" title="点击查看专业设置">已应用专业设置</span>
        <span class="wb-save" :class="saveStatus">{{ saveStatusText }}</span>
        <span class="wb-task" :class="taskClass">{{ taskStatusText }}</span>
        <button class="wb-close" @click="$emit('close')">✕</button>
      </header>

      <!-- 公共上下文：故事段原文 + 出场角色 + 台词/配音 + 接续方式 -->
      <div class="wb-ctx">
        <details class="ctx-story">
          <summary>故事段原文</summary>
          <p>{{ sceneStory || '（未填写）' }}</p>
        </details>
        <div class="ctx-row">
          <span class="ctx-label">出场角色</span>
          <div class="char-chips">
            <button v-for="c in projectCharacters" :key="c.name" class="chip" :class="{ on: form.characters.includes(c.name) }" @click="toggleChar(c.name)">
              {{ c.name }}
            </button>
            <span v-if="!projectCharacters.length" class="muted">项目无角色</span>
          </div>
        </div>
        <div class="ctx-row">
          <span class="ctx-label">台词 / 配音</span>
          <input class="ctx-input" v-model="form.dialogue" placeholder="台词（可选）" />
          <span class="audio-status">{{ audioStatusText }}</span>
          <button class="mini-btn" :disabled="audioBusy" @click="genAudio">{{ audioBusy ? '配音中…' : (shot.audio?.status === 'done' ? '重配' : '配音') }}</button>
        </div>
        <div class="ctx-row">
          <span class="ctx-label">接续方式</span>
          <select class="ctx-input" v-model="form.link_mode">
            <option value="i2v">图生视频（跨场景硬切）</option>
            <option value="keyframes">首尾帧过渡（同场景接力）</option>
            <option value="none">不接续</option>
          </select>
        </div>
      </div>

      <!-- 阶段标签（左右一起切） -->
      <div class="stage-tabs">
        <button :class="{ on: stage === 'first' }" @click="stage = 'first'">首帧</button>
        <button :class="{ on: stage === 'last' }" @click="stage = 'last'">尾帧</button>
        <button :class="{ on: stage === 'video' }" @click="stage = 'video'">视频</button>
      </div>

      <div class="wb-body">
        <!-- 左：当前阶段编辑面板 -->
        <div class="wb-left">
          <!-- 首帧 -->
          <template v-if="stage === 'first'">
            <div class="field"><label>景别</label>
              <select v-model="form.shot_size"><option v-for="o in SHOT_SIZES" :key="o.v" :value="o.v">{{ o.l }}</option></select>
            </div>
            <div class="field"><label>起始画面描述</label>
              <textarea v-model="form.description" rows="3" placeholder="地点、主体、画面中正在发生的状态"></textarea>
            </div>
            <div class="field"><label>主体起始状态</label>
              <textarea v-model="form.opening_state" rows="2" placeholder="角色姿态/朝向/表情或物体初始状态"></textarea>
            </div>
            <div class="field"><label>机位角度</label>
              <select v-model="form.camera_angle"><option value="">（不指定）</option><option v-for="o in CAMERA_ANGLES" :key="o.v" :value="o.v">{{ o.l }}</option></select>
            </div>
            <div class="field"><label>光线与氛围</label>
              <select v-model="form.lighting"><option value="">（不指定）</option><option v-for="o in LIGHTINGS" :key="o.v" :value="o.v">{{ o.l }}</option></select>
            </div>
            <template v-if="mode === 'professional'">
              <div class="field"><label>构图</label>
                <select v-model="form.composition"><option value="">（不指定）</option><option v-for="o in COMPOSITIONS" :key="o.v" :value="o.v">{{ o.l }}</option></select>
              </div>
              <div class="field"><label>重要画面细节</label>
                <textarea v-model="form.important_details" rows="2" placeholder="背景标志物/道具/特效"></textarea>
              </div>
              <div class="field"><label>必须保持/出现的元素（每行一个）</label>
                <textarea :value="(form.required_elements||[]).join('\n')" @input="form.required_elements = ($event.target as HTMLTextAreaElement).value.split('\n').map(s=>s.trim()).filter(Boolean)" rows="2" placeholder="爆炸火光&#10;右臂"></textarea>
              </div>
            </template>
            <PromptEditor v-model="firstPromptModel" :status="firstPromptStatus" :busy="promptBusy.first" :show-full="mode === 'professional'" @generate="generatePrompt('first')" @regenerate="generatePrompt('first')" @reset-auto="clearOverride('first')" />
          </template>

          <!-- 尾帧 -->
          <template v-else-if="stage === 'last'">
            <div v-if="form.link_mode !== 'keyframes'" class="muted pad">当前接续方式不需要尾帧。</div>
            <template v-else>
              <div class="field"><label>首帧基础图</label>
                <div class="ff-ref">
                  <img v-if="ffUrl" :src="ffUrl" /><span v-else class="muted">未选择首帧</span>
                  <span class="muted">尾帧将基于此图生成</span>
                </div>
              </div>
              <div class="field"><label>结束画面</label>
                <textarea v-model="form.last_frame_description" rows="2" placeholder="镜头结束时最终看到的画面"></textarea>
              </div>
              <div class="field"><label>主体结束状态</label>
                <textarea v-model="form.end_state" rows="2" placeholder="人物/物体最终姿态、位置、方向、表情"></textarea>
              </div>
              <div class="field"><label>相对首帧的变化</label>
                <textarea v-model="form.frame_change" rows="2" placeholder="例：机甲抬起右臂，身体后倾"></textarea>
              </div>
              <div class="field"><label>保持一致</label>
                <div class="check-grid">
                  <label v-for="o in PRESERVE_OPTIONS" :key="o.v"><input type="checkbox" :checked="form.preserve_elements.includes(o.v)" @change="toggleList(form.preserve_elements, o.v)" />{{ o.l }}</label>
                </div>
              </div>
              <template v-if="mode === 'professional'">
                <div class="field"><label>新增元素</label><input v-model="form.add_elements" placeholder="右侧爆炸火光" /></div>
                <div class="field"><label>删除元素</label><input v-model="form.remove_elements" placeholder="地面碎石" /></div>
                <div class="field"><label>结束构图 / 摄影机状态</label><input v-model="form.end_composition" /></div>
                <div class="field"><label>自定义保持要求</label><input v-model="form.preserve_custom" /></div>
              </template>
              <PromptEditor v-model="lastPromptModel" :status="lastPromptStatus" :busy="promptBusy.last" :show-full="mode === 'professional'" @generate="generatePrompt('last')" @regenerate="generatePrompt('last')" @reset-auto="clearOverride('last')" />
            </template>
          </template>

          <!-- 视频 -->
          <template v-else>
            <div class="field"><label>输入画面摘要</label>
              <div class="ff-ref">
                <img v-if="ffUrl" :src="ffUrl" /><span v-else class="muted">未选首帧</span>
                <template v-if="form.link_mode === 'keyframes'"><span class="muted">→</span><img v-if="lfUrl" :src="lfUrl" /><span v-else class="muted">未选尾帧</span></template>
              </div>
            </div>
            <div class="field"><label>主体动作</label>
              <textarea v-model="form.motion" rows="2" placeholder="例：机甲短暂停顿后迅速抬起右臂挡在胸前"></textarea>
            </div>
            <div class="field"><label>摄影机运动</label>
              <select v-model="form.camera_movement"><option value="">（不指定）</option><option v-for="o in CAMERA_MOVEMENTS" :key="o.v" :value="o.v">{{ o.l }}</option></select>
            </div>
            <div class="field"><label>动作节奏</label>
              <select v-model="form.motion_pace"><option value="">（不指定）</option><option v-for="o in MOTION_PACES" :key="o.v" :value="o.v">{{ o.l }}</option></select>
            </div>
            <div class="field"><label>镜头时长</label>
              <select v-model="durationMode">
                <option value="follow">跟随配音</option>
                <option value="3">约 3 秒</option>
                <option value="5">约 5 秒</option>
                <option value="10">约 10 秒</option>
                <option value="18">约 18 秒</option>
                <option value="custom">自定义</option>
              </select>
              <input v-if="durationMode === 'custom'" type="number" step="0.5" min="1" max="20" v-model.number="form.target_duration_s" class="dur-input" />
            </div>
            <div class="field"><label>保持稳定</label>
              <div class="check-grid">
                <label v-for="o in videoPreserveOptions" :key="o.v"><input type="checkbox" :checked="form.video_preserve_elements.includes(o.v)" @change="toggleList(form.video_preserve_elements, o.v)" />{{ o.l }}</label>
              </div>
            </div>
            <template v-if="mode === 'professional'">
              <div class="field"><label>环境运动</label><input v-model="form.environment_motion" placeholder="烟尘翻卷，远处火光闪烁" /></div>
              <div class="field"><label>动作发生顺序</label><input v-model="form.action_sequence" placeholder="先停顿，再转头，随后抬臂" /></div>
              <div class="field"><label>摄影机运动细节</label><input v-model="form.camera_movement_detail" /></div>
              <div class="field" v-if="form.link_mode === 'keyframes'"><label>首尾帧过渡关系</label><input v-model="form.transition_description" placeholder="从静止站姿自然过渡到格挡姿态" /></div>
              <div class="field"><label>自定义稳定要求</label><input v-model="form.video_preserve_custom" /></div>
              <div class="field"><label>Seed（固定 / 随机）</label>
                <div class="seed-row">
                  <select v-model="seedMode"><option value="random">随机</option><option value="fixed">固定 Seed</option></select>
                  <input v-if="seedMode === 'fixed'" type="number" v-model.number="form.seed" placeholder="整数 seed" />
                </div>
              </div>
              <div class="field"><label>Negative prompt（仅视频）</label><input v-model="form.negative_prompt" placeholder="留空则不发送" /></div>
            </template>
            <PromptEditor v-model="videoPromptModel" :status="videoPromptStatus" :busy="promptBusy.video" :show-full="mode === 'professional'" @generate="generatePrompt('video')" @regenerate="generatePrompt('video')" @reset-auto="clearOverride('video')" />
          </template>
        </div>

        <!-- 右：当前阶段候选/预览/结果 -->
        <div class="wb-right">
          <!-- 首帧结果 -->
          <div v-if="stage === 'first'">
            <button class="gen-btn" :disabled="busy('first_frame')" @click="genFirstFrame">{{ busy('first_frame') ? '生成中…' : '生成首帧（2 候选）' }}</button>
            <div v-for="b in ffBatchesReversed" :key="b.batch_id" class="batch">
              <div class="batch-head" @click="toggleFFBatch(b.batch_id)">
                <span class="batch-chevron">{{ isFFBatchExpanded(b.batch_id) ? '▾' : '▸' }}</span>
                <span class="batch-title">批次 · {{ b.candidates.length }} 候选 · {{ b.status === 'done' ? '完成' : b.status }}</span>
              </div>
              <div v-if="isFFBatchExpanded(b.batch_id)" class="cands">
                <div v-for="(c, i) in b.candidates" :key="c.candidate_id" class="cand" :class="{ sel: c.candidate_id === shot.selected_first_frame }" @click="chooseFirst(c.candidate_id)">
                  <img v-if="c.local" :src="imgUrl(c.local)" /><SafeFrameOverlay /><span class="cand-idx">v{{ i + 1 }}</span>
                </div>
              </div>
            </div>
            <div v-if="!shot.first_frame_batches?.length" class="empty-state">根据镜头画面和角色参考生成两张起始画面候选。</div>
            <button v-if="ffNeedsRehost" class="mini-btn rehost" @click="rehost">修复图片链接（本地图待托管）</button>
          </div>
          <!-- 尾帧结果 -->
          <div v-else-if="stage === 'last'">
            <div v-if="form.link_mode !== 'keyframes'" class="empty-state">当前接续方式不需要尾帧。</div>
            <template v-else>
              <button class="gen-btn" :disabled="!ffUrlReady || busy('last_frame')" @click="genLastFrame" :title="!ffUrlReady ? '需先选定首帧' : ''">{{ busy('last_frame') ? '生成中…' : '生成尾帧' }}</button>
              <div v-if="!ffUrlReady" class="empty-state">请先选择首帧，再生成尾帧。</div>
              <div class="cands">
                <div v-for="c in latestLFCands" :key="c.candidate_id" class="cand" :class="{ sel: c.candidate_id === shot.selected_last_frame }">
                  <img v-if="c.local" :src="imgUrl(c.local)" /><SafeFrameOverlay />
                </div>
              </div>
              <div v-if="!latestLFCands.length" class="empty-state">基于已选首帧生成镜头结束状态，保持角色和场景连续。</div>
              <div class="ref-count">参考图：首帧 1{{ shot.characters?.length ? ' + 角色 ' + shot.characters.length : '' }}</div>
            </template>
          </div>
          <!-- 视频结果 -->
          <div v-else>
            <button class="gen-btn" :disabled="!canGenVideo || busy('video')" @click="genVideo" :title="videoBlockReason">{{ busy('video') ? jobMsg('video') : '生成视频' }}</button>
            <div v-if="videoBlockReason && !busy('video')" class="warn">⚠ {{ videoBlockReason }}</div>
            <div v-if="videoVersionsReversed.length" class="video-versions">
              <div v-for="v in videoVersionsReversed" :key="v.version_id" class="video-version" :class="{ final: v.version_id === shot.final_video }">
                <video v-if="v.local" :src="vidUrl(v.local)" controls preload="metadata"></video>
                <div class="vv-meta">
                  <span class="vv-ret" v-if="v.seconds_returned">{{ v.seconds_returned }}s / {{ v.size_returned }}</span>
                  <span class="vv-final" v-if="v.version_id === shot.final_video">✓ 最终视频</span>
                  <button v-else class="mini-btn" @click="setFinalVideo(v.version_id)">设为最终</button>
                </div>
              </div>
            </div>
            <div v-if="!videoVersionsReversed.length" class="empty-state">根据输入帧、主体动作和运镜生成视频。</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch } from 'vue';
import { apiClient } from '@/api';
import type { ShotData } from '@/api';
import { getImageUrl, getVideoUrl } from '@/utils/media';
import SafeFrameOverlay from './SafeFrameOverlay.vue';
import PromptEditor from './PromptEditor.vue';
import { useJobStore } from '@/store/jobs';
import { useTaskStream } from '@/composables/useTaskStream';
import { createDiscreteApi, darkTheme } from 'naive-ui';

const { message } = createDiscreteApi(['message'], { configProviderProps: { theme: darkTheme } });

const props = defineProps<{
  shot: ShotData; projectName: string;
  projectCharacters: Array<{ name: string; description_en: string; views: any }>;
  projectStyleLabel: string; projectRatio: string;
  sceneStory: string; sceneId: string;
}>();
const emit = defineEmits<{ (e: 'close'): void; (e: 'refresh'): void }>();
const jobStore = useJobStore();

// ---------- 预设（中文标签 -> 英文值，进 prompt） ----------
const SHOT_SIZES = [
  { l: '大远景', v: 'extreme wide shot' }, { l: '远景', v: 'wide shot' }, { l: '全景', v: 'full shot' },
  { l: '中景', v: 'medium shot' }, { l: '中近景', v: 'medium close-up shot' }, { l: '近景', v: 'close-up shot' },
  { l: '特写', v: 'tight close-up shot' }, { l: '大特写', v: 'extreme close-up shot' },
];
const CAMERA_ANGLES = [
  { l: '平视', v: 'eye level' }, { l: '低机位', v: 'low angle' }, { l: '高机位', v: 'high angle' },
  { l: '俯拍', v: 'overhead' }, { l: '顶视', v: 'top-down' }, { l: '仰拍', v: 'low angle looking up' },
  { l: '过肩', v: 'over the shoulder' }, { l: '主观视角', v: 'point of view' },
];
const LIGHTINGS = [
  { l: '自然柔光', v: 'soft natural light' }, { l: '清晨冷光', v: 'cool morning light' },
  { l: '黄昏暖光', v: 'warm dusk light' }, { l: '夜景冷光', v: 'cool night light' }, { l: '逆光', v: 'backlight' },
  { l: '侧光', v: 'side light' }, { l: '低调光', v: 'low-key lighting' }, { l: '霓虹光', v: 'neon lighting' },
];
const COMPOSITIONS = [
  { l: '主体居中', v: 'centered composition' }, { l: '三分法', v: 'rule of thirds' },
  { l: '对称构图', v: 'symmetrical composition' }, { l: '左侧留白', v: 'left negative space' },
  { l: '右侧留白', v: 'right negative space' }, { l: '前景遮挡', v: 'foreground framing' },
  { l: '纵深构图', v: 'depth composition' }, { l: '双人构图', v: 'two-shot composition' },
];
const CAMERA_MOVEMENTS = [
  { l: '固定镜头', v: 'fixed camera' }, { l: '缓慢推近', v: 'slow push-in' }, { l: '缓慢拉远', v: 'slow pull-out' },
  { l: '左摇', v: 'pan left' }, { l: '右摇', v: 'pan right' }, { l: '上摇', v: 'tilt up' }, { l: '下摇', v: 'tilt down' },
  { l: '横向跟随', v: 'lateral tracking' }, { l: '向前跟随', v: 'forward tracking' },
  { l: '轻微环绕', v: 'slight orbit' }, { l: '轻微手持', v: 'slight handheld' },
];
const MOTION_PACES = [
  { l: '轻微', v: 'subtle' }, { l: '缓慢', v: 'slow' }, { l: '自然', v: 'natural' },
  { l: '快速', v: 'fast' }, { l: '突然', v: 'sudden' }, { l: '强烈', v: 'intense' },
];
const PRESERVE_OPTIONS = [
  { l: '角色身份', v: 'character identity' }, { l: '面部特征', v: 'facial features' },
  { l: '服装 / 装甲', v: 'outfit and armor' }, { l: '场景布局', v: 'scene layout' },
  { l: '摄影机角度', v: 'camera angle' }, { l: '主要物体造型', v: 'main object shape' },
  { l: '视觉风格', v: 'visual style' },
];
const VIDEO_PRESERVE_OPTIONS = [
  { l: '角色身份', v: 'character identity' }, { l: '面部', v: 'facial features' },
  { l: '服装 / 装甲', v: 'outfit and armor' }, { l: '主体造型', v: 'subject shape' },
  { l: '场景布局', v: 'scene layout' }, { l: '画面风格', v: 'visual style' },
];

// ---------- 模式 + 阶段 ----------
type Mode = 'standard' | 'professional';
type Stage = 'first' | 'last' | 'video';
const mode = ref<Mode>((localStorage.getItem('wb_mode') as Mode) || 'standard');
const stage = ref<Stage>('first');
function setMode(m: Mode) { mode.value = m; localStorage.setItem('wb_mode', m); }

// ---------- 表单（全部可编辑字段，从 shot 初始化；切换模式/阶段不清空） ----------
const form = reactive({
  characters: [...props.shot.characters],
  dialogue: props.shot.dialogue || '',
  link_mode: props.shot.link_mode || 'i2v',
  // 首帧
  shot_size: props.shot.shot_size || 'medium shot',
  description: props.shot.description || '',
  opening_state: props.shot.opening_state || '',
  camera_angle: props.shot.camera_angle || '',
  lighting: props.shot.lighting || '',
  composition: props.shot.composition || '',
  important_details: props.shot.important_details || '',
  required_elements: [...(props.shot.required_elements || [])],
  // 尾帧
  last_frame_description: props.shot.last_frame_description || '',
  end_state: props.shot.end_state || '',
  frame_change: props.shot.frame_change || '',
  preserve_elements: [...(props.shot.preserve_elements || [])],
  add_elements: props.shot.add_elements || '',
  remove_elements: props.shot.remove_elements || '',
  end_composition: props.shot.end_composition || '',
  preserve_custom: props.shot.preserve_custom || '',
  // 视频
  motion: props.shot.motion || '',
  camera_movement: props.shot.camera_movement || '',
  motion_pace: props.shot.motion_pace || '',
  environment_motion: props.shot.environment_motion || '',
  action_sequence: props.shot.action_sequence || '',
  camera_movement_detail: props.shot.camera_movement_detail || '',
  transition_description: props.shot.transition_description || '',
  video_preserve_elements: [...(props.shot.video_preserve_elements || [])],
  video_preserve_custom: props.shot.video_preserve_custom || '',
  target_duration_s: props.shot.target_duration_s ?? null as number | null,
  negative_prompt: props.shot.negative_prompt || '',
  seed: props.shot.seed ?? null as number | null,
  // 三类提示词 + override + sig
  prompt_en: props.shot.prompt_en || '',
  last_frame_prompt_en: props.shot.last_frame_prompt_en || '',
  video_prompt_en: props.shot.video_prompt_en || '',
  first_frame_prompt_override: props.shot.first_frame_prompt_override || '',
  last_frame_prompt_override: props.shot.last_frame_prompt_override || '',
  video_prompt_override: props.shot.video_prompt_override || '',
  first_frame_prompt_sig: props.shot.first_frame_prompt_sig || '',
  last_frame_prompt_sig: props.shot.last_frame_prompt_sig || '',
  video_prompt_sig: props.shot.video_prompt_sig || '',
});

// ---------- 保存（debounce，失败保留本地输入） ----------
const saveStatus = ref<'idle' | 'saving' | 'saved' | 'failed'>('idle');
let saveTimer: number | null = null;
function scheduleSave() {
  saveStatus.value = 'saving';
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = window.setTimeout(() => { saveTimer = null; doSave(); }, 700);
}
async function doSave() {
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
  saveStatus.value = 'saving';
  try {
    await apiClient.updateShot(props.projectName, props.shot.shot_id, { ...form });
    saveStatus.value = 'saved';
  } catch {
    saveStatus.value = 'failed';   // 保留本地输入，不 reset form
  }
}
const saveStatusText = computed(() => saveStatus.value === 'saving' ? '保存中…' : saveStatus.value === 'saved' ? '已保存' : saveStatus.value === 'failed' ? '保存失败' : '');
watch(form, scheduleSave, { deep: true });

function toggleChar(name: string) {
  const i = form.characters.indexOf(name);
  if (i > -1) form.characters.splice(i, 1); else form.characters.push(name);
}
function toggleList(arr: string[], v: string) {
  const i = arr.indexOf(v);
  if (i > -1) arr.splice(i, 1); else arr.push(v);
}

// ---------- 时长 / seed 模式（UI 衍生） ----------
const durationMode = computed({
  get: () => {
    const v = form.target_duration_s;
    if (v === null || v === undefined) return 'follow';
    if ([3, 5, 10, 18].includes(v)) return String(v);
    return 'custom';
  },
  set: (m: string) => {
    if (m === 'follow') form.target_duration_s = null;
    else if (m === 'custom') form.target_duration_s = form.target_duration_s ?? 5;
    else form.target_duration_s = Number(m);
  },
});
const seedMode = computed({
  get: () => form.seed === null || form.seed === undefined ? 'random' : 'fixed',
  set: (m: string) => { form.seed = m === 'fixed' ? (form.seed ?? 0) : null; },
});
// 无角色时隐藏角色相关稳定项
const videoPreserveOptions = computed(() =>
  form.characters.length ? VIDEO_PRESERVE_OPTIONS : VIDEO_PRESERVE_OPTIONS.filter(o => !['character identity', 'facial features', 'outfit and armor'].includes(o.v)));

// ---------- 提示词签名 + 状态（每阶段） ----------
function sigOf(stage: Stage): string {
  const f = form;
  if (stage === 'first') return [f.description, f.opening_state, f.shot_size, f.camera_angle, f.lighting, f.composition, f.important_details, (f.required_elements || []).join(','), (f.characters || []).join(',')].join('|');
  if (stage === 'last') return [f.last_frame_description, f.end_state, f.frame_change, (f.preserve_elements || []).join(','), f.add_elements, f.remove_elements, f.end_composition, f.preserve_custom].join('|');
  return [f.motion, f.camera_movement, f.motion_pace, f.environment_motion, f.action_sequence, f.camera_movement_detail, f.transition_description, (f.video_preserve_elements || []).join(','), f.video_preserve_custom, String(f.target_duration_s)].join('|');
}
type PromptStatus = 'empty' | 'auto' | 'manual' | 'stale';
function promptStatusOf(stage: Stage): PromptStatus {
  const f = form;
  const override = stage === 'first' ? f.first_frame_prompt_override : stage === 'last' ? f.last_frame_prompt_override : f.video_prompt_override;
  const en = stage === 'first' ? f.prompt_en : stage === 'last' ? f.last_frame_prompt_en : f.video_prompt_en;
  const sig = stage === 'first' ? f.first_frame_prompt_sig : stage === 'last' ? f.last_frame_prompt_sig : f.video_prompt_sig;
  if ((override || '').trim()) return 'manual';
  if (!(en || '').trim()) return 'empty';
  return sig === sigOf(stage) ? 'auto' : 'stale';
}
const firstPromptStatus = computed(() => promptStatusOf('first'));
const lastPromptStatus = computed(() => promptStatusOf('last'));
const videoPromptStatus = computed(() => promptStatusOf('video'));

// 提示词 v-model（override 优先，编辑写 override）
function promptModel(stage: Stage) {
  return computed({
    get: () => {
      const f = form;
      const override = stage === 'first' ? f.first_frame_prompt_override : stage === 'last' ? f.last_frame_prompt_override : f.video_prompt_override;
      const en = stage === 'first' ? f.prompt_en : stage === 'last' ? f.last_frame_prompt_en : f.video_prompt_en;
      return override || en || '';
    },
    set: (v: string) => {
      if (stage === 'first') form.first_frame_prompt_override = v;
      else if (stage === 'last') form.last_frame_prompt_override = v;
      else form.video_prompt_override = v;
    },
  });
}
const firstPromptModel = promptModel('first');
const lastPromptModel = promptModel('last');
const videoPromptModel = promptModel('video');

const promptBusy = reactive({ first: false, last: false, video: false });
function buildBrief(stage: Stage): string {
  const f = form;
  // 用中文标签 + 用户文本拼成中文简述，送文本模型译成英文提示词
  const charDesc = props.projectCharacters.filter(c => f.characters.includes(c.name)).map(c => c.description_en).join('，');
  if (stage === 'first') {
    return [charDesc, f.description, f.opening_state, f.shot_size, f.camera_angle, f.lighting, f.composition, f.important_details, (f.required_elements || []).join('，')]
      .filter(Boolean).join('；');
  }
  if (stage === 'last') {
    return ['基于首帧', f.frame_change, f.last_frame_description, f.end_state, f.add_elements ? '新增 ' + f.add_elements : '', f.remove_elements ? '删除 ' + f.remove_elements : '', f.end_composition, (f.preserve_elements || []).join('，'), f.preserve_custom].filter(Boolean).join('；');
  }
  return [f.motion, f.camera_movement, f.motion_pace, f.environment_motion, f.action_sequence, f.camera_movement_detail, f.transition_description, (f.video_preserve_elements || []).join('，'), f.video_preserve_custom].filter(Boolean).join('；');
}
async function generatePrompt(stage: Stage) {
  const brief = buildBrief(stage);
  if (!brief.trim()) { message.info('请先填写该阶段的画面/动作字段'); return; }
  if (promptStatusOf(stage) === 'manual' && !confirm('将按当前字段重新生成，会覆盖手动版本，是否继续？')) return;
  promptBusy[stage] = true;
  try {
    const r = await apiClient.translateText(brief);
    const en = (r.english || '').trim();
    if (!en) { message.error('提示词生成返回空'); return; }
    if (stage === 'first') { form.prompt_en = en; form.first_frame_prompt_override = ''; form.first_frame_prompt_sig = sigOf('first'); }
    else if (stage === 'last') { form.last_frame_prompt_en = en; form.last_frame_prompt_override = ''; form.last_frame_prompt_sig = sigOf('last'); }
    else { form.video_prompt_en = en; form.video_prompt_override = ''; form.video_prompt_sig = sigOf('video'); }
    message.success('提示词已生成，可手改');
  } catch (e: any) { message.error(`提示词生成失败: ${e.message || e}`); }
  finally { promptBusy[stage] = false; }
}
function clearOverride(stage: Stage) {
  if (stage === 'first') form.first_frame_prompt_override = '';
  else if (stage === 'last') form.last_frame_prompt_override = '';
  else form.video_prompt_override = '';
}

// 标准模式下存在非空专业字段 -> "已应用专业设置"
const hasProfessionalSettings = computed(() => {
  const f = form;
  return !!(f.composition || f.important_details || (f.required_elements || []).length
    || f.add_elements || f.remove_elements || f.end_composition || f.preserve_custom
    || f.environment_motion || f.action_sequence || f.camera_movement_detail || f.transition_description
    || f.video_preserve_custom || f.negative_prompt || f.seed !== null);
});

// ---------- 任务状态 ----------
function busy(kind: string) {
  return jobStore.byShot(props.shot.shot_id).some(j => j.kind === kind && (j.state === 'running' || j.state === 'queued'));
}
function jobMsg(kind: string) {
  const j = jobStore.byShot(props.shot.shot_id).find(j => j.kind === kind && (j.state === 'running' || j.state === 'queued'));
  return j?.message || '生成中…';
}
const taskStatusText = computed(() => {
  const js = jobStore.byShot(props.shot.shot_id).filter(j => j.state === 'running' || j.state === 'queued');
  if (!js.length) return '';
  const names = js.map(j => ({ first_frame: '首帧', last_frame: '尾帧', video: '视频', audio: '配音' } as any)[j.kind] || j.kind);
  return names.join('、') + (js.some(j => j.state === 'running') ? ' 生成中' : ' 排队中');
});
const taskClass = computed(() => taskStatusText.value ? 'busy' : '');

// ---------- 首帧 ----------
const ffUrl = computed(() => {
  const cid = props.shot.selected_first_frame;
  if (!cid) return '';
  for (const b of props.shot.first_frame_batches || []) {
    const c = (b.candidates || []).find(x => x.candidate_id === cid);
    if (c?.local) return getImageUrl(c.local);
  }
  return '';
});
const selectedFFCand = computed(() => {
  const cid = props.shot.selected_first_frame; if (!cid) return null;
  for (const b of props.shot.first_frame_batches || []) {
    const c = (b.candidates || []).find(x => x.candidate_id === cid);
    if (c) return c;
  }
  return null;
});
const ffUrlReady = computed(() => !!selectedFFCand.value?.url);
const ffNeedsRehost = computed(() => !!selectedFFCand.value?.local && !selectedFFCand.value?.url);
const ffBatchesReversed = computed(() => [...(props.shot.first_frame_batches || [])].reverse());
const expandedFFBatches = ref<Set<string>>(new Set());
function isFFBatchExpanded(batchId: string): boolean {
  if (expandedFFBatches.value.size === 0) {
    const latest = props.shot.first_frame_batches?.[props.shot.first_frame_batches.length - 1];
    return latest?.batch_id === batchId;
  }
  return expandedFFBatches.value.has(batchId);
}
function toggleFFBatch(batchId: string) {
  if (expandedFFBatches.value.size === 0) {
    const latest = props.shot.first_frame_batches?.[props.shot.first_frame_batches.length - 1];
    if (latest) expandedFFBatches.value = new Set([latest.batch_id]);
  }
  const s = new Set(expandedFFBatches.value);
  if (s.has(batchId)) s.delete(batchId); else s.add(batchId);
  expandedFFBatches.value = s;
}
async function genFirstFrame() {
  try { await apiClient.generateFirstFrame(props.projectName, props.shot.shot_id, { variants: 2 }); message.info('首帧生成已入队（2 候选）'); }
  catch (e: any) { message.error(`入队失败: ${e.message || e}`); }
}
async function chooseFirst(candidateId: string) {
  try { await apiClient.chooseFirstFrame(props.projectName, props.shot.shot_id, candidateId); emit('refresh'); }
  catch (e: any) { message.error(`选定失败: ${e.message || e}`); }
}
async function rehost() {
  try { await apiClient.rehostFirstFrame(props.projectName, props.shot.shot_id); message.success('已托管'); emit('refresh'); }
  catch (e: any) { message.error(`失败: ${e.message || e}`); }
}

// ---------- 尾帧 ----------
const lfUrl = computed(() => {
  if (props.shot.link_mode !== 'keyframes') return '';
  const cid = props.shot.selected_last_frame; if (!cid) return '';
  for (const b of props.shot.last_frame_batches || []) {
    const c = (b.candidates || []).find(x => x.candidate_id === cid);
    if (c?.local) return getImageUrl(c.local);
  }
  return '';
});
const latestLFBatch = computed(() => props.shot.last_frame_batches?.[props.shot.last_frame_batches.length - 1]);
const latestLFCands = computed(() => latestLFBatch.value?.candidates || []);
const selectedLFCand = computed(() => {
  const cid = props.shot.selected_last_frame; if (!cid) return null;
  for (const b of props.shot.last_frame_batches || []) {
    const c = (b.candidates || []).find(x => x.candidate_id === cid);
    if (c) return c;
  }
  return null;
});
const lfUrlReady = computed(() => !!selectedLFCand.value?.url);
async function genLastFrame() {
  try { await apiClient.generateLastFrame(props.projectName, props.shot.shot_id); message.info('尾帧生成已入队'); }
  catch (e: any) { message.error(`入队失败: ${e.message || e}`); }
}

// ---------- 视频 ----------
const videoVersionsReversed = computed(() => [...(props.shot.video_versions || [])].reverse());
const finalVideoVersion = computed(() => {
  const vid = props.shot.final_video; if (!vid) return null;
  return (props.shot.video_versions || []).find(v => v.version_id === vid) || null;
});
const videoBlockReason = computed(() => {
  if (finalVideoVersion.value?.local) return '';
  if (!props.shot.video?.num_frames && form.target_duration_s === null) return props.shot.audio?.status === 'done' ? '配音时长待反推（刷新自动补）' : '尚未配音且未设置时长';
  const ffUsable = ffUrlReady.value || ffNeedsRehost.value;
  if (!ffUsable) return '请先生成并选择首帧';
  if (form.link_mode === 'keyframes' && !lfUrlReady.value) return '请先生成尾帧';
  return '';
});
const canGenVideo = computed(() => !videoBlockReason.value);
async function genVideo() {
  try { await apiClient.generateShotVideo(props.projectName, props.shot.shot_id); message.info('视频生成已入队'); }
  catch (e: any) { message.error(`入队失败: ${e.message || e}`); }
}
async function setFinalVideo(versionId: string) {
  try { await apiClient.setFinalVideo(props.projectName, props.shot.shot_id, versionId); message.success('已设为最终视频'); emit('refresh'); }
  catch (e: any) { message.error(`设置失败: ${e.message || e}`); }
}

// ---------- 配音 ----------
const audioBusy = ref(false);
const audioStatusText = computed(() => {
  if (audioBusy.value) return '配音中…';
  if (props.shot.audio?.status === 'done' && props.shot.audio?.local) return `已配音 ${((props.shot.audio.duration_ms || 0) / 1000).toFixed(2)}s`;
  return '未配音';
});
const { subscribe: subscribeTask } = useTaskStream();
async function genAudio() {
  audioBusy.value = true;
  try {
    const r: any = await apiClient.generateShotAudio(props.projectName, props.shot.shot_id, {});
    subscribeTask(r.task_id, (s: any) => {
      if (s.status === 'completed') { audioBusy.value = false; message.success('配音完成'); emit('refresh'); }
      else if (s.status === 'failed') { audioBusy.value = false; message.error('配音失败'); }
    });
  } catch (e: any) { audioBusy.value = false; message.error(`配音失败: ${e.message || e}`); }
}

function imgUrl(p: string) { return getImageUrl(p); }
function vidUrl(p: string) { return getVideoUrl(p); }
</script>

<style lang="scss" scoped>
.wb-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.6); z-index: 1500; display: flex; align-items: center; justify-content: center; }
.wb { width: 94vw; height: 92vh; background: var(--bg-root); border: 1px solid var(--border); border-radius: 10px; display: flex; flex-direction: column; overflow: hidden; box-shadow: 0 20px 60px rgba(0,0,0,0.4); }
.wb-head { display: flex; align-items: center; gap: 12px; padding: 10px 18px; border-bottom: 1px solid var(--border); flex-shrink: 0; }
.wb-title { font-size: 13px; font-weight: 600; color: var(--text-primary); font-family: monospace; }
.wb-badge { font-size: 11px; color: var(--text-muted); border: 1px solid var(--border); border-radius: 10px; padding: 1px 8px; }
.mode-switch { display: flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; button { padding: 4px 12px; background: none; border: none; color: var(--text-muted); font-size: 12px; cursor: pointer; &.on { background: var(--accent); color: #fff; } } }
.prof-hint { font-size: 11px; color: #fbbf24; cursor: pointer; }
.wb-save { font-size: 12px; color: var(--text-muted); &.saved { color: #4ade80; } &.failed { color: #f87171; } }
.wb-task { font-size: 12px; color: var(--text-muted); &.busy { color: #60a5fa; } }
.wb-close { margin-left: auto; background: none; border: none; color: var(--text-muted); font-size: 16px; cursor: pointer; &:hover { color: var(--text-primary); } }

.wb-ctx { padding: 8px 18px; border-bottom: 1px solid var(--border); background: var(--bg-surface); flex-shrink: 0; }
.ctx-story { summary { font-size: 12px; color: var(--text-muted); cursor: pointer; } p { font-size: 12px; color: var(--text-secondary); margin: 4px 0 0; max-height: 60px; overflow-y: auto; } }
.ctx-row { display: flex; align-items: center; gap: 10px; margin-top: 6px; flex-wrap: wrap; }
.ctx-label { font-size: 11px; color: var(--text-muted); min-width: 64px; }
.ctx-input { background: var(--bg-root); border: 1px solid var(--border); border-radius: 5px; padding: 5px 10px; font-size: 12px; color: var(--text-primary); outline: none; &:focus { border-color: var(--accent); } }
.audio-status { font-size: 11px; color: var(--text-secondary); }
.char-chips { display: flex; flex-wrap: wrap; gap: 5px; }
.chip { padding: 2px 9px; border-radius: 12px; border: 1px solid var(--border); background: none; color: var(--text-muted); font-size: 11px; cursor: pointer; &.on { background: var(--accent); border-color: var(--accent); color: #fff; } }

.stage-tabs { display: flex; gap: 4px; padding: 8px 18px 0; border-bottom: 1px solid var(--border); flex-shrink: 0; button { padding: 6px 22px; border: none; background: none; color: var(--text-muted); font-size: 13px; cursor: pointer; border-bottom: 2px solid transparent; &.on { color: var(--text-primary); border-bottom-color: var(--accent); } } }

.wb-body { flex: 1; display: flex; min-height: 0; }
.wb-left { width: 38%; min-width: 500px; flex-shrink: 0; overflow-y: auto; padding: 14px 18px; border-right: 1px solid var(--border); }
.field { margin-bottom: 12px; label { display: block; font-size: 11px; color: var(--text-muted); margin-bottom: 4px; } }
.field input, .field select, .field textarea { width: 100%; background: var(--bg-surface); border: 1px solid var(--border); border-radius: 5px; padding: 6px 10px; font-size: 12px; color: var(--text-primary); outline: none; font-family: inherit; &:focus { border-color: var(--accent); } }
.field textarea { resize: vertical; line-height: 1.5; }
.check-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 10px; label { display: flex; align-items: center; gap: 5px; font-size: 12px; color: var(--text-secondary); cursor: pointer; } }
.ff-ref { display: flex; align-items: center; gap: 8px; img { width: 64px; height: 36px; object-fit: cover; border-radius: 4px; border: 1px solid var(--border); } }
.seed-row, .dur-input { display: flex; gap: 6px; align-items: center; margin-top: 4px; }
.muted { font-size: 11px; color: var(--text-faint); }
.pad { padding: 12px 0; }
.mini-btn { padding: 4px 10px; border-radius: 5px; border: 1px solid var(--border); background: var(--bg-surface); color: var(--text-secondary); font-size: 11px; cursor: pointer; white-space: nowrap; &:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); } &:disabled { opacity: 0.5; cursor: not-allowed; } &.rehost { margin-top: 8px; } }

.wb-right { flex: 1; min-width: 0; overflow-y: auto; padding: 16px 18px; }
.gen-btn { padding: 7px 18px; border-radius: 6px; border: none; background: var(--accent); color: var(--accent-text); font-size: 13px; cursor: pointer; margin-bottom: 12px; &:hover:not(:disabled) { background: var(--accent-hover); } &:disabled { opacity: 0.5; cursor: not-allowed; } }
.cands { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 10px; }
.cand { position: relative; aspect-ratio: 16/9; border-radius: 6px; overflow: hidden; cursor: pointer; border: 2px solid transparent; background: var(--bg-surface); &.sel { border-color: #4ade80; } img { width: 100%; height: 100%; object-fit: cover; } }
.cand-idx { position: absolute; top: 3px; left: 4px; font-size: 10px; color: #fff; background: rgba(0,0,0,0.5); padding: 0 4px; border-radius: 2px; }
.batch { margin-bottom: 12px; }
.batch-head { display: flex; align-items: center; gap: 6px; padding: 5px 10px; background: var(--bg-surface); border: 1px solid var(--border); border-radius: 5px; cursor: pointer; margin-bottom: 8px; }
.batch-chevron { font-size: 12px; color: var(--text-muted); }
.batch-title { font-size: 12px; color: var(--text-secondary); }
.video-versions { display: flex; flex-direction: column; gap: 12px; margin-top: 12px; }
.video-version { border: 1px solid var(--border); border-radius: 6px; padding: 10px; &.final { border-color: #4ade80; } video { width: 100%; max-height: 44vh; border-radius: 4px; background: #000; } }
.vv-meta { display: flex; align-items: center; gap: 10px; margin-top: 6px; }
.vv-ret { font-size: 11px; color: var(--text-muted); }
.vv-final { font-size: 12px; color: #4ade80; font-weight: 600; }
.warn { font-size: 12px; color: #fbbf24; margin-top: 8px; }
.empty-state { font-size: 12px; color: var(--text-faint); padding: 16px; background: var(--bg-surface); border: 1px dashed var(--border); border-radius: 6px; margin-top: 8px; }
.ref-count { font-size: 11px; color: var(--text-muted); margin-top: 8px; }
</style>
