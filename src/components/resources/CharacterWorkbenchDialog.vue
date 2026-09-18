<template>
  <div class="cwd-overlay" @click.self="$emit('close')">
    <div class="cwd" role="dialog" aria-label="角色工作台">
      <!-- 头部：标题 + 角色切换 + 锁定 + 关闭 -->
      <header class="cwd-head">
        <span class="cwd-title">角色工作台</span>
        <span class="cwd-nav">
          <button class="nav-btn" :disabled="charIndex <= 0" title="上一个角色" @click="switchChar(-1)">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><polyline points="15 18 9 12 15 6" /></svg>
          </button>
          <span class="nav-pos mono">{{ charIndex + 1 }} / {{ characters.length }}</span>
          <button class="nav-btn" :disabled="charIndex >= characters.length - 1" title="下一个角色" @click="switchChar(1)">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><polyline points="9 18 15 12 9 6" /></svg>
          </button>
        </span>
        <span v-if="charactersLocked" class="cwd-lock">AI 正在修改角色 · 只读</span>
        <button class="cwd-close" @click="$emit('close')">✕</button>
      </header>

      <div class="cwd-body" v-if="character">
        <!-- 角色头 -->
        <div class="dt-head">
          <span class="portrait" @click="openLightbox(avatarUrl(character.name))">
            <img v-if="avatarUrl(character.name)" :src="avatarUrl(character.name)" alt="" />
            <span v-else class="portrait-ph"></span>
          </span>
          <div class="dt-main">
            <div class="dt-name">
              <h2 v-if="!editingName" @dblclick="startEditName">{{ character.name }}</h2>
              <input v-else v-model="editNameValue" class="inline-input"
                     @blur="saveName" @keyup.enter="saveName" @keyup.escape="cancelEditName" ref="nameInput" />
              <span class="dt-hint" v-if="!editingName">双击改名</span>
            </div>
            <p v-if="!editingDesc && character.description" class="dt-desc" @dblclick="startEditDesc">{{ character.description }}</p>
            <p v-else-if="!editingDesc" class="dt-desc empty" @dblclick="startEditDesc">（还没有生成用描述，双击填写）</p>
            <textarea v-else v-model="editDescValue" class="inline-textarea" rows="2"
                      @blur="saveDesc" @keyup.escape="cancelEditDesc" ref="descInput"></textarea>
            <div class="dt-stats">
              <span class="dt-stat"><span class="dot" :class="statusDot(character)"></span>三视图 <b>{{ doneCount(character) }}/3</b></span>
              <span class="dt-stat">Look <b>{{ looks.length }}</b></span>
              <span class="dt-stat">组合参考 <b>{{ compositions.length }}</b></span>
              <span class="dt-stat" v-if="selectedDesign">设定 <b>{{ designStatusLabel(selectedDesign.status) }}</b></span>
            </div>
          </div>
          <div class="dt-actions">
            <button class="btn btn-primary btn-sm" @click="goGenerate">生成设定图</button>
            <button class="btn btn-outline btn-sm" @click="openDesignStudio(character)">AI 设计角色</button>
            <button class="btn btn-outline btn-sm" :disabled="!doneCount(character)" @click="saveCharacter(character)">存入角色库</button>
            <button class="btn btn-quiet btn-sm" @click="publishChar" title="发布到共享资源库">发布</button>
            <button class="btn btn-quiet btn-sm" @click="confirmDeleteChar(character)">删除</button>
          </div>
        </div>

        <div class="dt-body">
          <!-- ① 身份设定 -->
          <section class="sec" id="sec-identity">
            <div class="sec-head">
              <span class="section-label">身份设定</span>
              <span class="meta" v-if="selectedDesign">{{ designStatusLabel(selectedDesign.status) }}</span>
              <span class="spacer"></span>
              <button class="btn btn-quiet btn-sm" @click="openDesignStudio(character)">
                {{ selectedDesign ? '在工作室中编辑' : 'AI 设计设定' }}
              </button>
            </div>
            <div v-if="selectedDesign" class="fields">
              <div class="field-row" v-if="draftOf('visual_concept')"><span class="k">视觉概念</span><span class="v">{{ draftOf('visual_concept') }}</span></div>
              <div class="field-row" v-if="draftOf('appearance')"><span class="k">外貌</span><span class="v">{{ draftOf('appearance') }}</span></div>
              <div class="field-row" v-if="draftOf('clothing_or_body')"><span class="k">服装 / 体型</span><span class="v">{{ draftOf('clothing_or_body') }}</span></div>
              <div class="field-row" v-if="draftOf('distinctive_features')"><span class="k">显著特征</span><span class="v">{{ draftOf('distinctive_features') }}</span></div>
              <div class="field-row" v-if="draftOf('color_palette')"><span class="k">配色</span><span class="v">{{ draftOf('color_palette') }}</span></div>
              <div class="field-row" v-if="(selectedDesign.draft?.consistency_rules || []).length">
                <span class="k">一致性规则</span>
                <span class="v"><span class="rules"><span class="rule" v-for="(r, i) in selectedDesign.draft.consistency_rules" :key="i">{{ r }}</span></span></span>
              </div>
            </div>
            <div v-else class="sec-empty">还没有 AI 设定。「AI 设计设定」会依据剧本与项目风格生成视觉概念、外貌、服装、配色与一致性规则。</div>
          </section>

          <!-- ② 三视图与设定图 -->
          <section class="sec" id="sec-views" ref="secViews">
            <div class="sec-head">
              <span class="section-label">三视图与设定图</span>
              <span class="meta">生成方式：设定图</span>
            </div>
            <div class="views">
              <div v-for="v in viewCards" :key="v.key" class="view" @click="v.img && openLightbox(v.img)">
                <div class="v-ph" :class="{ empty: !v.img }">
                  <img v-if="v.img" :src="v.img" alt="" />
                  <span v-else>未生成</span>
                </div>
                <div class="v-meta">
                  <span class="dot" :class="v.img ? 'live' : ''"></span>{{ v.label }}
                  <span class="spacer"></span>
                  <span class="v-state">{{ v.img ? '已生成' : '未生成' }}</span>
                  <span class="tag-mini" v-if="v.seg">抠图</span>
                </div>
              </div>
              <div class="view cta" @click="goGenerate">
                <div class="v-ph empty cta-ph"><span class="cta-plus">+</span><span>生成设定图</span></div>
                <div class="v-meta"><span class="dot"></span>设定图（整张）<span class="spacer"></span><span class="v-state">生成后自动裁剪为三视图</span></div>
              </div>
            </div>
            <div class="gen-panel">
              <component :is="sheetComponent" :char="character" :key="character.name" />
            </div>
          </section>

          <!-- ③ Looks 与候选审查 -->
          <section class="sec" id="sec-looks">
            <div class="sec-head">
              <span class="section-label">Looks 与候选审查</span>
              <span class="meta mono-num" v-if="lookDetail">{{ lookDetail.reference_assets?.length || 0 }} 个参考</span>
              <span class="spacer"></span>
              <button class="btn btn-quiet btn-sm" @click="showCreateLook = !showCreateLook">＋ 新建 Look</button>
              <template v-if="lookDetail">
                <button class="btn btn-quiet btn-sm" :disabled="lookBusy" @click="generateLookAction">
                  生成候选 <select class="mini-select" v-model.number="genCount" @click.stop>
                    <option :value="1">1</option><option :value="2">2</option><option :value="3">3</option><option :value="4">4</option>
                  </select>
                </button>
                <button class="btn btn-quiet btn-sm" :disabled="lookBusy || !lookDetail.reference_asset_ids?.length" @click="reviewLookAction">视觉审查</button>
                <button class="btn btn-quiet btn-sm" :disabled="lookBusy" @click="publishLookAction">发布</button>
              </template>
            </div>
            <div v-if="showCreateLook" class="inline-form">
              <input v-model="newLook.label" placeholder="显示名（如 Alice · Black Coat）" />
              <select v-model="newLook.clothing_id">
                <option :value="''">无服装</option>
                <option v-for="cl in clothings" :key="cl.id" :value="cl.id">{{ cl.name }}</option>
              </select>
              <input v-model="newLook.appearance_delta" placeholder="外观差异（可选）" />
              <button class="btn btn-sm btn-primary" :disabled="lookBusy" @click="createLook">创建</button>
            </div>

            <div class="look-tabs" v-if="looks.length">
              <button v-for="lk in looks" :key="lk.id" class="chip" :class="{ on: activeLookId === lk.id }" @click="openLook(lk.id)">
                {{ lk.name || '(未命名 Look)' }}<span v-if="lk.status" class="chip-sub">{{ lk.status }}</span>
              </button>
            </div>

            <template v-if="lookDetail">
              <div class="cands" v-if="lookDetail.reference_assets?.length">
                <div v-for="a in lookDetail.reference_assets" :key="a.asset_id" class="cand"
                     :class="{ cover: lookDetail.cover_asset_id === a.asset_id }" @click="openLightbox(assetUrl(a))">
                  <img v-if="assetUrl(a)" :src="assetUrl(a)" alt="" />
                  <div v-else class="cand-ph">{{ a.missing ? '缺失' : '无图' }}</div>
                  <span v-if="lookDetail.cover_asset_id === a.asset_id" class="flag">★</span>
                  <span v-else-if="isApproved(a)" class="flag ok">已批准</span>
                  <div class="cand-acts">
                    <button v-if="!isApproved(a)" class="mini" :disabled="lookBusy"
                            :title="reviewOf(a)?.overall === 'pass' ? '' : '需要审查通过后才能批准'"
                            @click.stop="approveRef(a)">批准</button>
                    <button v-else class="mini" :disabled="lookBusy" @click.stop="revokeRef(a)">撤销</button>
                  </div>
                  <p v-if="reviewNote(a)" class="review-note">{{ reviewNote(a) }}</p>
                </div>
              </div>
              <div v-else class="sec-empty">该 Look 还没有参考候选，点「生成候选」生成。</div>
            </template>
            <div v-else-if="!looks.length" class="empty-cta">
              <p>还没有 Look。Look 是该角色的一套参考图（候选 → 视觉审查 → 批准），批准后供分镜与合成引用。</p>
              <button class="btn btn-sm btn-primary" @click="showCreateLook = true">＋ 新建 Look</button>
            </div>
          </section>

          <!-- ④ 组合参考 -->
          <section class="sec" id="sec-comps">
            <div class="sec-head">
              <span class="section-label">组合参考</span>
              <span class="meta">角色 + 已批准 Look + 道具 → 组合参考图</span>
              <span class="spacer"></span>
              <button class="btn btn-quiet btn-sm" :disabled="!approvedLooks.length"
                      :title="approvedLooks.length ? '' : '需要先批准一个 Look 才能创建组合'"
                      @click="showCreateComp = !showCreateComp">＋ 新建组合</button>
            </div>
            <p class="hint">只使用「已批准」的 Look 与道具参考图。是 Reference，不是剧情持有事实。</p>

            <div v-if="showCreateComp" class="inline-form col">
              <select v-model="newComp.look_id" required>
                <option value="" disabled>选择 approved Look（必选）</option>
                <option v-for="lk in approvedLooks" :key="lk.id" :value="lk.id">{{ lk.name || lk.id }}（{{ lk.status }}）</option>
              </select>
              <div v-for="(b, i) in newComp.bindings" :key="i" class="binding-row">
                <select v-model="b.prop_id">
                  <option value="" disabled>道具</option>
                  <option v-for="pt in propOptions" :key="pt.id" :value="pt.id">{{ pt.name }}</option>
                </select>
                <select v-model="b.relation">
                  <option v-for="r in RELATIONS" :key="r" :value="r">{{ r }}</option>
                </select>
                <select v-model="b.anchor">
                  <option v-for="an in ANCHORS" :key="an" :value="an">{{ an }}</option>
                </select>
                <input v-model="b.visual_state_note" placeholder="状态备注（可选，如 sheathed）" />
                <button class="btn btn-sm btn-quiet" type="button" @click="newComp.bindings.splice(i, 1)">移除</button>
              </div>
              <div class="form-row">
                <button class="btn btn-sm btn-quiet" type="button" :disabled="newComp.bindings.length >= 3"
                        @click="newComp.bindings.push({ prop_id: '', relation: 'held', anchor: 'right_hand', visual_state_note: '' })">＋ 加道具（最多 3）</button>
                <input v-model="newComp.label" placeholder="显示名（可选）" />
                <input v-model="newComp.pose_note" placeholder="姿态备注（可选）" />
                <button class="btn btn-sm btn-primary" :disabled="lookBusy" @click="createComp">创建组合</button>
              </div>
            </div>

            <div class="comp-tabs" v-if="compositions.length">
              <button v-for="cp in compositions" :key="cp.id" class="chip" :class="{ on: selectedComp?.composition_id === cp.id }" @click="openComp(cp.id)">
                {{ cp.name || cp.label || '(未命名组合)' }}<span v-if="cp.status" class="chip-sub">{{ cp.status }}</span>
              </button>
            </div>

            <template v-if="selectedComp">
              <div class="comp-meta">
                <span class="meta">Look: {{ selectedComp.look?.label || selectedComp.look_id }}</span>
                <span v-for="b in selectedComp.prop_bindings_resolved" :key="b.prop_id" class="meta">{{ b.prop?.name || b.prop_id }} · {{ b.relation }} @ {{ b.anchor }}</span>
                <span class="spacer"></span>
                <select class="mini-select" v-model.number="compGenCount">
                  <option :value="1">1</option><option :value="2">2</option><option :value="3">3</option><option :value="4">4</option>
                </select>
                <button class="btn btn-quiet btn-sm" :disabled="lookBusy" @click="generateCompAction">生成候选</button>
                <button class="btn btn-quiet btn-sm" :disabled="lookBusy || !selectedComp.reference_asset_ids?.length" @click="reviewCompAction">视觉审查</button>
                <button class="btn btn-quiet btn-sm" :disabled="lookBusy" @click="publishCompAction">发布</button>
              </div>
              <div class="cands" v-if="selectedComp.reference_assets?.length">
                <div v-for="a in selectedComp.reference_assets" :key="a.asset_id" class="cand"
                     :class="{ cover: selectedComp.cover_asset_id === a.asset_id }" @click="openLightbox(assetUrl(a))">
                  <img v-if="assetUrl(a)" :src="assetUrl(a)" alt="" />
                  <div v-else class="cand-ph">{{ a.missing ? '缺失' : '无图' }}</div>
                  <span v-if="selectedComp.cover_asset_id === a.asset_id" class="flag">★</span>
                  <span v-else-if="isCompApproved(a)" class="flag ok">已批准</span>
                  <div class="cand-acts">
                    <button v-if="!isCompApproved(a)" class="mini" :disabled="lookBusy"
                            :title="compReviewBadge(a) === 'pass' ? '' : '需要六维度审查通过后才能批准'"
                            @click.stop="approveCompRef(a)">批准</button>
                    <button v-else class="mini" :disabled="lookBusy" @click.stop="revokeCompRef(a)">撤销</button>
                  </div>
                  <div v-if="compReview(a)" class="dims">
                    <em v-for="d in COMP_DIMS" :key="d.k">{{ d.label }}: {{ compReview(a)[d.k] || '?' }}</em>
                  </div>
                  <p v-if="compReview(a)?.summary" class="review-note">{{ compReview(a).summary }}</p>
                </div>
              </div>
              <div v-else class="sec-empty">还没有候选——点「生成候选」。</div>
            </template>
            <div v-else-if="!compositions.length" class="empty-cta">
              <template v-if="approvedLooks.length">
                <p>还没有组合参考。组合 = 角色 + 已批准 Look + 1~3 个道具，提前生成组合参考图供分镜引用。</p>
                <button class="btn btn-sm btn-primary" @click="showCreateComp = true">＋ 新建组合</button>
              </template>
              <p v-else>还没有组合参考——需先在上方「Looks」批准一个 Look，并给道具批准参考图。</p>
            </div>
          </section>
        </div>
      </div>
      <div class="cwd-body empty-body" v-else>角色不存在或已被删除</div>

      <!-- 灯箱 -->
      <div v-if="lightboxSrc" class="cwd-lightbox" @click="lightboxSrc = ''">
        <img :src="lightboxSrc" @click.stop />
        <button class="lb-close" @click="lightboxSrc = ''">×</button>
      </div>

      <!-- AI 角色设计工作室（全屏，浮于工作台之上） -->
      <CharacterDesignStudio v-if="showStudio" :project-name="projectName"
                             :initial-character-name="studioInitialName"
                             @close="showStudio = false" @applied="onStudioApplied" @generated="onStudioApplied" />

      <!-- 删除确认 -->
      <div v-if="showDeleteConfirm" class="cwd-confirm" @click.self="showDeleteConfirm = false">
        <div class="confirm-box">
          <h4>确认删除</h4>
          <p>确定删除角色「{{ character?.name }}」吗？</p>
          <div class="confirm-btns">
            <button class="btn btn-quiet btn-sm" @click="showDeleteConfirm = false">取消</button>
            <button class="btn btn-sm danger" :disabled="deleting" @click="deleteChar">删除</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 角色工作台大弹窗：合并原「角色管理」页右侧工作台 + 资源页 Look/组合参考工作区。
 * 表面（资源页）只留卡片网格；一个角色的一切都在这里完成。
 */
