<template>
  <div class="scene-page">
    <!-- 无项目 -->
    <div v-if="!projectName" class="no-project">
      <p>还没有选择项目</p>
      <button class="btn btn-primary" @click="router.push('/projects')">前往项目序列</button>
    </div>

    <template v-else>
      <!-- 保存/就绪标志由 useTopbarStatus 上报，显示在顶栏最右上角 -->

      <div v-if="errorMsg" class="error-strip">
        <span>{{ errorMsg }}</span>
        <button class="icon-btn" @click="errorMsg = ''">×</button>
      </div>

      <div class="workbench">
        <!-- ============ 左：分组列表 ============ -->
        <aside class="side">
          <div class="group-switch">
            <button :class="{ on: tab === 'locations' }" @click="switchTab('locations')">地点</button>
            <button :class="{ on: tab === 'instances' }" @click="switchTab('instances')">布景状态</button>
          </div>

          <!-- 地点列表 -->
          <div v-if="tab === 'locations'" class="side-scroll">
            <div class="side-block-head">
              <span class="section-label">地点</span>
              <span class="meta mono-num" v-if="locations.length">{{ locations.length }}</span>
              <span class="spacer"></span>
              <button class="btn btn-sm btn-outline" @click="showCreateLocation = !showCreateLocation">+ 新建</button>
            </div>

            <form v-if="showCreateLocation" class="side-form" @submit.prevent="createLocationAction">
              <input v-model="newLocation.name" placeholder="名称（如 老城站台）" required />
              <textarea v-model="newLocation.identity_description" rows="2"
                placeholder="空间身份（建议英文，进生成提示词）：stable spatial identity…"></textarea>
              <button class="btn btn-sm btn-primary" type="submit" :disabled="busy">创建</button>
            </form>

            <div
              v-for="l in locations" :key="l.location_id"
              class="item" :class="{ on: selectedLocationId === l.location_id }"
              @click="selectLocation(l.location_id)"
            >
              <span class="nm">{{ l.name || '(未命名地点)' }}</span>
              <span class="sub mono-num">{{ l.anchor_ids.length }} 锚点 · {{ l.view_ids.length }} 视角{{ l.layout_id ? ' · 有布局' : '' }}</span>
            </div>
            <div v-if="!locations.length" class="empty-mini">还没有地点——先创建一个（如 老城站台 / 城际车厢）</div>
          </div>

          <!-- 布景状态列表 -->
          <div v-else class="side-scroll">
            <div class="side-block-head">
              <span class="section-label">布景状态</span>
              <span class="meta mono-num" v-if="instances.length">{{ instances.length }}</span>
              <span class="spacer"></span>
              <button class="btn btn-sm btn-outline" @click="showCreateInstance = !showCreateInstance">+ 新建</button>
            </div>

            <form v-if="showCreateInstance" class="side-form" @submit.prevent="createInstanceAction">
              <label>故事段（当前后端必填）
                <select v-model="newInstance.story_scene_id" required>
                  <option value="" disabled>选择</option>
                  <option v-for="s in storyScenes" :key="s.scene_id" :value="s.scene_id">{{ s.scene_id }}（{{ (s.summary || '').slice(0, 12) }}）</option>
                </select>
              </label>
              <label>地点
                <select v-model="newInstance.location_id" required>
                  <option value="" disabled>选择</option>
                  <option v-for="l in locations" :key="l.location_id" :value="l.location_id">{{ l.name || '(未命名)' }}</option>
                </select>
              </label>
              <button class="btn btn-sm btn-primary" type="submit" :disabled="busy || !locations.length">创建</button>
            </form>

            <div
              v-for="i in instances" :key="i.scene_instance_id"
              class="item" :class="{ on: selectedInstanceId === i.scene_instance_id }"
              @click="selectInstance(i.scene_instance_id)"
            >
              <span class="nm">{{ instanceTitle(i) }}</span>
              <span class="sub">
                <span class="dot" :class="{ fail: i.story_scene_missing }"></span>{{ locationName(i.location_id) }}
                {{ i.story_scene_missing ? ' · 故事段已删除' : '' }}
              </span>
            </div>
            <div v-if="!instances.length" class="empty-mini">还没有布景状态——一条剧幕 × 一个地点 = 一个状态版本</div>
          </div>
        </aside>

        <!-- ============ 右：详情 ============ -->
        <section class="detail">
          <!-- ===== 地点模式 ===== -->
          <template v-if="tab === 'locations'">
            <template v-if="selectedLocation">
              <div class="dt-head">
                <span class="dt-cover thumb-ph" @click="viewCoverMap[selectedLocationId] && openPreview(candidateUrl(viewCoverMap[selectedLocationId]))">
                  <img v-if="viewCoverMap[selectedLocationId]" :src="candidateUrl(viewCoverMap[selectedLocationId])" alt="" />
                </span>
                <div class="dt-main">
                  <div class="dt-name">
                    <h2>{{ locForm.name || '(未命名地点)' }}</h2>
                    <span class="dt-tag">地点</span>
                    <span class="dt-hint">双击名称可改（在下方「地点身份」）</span>
                  </div>
                  <p class="dt-desc">{{ locForm.identity_description || locForm.description || '（还没有空间身份描述）' }}</p>
                  <div class="dt-stats">
                    <span class="dt-stat">锚点 <b>{{ anchors.length }}</b></span>
                    <span class="dt-stat">视角 <b>{{ views.length }}</b>（已批准 <b>{{ approvedViewCount }}</b>）</span>
                    <span class="dt-stat">布局 <b>{{ layout ? '有' : '无' }}</b></span>
                    <span class="dt-stat">布景状态 <b>{{ locationInstanceCount }}</b></span>
                  </div>
                </div>
                <div class="dt-actions">
                  <button class="btn btn-primary btn-sm" :disabled="busy" @click="saveLocation">保存身份</button>
                  <button class="btn btn-outline btn-sm" :disabled="busy" @click="showCreateView = !showCreateView">+ 新建视角</button>
                </div>
              </div>

              <div class="dt-body">
                <!-- ① 地点身份 -->
                <section class="sec">
                  <div class="sec-head">
                    <span class="section-label">地点身份</span>
                    <span class="hint">空间身份 / 风格注记会被拼进英文提示词，建议用英文写</span>
                    <span class="spacer"></span>
                    <button class="btn btn-quiet btn-sm" :disabled="busy" @click="saveLocation">保存</button>
                  </div>
                  <div class="fields">
                    <div class="field-row"><span class="k">名称</span><span class="v"><input v-model="locForm.name" class="inline-in" placeholder="显示名（可用中文）" /></span></div>
                    <div class="field-row">
                      <span class="k">空间身份</span>
                      <span class="v"><textarea v-model="locForm.identity_description" class="inline-ta" rows="2"
                        placeholder="e.g. An old railway station platform with a cast-iron and frosted-glass canopy…（英文，长期稳定的结构描述）"></textarea></span>
                    </div>
                    <div class="field-row">
                      <span class="k">补充描述</span>
                      <span class="v"><textarea v-model="locForm.description" class="inline-ta" rows="2" placeholder="给人看的备注"></textarea></span>
                    </div>
                    <div class="field-row">
                      <span class="k">风格注记</span>
                      <span class="v"><textarea v-model="locForm.style_notes" class="inline-ta" rows="2"
                        placeholder="e.g. hand-drawn 2D cel-shaded anime style, muted cool grey-teal palette…（英文，材质与色调；不写时间/天气）"></textarea></span>
                    </div>
                  </div>
                </section>

                <!-- ② 视角库（场景图） -->
                <section class="sec">
                  <div class="sec-head">
                    <span class="section-label">视角库（场景图）</span>
                    <span class="meta">点选视角查看候选与审查</span>
                    <span class="spacer"></span>
                    <span class="hint" v-if="views.length">未批准的视角同样显示最新候选</span>
                  </div>

                  <form v-if="showCreateView" class="side-form wide" @submit.prevent="createViewAction">
                    <div class="form-row-3">
                      <input v-model="newView.label" placeholder="视角名（如 从东端看西侧站台）" required />
                      <input v-model="newView.camera_zone" placeholder="camera_zone（英文，如 east）" />
                      <input v-model="newView.look_direction" placeholder="look_direction（英文，如 west）" />
                    </div>
                    <div class="check-row">
                      <span class="hint">应可见锚点：</span>
                      <label v-for="a in anchors" :key="a.anchor_id" class="check">
                        <input type="checkbox" :value="a.anchor_id" v-model="newView.visible_anchor_ids" /> {{ a.name }}
                      </label>
                    </div>
                    <button class="btn btn-sm btn-primary" type="submit" :disabled="busy">创建视角</button>
                  </form>

                  <div class="views">
                    <div
                      v-for="v in views" :key="v.view_id"
                      class="view" :class="{ sel: selectedViewId === v.view_id }"
                      @click="selectView(v.view_id)"
                    >
                      <div class="v-ph" @click.stop="viewCoverMap[v.view_id] && openPreview(candidateUrl(viewCoverMap[v.view_id]))">
                        <img v-if="viewCoverMap[v.view_id]" :src="candidateUrl(viewCoverMap[v.view_id])" alt="" />
                        <span v-else class="v-empty">未生成</span>
                        <span v-if="v.approval_status === 'approved'" class="flag">已批准</span>
                      </div>
                      <div class="v-meta">
                        <div class="line">
                          <span class="dot" :class="genDotClass(v.generation_status)"></span>
                          <span class="v-name">{{ v.label || v.view_id }}</span>
                          <span class="spacer"></span>
                          <span class="v-state">{{ genStateLabel(v.generation_status) }}</span>
                        </div>
                        <div class="dir mono-num">{{ v.camera_zone || '—' }} → {{ v.look_direction || '—' }} · 应见 {{ v.visible_anchor_ids.length }} 锚点</div>
                      </div>
                    </div>
                    <div class="view cta" @click="showCreateView = true">
                      <div class="v-ph empty"><span class="cta-plus">+</span><span>新建视角</span></div>
                      <div class="v-meta"><div class="line"><span class="dot"></span>定义相机方位与应可见锚点</div></div>
                    </div>
                  </div>
                </section>

                <!-- ③ 候选与审查（选中视角） -->
                <section class="sec" v-if="selectedView">
                  <div class="sec-head">
                    <span class="section-label">候选与审查</span>
                    <span class="meta">{{ selectedView.label || selectedView.view_id }} · {{ viewCandidates.length }} 个候选</span>
                    <span class="spacer"></span>
                    <select v-model="locGenCount" class="mini-select">
                      <option v-for="n in 4" :key="n" :value="n">{{ n }} 张</option>
                    </select>
                    <button class="btn btn-outline btn-sm" :disabled="busy" @click="generateViewAction">
                      {{ selectedView.generation_status === 'ready' ? '重新生成候选' : '生成候选' }}
                    </button>
                    <button class="btn btn-outline btn-sm" :disabled="busy || !viewCandidates.length" @click="reviewViewAction">视觉审查</button>
                    <button v-if="selectedView.approval_status === 'approved'" class="btn btn-quiet btn-sm" :disabled="busy" @click="revokeViewAction">撤销批准</button>
                  </div>

                  <div v-if="needReview[selectedView.view_id]" class="notice-strip">
                    已撤销批准——需重新「视觉审查」后才能再次批准（后端 fail-closed 规则）
                  </div>

                  <div class="cand-grid">
                    <div v-for="c in viewCandidates" :key="c.index" class="cand" :class="{ approved: isApprovedCandidate(c) }">
                      <img :src="candidateUrl(c.file)" class="zoomable" @click.stop="openPreview(candidateUrl(c.file))" />
                      <div class="cand-info">
                        <span class="meta mono-num">#{{ c.index }}</span>
                        <span v-if="isApprovedCandidate(c)" class="tag solid">已批准</span>
                        <span v-else-if="viewReviewBadge(c.index)" class="tag">{{ reviewBadgeLabel(viewReviewBadge(c.index)) }}</span>
                      </div>
                      <div v-if="viewReviewDims(c.index)" class="dims">
                        <em v-for="d in DIM_ORDER" :key="d.k" :class="dimClass(viewReviewDims(c.index)[d.k])">
                          {{ d.label }} {{ dimLabel(viewReviewDims(c.index)[d.k]) }}
                        </em>
                      </div>
                      <div class="cand-actions">
                        <button
                          class="btn btn-sm btn-primary"
                          :disabled="busy || viewReviewBadge(c.index) !== 'pass' || !!needReview[selectedView.view_id]"
                          :title="approveDisabledTitle(c.index)"
                          @click="approveViewAction(c.index)"
                        >批准</button>
                      </div>
                      <p v-if="viewReviewNote(c.index)" class="review-note">{{ viewReviewNote(c.index) }}</p>
                    </div>
                    <div v-if="!viewCandidates.length" class="empty">
                      还没有候选——点「生成候选」。注意：生成会自动使用「已批准的同地点视角」作为身份参考；
                      想要真正不同的机位，需在无已批准视角时生成（参考策略问题见《前端测试发现/S-2》）。
                    </div>
                  </div>
                </section>

                <!-- ④ 空间锚点 -->
                <section class="sec">
                  <div class="sec-head">
                    <span class="section-label">空间锚点</span>
                    <span class="meta">门 / 窗 / 家具 / 楼梯…被视角引用后不可删除</span>
                    <span class="spacer"></span>
                    <button class="btn btn-sm btn-outline" @click="showCreateAnchor = !showCreateAnchor">+ 新建锚点</button>
                  </div>

                  <form v-if="showCreateAnchor" class="side-form wide" @submit.prevent="createAnchorAction">
                    <div class="form-row-3">
                      <input v-model="newAnchor.name" placeholder="名称（如 西侧顶棚支柱）" required />
                      <select v-model="newAnchor.kind">
                        <option v-for="k in ANCHOR_KINDS" :key="k" :value="k">{{ kindLabel(k) }}</option>
                      </select>
                      <input v-model="newAnchor.semantic_zone" placeholder="语义区域（如 west_wall）" />
                    </div>
                    <textarea v-model="newAnchor.description" rows="2" placeholder="可见特征与方位（建议英文，进提示词）"></textarea>
                    <button class="btn btn-sm btn-primary" type="submit" :disabled="busy">创建</button>
                  </form>

                  <div class="rows">
                    <div v-for="a in anchors" :key="a.anchor_id" class="row">
                      <span class="nm">{{ a.name || a.anchor_id }} <span class="tag">{{ kindLabel(a.kind) }}</span></span>
                      <span class="desc">{{ a.description || '（无描述——建议补上可见特征与方位）' }}</span>
                      <span class="go">
                        <button class="btn btn-quiet btn-sm" @click="startEditAnchor(a)">编辑</button>
                        <button class="btn btn-quiet btn-sm" :disabled="busy" @click="deleteAnchorAction(a)">删除</button>
                      </span>
                    </div>
                    <div v-if="!anchors.length" class="empty-mini">还没有锚点——门、窗、家具、楼梯都是锚点</div>
                  </div>

                  <form v-if="editAnchor" class="side-form wide" @submit.prevent="saveAnchor">
                    <div class="form-row-3">
                      <input v-model="editAnchor.name" required />
                      <select v-model="editAnchor.kind">
                        <option v-for="k in ANCHOR_KINDS" :key="k" :value="k">{{ kindLabel(k) }}</option>
                      </select>
                      <input v-model="editAnchor.semantic_zone" placeholder="语义区域" />
                    </div>
                    <textarea v-model="editAnchor.description" rows="2"></textarea>
                    <div class="form-actions">
                      <button class="btn btn-sm btn-primary" type="submit" :disabled="busy">保存锚点</button>
                      <button class="btn btn-sm btn-quiet" type="button" @click="editAnchor = null">取消</button>
                    </div>
                  </form>
                </section>

                <!-- ⑤ 平面布局（折叠） -->
                <section class="sec">
                  <div class="fold" @click="layoutFolded = !layoutFolded">
                    <div class="fold-head">
                      <span class="section-label">平面布局</span>
                      <span class="hint">可选 · 只作为生成时的方位提示</span>
                      <span class="spacer"></span>
                      <span class="hint">{{ layoutFolded ? '展开 ▾' : '收起 ▴' }}</span>
                    </div>
                    <div class="fold-body" v-show="!layoutFolded" @click.stop>
                      <template v-if="layout">
                        <div class="form-row-inline">
                          <label class="inline-label">方位约定
                            <select v-model="layoutForm.orientation_convention">
                              <option value="north_up">north_up</option>
                              <option value="screen_north_up">screen_north_up</option>
                            </select>
                          </label>
                          <input v-model="layoutForm.notes" placeholder="备注" class="grow" />
                          <button class="btn btn-sm btn-primary" :disabled="busy" @click="saveLayout">保存布局</button>
                        </div>
                        <div class="place-list">
                          <div v-for="(p, i) in layoutForm.placements" :key="i" class="place-row">
                            <select v-model="p.anchor_id">
                              <option value="" disabled>锚点</option>
                              <option v-for="a in anchors" :key="a.anchor_id" :value="a.anchor_id">{{ a.name }}</option>
                            </select>
                            <input v-model="p.zone" placeholder="区域" />
                            <input v-model="p.x" type="number" step="0.05" min="0" max="1" placeholder="x 0~1" />
                            <input v-model="p.y" type="number" step="0.05" min="0" max="1" placeholder="y 0~1" />
                            <input v-model="p.note" placeholder="备注" class="grow" />
                            <button class="icon-btn" type="button" @click="layoutForm.placements.splice(i, 1)">×</button>
                          </div>
                          <button class="btn btn-sm btn-outline"
                            @click="layoutForm.placements.push({ anchor_id: '', zone: '', x: null, y: null, note: '' })">+ 添加放置项</button>
                        </div>
                      </template>
                      <template v-else>
                        <p class="hint">无布局——为生成提供方位 hint（可选）</p>
                        <button class="btn btn-sm btn-outline" :disabled="busy" @click="createLayoutAction">创建布局</button>
                      </template>
                    </div>
                  </div>
                </section>
              </div>
            </template>
            <div v-else class="select-prompt">选择或创建一个地点</div>
          </template>

          <!-- ===== 布景状态模式 ===== -->
          <template v-else>
            <template v-if="selectedInstance">
              <div class="dt-head">
                <span class="dt-cover thumb-ph" @click="ivCoverOf(selectedStateViewId) && openPreview(candidateUrl(ivCoverOf(selectedStateViewId)!))">
                  <img v-if="ivCoverOf(selectedStateViewId)" :src="candidateUrl(ivCoverOf(selectedStateViewId)!)" alt="" />
                </span>
                <div class="dt-main">
                  <div class="dt-name">
                    <h2>{{ instanceTitle(selectedInstance) }}</h2>
                    <span class="dt-tag">布景状态</span>
                    <span v-if="selectedInstance.story_scene_missing" class="tag fail">故事段已删除</span>
                  </div>
                  <p class="dt-desc">
                    {{ locationName(selectedInstance.location_id) }} 在本场剧幕中的状态版本——地点身份不变，只有环境与结果状态变化。
                  </p>
                  <div class="dt-stats">
                    <span class="dt-stat">地点 <b>{{ locationName(selectedInstance.location_id) }}</b></span>
                    <span class="dt-stat">实例视图 <b>{{ stateViews.length }}</b>（已批准 <b>{{ approvedIvCount }}</b>）</span>
                    <span class="dt-stat" v-if="selectedInstance.missing_props?.length"><span class="dot fail"></span>缺失道具 <b>{{ selectedInstance.missing_props.length }}</b></span>
                  </div>
                </div>
                <div class="dt-actions">
                  <button class="btn btn-primary btn-sm" :disabled="busy" @click="saveState">保存状态</button>
                  <button class="btn btn-outline btn-sm" :disabled="busy" @click="createStateViewAction">+ 实例视图</button>
                </div>
              </div>

              <div class="dt-body">
                <!-- ① 剧情状态 -->
                <section class="sec">
                  <div class="sec-head">
                    <span class="section-label">剧情状态</span>
                    <span class="hint">时段 / 天气 / 灯光 / 环境会被拼进英文提示词，建议用英文写</span>
                    <span class="spacer"></span>
                    <button class="btn btn-quiet btn-sm" :disabled="busy" @click="saveState">保存</button>
                  </div>
                  <div class="fields">
                    <div class="field-row"><span class="k">时段</span><span class="v"><input v-model="stateForm.time_of_day" class="inline-in" placeholder="e.g. late night, around 23:40" /></span></div>
                    <div class="field-row"><span class="k">天气</span><span class="v"><input v-model="stateForm.weather" class="inline-in" placeholder="e.g. heavy rain, wet reflective ground" /></span></div>
                    <div class="field-row"><span class="k">灯光</span><span class="v"><input v-model="stateForm.lighting" class="inline-in" placeholder="e.g. cold white platform ceiling lamps" /></span></div>
                    <div class="field-row"><span class="k">环境条件</span><span class="v"><input v-model="stateForm.environment_conditions" class="inline-in" placeholder="e.g. windy, water dripping from the canopy edge" /></span></div>
                    <div class="field-row"><span class="k">备注</span><span class="v"><input v-model="stateForm.notes" class="inline-in" placeholder="不进视觉生成" /></span></div>
                  </div>
                </section>

                <!-- ② 锚点状态 -->
                <section class="sec" v-if="instanceAnchors.length">
                  <div class="sec-head">
                    <span class="section-label">锚点状态</span>
                    <span class="hint">本场持续成立的结果状态；留空 = 无指定变化</span>
                  </div>
                  <div class="rows">
                    <div v-for="a in instanceAnchors" :key="a.anchor_id" class="row">
                      <span class="nm">{{ a.name }}</span>
                      <span class="desc">
                        <input v-model="stateForm.anchor_states[a.anchor_id].condition" class="inline-in" placeholder="如 ajar / wet /（留空）" />
                        <input v-model="stateForm.anchor_states[a.anchor_id].note" class="inline-in" placeholder="补充（可选）" />
                      </span>
                    </div>
                  </div>
                </section>

                <!-- ③ 道具位置 -->
                <section class="sec" v-if="props.length">
                  <div class="sec-head">
                    <span class="section-label">道具位置</span>
                    <span class="hint">只引用资源库的道具——回答「现在在哪里」</span>
                  </div>
                  <div class="rows">
                    <div v-for="p in props" :key="p.prop_id" class="row">
                      <span class="nm">{{ p.name }}</span>
                      <span class="desc">
                        <select v-model="stateForm.prop_placements[p.prop_id].anchor_id">
                          <option value="">（不在本场景）</option>
                          <option v-for="a in instanceAnchors" :key="a.anchor_id" :value="a.anchor_id">{{ a.name }}</option>
                        </select>
                        <input v-model="stateForm.prop_placements[p.prop_id].state" class="inline-in" placeholder="状态（如 intact）" />
                      </span>
                    </div>
                  </div>
                </section>

                <!-- ④ 实例视图 -->
                <section class="sec">
                  <div class="sec-head">
                    <span class="section-label">实例视图</span>
                    <span class="meta">同一场景多角度：每个已批准的地点视角 × 本场状态 = 一张实例参考图</span>
                    <span class="spacer"></span>
                    <button class="btn btn-outline btn-sm" :disabled="busy" @click="toggleCreateStateView">+ 选择角度创建</button>
                  </div>

                  <!-- 角度选择面板（可多选批量创建） -->
                  <div v-if="showCreateStateView" class="cvp">
                    <div class="cvp-head">
                      <span class="cvp-label">选择角度（该地点已批准、且本状态尚未创建的视角）</span>
                      <span class="spacer"></span>
                      <button v-if="freeBaseViews.length" class="btn btn-quiet btn-sm" @click="toggleAllFreeViews">
                        {{ createViewIds.length === freeBaseViews.length ? '全不选' : '全选' }}
                      </button>
                    </div>
                    <div v-if="freeBaseViews.length" class="cvp-rows">
                      <label v-for="v in freeBaseViews" :key="v.view_id" class="cvp-row">
                        <input type="checkbox" :value="v.view_id" v-model="createViewIds" />
                        <span class="nm">{{ v.label || v.view_id }}</span>
                        <span class="meta">{{ v.camera_zone || '—' }}<template v-if="v.look_direction"> · 看向 {{ v.look_direction }}</template></span>
                        <span class="meta">可见锚点 {{ (v.visible_anchor_ids || []).length }}</span>
                      </label>
                    </div>
                    <p v-else class="hint">没有可创建的角度——该地点需先有更多「已批准」的定位视角，或本状态已覆盖全部角度。</p>
                    <div class="cvp-foot">
                      <button class="btn btn-quiet btn-sm" @click="showCreateStateView = false">取消</button>
                      <button class="btn btn-primary btn-sm" :disabled="!createViewIds.length || busy" @click="createStateViewsAction">
                        {{ busy ? '创建中…' : `创建所选（${createViewIds.length}）` }}
                      </button>
                    </div>
                  </div>

                  <div class="views">
                    <div
                      v-for="sv in stateViews" :key="sv.scene_instance_view_id"
                      class="view" :class="{ sel: selectedStateViewId === sv.scene_instance_view_id }"
                      @click="selectStateView(sv.scene_instance_view_id)"
                    >
                      <div class="v-ph" @click.stop="onIvCoverClick(sv)">
                        <img v-if="ivCoverOf(sv.scene_instance_view_id)" :src="candidateUrl(ivCoverOf(sv.scene_instance_view_id)!)"
                          alt="" @error="ivBroken.add(sv.scene_instance_view_id); ivBroken = new Set(ivBroken)" />
                        <span v-else class="v-empty">未生成</span>
                        <span v-if="sv.approval_status === 'approved'" class="flag">已批准</span>
                      </div>
                      <div class="v-meta">
                        <div class="line">
                          <span class="dot" :class="genDotClass(sv.generation_status)"></span>
                          <span class="v-name">{{ sv.label || baseViewLabel(sv.location_view_id) }}</span>
                          <span class="spacer"></span>
                          <span class="v-state">
                            <template v-if="svStatus(sv.scene_instance_view_id)?.is_stale">stale</template>
                            <template v-else>{{ genStateLabel(sv.generation_status) }}</template>
                          </span>
                        </div>
                        <div class="dir">base：{{ baseViewLabel(sv.location_view_id) }}</div>
                      </div>
                    </div>
                    <div class="view cta" @click="toggleCreateStateView">
                      <div class="v-ph empty"><span class="cta-plus">+</span><span>生成实例视图</span></div>
                      <div class="v-meta"><div class="line"><span class="dot"></span>选择角度 + 带入本场状态</div></div>
                    </div>
                  </div>
                </section>

                <!-- ⑤ 选中实例视图：候选与审查 -->
                <section class="sec" v-if="selectedStateView">
                  <div class="sec-head">
                    <span class="section-label">候选与审查</span>
                    <span class="meta">{{ selectedStateView.label || baseViewLabel(selectedStateView.location_view_id) }}</span>
                    <span v-if="svStatus(selectedStateView.scene_instance_view_id)?.is_stale" class="tag fail">
                      stale：{{ (svStatus(selectedStateView.scene_instance_view_id)?.stale_reasons || []).join('；') }}
                    </span>
                    <span class="spacer"></span>
                    <select v-model="stateGenCount" class="mini-select">
                      <option v-for="n in 4" :key="n" :value="n">{{ n }} 张</option>
                    </select>
                    <button class="btn btn-outline btn-sm" :disabled="busy" @click="generateStateViewAction">
                      {{ selectedStateView.approval_status === 'approved' ? '按当前状态重新生成' : '生成候选' }}
                    </button>
                    <button class="btn btn-outline btn-sm" :disabled="busy || !stateViewCandidates.length" @click="reviewStateViewAction">视觉审查</button>
                    <button v-if="selectedStateView.approval_status === 'approved'" class="btn btn-quiet btn-sm" :disabled="busy" @click="revokeStateViewAction">撤销批准</button>
                  </div>

                  <div v-if="needReview[selectedStateView.scene_instance_view_id]" class="notice-strip">
                    已撤销批准——需重新「视觉审查」后才能再次批准
                  </div>

                  <div class="side-by-side">
                    <div class="sbs-item">
                      <span class="sbs-label">base 地点参考（{{ baseViewLabel(selectedStateView.location_view_id) }}）</span>
                      <img v-if="approvedFileMap[selectedStateView.location_view_id]"
                        :src="candidateUrl(approvedFileMap[selectedStateView.location_view_id])"
                        class="zoomable" @click.stop="openPreview(candidateUrl(approvedFileMap[selectedStateView.location_view_id]))" />
                      <div v-else class="img-ph">base 未批准</div>
                    </div>
                    <div class="sbs-item">
                      <span class="sbs-label">本状态（生成时投影的状态）</span>
                      <div class="proj-chips" v-if="currentProjection">
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
                      <div v-else class="img-ph small">（保存状态并生成后显示）</div>
                    </div>
                  </div>

                  <div class="cand-grid">
                    <div v-for="c in stateViewCandidates" :key="c.index" class="cand" :class="{ approved: isApprovedIvCandidate(c) }">
                      <img :src="candidateUrl(c.file)" class="zoomable" @click.stop="openPreview(candidateUrl(c.file))"
                        @error="(e: Event) => (e.target as HTMLImageElement).style.display = 'none'" />
                      <div class="cand-info">
                        <span class="meta mono-num">#{{ c.index }}</span>
                        <span v-if="isApprovedIvCandidate(c)" class="tag solid">已批准</span>
                        <span v-else-if="stateReviewBadge(c.index)" class="tag">{{ reviewBadgeLabel(stateReviewBadge(c.index)) }}</span>
                      </div>
                      <div v-if="stateReviewDims(c.index)" class="dims">
                        <em v-for="d in SIV_DIM_ORDER" :key="d.k" :class="dimClass(stateReviewDims(c.index)[d.k])">
                          {{ d.label }} {{ dimLabel(stateReviewDims(c.index)[d.k]) }}
                        </em>
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
                        <button
                          class="btn btn-sm btn-primary"
                          :disabled="busy || stateReviewBadge(c.index) !== 'pass' || !!needReview[selectedStateView.scene_instance_view_id]"
                          :title="approveDisabledTitleIv(c.index)"
                          @click="approveStateViewAction(c.index)"
                        >批准</button>
                      </div>
                      <p v-if="stateReviewNote(c.index)" class="review-note">{{ stateReviewNote(c.index) }}</p>
                    </div>
                    <div v-if="!stateViewCandidates.length" class="empty">
                      还没有候选——点「生成候选」。
                    </div>
                  </div>
                </section>
              </div>
            </template>
            <div v-else class="select-prompt">选择或创建一个布景状态</div>
          </template>
        </section>
      </div>

      <!-- 大图预览 -->
      <div v-if="previewImg" class="lightbox" @click="previewImg = ''">
        <img :src="previewImg" @click.stop />
        <button class="lightbox-close" @click="previewImg = ''">×</button>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, reactive, onMounted, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { useRouter } from 'vue-router';
