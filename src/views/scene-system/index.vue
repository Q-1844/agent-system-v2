<template>
  <div class="scene-page">
    <!-- 顶部：Tab + 概览 -->
    <header class="scene-header">
      <nav class="type-tabs">
        <button :class="{ active: tab === 'locations' }" @click="switchTab('locations')">地点 · 视角库</button>
        <button :class="{ active: tab === 'instances' }" @click="switchTab('instances')">场景实例 · 状态视图</button>
      </nav>
      <span v-if="overview" class="ov">
        地点 {{ overview.counts.locations }} · 锚点 {{ overview.counts.anchors }} · 视角 {{ overview.counts.views }} · 实例 {{ overview.counts.scene_instances }}
      </span>
      <span class="proj-name">{{ projectName }}</span>
    </header>

    <div v-if="errorMsg" class="scene-error">
      <span>{{ errorMsg }}</span>
      <button class="dismiss" @click="errorMsg = ''">×</button>
    </div>
    <div v-if="busy" class="scene-busy">处理中…（真实生成/审查可能需要数十秒）</div>

    <!-- ================= TAB 1：地点 · 视角库 ================= -->
    <section v-if="tab === 'locations'" class="split">
      <aside class="side-list">
        <div class="side-head">
          <span>地点（Click 查看）</span>
          <button class="mini primary" @click="showCreateLocation = !showCreateLocation">＋ 新建</button>
        </div>
        <form v-if="showCreateLocation" class="inline-form col" @submit.prevent="createLocationAction">
          <input v-model="newLocation.name" placeholder="地点名（如 灯塔控制室）" required />
          <textarea v-model="newLocation.identity_description" rows="3" placeholder="空间身份描述（建筑/结构/材质——长期稳定，不含时间天气）" />
          <button class="mini primary" type="submit">创建</button>
        </form>
        <button v-for="l in locations" :key="l.location_id" class="side-item" :class="{ active: selectedLocationId === l.location_id }" @click="selectLocation(l.location_id)">
          {{ l.name }}<span class="sub">{{ l.anchor_ids.length }} 锚点 · {{ l.view_ids.length }} 视角{{ l.layout_id ? ' · 有布局' : '' }}</span>
        </button>
        <p v-if="!locations.length" class="empty">还没有地点——先创建一个（如 主角的家 / 灯塔控制室）</p>
      </aside>

      <div v-if="selectedLocation" class="main-area">
        <!-- 地点身份 -->
        <div class="card">
          <div class="panel-head">
            <h3>{{ locForm.name || '(未命名地点)' }}</h3>
            <span class="meta">id {{ selectedLocationId }}</span>
            <button class="mini primary" :disabled="busy" @click="saveLocation">保存身份</button>
          </div>
          <div class="form-grid">
            <label>名称<input v-model="locForm.name" /></label>
            <label class="full">空间身份（长期稳定的结构/材质描述，进生成 prompt 的语义基底）<textarea v-model="locForm.identity_description" rows="2" /></label>
            <label class="full">补充描述<textarea v-model="locForm.description" rows="2" /></label>
            <label class="full">风格注记（材质/色调；不要写时间天气）<input v-model="locForm.style_notes" /></label>
          </div>
        </div>

        <!-- 锚点 -->
        <div class="card">
          <div class="panel-head">
            <h3>空间锚点（门/窗/家具/楼梯…）</h3>
            <button class="mini primary" @click="showCreateAnchor = !showCreateAnchor">＋ 添加锚点</button>
          </div>
          <form v-if="showCreateAnchor" class="inline-form col" @submit.prevent="createAnchorAction">
            <div class="form-row">
              <input v-model="newAnchor.name" placeholder="名称（如 北门 / 西窗 02）" required />
              <select v-model="newAnchor.kind">
                <option v-for="k in ANCHOR_KINDS" :key="k" :value="k">{{ k }}</option>
              </select>
              <input v-model="newAnchor.semantic_zone" placeholder="区域（如 north_wall，可选）" />
            </div>
            <textarea v-model="newAnchor.description" rows="2" placeholder="视觉描述（写清可见特征与方位，如『三扇西窗中位于中间的一扇』——生成与审查都依赖它）" />
            <button class="mini primary" type="submit">创建</button>
          </form>
          <div class="anchor-list">
            <div v-for="a in anchors" :key="a.anchor_id" class="anchor-row" :class="{ active: selectedAnchorId === a.anchor_id }">
              <button class="anchor-main" @click="selectedAnchorId = selectedAnchorId === a.anchor_id ? '' : a.anchor_id">
                <span class="anchor-name">{{ a.name }}</span>
                <em class="badge kind">{{ a.kind }}</em>
                <span class="anchor-desc">{{ a.description || '（无描述——建议补上可见特征与方位）' }}</span>
              </button>
              <div class="anchor-ops">
                <button class="mini" @click="startEditAnchor(a)">编辑</button>
                <button class="mini danger" :disabled="busy" @click="deleteAnchorAction(a)">删除</button>
              </div>
            </div>
            <p v-if="!anchors.length" class="empty">还没有锚点——门、窗、家具、楼梯都是锚点</p>
          </div>
          <form v-if="editAnchor" class="inline-form col" @submit.prevent="saveAnchor">
            <div class="form-row">
              <input v-model="editAnchor.name" required />
              <select v-model="editAnchor.kind">
                <option v-for="k in ANCHOR_KINDS" :key="k" :value="k">{{ k }}</option>
              </select>
              <input v-model="editAnchor.semantic_zone" placeholder="区域" />
            </div>
            <textarea v-model="editAnchor.description" rows="2" />
            <div class="form-row">
              <button class="mini primary" type="submit">保存锚点</button>
              <button class="mini" type="button" @click="editAnchor = null">取消</button>
            </div>
          </form>
        </div>

        <!-- SetLayout -->
        <div class="card">
          <div class="panel-head">
            <h3>SetLayout（平面布局 hint）</h3>
            <button v-if="!layout" class="mini primary" @click="createLayoutAction">创建布局</button>
          </div>
          <p v-if="!layout" class="empty">无布局（可选）——为生成提供方位 hint</p>
          <template v-if="layout">
            <div class="form-row">
              <label class="inline-label">方位约定
                <select v-model="layoutForm.orientation_convention">
                  <option value="north_up">north_up（y=0 为北）</option>
                  <option value="">（无）</option>
                </select>
              </label>
              <label class="inline-label grow">备注<input v-model="layoutForm.notes" /></label>
              <button class="mini primary" :disabled="busy" @click="saveLayout">保存布局</button>
            </div>
            <div class="placement-list">
              <div v-for="(p, i) in layoutForm.placements" :key="i" class="form-row">
                <select v-model="p.anchor_id">
                  <option value="" disabled>选择锚点</option>
                  <option v-for="a in anchors" :key="a.anchor_id" :value="a.anchor_id">{{ a.name }}</option>
                </select>
                <input v-model="p.zone" placeholder="区域（如 west_wall）" />
                <input v-model.number="p.x" type="number" min="0" max="1" step="0.05" placeholder="x" />
                <input v-model.number="p.y" type="number" min="0" max="1" step="0.05" placeholder="y" />
                <button class="mini danger" @click="layoutForm.placements.splice(i, 1)">移除</button>
              </div>
              <button class="mini" @click="layoutForm.placements.push({ anchor_id: '', zone: '', x: null, y: null, note: '' })">＋ 添加放置项</button>
            </div>
          </template>
        </div>

        <!-- LocationViews -->
        <div class="card">
          <div class="panel-head">
            <h3>视角库（View Bank：最少但足够，不自动生成 8 方向）</h3>
            <button class="mini primary" @click="showCreateView = !showCreateView">＋ 新建视角</button>
          </div>
          <form v-if="showCreateView" class="inline-form col" @submit.prevent="createViewAction">
            <div class="form-row">
              <input v-model="newView.label" placeholder="视角名（如 South-west to North-east）" required />
              <input v-model="newView.camera_zone" placeholder="相机区（如 south_west）" />
              <input v-model="newView.look_direction" placeholder="看向（如 north_east）" />
            </div>
            <div class="form-row check-row">
              <span class="inline-label">应可见锚点：</span>
              <label v-for="a in anchors" :key="a.anchor_id" class="check">
                <input type="checkbox" :value="a.anchor_id" v-model="newView.visible_anchor_ids" /> {{ a.name }}
              </label>
            </div>
            <button class="mini primary" type="submit">创建</button>
          </form>
          <div class="view-cards">
            <button v-for="v in views" :key="v.view_id" class="view-card" :class="{ active: selectedViewId === v.view_id }" @click="selectView(v.view_id)">
              <img v-if="approvedFileMap[v.view_id]" :src="candidateUrl(approvedFileMap[v.view_id])" class="cover" />
              <div v-else class="cover placeholder">未生成</div>
              <span class="view-name">{{ v.label || v.view_id }}</span>
              <span class="sub">{{ v.camera_zone || '—' }} → {{ v.look_direction || '—' }}</span>
              <span class="badges">
                <em :class="'gs-' + v.generation_status">{{ v.generation_status }}</em>
                <em :class="'badge as-' + v.approval_status">{{ v.approval_status }}</em>
              </span>
            </button>
            <p v-if="!views.length" class="empty">还没有视角——建议先建 Hero/Establishing + 一个反打方向</p>
          </div>

          <!-- 选中视角详情 -->
          <div v-if="selectedView" class="view-detail">
            <div class="detail-head">
              <h4>{{ selectedView.label || selectedView.view_id }}</h4>
              <span class="meta">{{ selectedView.camera_zone || '—' }} → {{ selectedView.look_direction || '—' }}</span>
              <span class="meta">可见 {{ selectedView.visible_anchor_ids.length }} 锚点</span>
              <div class="actions">
                <select v-model="locGenCount"><option v-for="n in 4" :key="n" :value="n">{{ n }} 张</option></select>
                <button class="mini primary" :disabled="busy" @click="generateViewAction">生成候选</button>
                <button class="mini" :disabled="busy || !viewCandidates.length" @click="reviewViewAction">视觉审查</button>
                <button v-if="selectedView.approval_status === 'approved'" class="mini danger" :disabled="busy" @click="revokeViewAction">撤销批准</button>
              </div>
            </div>
            <p class="hint">可见锚点：{{ visibleAnchorNames(selectedView) }}</p>

            <div v-if="selectedView.approval_status === 'approved' && approvedFileMap[selectedView.view_id]" class="approved-strip">
              <div class="approved-main">
                <img :src="candidateUrl(approvedFileMap[selectedView.view_id])" />
                <span class="badge approved">当前批准参考</span>
              </div>
            </div>

            <div class="cand-grid">
              <div v-for="c in viewCandidates" :key="c.index" class="cand">
                <img :src="candidateUrl(c.file)" />
                <div class="cand-info">
                  <span class="meta">#{{ c.index }}</span>
                  <span v-if="selectedView.reference_asset_id && approvedFileMap[selectedView.view_id] === c.file" class="badge approved">已批准</span>
                  <span v-else-if="viewReviewBadge(c.index)" class="badge" :class="viewReviewBadge(c.index)">{{ viewReviewBadge(c.index) }}</span>
                </div>
                <div v-if="viewReviewDims(c.index)" class="dims">
                  <em v-for="d in DIM_ORDER" :key="d.k">{{ d.label }}: {{ viewReviewDims(c.index)[d.k] || '—' }}</em>
                </div>
                <div class="cand-actions">
                  <button class="mini primary" :disabled="busy || viewReviewBadge(c.index) !== 'pass'"
                          :title="viewReviewBadge(c.index) === 'pass' ? '' : '需要该候选审查通过后才能批准'"
                          @click="approveViewAction(c.index)">批准</button>
                </div>
                <p v-if="viewReviewNote(c.index)" class="review-note">{{ viewReviewNote(c.index) }}</p>
              </div>
              <p v-if="!viewCandidates.length" class="empty">还没有候选——点「生成候选」（生成 + 视觉审查 + 批准，全链真实调用）</p>
            </div>
          </div>
        </div>
      </div>
      <div v-else class="main-area empty">← 选择或创建一个地点</div>
    </section>

    <!-- ================= TAB 2：场景实例 · 状态视图 ================= -->
    <section v-if="tab === 'instances'" class="split">
      <aside class="side-list">
        <div class="side-head">
          <span>场景实例</span>
          <button class="mini primary" @click="showCreateInstance = !showCreateInstance">＋ 新建</button>
        </div>
        <form v-if="showCreateInstance" class="inline-form col" @submit.prevent="createInstanceAction">
          <label class="inline-label">故事段（Story Scene）
            <select v-model="newInstance.story_scene_id" required>
              <option value="" disabled>选择</option>
              <option v-for="s in storyScenes" :key="s.scene_id" :value="s.scene_id">{{ s.scene_id }}（{{ (s.summary || '').slice(0, 18) }}）</option>
            </select>
          </label>
          <label class="inline-label">地点
            <select v-model="newInstance.location_id" required>
              <option value="" disabled>选择</option>
              <option v-for="l in locations" :key="l.location_id" :value="l.location_id">{{ l.name }}</option>
            </select>
          </label>
          <button class="mini primary" type="submit" :disabled="!locations.length">创建</button>
          <p class="hint">一个故事段在一个地点中的一次发生；V1 每段至多一个 active 实例</p>
        </form>
        <button v-for="i in instances" :key="i.scene_instance_id" class="side-item" :class="{ active: selectedInstanceId === i.scene_instance_id }" @click="selectInstance(i.scene_instance_id)">
          {{ i.story_scene_id }} @ {{ locationName(i.location_id) }}
          <span class="sub" :class="{ stale: i.story_scene_missing }">{{ i.story_scene_missing ? '⚠ 故事段已删除' : (i.state.lighting || i.state.time_of_day || '无状态') }}</span>
        </button>
        <p v-if="!instances.length" class="empty">还没有场景实例——先在上方创建</p>
      </aside>

      <div v-if="selectedInstance" class="main-area">
        <!-- SceneState 编辑 -->
        <div class="card">
          <div class="panel-head">
            <h3>SceneState（剧情时刻状态）</h3>
            <span v-if="selectedInstance.story_scene_missing" class="badge needs_fix">故事段已删除（悬空引用）</span>
            <span v-if="selectedInstance.missing_props?.length" class="badge needs_fix">缺失 Prop: {{ selectedInstance.missing_props.join(', ') }}</span>
            <button class="mini primary" :disabled="busy" @click="saveState">保存状态</button>
          </div>
          <div class="form-grid">
            <label>时段<input v-model="stateForm.time_of_day" placeholder="如 深夜 / 黎明" /></label>
            <label>天气<input v-model="stateForm.weather" placeholder="如 暴雨 / 晴" /></label>
            <label>灯光<input v-model="stateForm.lighting" placeholder="如 红色应急灯 / 正常白光" /></label>
            <label>环境条件<input v-model="stateForm.environment_conditions" placeholder="如 地面积水 / 落灰" /></label>
            <label class="full">备注（不进视觉生成）<input v-model="stateForm.notes" /></label>
          </div>
          <template v-if="instanceAnchors.length">
            <p class="hint">锚点状态（只保存跨镜头持续成立的结果；留空 = 无指定变化）</p>
            <div class="anchor-state-list">
              <div v-for="a in instanceAnchors" :key="a.anchor_id" class="form-row">
                <span class="anchor-name">{{ a.name }}</span>
                <input v-model="stateForm.anchor_states[a.anchor_id].condition" placeholder="如 broken / intact / （留空）" />
                <input v-model="stateForm.anchor_states[a.anchor_id].note" placeholder="补充（可选）" />
              </div>
            </div>
          </template>
          <template v-if="props.length">
            <p class="hint">道具位置（只引用 Resources Prop——回答『现在在哪里』）</p>
            <div class="anchor-state-list">
              <div v-for="p in props" :key="p.prop_id" class="form-row">
                <span class="anchor-name">{{ p.name }}</span>
                <select v-model="stateForm.prop_placements[p.prop_id].anchor_id">
                  <option value="">（不在本场景）</option>
                  <option v-for="a in instanceAnchors" :key="a.anchor_id" :value="a.anchor_id">{{ a.name }}</option>
                </select>
                <input v-model="stateForm.prop_placements[p.prop_id].state" placeholder="状态（如 intact）" />
              </div>
            </div>
          </template>
        </div>

        <!-- StateViews -->
        <div class="card">
          <div class="panel-head">
            <h3>状态视图（该剧情时刻长什么样）</h3>
            <button class="mini primary" :disabled="busy" @click="createStateViewAction">＋ 从 base 视角新建</button>
          </div>
          <p class="hint">一个「场景实例 × base 视角」一张状态图；生成用 base 批准参考 + 可见锚点状态 + 可见道具</p>
          <div class="view-cards">
            <button v-for="sv in stateViews" :key="sv.scene_instance_view_id" class="view-card" :class="{ active: selectedStateViewId === sv.scene_instance_view_id }" @click="selectStateView(sv.scene_instance_view_id)">
              <img v-if="approvedFileMap[sv.scene_instance_view_id]" :src="candidateUrl(approvedFileMap[sv.scene_instance_view_id])" class="cover" />
              <div v-else class="cover placeholder">未生成</div>
              <span class="view-name">{{ sv.label || baseViewLabel(sv.location_view_id) }}</span>
              <span class="badges">
                <em :class="'gs-' + sv.generation_status">{{ sv.generation_status }}</em>
                <em :class="'badge as-' + sv.approval_status">{{ sv.approval_status }}</em>
                <em v-if="svStatus(sv.scene_instance_view_id)?.is_stale" class="badge needs_fix">stale</em>
              </span>
            </button>
            <p v-if="!stateViews.length" class="empty">还没有状态视图——点上方「从 base 视角新建」</p>
          </div>

          <div v-if="selectedStateView" class="view-detail">
            <div class="detail-head">
              <h4>{{ selectedStateView.label || baseViewLabel(selectedStateView.location_view_id) }}</h4>
              <span v-if="svStatus(selectedStateView.scene_instance_view_id)?.is_stale" class="badge needs_fix">
                stale：{{ (svStatus(selectedStateView.scene_instance_view_id)?.stale_reasons || []).join('；') }}
              </span>
              <div class="actions">
                <select v-model="stateGenCount"><option v-for="n in 4" :key="n" :value="n">{{ n }} 张</option></select>
                <button class="mini primary" :disabled="busy" @click="generateStateViewAction">
                  {{ selectedStateView.approval_status === 'approved' ? '按当前状态重新生成' : '生成候选' }}
                </button>
                <button class="mini" :disabled="busy || !stateViewCandidates.length" @click="reviewStateViewAction">视觉审查</button>
                <button v-if="selectedStateView.approval_status === 'approved'" class="mini danger" :disabled="busy" @click="revokeStateViewAction">撤销批准</button>
              </div>
            </div>

            <div class="side-by-side">
              <div class="sbs-item">
                <span class="sbs-label">base 地点参考（{{ baseViewLabel(selectedStateView.location_view_id) }}）</span>
                <img v-if="approvedFileMap[selectedStateView.location_view_id]" :src="candidateUrl(approvedFileMap[selectedStateView.location_view_id])" />
                <div v-else class="img placeholder">base 未批准</div>
              </div>
              <div class="sbs-item">
                <span class="sbs-label">本状态视图</span>
                <img v-if="approvedFileMap[selectedStateView.scene_instance_view_id]" :src="candidateUrl(approvedFileMap[selectedStateView.scene_instance_view_id])" />
                <div v-else class="img placeholder">未批准</div>
                <div v-if="currentProjection" class="proj-chips">
                  <em v-if="currentProjection.time_of_day">时段: {{ currentProjection.time_of_day }}</em>
                  <em v-if="currentProjection.weather">天气: {{ currentProjection.weather }}</em>
                  <em v-if="currentProjection.lighting">灯光: {{ currentProjection.lighting }}</em>
                  <em v-if="currentProjection.environment_conditions">环境: {{ currentProjection.environment_conditions }}</em>
                  <em v-for="(st, aid) in currentProjection.anchor_states || {}" :key="aid">
                    {{ anchorName(String(aid)) }}: {{ st.condition }}
                  </em>
                  <em v-for="(pp, pid) in currentProjection.prop_placements || {}" :key="pid">
                    {{ propName(String(pid)) }} @ {{ anchorName(pp.anchor_id) }}
                  </em>
                </div>
              </div>
            </div>

            <div class="cand-grid">
              <div v-for="c in stateViewCandidates" :key="c.index" class="cand">
                <img :src="candidateUrl(c.file)" />
                <div class="cand-info">
                  <span class="meta">#{{ c.index }}</span>
                  <span v-if="approvedFileMap[selectedStateView.scene_instance_view_id] === c.file" class="badge approved">已批准</span>
                  <span v-else-if="stateReviewBadge(c.index)" class="badge" :class="stateReviewBadge(c.index)">{{ stateReviewBadge(c.index) }}</span>
                </div>
                <div v-if="stateReviewDims(c.index)" class="dims">
                  <em v-for="d in SIV_DIM_ORDER" :key="d.k">{{ d.label }}: {{ stateReviewDims(c.index)[d.k] || '—' }}</em>
                </div>
                <div v-if="stateReviewItems(c.index)" class="evidence">
                  <p v-for="it in stateReviewItems(c.index)" :key="it.item" class="review-note">
                    {{ it.item }}: {{ it.verdict }}<template v-if="it.expected">（期望 {{ it.expected }}）</template>
                  </p>
                  <p v-for="pp in stateReviewProps(c.index)" :key="pp.prop_id" class="review-note">
                    {{ propName(pp.prop_id) }}: {{ pp.verdict }}/{{ pp.observed }}
                  </p>
                </div>
                <div class="cand-actions">
                  <button class="mini primary" :disabled="busy || stateReviewBadge(c.index) !== 'pass'"
                          :title="stateReviewBadge(c.index) === 'pass' ? '' : '需要该候选审查通过后才能批准'"
                          @click="approveStateViewAction(c.index)">批准</button>
                </div>
                <p v-if="stateReviewNote(c.index)" class="review-note">{{ stateReviewNote(c.index) }}</p>
              </div>
              <p v-if="!stateViewCandidates.length" class="empty">还没有候选——点「生成候选」</p>
            </div>
          </div>
        </div>
      </div>
      <div v-else class="main-area empty">← 选择或创建一个场景实例</div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { storeToRefs } from 'pinia';