import { ref, computed, watch, nextTick, defineAsyncComponent } from 'vue';
import { storeToRefs } from 'pinia';
import { useProjectStore } from '@/store';
import { useAgentUiStore } from '@/store/agentUi';
import { useProjectEventsStore } from '@/store/projectEvents';
import { apiClient } from '@/api';
import CharacterDesignStudio from '@/components/CharacterDesignStudio.vue';

const props = defineProps<{
  projectName: string;
  characterName: string;
  characters: any[];
  clothings: any[];
  propOptions: any[];
  docRev: string;
}>();
const emit = defineEmits<{ (e: 'close'): void; (e: 'refresh'): void }>();

const store = useProjectStore();
const agentUi = useAgentUiStore();
const pev = useProjectEventsStore();
const { characters } = storeToRefs(store);
const charactersLocked = computed(() => pev.charactersLocked);

const sheetComponent = computed(() => defineAsyncComponent(() => import('./TabSheet.vue')));

// ---- 当前角色 ----
const currentName = ref(props.characterName);
watch(() => props.characterName, (n) => { if (n && n !== currentName.value) currentName.value = n; });
const charIndex = computed(() => props.characters.findIndex(c => c.name === currentName.value));
const character = computed(() => props.characters[charIndex.value] || null);
function switchChar(dir: number) {
  const i = charIndex.value + dir;
  if (i < 0 || i >= props.characters.length) return;
  currentName.value = props.characters[i].name;
  selectedClipReset();
}
function selectedClipReset() {
  editingName.value = false; editingDesc.value = false;
  activeLookId.value = ''; lookDetail.value = null; selectedComp.value = null;
  showCreateLook.value = false; showCreateComp.value = false;
  lightboxSrc.value = '';
  agentUi.setContext({ type: 'character', name: currentName.value });
  loadLooks(); loadCompositions();
}
watch(currentName, () => { if (character.value) selectedClipReset(); });