import { useProjectStore } from '@/store';
import { useTopbarStatus } from '@/store/topbarStatus';
import { apiClient } from '@/api';

const router = useRouter();
const store = useProjectStore();
const { projectName } = storeToRefs(store);

const tab = ref<'locations' | 'instances'>('locations');
const busy = ref(false);
// 就绪/处理中标志：上报到顶栏最右上角
useTopbarStatus('scene-system', () => (busy.value
  ? { text: '处理中…（生成/审查可能需要数十秒）', tone: 'busy' as const }
  : { text: '就绪', tone: 'idle' as const }));
const previewImg = ref('');
function openPreview(url: string) { if (url) previewImg.value = url; }
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
const KIND_LABELS: Record<string, string> = {
  door: '门', window: '窗', stairs: '楼梯', furniture: '家具', landmark: '地标',
  zone: '区域', entry: '入口', exit: '出口', other: '其他',
};
function kindLabel(k: string): string { return KIND_LABELS[k] || k; }

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

function genStateLabel(s: string): string {
  const m: Record<string, string> = { none: '未生成', pending: '排队中', generating: '生成中', ready: '候选就绪', failed: '生成失败' };
  return m[s] || s;
}
function genDotClass(s: string): string {
  if (s === 'ready') return 'live';
  if (s === 'failed') return 'fail';
  if (s === 'generating' || s === 'pending') return 'on';
  return '';
}
function reviewBadgeLabel(b: string): string {
  const m: Record<string, string> = { pass: '审查通过', needs_fix: '需修正', uncertain: '不确定' };
  return m[b] || b;
}
function dimLabel(d: string): string {
  const m: Record<string, string> = { pass: '✓', concern: '!', fail: '✗' };
  return m[d] || '—';
}
function dimClass(d: string): string {
  return d === 'pass' ? 'ok' : d === 'fail' ? 'bad' : d === 'concern' ? 'warn' : '';
}