import { useProjectStore } from '@/store';
import { apiClient } from '@/api';

const store = useProjectStore();
const { projectName } = storeToRefs(store);

const tab = ref<'locations' | 'instances'>('locations');
const busy = ref(false);
const errorMsg = ref('');
const overview = ref<any>(null);

function fail(e: any) {
  if (e && typeof e === 'object' && 'error' in e) {
    errorMsg.value = `${e.error}: ${e.detail || ''}`;
  } else {
    errorMsg.value = e?.message || String(e);
  }
}
function guard(rev: string) {
  if (!rev) { errorMsg.value = '缺少 OCC 期望值——请刷新页面后重试'; return false; }
  return true;
}

const ANCHOR_KINDS = ['door', 'window', 'stairs', 'furniture', 'landmark', 'zone', 'entry', 'exit', 'other'];
const DIM_ORDER = [
  { k: 'location_identity', label: '身份' },
  { k: 'anchor_visibility', label: '锚点可见' },
  { k: 'anchor_consistency', label: '锚点一致' },
  { k: 'layout_consistency', label: '布局' },
  { k: 'view_consistency', label: '视角' },
  { k: 'visual_defects', label: '缺陷' },
] as const;
const SIV_DIM_ORDER = [
  { k: 'location_identity', label: '身份' },
  { k: 'view_consistency', label: '视角' },
  { k: 'anchor_consistency', label: '锚点' },
  { k: 'scene_state_consistency', label: '状态' },
  { k: 'lighting_weather_consistency', label: '光效' },
  { k: 'prop_placement_consistency', label: '道具' },
  { k: 'visual_defects', label: '缺陷' },
] as const;