// ---- 名称 / 描述编辑 ----
const editingName = ref(false);
const editingDesc = ref(false);
const editNameValue = ref('');
const editDescValue = ref('');
const nameInput = ref<HTMLInputElement | null>(null);
const descInput = ref<HTMLTextAreaElement | null>(null);
function startEditName() {
  if (charactersLocked.value) { store.showNotification('Agent 正在修改角色，请稍候', 'info'); return; }
  editNameValue.value = character.value.name;
  editingName.value = true;
  nextTick(() => nameInput.value?.focus());
}
async function saveName() {
  const newName = editNameValue.value.trim();
  editingName.value = false;
  if (!newName || newName === character.value.name || charactersLocked.value) return;
  const oldName = character.value.name;
  character.value.name = newName;
  currentName.value = newName;
  try {
    await apiClient.updateProjectCharacter(props.projectName, oldName, { new_name: newName });
    loadLooks(); loadCompositions();
    emit('refresh');
  } catch { /* 角色尚未落盘时静默（本地已更新） */ }
}
function cancelEditName() { editingName.value = false; }
function startEditDesc() {
  if (charactersLocked.value) { store.showNotification('Agent 正在修改角色，请稍候', 'info'); return; }
  editDescValue.value = character.value.description;
  editingDesc.value = true;
  nextTick(() => descInput.value?.focus());
}
async function saveDesc() {
  const newDesc = editDescValue.value.trim();
  editingDesc.value = false;
  if (!newDesc || charactersLocked.value) return;
  character.value.description = newDesc;
  try { await apiClient.updateProjectCharacter(props.projectName, character.value.name, { description: newDesc }); }
  catch { /* 同上 */ }
}
function cancelEditDesc() { editingDesc.value = false; }