// ===== 地点（TAB1）状态 =====
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
const editAnchor = ref<any>(null);
const layoutForm = ref({ orientation_convention: '', notes: '', placements: [] as any[] });
const layoutFolded = ref(true);
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
// 视角封面：已批准图优先，否则最新候选（未审批也要显示）
const viewCoverMap = ref<Record<string, string>>({});
// 撤销批准后必须先重新审查（后端 409 实测行为）
const needReview = ref<Record<string, boolean>>({});

const approvedViewCount = computed(() => views.value.filter(v => v.approval_status === 'approved').length);
const locationInstanceCount = computed(() => instances.value.filter(i => i.location_id === selectedLocationId.value).length);

// ===== 布景状态（TAB2）状态 =====
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
// 已批准候选索引（approval_records 恢复；null = 未批准）
const ivApprovedIndex = ref<Record<string, number | null>>({});
// 实例视图候选路径推导（仅作老数据兜底；正式读取走 generation_records）
const ivBroken = reactive(new Set<string>());

const currentProjection = computed(() => {
  const s = selectedStateView.value;
  if (!s) return null;
  return stateViewStatus.value[s.scene_instance_view_id]?.view_relevant_scene_state || null;
});
const instanceAnchors = computed(() => anchors.value);
const approvedIvCount = computed(() => stateViews.value.filter(v => v.approval_status === 'approved').length);

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
function svStatus(id: string) {
  return stateViewStatus.value[id] || null;
}
function instanceTitle(i: any): string {
  const st = i.state || {};
  const bits = [st.time_of_day, st.weather, st.lighting].filter(Boolean).map((x: string) => x.split(',')[0].trim()).filter(Boolean);
  return bits.length ? bits.join(' · ') : (i.story_scene_id + ' 状态');
}