// ===== TAB1 状态 =====
const locations = ref<any[]>([]);
const selectedLocationId = ref('');
const selectedLocation = ref<any>(null);
const anchors = ref<any[]>([]);
const layout = ref<any>(null);
const views = ref<any[]>([]);
const locForm = ref({ name: '', identity_description: '', description: '', style_notes: '' });
const showCreateLocation = ref(false);
const newLocation = ref({ name: '', identity_description: '' });
const showCreateAnchor = ref(false);
const newAnchor = ref({ name: '', kind: 'door', description: '', semantic_zone: '' });
const selectedAnchorId = ref('');
const editAnchor = ref<any>(null);
const layoutForm = ref({ orientation_convention: '', notes: '', placements: [] as any[] });
const showCreateView = ref(false);
const newView = ref({ label: '', camera_zone: '', look_direction: '', visible_anchor_ids: [] as string[] });
const selectedViewIdRef = ref('');
const selectedView = computed(() => views.value.find(v => v.view_id === selectedViewIdRef.value) || null);
const selectedViewId = computed({
  get: () => selectedViewIdRef.value,
  set: (v: string) => { selectedViewIdRef.value = v; },
});
const locGenCount = ref(2);
const viewCandidates = ref<{ index: number; file: string }[]>([]);
const approvedFileMap = ref<Record<string, string>>({});
const viewReview = ref<Record<string, any>>({});