// ---- 三视图（store 缓存，base64）----
function doneCount(char: any): number {
  const views = store.getCharacterCache(char?.name)?.views || {};
  return (['front', 'side', 'back'] as const).filter(k => views[k]?.seg || views[k]?.raw).length;
}
function statusDot(char: any): string {
  const gen = store.getGeneratingState(char.name);
  if (gen && Object.values(gen.busy || {}).some(Boolean)) return '';
  const done = doneCount(char);
  if (done === 3) return 'live';
  if (done === 0) return '';
  return 'on';
}
const viewCards = computed(() => {
  const views = store.getCharacterCache(character.value?.name)?.views || {};
  const mk = (key: 'front' | 'side' | 'back', label: string) => {
    const v = views[key] || {};
    return { key, label, img: v.raw ? `data:image/png;base64,${v.raw}` : '', seg: !!v.seg };
  };
  return [mk('front', '正面'), mk('side', '侧面'), mk('back', '背面')];
});
function avatarUrl(name: string): string {
  const views = store.getCharacterCache(name)?.views || {};
  const src = views.front?.seg || views.front?.raw || views.side?.seg || views.side?.raw || views.back?.seg || views.back?.raw;
  return src ? `data:image/png;base64,${src}` : '';
}
const secViews = ref<HTMLElement | null>(null);
const lightboxSrc = ref('');
function openLightbox(src: string) { if (src) lightboxSrc.value = src; }
function goGenerate() { nextTick(() => secViews.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })); }

// ---- AI 设定草稿 ----
const designs = ref<Record<string, any>>({});
const selectedDesign = computed(() => (character.value ? designs.value[character.value.name] || null : null));
const designStatusMap: Record<string, string> = { draft: '草稿', applied: '已应用', stale: '已过期' };
function designStatusLabel(s: string): string { return designStatusMap[s] || s || ''; }
function draftOf(key: string): string { return String(selectedDesign.value?.draft?.[key] || '').trim(); }
async function loadDesigns() {
  if (!props.projectName) { designs.value = {}; return; }
  try { designs.value = (await apiClient.getCharacterDesigns(props.projectName)).designs || {}; }
  catch { designs.value = {}; }
}

// ---- Looks（资源域能力）----
const looks = ref<any[]>([]);
const activeLookId = ref('');
const lookDetail = ref<any>(null);
const lookBusy = ref(false);
const showCreateLook = ref(false);
const newLook = ref({ label: '', clothing_id: '', appearance_delta: '' });
const genCount = ref(2);
const approvedLooks = computed(() => looks.value.filter((l: any) => l.status === 'approved'));