// 实例视图候选路径推导（S-1 临时兜底）：read 模型给 active_generation_id，路径模式稳定
function deriveIvCandidates(sv: any): string[] {
  const gen = sv?.active_generation_id || stateViewStatus.value[sv?.scene_instance_view_id]?.active_generation_id;
  if (!gen) return [];
  const base = `output/scene_system/scene_instances/${sv.scene_instance_id}/views/${sv.scene_instance_view_id}/generations/${gen}`;
  return [1, 2, 3, 4].map(n => `${base}/candidate_0${n}.png`);
}
function ivCoverOf(scivId: string): string | null {
  const sv = stateViews.value.find(v => v.scene_instance_view_id === scivId);
  if (!sv || ivBroken.has(scivId)) return null;
  return deriveIvCandidates(sv)[0] || null;
}
/** 实例视图封面点击：总是选中该视图；有封面时同时打开大图预览。 */
function onIvCoverClick(sv: any) {
  selectStateView(sv.scene_instance_view_id);
  const cover = ivCoverOf(sv.scene_instance_view_id);
  if (cover) openPreview(candidateUrl(cover));
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
  // 每个视角：已批准图 + 历史审查记录 + 最新候选（封面兜底用）
  await Promise.all(views.value.map(async v => {
    const ap = await apiClient.sceneViewApprovals(projectName.value, v.view_id);
    const approved = (ap.approval_records || []).filter((r: any) => r.status === 'approved');
    if (approved.length) approvedFileMap.value[v.view_id] = approved[approved.length - 1].candidate_file;
    const g = await apiClient.sceneViewGenerations(projectName.value, v.view_id, 8).catch(() => ({ generation_records: [] }));
    const committed = (g.generation_records || []).filter((x: any) => x.status === 'committed');
    const latest = committed[committed.length - 1];
    const files: string[] = latest?.candidate_files || [];
    // F-1：未审批也显示——封面 = 已批准图，否则最新候选
    viewCoverMap.value[v.view_id] = approvedFileMap.value[v.view_id] || files[0] || '';
    if (v.view_id === selectedViewIdRef.value) {
      viewCandidates.value = files.map((f: string, i: number) => ({ index: i, file: f }));
      await loadViewReview(v.view_id);
    }
  }));
}
async function loadViewReview(viewId: string) {
  // 历史审查记录（刷新后恢复"哪些候选已通过"→ 批准按钮可用性）
  const r = await apiClient.sceneViewReviews(projectName.value, viewId, 5).catch(() => ({ review_records: [] }));
  const recs = r.review_records || [];
  const latest = recs[recs.length - 1];
  if (latest?.candidate_reviews) {
    viewReview.value[viewId] = { candidate_reviews: latest.candidate_reviews };
  }
}
async function refreshViewCandidates(viewId: string) {
  const r = await apiClient.sceneViewGenerations(projectName.value, viewId, 8);
  const committed = (r.generation_records || []).filter((x: any) => x.status === 'committed');
  const latest = committed[committed.length - 1];
  viewCandidates.value = latest
    ? (latest.candidate_files || []).map((f: string, i: number) => ({ index: i, file: f }))
    : [];
  await loadViewReview(viewId);
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
    const st = await apiClient.sceneReadInstanceViewStatus(projectName.value, sv.scene_instance_view_id).catch(() => null);
    if (st) stateViewStatus.value[sv.scene_instance_view_id] = st;
  }));
  // 候选：优先正式接口（S-1 落地前为空），回退到路径推导
  if (selectedStateViewId.value) refreshStateViewCandidates(selectedStateViewId.value);
}
async function refreshStateViewCandidates(scivId: string) {
  const sv = stateViews.value.find(v => v.scene_instance_view_id === scivId);
  if (!sv) { stateViewCandidates.value = []; return; }
  const [gen, rev, appr] = await Promise.all([
    apiClient.sceneInstanceViewGenerations(projectName.value, scivId, 8).catch(() => ({ generation_records: [] })),
    apiClient.sceneInstanceViewReviews(projectName.value, scivId, 8).catch(() => ({ review_records: [] })),
    apiClient.sceneInstanceViewApprovals(projectName.value, scivId).catch(() => ({ approval_records: [] })),
  ]);
  // 候选：正式 generation record 优先；无记录（老数据）再回退路径推导
  const committed = (gen.generation_records || []).filter((x: any) => x.status === 'committed');
  const latest = committed[committed.length - 1];
  if (latest?.candidate_files?.length) {
    stateViewCandidates.value = latest.candidate_files.map((f: string, i: number) => ({ index: i, file: f }));
  } else {
    stateViewCandidates.value = deriveIvCandidates(sv).map((f: string, i: number) => ({ index: i, file: f }));
  }
  // 审查：最新 completed record 恢复徽章（刷新后不必重新点审查——S-8）
  const revs = (rev.review_records || []).filter((x: any) => x.status === 'completed');
  if (revs.length) stateReview.value[scivId] = revs[revs.length - 1];
  // 批准：最新事件恢复「已批准候选索引 / 撤销后需重审」（S-3/S-8）
  const evs = appr.approval_records || [];
  const lastEv = evs[evs.length - 1];
  if (lastEv) {
    const op = String(lastEv.operation || '');
    if (op.includes('approve') && lastEv.status === 'approved') {
      ivApprovedIndex.value[scivId] = typeof lastEv.candidate_index === 'number' ? lastEv.candidate_index : null;
      needReview.value[scivId] = false;
    } else if (op.includes('revoke')) {
      ivApprovedIndex.value[scivId] = null;
      needReview.value[scivId] = true;
    }
  }
}