// ===== TAB2 状态 =====
const instances = ref<any[]>([]);
const selectedInstanceId = ref('');
const selectedInstance = ref<any>(null);
const props = ref<any[]>([]);
const storyScenes = ref<any[]>([]);
const stateForm = ref<any>({ time_of_day: '', weather: '', lighting: '', environment_conditions: '', notes: '', anchor_states: {}, prop_placements: {} });
const stateViews = ref<any[]>([]);
const selectedStateViewId = ref('');
const selectedStateView = computed(() => stateViews.value.find(v => v.scene_instance_view_id === selectedStateViewId.value) || null);
const stateViewCandidates = ref<{ index: number; file: string }[]>([]);
const stateGenCount = ref(3);
const showCreateInstance = ref(false);
const newInstance = ref({ story_scene_id: '', location_id: '' });
const stateReview = ref<Record<string, any>>({});
const stateViewStatus = ref<Record<string, any>>({});

const currentProjection = computed(() => {
  const s = selectedStateView.value;
  if (!s) return null;
  return stateViewStatus.value[s.scene_instance_view_id]?.view_relevant_scene_state || null;
});
const instanceAnchors = computed(() => anchors.value);

function candidateUrl(file: string): string {
  return `/projects/${encodeURIComponent(projectName.value)}/${String(file).replace(/^\/+/, '')}`;
}
function locationName(id: string): string {
  return locations.value.find(l => l.location_id === id)?.name || id;
}
function baseViewLabel(vid: string): string {
  const hit = views.value.find(v => v.view_id === vid);
  if (hit) return hit.label || vid;
  const bl = stateViewStatus.value[selectedStateViewId.value]?.base_location_view;
  return bl?.label || vid;
}
function anchorName(aid: string): string {
  return anchors.value.find(a => a.anchor_id === aid)?.name || aid;
}
function propName(pid: string): string {
  return props.value.find(p => p.prop_id === pid)?.name || pid;
}
function visibleAnchorNames(v: any): string {
  return (v.visible_anchor_ids || []).map((aid: string) => anchorName(aid)).join('、') || '（无）';
}
function svStatus(id: string) {
  return stateViewStatus.value[id] || null;
}