function assetUrl(a: any): string {
  if (!a || a.missing) return '';
  const local: string = a.local || '';
  if (local && !/^https?:/.test(local)) {
    if (local.startsWith('shared_library/')) return `/${local}`;
    return `/projects/${encodeURIComponent(props.projectName)}/${local.replace(/^\/+/, '')}`;
  }
  return a.url || '';
}
function isApproved(a: any): boolean {
  return (lookDetail.value?.approved_reference_asset_ids || []).includes(a.asset_id);
}
function reviewOf(a: any): any { return lookDetail.value?.latest_reviews?.[a.asset_id] || null; }
function reviewNote(a: any): string { return reviewOf(a)?.summary || ''; }

async function loadLooks() {
  if (!props.projectName || !character.value) { looks.value = []; lookDetail.value = null; return; }
  try {
    const r = await apiClient.listLooks(props.projectName, character.value.name);
    looks.value = r.looks || [];
    const first = looks.value[0];
    if (first) await openLook(first.id);
    else lookDetail.value = null;
  } catch { looks.value = []; lookDetail.value = null; }
}
async function openLook(id: string) {
  activeLookId.value = id;
  try { lookDetail.value = await apiClient.readLook(props.projectName, id); }
  catch { lookDetail.value = null; }
}
async function createLook() {
  if (!character.value || !newLook.value.label.trim()) return;
  lookBusy.value = true;
  try {
    const r = await apiClient.createLook(props.projectName, {
      character_name: character.value.name,
      label: newLook.value.label.trim(),
      clothing_id: newLook.value.clothing_id || null,
      appearance_delta: newLook.value.appearance_delta.trim(),
      expected_document_revision: props.docRev,
    });
    newLook.value = { label: '', clothing_id: '', appearance_delta: '' };
    showCreateLook.value = false;
    await loadLooks();
    if (r?.target) await openLook(r.target);
    emit('refresh');
  } catch (e: any) { store.showNotification(`创建 Look 失败: ${e.message}`, 'error'); }
  finally { lookBusy.value = false; }
}
async function generateLookAction() {
  if (!lookDetail.value) return;
  lookBusy.value = true;
  try {
    await apiClient.generateLookCandidates(props.projectName, lookDetail.value.look_id, {
      expected_revision: lookDetail.value.revision, candidate_count: genCount.value,
    });
    await openLook(lookDetail.value.look_id);
    await loadLooks();
  } catch (e: any) { store.showNotification(`生成候选失败: ${e.message}`, 'error'); }
  finally { lookBusy.value = false; }
}
async function reviewLookAction() {
  if (!lookDetail.value) return;
  lookBusy.value = true;
  try {
    await apiClient.reviewLookCandidates(props.projectName, lookDetail.value.look_id, {});
    await openLook(lookDetail.value.look_id);
  } catch (e: any) { store.showNotification(`审查失败: ${e.message}`, 'error'); }
  finally { lookBusy.value = false; }
}
async function approveRef(a: any) {
  if (!lookDetail.value) return;
  lookBusy.value = true;
  try {
    await apiClient.approveLookReference(props.projectName, lookDetail.value.look_id, {
      asset_id: a.asset_id, expected_revision: lookDetail.value.revision,
    });
    await openLook(lookDetail.value.look_id);
    await loadLooks();
  } catch (e: any) { store.showNotification(`批准失败: ${e.message}`, 'error'); }
  finally { lookBusy.value = false; }
}
async function revokeRef(a: any) {
  if (!lookDetail.value) return;
  lookBusy.value = true;
  try {
    await apiClient.revokeLookApproval(props.projectName, lookDetail.value.look_id, {
      asset_id: a.asset_id, expected_revision: lookDetail.value.revision,
    });
    await openLook(lookDetail.value.look_id);
  } catch (e: any) { store.showNotification(`撤销失败: ${e.message}`, 'error'); }
  finally { lookBusy.value = false; }
}
async function publishLookAction() {
  if (!lookDetail.value) return;
  lookBusy.value = true;
  try {
    await apiClient.publishResource(props.projectName, lookDetail.value.look_id);
    store.showNotification('Look 已发布到共享库', 'success');
  } catch (e: any) {
    if (e?.error === 'clothing_not_published' && lookDetail.value.clothing_id) {
      if (confirm('Look 依赖的服装尚未发布，先发布服装再发布 Look？')) {
        try {
          await apiClient.publishResource(props.projectName, lookDetail.value.clothing_id);
          await apiClient.publishResource(props.projectName, lookDetail.value.look_id);
          store.showNotification('Look 已发布到共享库', 'success');
        } catch (e2) { store.showNotification(`发布失败: ${e2?.message || e2}`, 'error'); }
      }
    } else store.showNotification(`发布失败: ${e?.message || e}`, 'error');
  } finally { lookBusy.value = false; }
}

// ---- 组合参考 ----
const RELATIONS = ['held', 'carried', 'worn', 'attached', 'near'];
const ANCHORS = ['right_hand', 'left_hand', 'back', 'waist', 'neck', 'beside_character'];
const COMP_DIMS = [
  { k: 'character_identity', label: '身份' }, { k: 'look_consistency', label: 'Look' },
  { k: 'prop_fidelity', label: '道具' }, { k: 'prop_binding_correctness', label: '绑定' },
  { k: 'required_prop_visibility', label: '可见' }, { k: 'visual_defects', label: '缺陷' },
] as const;
const compositions = ref<any[]>([]);
const selectedComp = ref<any>(null);
const showCreateComp = ref(false);
const compGenCount = ref(2);
const newComp = ref({ look_id: '', label: '', pose_note: '',
  bindings: [{ prop_id: '', relation: 'held', anchor: 'right_hand', visual_state_note: '' }] as any[] });