// ===== 通用动作 =====
function switchTab(t: 'locations' | 'instances') {
  tab.value = t;
  if (t === 'instances' && !storyScenes.value.length) {
    apiClient.getProjectShots(projectName.value)
      .then(r => { storyScenes.value = r.scenes || []; })
      .catch(() => {});
  }
}

// ===== 地点动作 =====
async function selectLocation(id: string) {
  selectedLocationId.value = id;
  selectedViewIdRef.value = '';
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
    await refreshOverview();
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
    await refreshLocations();
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
    needReview.value[selectedView.value.view_id] = false;
    await refreshLocationDetail();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function reviewViewAction() {
  busy.value = true; errorMsg.value = '';
  try {
    const r = await apiClient.sceneReviewLocationView(projectName.value, selectedView.value.view_id);
    viewReview.value[selectedView.value.view_id] = r;
    needReview.value[selectedView.value.view_id] = false;
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
    needReview.value[selectedView.value.view_id] = false;
    await refreshLocationDetail();
    await refreshOverview();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function revokeViewAction() {
  if (!guard(selectedView.value?.revision)) return;
  if (!confirm('撤销该视角的批准？（生成/审查/批准历史保留；撤销后需重新审查才能再次批准）')) return;
  busy.value = true; errorMsg.value = '';
  try {
    await apiClient.sceneRevokeLocationView(projectName.value, selectedView.value.view_id, {
      expected_revision: selectedView.value.revision, reason: 'UI revoke' });
    needReview.value[selectedView.value.view_id] = true;
    await refreshLocationDetail();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
function isApprovedCandidate(c: { file: string }): boolean {
  return !!selectedView.value && approvedFileMap.value[selectedView.value.view_id] === c.file
    && selectedView.value.approval_status === 'approved';
}
function approveDisabledTitle(index: number): string {
  if (needReview.value[selectedView.value?.view_id || '']) return '撤销后需先重新视觉审查';
  if (viewReviewBadge(index) !== 'pass') return '该候选审查通过后才能批准';
  return '';
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

// ===== 布景状态动作 =====
async function selectInstance(id: string) {
  selectedInstanceId.value = id;
  selectedStateViewId.value = '';
  stateViewCandidates.value = [];
  busy.value = true;
  try {
    // 实例详情需要其地点的锚点（锚点状态编辑用）
    const inst0 = await apiClient.sceneReadSceneInstance(projectName.value, id);
    if (inst0.location_id && inst0.location_id !== selectedLocationId.value) {
      const ans = await apiClient.sceneListAnchors(projectName.value, inst0.location_id).catch(() => ({ anchors: [] }));
      anchors.value = ans.anchors || [];
      const vs = await apiClient.sceneListViews(projectName.value, inst0.location_id).catch(() => ({ views: [] }));
      views.value = vs.views || [];
    }
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
// ===== 实例视图创建：角度可选 + 多选批量 =====
const showCreateStateView = ref(false);
const createViewIds = ref<string[]>([]);
// 可创建角度 = 该地点已批准、且本布景状态尚未创建的 base 视角
const freeBaseViews = computed(() => {
  const taken = new Set(stateViews.value.map((v: any) => v.location_view_id));
  return views.value.filter((v: any) => v.approval_status === 'approved' && !taken.has(v.view_id));
});
function toggleCreateStateView() {
  showCreateStateView.value = !showCreateStateView.value;
  if (showCreateStateView.value) {
    createViewIds.value = [];
    if (!freeBaseViews.value.length && !views.value.some((v: any) => v.approval_status === 'approved')) {
      errorMsg.value = '该地点还没有 approved 的 base 视角——先在「地点」页生成并批准';
    }
  }
}
function toggleAllFreeViews() {
  createViewIds.value = createViewIds.value.length === freeBaseViews.value.length
    ? [] : freeBaseViews.value.map((v: any) => v.view_id);
}
async function createStateViewsAction() {
  if (!createViewIds.value.length) return;
  busy.value = true; errorMsg.value = '';
  try {
    let firstId = '';
    for (const vid of createViewIds.value) {
      const v = views.value.find((x: any) => x.view_id === vid);
      const ov = await apiClient.sceneOverview(projectName.value);   // OCC：每次创建后文档版本都会变
      const r = await apiClient.sceneCreateInstanceView(projectName.value, {
        scene_instance_id: selectedInstanceId.value, location_view_id: vid,
        label: `${v?.label || vid} · ${stateForm.value.time_of_day ? stateForm.value.time_of_day.split(',')[0] : '状态'}`,
        expected_document_revision: ov.document_revision,
      });
      firstId = firstId || r.target;
    }
    showCreateStateView.value = false;
    createViewIds.value = [];
    await refreshInstanceDetail();
    if (firstId) await selectStateView(firstId);
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
    needReview.value[selectedStateView.value.scene_instance_view_id] = false;
    await refreshInstanceDetail();
    if (selectedStateViewId.value) await refreshStateViewCandidates(selectedStateViewId.value);
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function reviewStateViewAction() {
  busy.value = true; errorMsg.value = '';
  try {
    const r = await apiClient.sceneReviewInstanceView(projectName.value, selectedStateView.value.scene_instance_view_id);
    stateReview.value[selectedStateView.value.scene_instance_view_id] = r;
    needReview.value[selectedStateView.value.scene_instance_view_id] = false;
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
    needReview.value[selectedStateView.value.scene_instance_view_id] = false;
    ivApprovedIndex.value[selectedStateView.value.scene_instance_view_id] = candidateIndex;
    await refreshInstanceDetail();
    await refreshOverview();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function revokeStateViewAction() {
  if (!guard(selectedStateView.value?.revision)) return;
  if (!confirm('撤销该状态视图的批准？（历史保留；撤销后需重新审查才能再次批准）')) return;
  busy.value = true; errorMsg.value = '';
  try {
    await apiClient.sceneRevokeInstanceView(projectName.value, selectedStateView.value.scene_instance_view_id, {
      expected_revision: selectedStateView.value.revision, reason: 'UI revoke' });
    needReview.value[selectedStateView.value.scene_instance_view_id] = true;
    ivApprovedIndex.value[selectedStateView.value.scene_instance_view_id] = null;
    await refreshInstanceDetail();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
function isApprovedIvCandidate(c: { index: number }): boolean {
  const key = selectedStateView.value?.scene_instance_view_id || '';
  const idx = ivApprovedIndex.value[key];
  if (typeof idx === 'number') return c.index === idx;   // 批准记录精确到候选
  return !!selectedStateView.value && selectedStateView.value.approval_status === 'approved';
}
function approveDisabledTitleIv(index: number): string {
  const key = selectedStateView.value?.scene_instance_view_id || '';
  if (needReview.value[key]) return '撤销后需先重新视觉审查';
  if (stateReviewBadge(index) !== 'pass') return '该候选审查通过后才能批准（点上方「视觉审查」）';
  return '';
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

async function loadAll() {
  busy.value = true;
  try {
    await Promise.all([
      refreshOverview(), refreshLocations(), refreshInstances(),
      apiClient.listProps(projectName.value).then(r => { props.value = r.props || []; }).catch(() => {}),
      apiClient.getProjectShots(projectName.value).then(r => { storyScenes.value = r.scenes || []; }).catch(() => {}),
    ]);
  } catch (e) { fail(e); } finally { busy.value = false; }
}
onMounted(loadAll);

// 切项目：清选中 + 重载。F5 直开本页时项目身份是异步恢复的（store.restoreSelectedProject），
// 若不响应式重载，页面会一直停留在默认项目的空数据上（其他页面均有同款 watch）。
watch(projectName, () => {
  selectedLocationId.value = '';
  selectedViewIdRef.value = '';
  selectedInstanceId.value = '';
  selectedStateViewId.value = '';
  selectedLocation.value = null;
  selectedInstance.value = null;
  anchors.value = []; layout.value = null; views.value = [];
  instances.value = []; stateViews.value = [];
  viewCandidates.value = []; stateViewCandidates.value = [];
  viewReview.value = {}; stateReview.value = {}; stateViewStatus.value = {};
  viewCoverMap.value = {}; approvedFileMap.value = {}; needReview.value = {}; ivApprovedIndex.value = {};
  errorMsg.value = '';
  loadAll();
});
</script>

<style lang="scss" scoped>
// ============================================================
// 场景系统 · 布局 A：左列表（地点/布景状态分组）+ 右区块流详情
// 配色：全部使用 global.scss 的黑白灰 token（本页原先是另一套
// 不存在的 --text-1/--bg-3 变量 + 蓝紫绿硬编码，已全部替换）
// ============================================================

.scene-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  min-height: 0;
  position: relative;
}

.no-project {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  color: var(--text-muted);
}

/* ---------- 错误条 ---------- */
.mono-num { font-variant-numeric: tabular-nums; }

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-faint);
  flex-shrink: 0;

  &.on { background: var(--text-secondary); }
  &.live { background: var(--text-primary); }
  &.fail { background: var(--error); }
}

.error-strip {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 20px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-hover);
  font-size: 12px;
  color: var(--text-secondary);
}

.icon-btn {
  width: 24px;
  height: 24px;
  border: none;
  border-radius: 4px;
  background: none;
  color: var(--text-faint);
  cursor: pointer;

  &:hover { background: var(--bg-hover); color: var(--text-primary); }
}

.section-label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: var(--text-faint);
}

.hint { font-size: 12px; color: var(--text-faint); }

/* ---------- 工作台 ---------- */
.workbench {
  flex: 1;
  min-height: 0;
  display: flex;
}

/* ---------- 左侧 ---------- */
.side {
  width: 264px;
  flex-shrink: 0;
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.group-switch {
  flex-shrink: 0;
  display: flex;
  gap: 4px;
  margin: 12px;
  padding: 3px;
  border: 1px solid var(--border);
  border-radius: 8px;

  button {
    flex: 1;
    padding: 6px 8px;
    border: none;
    border-radius: 5px;
    background: none;
    color: var(--text-muted);
    font-size: 12px;
    cursor: pointer;

    &:hover { color: var(--text-secondary); }
    &.on { background: var(--bg-active); color: var(--text-primary); font-weight: 500; }
  }
}

.side-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 8px 8px;
}

.side-block-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 8px 10px;

  .meta { font-size: 12px; color: var(--text-muted); }
  .spacer { flex: 1; }
}

.item {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 9px 10px;
  border-radius: 8px;
  cursor: pointer;

  &:hover { background: var(--bg-hover); }
  &.on { background: var(--bg-active); }

  .nm {
    font-size: 13px;
    font-weight: 500;
    color: var(--text-secondary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;

    .item.on & { color: var(--text-primary); }
  }

  .sub {
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 11px;
    color: var(--text-faint);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
}

.empty-mini {
  padding: 20px 10px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--text-faint);
}

.side-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px;
  margin-bottom: 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-surface);

  &.wide { margin: 0 0 14px; }

  label {
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 11px;
    color: var(--text-muted);
  }

  input, select, textarea {
    width: 100%;
    padding: 7px 9px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--bg-root);
    color: var(--text-primary);
    font-family: inherit;
    font-size: 12px;
    outline: none;

    &:focus { border-color: var(--border-hover); }
    &::placeholder { color: var(--text-faint); }
  }

  textarea { resize: vertical; line-height: 1.6; }

  .form-row-3 {
    display: grid;
    grid-template-columns: 1.4fr 1fr 1fr;
    gap: 8px;

    input, select { width: 100%; }
  }

  .form-actions { display: flex; gap: 8px; }

  .check-row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px 12px;
    font-size: 12px;
    color: var(--text-muted);
  }

  .check {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 12px;
    color: var(--text-secondary);
    cursor: pointer;
  }
}

/* ---------- 右侧详情 ---------- */
.detail {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.dt-head {
  flex-shrink: 0;
  display: flex;
  gap: 16px;
  padding: 18px 24px 14px;
  border-bottom: 1px solid var(--border);
}

.dt-cover {
  width: 120px;
  height: 72px;
  cursor: zoom-in;

  img { width: 100%; height: 100%; object-fit: cover; border-radius: 7px; }
}

.dt-main { flex: 1; min-width: 0; }

.dt-name {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;

  h2 {
    font-size: 19px;
    font-weight: 600;
    letter-spacing: -0.01em;
    color: var(--text-primary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 480px;
  }
}

.dt-tag {
  font-size: 11px;
  padding: 2px 8px;
  border: 1px solid var(--border);
  border-radius: 10px;
  color: var(--text-muted);

  &.fail { color: var(--error); border-color: rgba(239, 68, 68, 0.4); }
}

.dt-hint { font-size: 11px; color: var(--text-faint); }

.dt-desc {
  margin-top: 7px;
  max-width: 640px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--text-muted);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.dt-stats {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  margin-top: 10px;
}

.dt-stat {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-faint);
  font-variant-numeric: tabular-nums;

  b { color: var(--text-secondary); font-weight: 500; }
}

.dt-actions {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex-shrink: 0;
  align-items: flex-end;
}

.dt-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 4px 24px 64px;
}

.sec { padding-top: 24px; }

.sec-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
  flex-wrap: wrap;

  .meta { font-size: 12px; color: var(--text-muted); }
  .spacer { flex: 1; }
}

/* 字段 */
.fields { display: flex; flex-direction: column; }

.field-row {
  display: grid;
  grid-template-columns: 76px 1fr;
  gap: 14px;
  padding: 9px 0;
  border-bottom: 1px solid var(--border);
  align-items: start;

  &:last-child { border-bottom: none; }

  .k { font-size: 12px; line-height: 2; color: var(--text-faint); }
  .v { max-width: 640px; min-width: 0; }
}

.inline-in {
  width: 100%;
  padding: 7px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-root);
  color: var(--text-primary);
  font-family: inherit;
  font-size: 13px;
  outline: none;
  transition: border-color 0.15s ease;

  &:focus { border-color: var(--border-hover); }
  &::placeholder { color: var(--text-faint); }
}

.inline-ta {
  width: 100%;
  padding: 7px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-root);
  color: var(--text-primary);
  font-family: inherit;
  font-size: 13px;
  line-height: 1.7;
  outline: none;
  resize: vertical;

  &:focus { border-color: var(--border-hover); }
  &::placeholder { color: var(--text-faint); }
}

/* 视角卡 */
.views {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 12px;
}

/* 实例视图：角度选择面板（多选批量创建） */
.cvp {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-root);
  padding: 10px 12px;
  margin-bottom: 12px;
}
.cvp-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}
.cvp-label { font-size: 11px; font-weight: 600; letter-spacing: 0.06em; color: var(--text-muted); }
.cvp-rows { display: flex; flex-direction: column; gap: 4px; }
.cvp-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 8px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
  color: var(--text-secondary);
  &:hover { background: var(--bg-hover); }
  input { accent-color: var(--accent); }
  .nm { font-weight: 600; color: var(--text-primary); }
}
.cvp-foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 10px;
}