// ===== 数据加载 =====
async function refreshOverview() {
  overview.value = await apiClient.sceneOverview(projectName.value).catch(() => null);
}
async function refreshLocations() {
  const r = await apiClient.sceneListLocations(projectName.value);
  locations.value = r.locations || [];
}
async function refreshLocationDetail() {
  if (!selectedLocationId.value) return;
  const [loc, ans, lay, vs] = await Promise.all([
    apiClient.sceneReadLocation(projectName.value, selectedLocationId.value),
    apiClient.sceneListAnchors(projectName.value, selectedLocationId.value),
    apiClient.sceneReadLayoutByLocation(projectName.value, selectedLocationId.value),
    apiClient.sceneListViews(projectName.value, selectedLocationId.value),
  ]);
  selectedLocation.value = loc;
  anchors.value = ans.anchors || [];
  layout.value = lay;
  views.value = vs.views || [];
  locForm.value = {
    name: loc.name || '', identity_description: loc.identity_description || '',
    description: loc.description || '', style_notes: loc.style_notes || '',
  };
  layoutForm.value = lay ? {
    orientation_convention: lay.orientation_convention || '',
    notes: lay.notes || '',
    placements: (lay.placements || []).map((p: any) => ({ ...p })),
  } : { orientation_convention: 'north_up', notes: '', placements: [] };
  // 各视角的批准文件 + 最新候选
  await Promise.all(views.value.map(async v => {
    const ap = await apiClient.sceneViewApprovals(projectName.value, v.view_id);
    const approved = (ap.approval_records || []).filter((r: any) => r.status === 'approved');
    if (approved.length) approvedFileMap.value[v.view_id] = approved[approved.length - 1].candidate_file;
    if (v.view_id === selectedViewIdRef.value) await refreshViewCandidates(v.view_id);
  }));
}
async function refreshViewCandidates(viewId: string) {
  const r = await apiClient.sceneViewGenerations(projectName.value, viewId, 8);
  const committed = (r.generation_records || []).filter((x: any) => x.status === 'committed');
  const latest = committed[committed.length - 1];
  viewCandidates.value = latest
    ? (latest.candidate_files || []).map((f: string, i: number) => ({ index: i, file: f }))
    : [];
}
async function refreshInstances() {
  const r = await apiClient.sceneListSceneInstances(projectName.value);
  instances.value = r.scene_instances || [];
}
async function refreshInstanceDetail() {
  if (!selectedInstanceId.value) return;
  const inst = await apiClient.sceneReadSceneInstance(projectName.value, selectedInstanceId.value);
  selectedInstance.value = inst;
  stateForm.value = {
    time_of_day: inst.state.time_of_day || '',
    weather: inst.state.weather || '',
    lighting: inst.state.lighting || '',
    environment_conditions: inst.state.environment_conditions || '',
    notes: inst.state.notes || '',
    anchor_states: Object.fromEntries(instanceAnchors.value.map(a => [
      a.anchor_id, { condition: inst.state.anchor_states?.[a.anchor_id]?.condition || '',
                     note: inst.state.anchor_states?.[a.anchor_id]?.note || '' },
    ])),
    prop_placements: Object.fromEntries(props.value.map(p => [
      p.prop_id, { anchor_id: inst.state.prop_placements?.[p.prop_id]?.anchor_id || '',
                   state: inst.state.prop_placements?.[p.prop_id]?.state || '' },
    ])),
  };
  const list = await apiClient.sceneListInstanceViews(projectName.value, selectedInstanceId.value);
  stateViews.value = list.scene_instance_views || [];
  await Promise.all(stateViews.value.map(async sv => {
    const [st, ap] = await Promise.all([
      apiClient.sceneReadInstanceViewStatus(projectName.value, sv.scene_instance_view_id),
      apiClient.sceneInstanceViewApprovals(projectName.value, sv.scene_instance_view_id),
    ]);
    stateViewStatus.value[sv.scene_instance_view_id] = st;
    const approved = (ap.approval_records || []).filter((r: any) => r.status === 'approved');
    if (approved.length) approvedFileMap.value[sv.scene_instance_view_id] = approved[approved.length - 1].candidate_file;
    if (sv.scene_instance_view_id === selectedStateViewId.value) await refreshStateViewCandidates(sv.scene_instance_view_id);
  }));
}
async function refreshStateViewCandidates(scivId: string) {
  const r = await apiClient.sceneInstanceViewGenerations(projectName.value, scivId, 8);
  const committed = (r.generation_records || []).filter((x: any) => x.status === 'committed');
  const latest = committed[committed.length - 1];
  stateViewCandidates.value = latest
    ? (latest.candidate_files || []).map((f: string, i: number) => ({ index: i, file: f }))
    : [];
}