function compReview(a: any): any { return selectedComp.value?.latest_reviews?.[a.asset_id] || null; }
function compReviewBadge(a: any): string { return compReview(a)?.overall || ''; }
function isCompApproved(a: any): boolean {
  return (selectedComp.value?.approved_reference_asset_ids || []).includes(a.asset_id);
}
async function loadCompositions() {
  if (!props.projectName || !character.value) { compositions.value = []; return; }
  try { compositions.value = (await apiClient.listCompositions(props.projectName, character.value.name)).compositions || []; }
  catch { compositions.value = []; }
}
async function openComp(id: string) {
  try { selectedComp.value = await apiClient.readComposition(props.projectName, id); }
  catch { selectedComp.value = null; }
}
async function createComp() {
  if (!character.value || !newComp.value.look_id) return;
  lookBusy.value = true;
  try {
    const bindings = newComp.value.bindings
      .filter((b: any) => b.prop_id)
      .map((b: any) => ({ prop_id: b.prop_id, relation: b.relation, anchor: b.anchor, visual_state_note: b.visual_state_note || '' }));
    const r = await apiClient.createComposition(props.projectName, {
      character_name: character.value.name,
      look_id: newComp.value.look_id,
      prop_bindings: bindings,
      label: newComp.value.label,
      pose_note: newComp.value.pose_note,
      expected_document_revision: props.docRev,
    });
    showCreateComp.value = false;
    newComp.value = { look_id: '', label: '', pose_note: '', bindings: [{ prop_id: '', relation: 'held', anchor: 'right_hand', visual_state_note: '' }] };
    await loadCompositions();
    if (r?.target) await openComp(r.target);
    emit('refresh');
  } catch (e: any) { store.showNotification(`创建组合失败: ${e.message}`, 'error'); }
  finally { lookBusy.value = false; }
}
async function generateCompAction() {
  if (!selectedComp.value) return;
  lookBusy.value = true;
  try {
    await apiClient.generateCompositionCandidates(props.projectName, selectedComp.value.composition_id, {
      expected_revision: selectedComp.value.revision, candidate_count: compGenCount.value,
    });
    await openComp(selectedComp.value.composition_id);
    await loadCompositions();
  } catch (e: any) { store.showNotification(`生成候选失败: ${e.message}`, 'error'); }
  finally { lookBusy.value = false; }
}
async function reviewCompAction() {
  if (!selectedComp.value) return;
  lookBusy.value = true;
  try {
    await apiClient.reviewCompositionCandidates(props.projectName, selectedComp.value.composition_id, {});
    await openComp(selectedComp.value.composition_id);
  } catch (e: any) { store.showNotification(`审查失败: ${e.message}`, 'error'); }
  finally { lookBusy.value = false; }
}
async function approveCompRef(a: any) {
  if (!selectedComp.value) return;
  lookBusy.value = true;
  try {
    await apiClient.approveCompositionCandidate(props.projectName, selectedComp.value.composition_id, {
      asset_id: a.asset_id, expected_revision: selectedComp.value.revision,
    });
    await openComp(selectedComp.value.composition_id);
    await loadCompositions();
  } catch (e: any) { store.showNotification(`批准失败: ${e.message}`, 'error'); }
  finally { lookBusy.value = false; }
}
async function revokeCompRef(a: any) {
  if (!selectedComp.value) return;
  lookBusy.value = true;
  try {
    await apiClient.revokeCompositionApproval(props.projectName, selectedComp.value.composition_id, {
      asset_id: a.asset_id, expected_revision: selectedComp.value.revision,
    });
    await openComp(selectedComp.value.composition_id);
    await loadCompositions();
  } catch (e: any) { store.showNotification(`撤销失败: ${e.message}`, 'error'); }
  finally { lookBusy.value = false; }
}
async function publishCompAction() {
  if (!selectedComp.value) return;
  lookBusy.value = true;
  try {
    await apiClient.publishResource(props.projectName, selectedComp.value.composition_id);
    store.showNotification('组合已发布到共享库', 'success');
  } catch (e: any) {
    if (e?.error === 'dependency_not_published' && Array.isArray(e?.missing_dependencies)) {
      const prio: Record<string, number> = { character: 0, clothing: 1, prop: 2, look: 3 };
      const deps = [...e.missing_dependencies].sort((a: any, b: any) => (prio[a.kind] ?? 9) - (prio[b.kind] ?? 9));
      if (confirm(`组合依赖尚未发布（${deps.map((d: any) => `${d.kind}:${d.id}`).join('、')}）。\n按顺序自动发布全部依赖，然后发布组合？`)) {
        try {
          for (const d of deps) {
            if (d.kind === 'character') await apiClient.publishCharacter(props.projectName, d.id);
            else await apiClient.publishResource(props.projectName, d.id);
          }
          await apiClient.publishResource(props.projectName, selectedComp.value.composition_id);
          store.showNotification('组合已发布到共享库', 'success');
        } catch (e2) { store.showNotification(`发布失败: ${e2?.message || e2}`, 'error'); }
      }
    } else store.showNotification(`发布失败: ${e?.message || e}`, 'error');
  } finally { lookBusy.value = false; }
}

// ---- 发布 / 存角色库 / 删除 ----
async function publishChar() {
  if (!character.value) return;
  try {
    await apiClient.publishCharacter(props.projectName, character.value.name);
    store.showNotification('已发布到共享资源库', 'success');
  } catch (e: any) { store.showNotification(`发布失败: ${e.message}`, 'error'); }
}
async function saveCharacter(char: any) {
  const charCache = store.getCharacterCache(char.name);
  if (charCache && charCache.views) {
    await store.saveCurrentCharacter(char.name, char.description, charCache.views);
  } else {
    store.showNotification('请先生成三视图后再保存', 'info');
  }
}
const showDeleteConfirm = ref(false);
const deleting = ref(false);
function confirmDeleteChar() { showDeleteConfirm.value = true; }
async function deleteChar() {
  if (!character.value || charactersLocked.value) return;
  deleting.value = true;
  try {
    await apiClient.deleteProjectCharacter(props.projectName, character.value.name);
    const idx = charIndex.value;
    emit('refresh');
    const remaining = props.characters.filter(c => c.name !== character.value?.name);
    showDeleteConfirm.value = false;
    if (remaining.length) {
      currentName.value = remaining[Math.min(idx, remaining.length - 1)].name;
    } else {
      emit('close');
    }
    store.showNotification('角色已删除', 'success');
  } catch (e: any) {
    store.showNotification(`删除失败: ${e.message}`, 'error');
  }
  deleting.value = false;
}