.view {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-surface);
  overflow: hidden;
  cursor: pointer;
  transition: border-color 0.15s ease, transform 0.25s ease;

  &:hover { border-color: var(--border-hover); transform: translateY(-2px); }
  &.sel { border-color: var(--text-muted); }
}

.v-ph {
  position: relative;
  aspect-ratio: 16 / 10;
  border-bottom: 1px solid var(--border);
  background: linear-gradient(160deg, var(--bg-root), var(--bg-surface));
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;

  img { width: 100%; height: 100%; object-fit: cover; }

  &.empty { border-bottom-style: dashed; flex-direction: column; gap: 6px; }
}

.v-empty { font-size: 12px; color: var(--text-faint); }
.cta-plus { font-size: 22px; color: var(--text-faint); }

.flag {
  position: absolute;
  top: 6px;
  left: 6px;
  padding: 1px 7px;
  border-radius: 9px;
  background: var(--accent);
  color: var(--accent-text);
  font-size: 10px;
  font-weight: 600;
}

.v-meta { padding: 9px 11px; display: flex; flex-direction: column; gap: 4px; }

.v-meta .line {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.v-name {
  font-size: 12px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;

  .view:hover & { color: var(--text-primary); }
}

.v-state { font-size: 11px; color: var(--text-faint); white-space: nowrap; }
.dir { font-size: 11px; color: var(--text-faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.spacer { flex: 1; }

/* 候选网格 */
.cand-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 12px;
}

.cand {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-surface);
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;

  &.approved { border-color: var(--text-muted); }

  img.zoomable {
    width: 100%;
    aspect-ratio: 16 / 10;
    object-fit: contain;
    border-radius: 6px;
    background: linear-gradient(160deg, var(--bg-root), var(--bg-surface));
    cursor: zoom-in;
  }
}

.cand-info {
  display: flex;
  align-items: center;
  gap: 8px;

  .meta { font-size: 11px; color: var(--text-faint); }
}

.tag {
  padding: 2px 8px;
  border: 1px solid var(--border);
  border-radius: 10px;
  font-size: 11px;
  color: var(--text-muted);
  white-space: nowrap;

  &.solid { background: var(--accent); color: var(--accent-text); border-color: transparent; font-weight: 600; }
  &.fail { color: var(--error); border-color: rgba(239, 68, 68, 0.4); }
}

.dims {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 6px;

  em {
    font-style: normal;
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 8px;
    border: 1px solid var(--border);
    color: var(--text-muted);

    &.ok { color: var(--text-secondary); }
    &.warn { color: var(--text-secondary); background: var(--bg-hover); }
    &.bad { color: var(--error); border-color: rgba(239, 68, 68, 0.4); }
  }
}

.cand-actions { display: flex; justify-content: flex-end; }

.review-note {
  font-size: 11px;
  line-height: 1.6;
  color: var(--text-muted);
}

.notice-strip {
  margin-bottom: 12px;
  padding: 8px 12px;
  border: 1px dashed var(--border-hover);
  border-radius: 6px;
  font-size: 12px;
  color: var(--text-secondary);
  background: var(--bg-hover);
}

.empty {
  padding: 26px 24px;
  border: 1px dashed var(--border);
  border-radius: 8px;
  text-align: center;
  font-size: 13px;
  line-height: 1.75;
  color: var(--text-muted);
  grid-column: 1 / -1;
}

/* 行列表（锚点等） */
.rows { display: flex; flex-direction: column; gap: 2px; }

.row {
  display: grid;
  grid-template-columns: minmax(140px, auto) 1fr auto;
  align-items: center;
  gap: 12px;
  padding: 9px 10px;
  border-radius: 6px;

  &:hover { background: var(--bg-hover); }

  .nm {
    font-size: 13px;
    color: var(--text-secondary);
    display: flex;
    align-items: center;
    gap: 8px;

    .row:hover & { color: var(--text-primary); }
  }

  .desc {
    display: flex;
    gap: 8px;
    min-width: 0;

    input, select { flex: 1; min-width: 0; }
  }

  .go { display: flex; gap: 4px; opacity: 0; transition: opacity 0.15s ease; }
  .row:hover &.go, &:hover .go { opacity: 1; }
}

/* 折叠区（平面布局） */
.fold {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-surface);
  padding: 12px 14px;
  cursor: pointer;
}