// ===== TAB1 动作 =====
function switchTab(t: 'locations' | 'instances') {
  tab.value = t;
}
async function selectLocation(id: string) {
  selectedLocationId.value = id;
  selectedViewIdRef.value = '';
  selectedAnchorId.value = '';
  editAnchor.value = null;
  viewCandidates.value = [];
  busy.value = true;
  try { await refreshLocationDetail(); } catch (e) { fail(e); } finally { busy.value = false; }
}
async function createLocationAction() {
  busy.value = true; errorMsg.value = '';
  try {
    const ov = await apiClient.sceneOverview(projectName.value);
    const r = await apiClient.sceneCreateLocation(projectName.value, {
      name: newLocation.value.name,
      identity_description: newLocation.value.identity_description,
      expected_document_revision: ov.document_revision,
    });
    showCreateLocation.value = false;
    newLocation.value = { name: '', identity_description: '' };
    await refreshLocations();
    await selectLocation(r.target);
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function saveLocation() {
  if (!guard(selectedLocation.value?.revision)) return;
  busy.value = true; errorMsg.value = '';
  try {
    await apiClient.sceneUpdateLocation(projectName.value, selectedLocationId.value, {
      ...locForm.value, expected_revision: selectedLocation.value.revision,
    });
    await refreshLocationDetail();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function createAnchorAction() {
  busy.value = true; errorMsg.value = '';
  try {
    const ov = await apiClient.sceneOverview(projectName.value);
    await apiClient.sceneCreateAnchor(projectName.value, {
      location_id: selectedLocationId.value, ...newAnchor.value,
      expected_document_revision: ov.document_revision,
    });
    showCreateAnchor.value = false;
    newAnchor.value = { name: '', kind: 'door', description: '', semantic_zone: '' };
    await refreshLocationDetail();
    await refreshOverview();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
function startEditAnchor(a: any) {
  selectedAnchorId.value = a.anchor_id;
  editAnchor.value = { anchor_id: a.anchor_id, name: a.name, kind: a.kind,
                       description: a.description, semantic_zone: a.semantic_zone,
                       expected_revision: a.revision };
}
async function saveAnchor() {
  if (!editAnchor.value || !guard(editAnchor.value.expected_revision)) return;
  busy.value = true; errorMsg.value = '';
  try {
    await apiClient.sceneUpdateAnchor(projectName.value, editAnchor.value.anchor_id, {
      name: editAnchor.value.name, kind: editAnchor.value.kind,
      description: editAnchor.value.description, semantic_zone: editAnchor.value.semantic_zone,
      expected_revision: editAnchor.value.expected_revision,
    });
    editAnchor.value = null;
    await refreshLocationDetail();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function deleteAnchorAction(a: any) {
  if (!confirm(`删除锚点「${a.name}」？被引用（视角/布局/状态）时会被拒绝。`)) return;
  busy.value = true; errorMsg.value = '';
  try {
    await apiClient.sceneDeleteAnchor(projectName.value, a.anchor_id, a.revision);
    await refreshLocationDetail();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function createLayoutAction() {
  busy.value = true; errorMsg.value = '';
  try {
    const ov = await apiClient.sceneOverview(projectName.value);
    await apiClient.sceneCreateLayout(projectName.value, selectedLocationId.value, {
      orientation_convention: 'north_up', placements: [],
      expected_document_revision: ov.document_revision,
    });
    await refreshLocationDetail();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function saveLayout() {
  if (!guard(layout.value?.revision)) return;
  busy.value = true; errorMsg.value = '';
  try {
    await apiClient.sceneUpdateLayout(projectName.value, layout.value.layout_id, {
      orientation_convention: layoutForm.value.orientation_convention,
      notes: layoutForm.value.notes,
      placements: layoutForm.value.placements.filter(p => p.anchor_id),
      expected_revision: layout.value.revision,
    });
    await refreshLocationDetail();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function createViewAction() {
  busy.value = true; errorMsg.value = '';
  try {
    const ov = await apiClient.sceneOverview(projectName.value);
    const r = await apiClient.sceneCreateView(projectName.value, {
      location_id: selectedLocationId.value, ...newView.value,
      expected_document_revision: ov.document_revision,
    });
    showCreateView.value = false;
    newView.value = { label: '', camera_zone: '', look_direction: '', visible_anchor_ids: [] };
    await refreshLocationDetail();
    await selectView(r.target);
    await refreshOverview();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function selectView(viewId: string) {
  selectedViewIdRef.value = viewId;
  viewReview.value = {};
  busy.value = true;
  try { await refreshViewCandidates(viewId); } catch (e) { fail(e); } finally { busy.value = false; }
}
async function generateViewAction() {
  if (!guard(selectedView.value?.revision)) return;
  busy.value = true; errorMsg.value = '';
  try {
    await apiClient.sceneGenerateLocationView(projectName.value, selectedView.value.view_id, {
      expected_revision: selectedView.value.revision,
      candidate_count: locGenCount.value,
    });
    await refreshLocationDetail();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function reviewViewAction() {
  busy.value = true; errorMsg.value = '';
  try {
    const r = await apiClient.sceneReviewLocationView(projectName.value, selectedView.value.view_id);
    viewReview.value[selectedView.value.view_id] = r;
    await refreshLocationDetail();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function approveViewAction(candidateIndex: number) {
  busy.value = true; errorMsg.value = '';
  try {
    const res = await apiClient.resourcesList(projectName.value);
    await apiClient.sceneApproveLocationView(projectName.value, selectedView.value.view_id, {
      candidate_index: candidateIndex,
      expected_revision: selectedView.value.revision,
      expected_resources_document_revision: res.document_revision,
    });
    await refreshLocationDetail();
    await refreshOverview();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function revokeViewAction() {
  if (!guard(selectedView.value?.revision)) return;
  if (!confirm('撤销该视角的批准？（生成/审查/批准历史保留）')) return;
  busy.value = true; errorMsg.value = '';
  try {
    await apiClient.sceneRevokeLocationView(projectName.value, selectedView.value.view_id, {
      expected_revision: selectedView.value.revision, reason: 'UI revoke' });
    await refreshLocationDetail();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
function viewReviewFor(): any {
  return viewReview.value[selectedViewIdRef.value] || null;
}
function viewReviewCandidate(index: number): any {
  return viewReviewFor()?.candidate_reviews?.find((c: any) => c.candidate_index === index) || null;
}
function viewReviewBadge(index: number): string {
  return viewReviewCandidate(index)?.overall || '';
}
function viewReviewDims(index: number): any {
  return viewReviewCandidate(index)?.dimensions || null;
}
function viewReviewNote(index: number): string {
  return viewReviewCandidate(index)?.summary || '';
}

// ===== TAB2 动作 =====
async function selectInstance(id: string) {
  selectedInstanceId.value = id;
  selectedStateViewId.value = '';
  stateViewCandidates.value = [];
  busy.value = true;
  try {
    const shots = await apiClient.getProjectShots(projectName.value).catch(() => ({ scenes: [] }));
    storyScenes.value = shots.scenes || [];
    await refreshInstanceDetail();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function createInstanceAction() {
  busy.value = true; errorMsg.value = '';
  try {
    const ov = await apiClient.sceneOverview(projectName.value);
    const r = await apiClient.sceneCreateSceneInstance(projectName.value, {
      story_scene_id: newInstance.value.story_scene_id,
      location_id: newInstance.value.location_id,
      expected_document_revision: ov.document_revision,
    });
    showCreateInstance.value = false;
    newInstance.value = { story_scene_id: '', location_id: '' };
    await refreshInstances();
    await refreshOverview();
    await selectInstance(r.target);
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function saveState() {
  if (!guard(selectedInstance.value?.revision)) return;
  busy.value = true; errorMsg.value = '';
  try {
    const f = stateForm.value;
    await apiClient.scenePatchSceneState(projectName.value, selectedInstanceId.value, {
      time_of_day: f.time_of_day, weather: f.weather, lighting: f.lighting,
      environment_conditions: f.environment_conditions, notes: f.notes,
      anchor_states: Object.fromEntries(Object.entries(f.anchor_states)
        .filter(([, v]: any) => v.condition)
        .map(([k, v]: any) => [k, { condition: v.condition, note: v.note }])),
      prop_placements: Object.fromEntries(Object.entries(f.prop_placements)
        .filter(([, v]: any) => v.anchor_id)
        .map(([k, v]: any) => [k, { anchor_id: v.anchor_id, state: v.state, note: '' }])),
      expected_revision: selectedInstance.value.revision,
    });
    await refreshInstanceDetail();
    await refreshInstances();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function selectStateView(id: string) {
  selectedStateViewId.value = id;
  stateReview.value = {};
  busy.value = true;
  try { await refreshStateViewCandidates(id); } catch (e) { fail(e); } finally { busy.value = false; }
}
async function createStateViewAction() {
  busy.value = true; errorMsg.value = '';
  try {
    const baseViews = views.value.filter(v => v.approval_status === 'approved');
    if (!baseViews.length) { errorMsg.value = '该地点还没有 approved 的 base 视角——先到「地点 · 视角库」生成并批准'; return; }
    const taken = new Set(stateViews.value.map((v: any) => v.location_view_id));
    const free = baseViews.find(v => !taken.has(v.view_id));
    if (!free) { errorMsg.value = '所有 approved base 视角都已有状态视图（可直接「按当前状态重新生成」）'; return; }
    const ov = await apiClient.sceneOverview(projectName.value);
    const r = await apiClient.sceneCreateInstanceView(projectName.value, {
      scene_instance_id: selectedInstanceId.value, location_view_id: free.view_id,
      label: `${free.label || free.view_id} · ${stateForm.value.time_of_day || '状态'}`,
      expected_document_revision: ov.document_revision,
    });
    await refreshInstanceDetail();
    await selectStateView(r.target);
    await refreshOverview();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function generateStateViewAction() {
  if (!guard(selectedStateView.value?.revision)) return;
  busy.value = true; errorMsg.value = '';
  try {
    await apiClient.sceneGenerateInstanceView(projectName.value, selectedStateView.value.scene_instance_view_id, {
      expected_revision: selectedStateView.value.revision,
      candidate_count: stateGenCount.value, include_peer: true,
    });
    await refreshInstanceDetail();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function reviewStateViewAction() {
  busy.value = true; errorMsg.value = '';
  try {
    const r = await apiClient.sceneReviewInstanceView(projectName.value, selectedStateView.value.scene_instance_view_id);
    stateReview.value[selectedStateView.value.scene_instance_view_id] = r;
    await refreshInstanceDetail();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function approveStateViewAction(candidateIndex: number) {
  busy.value = true; errorMsg.value = '';
  try {
    const res = await apiClient.resourcesList(projectName.value);
    await apiClient.sceneApproveInstanceView(projectName.value, selectedStateView.value.scene_instance_view_id, {
      candidate_index: candidateIndex,
      expected_revision: selectedStateView.value.revision,
      expected_resources_document_revision: res.document_revision,
    });
    await refreshInstanceDetail();
    await refreshOverview();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function revokeStateViewAction() {
  if (!guard(selectedStateView.value?.revision)) return;
  if (!confirm('撤销该状态视图的批准？（历史保留）')) return;
  busy.value = true; errorMsg.value = '';
  try {
    await apiClient.sceneRevokeInstanceView(projectName.value, selectedStateView.value.scene_instance_view_id, {
      expected_revision: selectedStateView.value.revision, reason: 'UI revoke' });
    await refreshInstanceDetail();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
function stateReviewFor(): any {
  return stateReview.value[selectedStateViewId.value] || null;
}
function stateReviewCandidate(index: number): any {
  return stateReviewFor()?.candidate_reviews?.find((c: any) => c.candidate_index === index) || null;
}
function stateReviewBadge(index: number): string {
  return stateReviewCandidate(index)?.overall || '';
}
function stateReviewDims(index: number): any {
  return stateReviewCandidate(index)?.dimensions || null;
}
function stateReviewNote(index: number): string {
  return stateReviewCandidate(index)?.summary || '';
}
function stateReviewItems(index: number): any[] {
  return stateReviewCandidate(index)?.scene_state_items || [];
}
function stateReviewProps(index: number): any[] {
  return stateReviewCandidate(index)?.per_prop || [];
}

onMounted(async () => {
  busy.value = true;
  try {
    await Promise.all([
      refreshOverview(), refreshLocations(), refreshInstances(),
      apiClient.listProps(projectName.value).then(r => { props.value = r.props || []; }).catch(() => {}),
    ]);
  } catch (e) { fail(e); } finally { busy.value = false; }
});
</script>

<style scoped>
.scene-page { padding: 20px 24px; color: var(--text-1, #e8e8ea); display: flex; flex-direction: column; gap: 14px; }
.scene-header { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.type-tabs { display: flex; gap: 4px; background: rgba(128,128,128,.12); border-radius: 8px; padding: 3px; }
.type-tabs button { border: 0; background: transparent; color: inherit; padding: 5px 14px; border-radius: 6px; cursor: pointer; opacity: .7; }
.type-tabs button.active { background: var(--bg-3, rgba(255,255,255,.14)); opacity: 1; font-weight: 600; }
.ov { font-size: 12px; opacity: .6; }
.proj-name { margin-left: auto; opacity: .55; font-size: 13px; }
.scene-error { background: rgba(220,80,80,.14); border: 1px solid rgba(220,80,80,.4); border-radius: 8px; padding: 8px 12px; display: flex; justify-content: space-between; gap: 12px; font-size: 13px; }
.scene-error .dismiss { border: 0; background: none; color: inherit; cursor: pointer; font-size: 15px; }
.scene-busy { background: rgba(120,160,255,.12); border: 1px solid rgba(120,160,255,.35); border-radius: 8px; padding: 6px 12px; font-size: 12px; }
.split { display: flex; gap: 18px; align-items: flex-start; min-height: 300px; }
.side-list { width: 260px; flex-shrink: 0; display: flex; flex-direction: column; gap: 6px; }
.side-head { display: flex; justify-content: space-between; align-items: center; font-size: 13px; opacity: .8; margin-bottom: 4px; }
.side-item { text-align: left; border: 1px solid rgba(128,128,128,.25); background: transparent; color: inherit; border-radius: 8px; padding: 8px 10px; cursor: pointer; display: flex; flex-direction: column; gap: 2px; }
.side-item.active { border-color: #6ea8ff; box-shadow: 0 0 0 1px #6ea8ff inset; }
.side-item .sub { font-size: 11px; opacity: .55; }
.side-item .sub.stale { color: #ffb59d; opacity: 1; }
.main-area { flex: 1; display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.card { border: 1px solid rgba(128,128,128,.2); border-radius: 12px; padding: 14px; display: flex; flex-direction: column; gap: 10px; }
.panel-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.panel-head h3, .detail-head h4 { margin: 0; font-size: 15px; }
.meta { font-size: 12px; opacity: .55; }
.inline-form { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; background: rgba(128,128,128,.08); padding: 10px; border-radius: 8px; }
.inline-form.col { flex-direction: column; align-items: stretch; }
.inline-form input, .inline-form select, .inline-form textarea,
.form-grid input, .form-grid select, .form-grid textarea,
.form-row input, .form-row select, .detail-head select {
  background: rgba(128,128,128,.12); border: 1px solid rgba(128,128,128,.3); color: inherit;
  border-radius: 6px; padding: 6px 9px; font-size: 13px; min-width: 0; font-family: inherit;
}
.inline-form input { flex: 1; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 12px; }
.form-grid label, .inline-label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; opacity: .85; }
.form-grid label.full { grid-column: 1 / -1; }
.form-row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.form-row > * { flex-shrink: 0; }
.form-row input, .form-row select { flex: 1; min-width: 90px; }
.grow { flex: 1; }
.mini { border: 1px solid rgba(128,128,128,.35); background: transparent; color: inherit; border-radius: 6px; padding: 4px 10px; font-size: 12px; cursor: pointer; }
.mini.primary { border-color: #5a8dee; color: #9ec2ff; }
.mini.danger { border-color: rgba(220,90,90,.6); color: #ff9d9d; }
.mini:disabled { opacity: .4; cursor: not-allowed; }
.anchor-list, .placement-list, .anchor-state-list { display: flex; flex-direction: column; gap: 6px; }
.anchor-row { display: flex; gap: 8px; align-items: center; border: 1px solid rgba(128,128,128,.18); border-radius: 8px; padding: 6px 8px; }
.anchor-row.active { border-color: #6ea8ff; }
.anchor-main { flex: 1; display: flex; gap: 8px; align-items: center; background: none; border: 0; color: inherit; cursor: pointer; text-align: left; padding: 0; }
.anchor-name { font-size: 13px; font-weight: 600; min-width: 90px; }
.anchor-desc { font-size: 12px; opacity: .6; flex: 1; }
.anchor-ops { display: flex; gap: 6px; }
.badge { font-style: normal; font-size: 10px; padding: 1px 6px; border-radius: 8px; background: rgba(128,128,128,.2); }
.badge.kind { background: rgba(140,120,255,.2); color: #c9bcff; }
.badge.approved { background: rgba(90,180,90,.25); color: #9fe0a8; }
.badge.pass { background: rgba(90,160,255,.2); color: #a9c9ff; }
.badge.needs_fix { background: rgba(230,120,90,.25); color: #ffb59d; }
.badge.uncertain { background: rgba(220,190,90,.2); color: #ffe6a1; }
.badge.rejected { background: rgba(220,90,90,.25); color: #ff9d9d; }
.badge.unreviewed { opacity: .6; }
.badge.stale { background: rgba(230,140,60,.25); color: #ffcf9d; font-weight: 600; }
.gs-generating { color: #ffe6a1; } .gs-failed { color: #ff9d9d; } .gs-ready { color: #9fe0a8; }
.view-cards { display: flex; gap: 12px; flex-wrap: wrap; }
.view-card { width: 170px; border: 1px solid rgba(128,128,128,.25); background: transparent; color: inherit; border-radius: 10px; padding: 8px; cursor: pointer; display: flex; flex-direction: column; gap: 5px; }
.view-card.active { border-color: #6ea8ff; box-shadow: 0 0 0 1px #6ea8ff inset; }
.view-card .cover { width: 100%; height: 105px; object-fit: cover; border-radius: 6px; background: rgba(128,128,128,.15); }
.cover.placeholder, .img.placeholder { display: flex; align-items: center; justify-content: center; opacity: .4; font-size: 12px; height: 105px; }
.img.placeholder { height: 100%; min-height: 120px; }
.view-name { font-size: 13px; text-align: left; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.view-detail { border-top: 1px solid rgba(128,128,128,.2); padding-top: 12px; display: flex; flex-direction: column; gap: 10px; }
.detail-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.detail-head .actions { margin-left: auto; display: flex; gap: 8px; align-items: center; }
.hint { margin: 0; font-size: 12px; opacity: .5; }
.approved-strip { display: flex; }
.approved-main { position: relative; max-width: 480px; }
.approved-main img { width: 100%; border-radius: 8px; display: block; }
.approved-main .badge { position: absolute; right: 8px; top: 8px; }
.cand-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 12px; }
.cand { border: 1px solid rgba(128,128,128,.25); border-radius: 10px; padding: 8px; display: flex; flex-direction: column; gap: 6px; }
.cand img { width: 100%; height: 130px; object-fit: cover; border-radius: 6px; background: rgba(128,128,128,.15); }
.cand-info { display: flex; gap: 6px; align-items: center; min-height: 18px; }
.cand-actions { display: flex; gap: 6px; }
.dims { display: flex; gap: 4px; flex-wrap: wrap; }
.dims em { font-style: normal; font-size: 10px; opacity: .8; background: rgba(128,128,128,.12); border-radius: 6px; padding: 1px 5px; }
.evidence { display: flex; flex-direction: column; gap: 2px; max-height: 90px; overflow: auto; }
.review-note { margin: 0; font-size: 11px; opacity: .65; }
.empty { opacity: .5; font-size: 13px; padding: 12px 4px; }
.check-row { align-items: flex-start; }
.check { display: inline-flex; gap: 4px; align-items: center; font-size: 12px; }
.side-by-side { display: flex; gap: 14px; flex-wrap: wrap; }
.sbs-item { flex: 1; min-width: 300px; display: flex; flex-direction: column; gap: 6px; }
.sbs-item img { width: 100%; border-radius: 8px; display: block; background: rgba(128,128,128,.15); }
.sbs-label { font-size: 12px; opacity: .6; }
.proj-chips { display: flex; gap: 5px; flex-wrap: wrap; }
.proj-chips em { font-style: normal; font-size: 10px; background: rgba(90,160,255,.15); color: #a9c9ff; border-radius: 6px; padding: 1px 6px; }
</style>