// ---- AI 设计工作室（内嵌全屏层）----
const showStudio = ref(false);
const studioInitialName = ref<string | null>(null);
function openDesignStudio(char: any) {
  studioInitialName.value = char?.name || null;
  showStudio.value = true;
}
function onStudioApplied() {
  store.loadProjectCharacters(props.projectName);
  loadDesigns();
  emit('refresh');
}

// ---- 初始化 + 角色表变化时刷新关联数据 ----
selectedClipReset();
loadDesigns();
watch(() => props.characters, () => { loadDesigns(); loadLooks(); loadCompositions(); });
</script>

<style lang="scss" scoped>
.cwd-overlay {
  position: fixed; inset: 0; z-index: 1200; background: rgba(0, 0, 0, 0.6);
  display: flex; align-items: center; justify-content: center; backdrop-filter: blur(3px);
}
.cwd {
  width: min(1120px, 94vw); height: min(88vh, 940px);
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 12px;
  box-shadow: 0 24px 70px rgba(0, 0, 0, 0.5);
  display: flex; flex-direction: column; overflow: hidden;
}
.cwd-head {
  flex-shrink: 0; display: flex; align-items: center; gap: 12px;
  padding: 12px 18px; border-bottom: 1px solid var(--border);
}
.cwd-title { font-size: 13px; font-weight: 600; color: var(--text-primary); }
.cwd-nav { display: flex; align-items: center; gap: 4px; }
.nav-btn {
  width: 22px; height: 22px; border-radius: 4px; border: none; background: none;
  color: var(--text-muted); cursor: pointer; display: flex; align-items: center; justify-content: center;
  &:hover:not(:disabled) { background: var(--bg-hover); color: var(--text-primary); }
  &:disabled { opacity: 0.3; cursor: not-allowed; }
}
.nav-pos { font-size: 11px; color: var(--text-faint); }
.cwd-lock { font-size: 11px; color: #f87171; border: 1px dashed rgba(248, 113, 113, 0.4); border-radius: 8px; padding: 0 8px; line-height: 18px; }
.cwd-close {
  margin-left: auto; width: 26px; height: 26px; border-radius: 6px; border: none; background: none;
  color: var(--text-muted); cursor: pointer; font-size: 14px;
  &:hover { background: var(--bg-hover); color: var(--text-primary); }
}

.cwd-body { flex: 1; min-height: 0; overflow-y: auto; padding: 16px 20px 24px; }
.empty-body { display: flex; align-items: center; justify-content: center; color: var(--text-faint); font-size: 13px; }

/* 角色头 */
.dt-head { display: flex; gap: 16px; align-items: flex-start; padding-bottom: 14px; border-bottom: 1px solid var(--border); }
.portrait {
  width: 72px; height: 72px; flex-shrink: 0; border-radius: 10px; overflow: hidden;
  background: var(--bg-root); border: 1px solid var(--border); cursor: zoom-in;
  img { width: 100%; height: 100%; object-fit: cover; display: block; }
}
.portrait-ph { display: block; width: 100%; height: 100%; }
.dt-main { flex: 1; min-width: 0; }
.dt-name { display: flex; align-items: baseline; gap: 10px;
  h2 { margin: 0; font-size: 18px; color: var(--text-primary); cursor: text; } }
.dt-hint { font-size: 11px; color: var(--text-faint); }
.inline-input {
  background: var(--bg-root); border: 1px solid var(--border); border-radius: 5px;
  color: var(--text-primary); font-size: 16px; font-weight: 600; padding: 3px 8px; font-family: inherit;
  &:focus { outline: none; border-color: var(--accent); }
}
.dt-desc { margin: 6px 0 0; font-size: 13px; color: var(--text-secondary); line-height: 1.6; cursor: text;
  &.empty { color: var(--text-faint); } }
.inline-textarea {
  width: 100%; margin-top: 6px; background: var(--bg-root); border: 1px solid var(--border); border-radius: 5px;
  color: var(--text-primary); font-size: 13px; padding: 6px 8px; font-family: inherit; resize: vertical;
  &:focus { outline: none; border-color: var(--accent); }
}
.dt-stats { display: flex; gap: 14px; margin-top: 10px; }
.dt-stat { font-size: 12px; color: var(--text-muted);
  b { color: var(--text-primary); font-weight: 600; } }
.dt-actions { display: flex; flex-direction: column; gap: 6px; flex-shrink: 0; }

/* 按钮（沿用全局 3 尺寸） */
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  height: 30px; padding: 0 12px; border: 1px solid var(--border); border-radius: 6px;
  background: none; color: var(--text-secondary); font-size: 12px; cursor: pointer;
  &:disabled { opacity: 0.4; cursor: not-allowed; }
  &.btn-sm { height: 26px; padding: 0 10px; font-size: 12px; }
  &.btn-primary { border: none; background: var(--accent); color: var(--accent-text, #fff); &:hover:not(:disabled) { background: var(--accent-hover); } }
  &.btn-outline { &:hover:not(:disabled) { border-color: var(--border-hover); color: var(--text-primary); } }
  &.btn-quiet { border-color: transparent; color: var(--text-muted); &:hover:not(:disabled) { background: var(--bg-hover); color: var(--text-primary); } }
}

/* 区块 */
.dt-body { display: flex; flex-direction: column; gap: 18px; padding-top: 14px; }
.sec { border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; background: var(--bg-root); }
.sec-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.section-label { font-size: 11px; font-weight: 600; letter-spacing: 0.08em; color: var(--text-muted); }
.meta { font-size: 11px; color: var(--text-faint); }
.spacer { flex: 1; }
.sec-empty { font-size: 12px; color: var(--text-faint); line-height: 1.7; padding: 6px 2px; }
.empty-cta {
  display: flex; flex-direction: column; align-items: center; gap: 10px;
  padding: 14px 8px; text-align: center;
  p { margin: 0; font-size: 12px; color: var(--text-faint); line-height: 1.7; }
}
.hint { font-size: 11px; color: var(--text-faint); margin: -4px 0 8px; }

.fields { display: flex; flex-direction: column; gap: 6px; }
.field-row { display: grid; grid-template-columns: 88px 1fr; gap: 12px; font-size: 12px;
  .k { color: var(--text-faint); } .v { color: var(--text-secondary); line-height: 1.6; } }
.rules { display: flex; flex-wrap: wrap; gap: 4px; }
.rule { border: 1px solid var(--border); border-radius: 4px; padding: 0 6px; font-size: 11px; color: var(--text-muted); }

/* 三视图 */
.views { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.view {
  border: 1px solid var(--border); border-radius: 8px; overflow: hidden; cursor: pointer; background: var(--bg-surface);
  &:hover { border-color: var(--border-hover); }
  &.cta .v-ph { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; color: var(--text-faint); }
}
.v-ph { aspect-ratio: 3 / 4; background: var(--bg-root); display: flex; align-items: center; justify-content: center;
  img { width: 100%; height: 100%; object-fit: cover; display: block; }
  &.empty { color: var(--text-faint); font-size: 12px; } }
.cta-plus { font-size: 22px; color: var(--text-faint); }
.v-meta { display: flex; align-items: center; gap: 6px; padding: 6px 8px; font-size: 11px; color: var(--text-muted); }
.v-state { color: var(--text-faint); margin-left: auto; }
.tag-mini { border: 1px solid var(--border); border-radius: 4px; padding: 0 4px; font-size: 10px; color: var(--text-faint); }
.dot { width: 6px; height: 6px; border-radius: 50%; background: var(--text-faint); flex-shrink: 0;
  &.live { background: var(--text-primary); } &.on { background: var(--text-secondary); } &.fail { background: #f87171; } }
.gen-panel { margin-top: 10px; }

/* Looks */
.inline-form { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 10px;
  &.col { flex-direction: column; align-items: stretch; }
  input, select {
    background: var(--bg-surface); border: 1px solid var(--border); border-radius: 5px;
    color: var(--text-primary); font-size: 12px; padding: 5px 8px; font-family: inherit;
    &:focus { outline: none; border-color: var(--border-hover); }
  } }
.form-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.binding-row { display: grid; grid-template-columns: 1.2fr 0.9fr 1fr 1.2fr auto; gap: 6px; }
.mini-select {
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 4px;
  color: var(--text-muted); font-size: 11px; padding: 1px 4px; font-family: inherit;
}
.look-tabs, .comp-tabs { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
.chip {
  display: inline-flex; align-items: center; gap: 5px;
  border: 1px solid var(--border); border-radius: 14px; background: none;
  color: var(--text-muted); font-size: 12px; padding: 3px 10px; cursor: pointer;
  &:hover { color: var(--text-secondary); border-color: var(--border-hover); }
  &.on { border-color: var(--accent); color: var(--text-primary); }
}
.chip-sub { font-size: 10px; color: var(--text-faint); }

.cands { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; }
.cand {
  position: relative; border: 1px solid var(--border); border-radius: 8px; overflow: hidden;
  background: var(--bg-surface); cursor: zoom-in;
  &:hover { border-color: var(--border-hover); }
  &.cover { border-color: var(--accent); }
  img { display: block; width: 100%; aspect-ratio: 3 / 4; object-fit: cover; }
}
.cand-ph { aspect-ratio: 3 / 4; display: flex; align-items: center; justify-content: center; color: var(--text-faint); font-size: 12px; }
.flag {
  position: absolute; left: 6px; top: 6px; font-size: 10px;
  background: rgba(0, 0, 0, 0.55); color: #d4d4d4; border-radius: 4px; padding: 1px 5px;
  &.ok { color: #a7f3d0; }
}
.cand-acts { display: flex; gap: 6px; padding: 6px 8px; }
.mini {
  border: 1px solid var(--border); background: none; color: var(--text-secondary);
  font-size: 11px; border-radius: 4px; padding: 2px 8px; cursor: pointer;
  &:hover:not(:disabled) { color: var(--text-primary); border-color: var(--border-hover); }
  &:disabled { opacity: 0.4; cursor: not-allowed; }
  &.primary { border-color: var(--accent); color: var(--accent); &:hover:not(:disabled) { background: var(--accent); color: var(--accent-text, #fff); } }
  &.danger { color: #f87171; }
}
.review-note { margin: 0; padding: 0 8px 8px; font-size: 11px; color: var(--text-faint); line-height: 1.5; }
.dims { display: flex; flex-wrap: wrap; gap: 4px; padding: 4px 8px 0; font-size: 10px; color: var(--text-faint);
  em { font-style: normal; border: 1px solid var(--border); border-radius: 4px; padding: 0 4px; } }
.comp-meta { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }

/* 删除确认 */
.cwd-confirm {
  position: fixed; inset: 0; z-index: 1450; background: rgba(0, 0, 0, 0.55);
  display: flex; align-items: center; justify-content: center;
}
.confirm-box {
  width: min(360px, 90vw); background: var(--bg-surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 16px 18px; box-shadow: 0 18px 50px rgba(0, 0, 0, 0.45);
  h4 { margin: 0 0 8px; font-size: 14px; color: var(--text-primary); }
  p { margin: 0 0 14px; font-size: 12px; color: var(--text-muted); line-height: 1.6; }
}
.confirm-btns { display: flex; justify-content: flex-end; gap: 8px; }
.btn.danger { border-color: rgba(248, 113, 113, 0.5); color: #f87171;
  &:hover:not(:disabled) { background: rgba(248, 113, 113, 0.12); color: #f87171; } }

/* 灯箱 / 工作室 */
.cwd-lightbox {
  position: fixed; inset: 0; z-index: 1400; background: rgba(0, 0, 0, 0.85);
  display: flex; align-items: center; justify-content: center; cursor: zoom-out;
  img { max-width: 90vw; max-height: 90vh; object-fit: contain; }
  .lb-close {
    position: absolute; right: 18px; top: 14px; border: none; background: none;
    color: #d4d4d4; font-size: 22px; cursor: pointer;
  }
}
</style>