.fold-head {
  display: flex;
  align-items: center;
  gap: 10px;

  .spacer { flex: 1; }
}

.fold-body {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  cursor: default;
}

.form-row-inline {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;

  .inline-label {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--text-muted);
  }

  select, input {
    padding: 6px 9px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--bg-root);
    color: var(--text-primary);
    font-family: inherit;
    font-size: 12px;
    outline: none;
  }

  .grow { flex: 1; min-width: 120px; }
}

.place-list { display: flex; flex-direction: column; gap: 8px; }

.place-row {
  display: grid;
  grid-template-columns: 1.2fr 0.8fr 56px 56px 1fr 24px;
  gap: 8px;
  align-items: center;

  select, input {
    width: 100%;
    padding: 6px 8px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--bg-root);
    color: var(--text-primary);
    font-family: inherit;
    font-size: 12px;
    outline: none;

    &:focus { border-color: var(--border-hover); }
  }
}

/* base 对比 */
.side-by-side {
  display: flex;
  gap: 16px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}

.sbs-item {
  flex: 1;
  min-width: 260px;
  display: flex;
  flex-direction: column;
  gap: 8px;

  img {
    width: 100%;
    border-radius: 8px;
    border: 1px solid var(--border);
    cursor: zoom-in;
  }
}

.sbs-label { font-size: 12px; color: var(--text-muted); }

.img-ph {
  aspect-ratio: 16 / 10;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px dashed var(--border);
  border-radius: 8px;
  font-size: 12px;
  color: var(--text-faint);

  &.small { aspect-ratio: auto; padding: 14px; }
}

.proj-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;

  em {
    font-style: normal;
    font-size: 11px;
    padding: 2px 8px;
    border: 1px solid var(--border);
    border-radius: 10px;
    color: var(--text-muted);
  }
}

.evidence { display: flex; flex-direction: column; gap: 4px; }

.mini-select {
  padding: 5px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-root);
  color: var(--text-secondary);
  font-family: inherit;
  font-size: 12px;
  outline: none;
}

.select-prompt {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  color: var(--text-faint);
}

/* ---------- 按钮 ---------- */
.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 12px;
  border: none;
  border-radius: 6px;
  background: none;
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  transition: background-color 0.15s ease, color 0.15s ease, border-color 0.15s ease;

  &:disabled { opacity: 0.45; cursor: not-allowed; }
}

.btn-sm { height: 28px; padding: 0 10px; font-size: 12px; }

.btn-outline {
  border: 1px solid var(--border);
  color: var(--text-secondary);

  &:hover:not(:disabled) { border-color: var(--border-hover); color: var(--text-primary); background: none; }
}

.btn-quiet {
  color: var(--text-faint);

  &:hover:not(:disabled) { color: var(--text-secondary); background: var(--bg-hover); }
}

.btn-primary {
  background: var(--accent);
  color: var(--accent-text);

  &:hover:not(:disabled) { background: var(--accent-hover); color: var(--accent-text); }
}

/* ---------- 大图预览 ---------- */
.lightbox {
  position: fixed;
  inset: 0;
  z-index: 1100;
  background: rgba(0, 0, 0, 0.82);
  backdrop-filter: blur(6px);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: zoom-out;

  img {
    max-width: 90vw;
    max-height: 90vh;
    border-radius: 8px;
    box-shadow: 0 24px 80px rgba(0, 0, 0, 0.6);
  }
}

.lightbox-close {
  position: absolute;
  top: 18px;
  right: 24px;
  width: 32px;
  height: 32px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-surface);
  color: var(--text-muted);
  font-size: 15px;
  cursor: pointer;

  &:hover { color: var(--text-primary); border-color: var(--border-hover); }
}

/* ---------- 缩略图占位 ---------- */
.thumb-ph {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: linear-gradient(160deg, var(--bg-root), var(--bg-surface));
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ---------- 窄屏 ---------- */
@media (max-width: 1080px) {
  .workbench { flex-direction: column; }

  .side {
    width: 100%;
    max-height: 36%;
    border-right: none;
    border-bottom: 1px solid var(--border);
  }

  .dt-head { flex-wrap: wrap; }
  .side-by-side { flex-direction: column; }
}
</style>
