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
        <span v-if="taskStatusText" class="wb-task" :class="taskClass">{{ taskStatusText }}</span>
        <button class="wb-close" @click="$emit('close')">✕</button>
      </header>

      <!-- 上下文摘要条（紧凑；故事原位展开；角色/配音/接续用 popover） -->
      <div class="ctx-bar">
        <button class="ctx-item" data-pop="story" @click="storyExpanded = !storyExpanded" :aria-expanded="storyExpanded">
          <span class="ctx-k">故事</span><span class="ctx-v">{{ storySummary }}</span>
          <span class="ctx-chev" aria-hidden="true">{{ storyExpanded ? '▾' : '▸' }}</span>
        </button>
        <button class="ctx-item" data-pop="chars" @click="togglePopover('chars')" :class="{ on: openPopover === 'chars' }">
          <span class="ctx-k">角色</span><span class="ctx-v">{{ charSummary }}</span>
        </button>
        <button class="ctx-item" data-pop="opening" @click="togglePopover('opening')" :class="{ on: openPopover === 'opening' }" :title="'开场来源'">
          <span class="ctx-k">开场</span><span class="ctx-v">{{ openingSourceLabel }}</span>
        </button>
        <button class="ctx-item" data-pop="link" @click="togglePopover('link')" :class="{ on: openPopover === 'link' }" :title="'生成方式'">
          <span class="ctx-k">生成</span><span class="ctx-v">{{ linkLabel }}</span>
        </button>
        <div v-if="storyExpanded" class="ctx-story-full">{{ sceneStory || '（未填写）' }}</div>
      </div>

      <!-- popover 层：同时只一个；backdrop 点外关闭；Escape 关闭；锚定触发控件 -->
      <div v-if="openPopover" class="popover-backdrop" @click="openPopover = null"></div>
      <div v-if="openPopover" class="popover popover-open" :style="popoverStyle" @keydown="onPopoverKeydown" role="dialog" aria-label="上下文编辑">
        <template v-if="openPopover === 'chars'">
          <div class="pop-head">出场角色</div>
          <div class="pop-chips" data-autofocus tabindex="-1">
            <button v-for="c in projectCharacters" :key="c.name" class="chip" :class="{ on: form.characters.includes(c.name) }" @click="toggleChar(c.name)">{{ c.name }}</button>
            <span v-if="!projectCharacters.length" class="muted">项目无角色</span>
          </div>
          <div v-if="form.characters.length" class="pop-refs">
            <div v-for="name in form.characters" :key="name" class="pop-ref">
              <img v-if="charRefUrl(name)" :src="charRefUrl(name)" />
              <span v-else class="muted">无图</span>
              <span class="pop-ref-name">{{ name }}</span>
            </div>
          </div>
        </template>
        <template v-else-if="openPopover === 'opening'">
          <div class="pop-head">开场来源</div>
          <div class="pop-list" data-autofocus tabindex="-1">
            <button v-for="m in OPENING_MODES" :key="m.v" class="pop-opt" :class="{ on: shot.opening_source === m.v }"
              :disabled="m.v !== 'independent' && !prevReady"
              :title="m.v !== 'independent' && !prevReady ? '上一镜头完成视频后可用' : ''"
              @click="m.v === 'independent' || prevReady ? setOpeningSource(m.v) : null">
              <span class="pop-opt-l">{{ m.l }}</span>
              <span class="pop-opt-d">{{ m.d }}</span>
              <span v-if="m.v !== 'independent' && !prevReady" class="pop-opt-pre">上一镜头完成视频后可用</span>
              <span v-if="m.v === 'inherit_previous' && prevReady && !prevEndUrlReady" class="pop-opt-pre">需先提取上一镜末帧</span>
            </button>
          </div>
          <div v-if="shot.opening_source !== 'independent'" class="pop-continuity" :class="continuityStatus.status">
            <span class="pop-cont-msg">{{ continuityStatus.message || '已就绪' }}</span>
            <div class="pop-cont-actions">
              <button v-if="continuityStatus.status === 'stale'" class="mini-btn" @click="syncSource">更新为最新末帧</button>
              <button v-if="shot.opening_source === 'inherit_previous'" class="mini-btn" @click="setOpeningSource('independent')">改为独立生成</button>
              <button v-if="shot.opening_source === 'inherit_previous' && sourceShot && !sourceShot.video_end_frame?.url" class="mini-btn" @click="refreshVideoEnd">提取来源末帧</button>
            </div>
          </div>
        </template>
        <template v-else>
          <div class="pop-head">生成方式</div>
          <div class="pop-list" data-autofocus tabindex="-1">
            <button v-for="o in LINK_OPTIONS" :key="o.v" class="pop-opt" :class="{ on: form.link_mode === o.v }" @click="form.link_mode = o.v; openPopover = null">
              <span class="pop-opt-l">{{ o.l }}</span>
              <span class="pop-opt-d">{{ o.d }}</span>
            </button>
          </div>
        </template>
      </div>

      <!-- 本镜意图（公共区域，三标签共享；复用 Shot.description） -->
      <div class="shot-intent-bar">
        <span class="si-label">本镜意图</span>
        <div v-if="!editingIntent" class="si-view" @click="startEditIntent" title="点击编辑本镜意图">
          <span class="si-text" :class="{ empty: !form.description }">{{ form.description || '（未填写，生成首帧时 AI 会推断）' }}</span>
          <button class="mini-btn" @click.stop="startEditIntent">编辑</button>
        </div>
        <div v-else class="si-edit">
          <textarea ref="intentTextarea" v-model="intentDraft" rows="3" class="si-textarea" placeholder="简要描述这一个镜头负责表现的剧情，不需要写机位或完整提示词" @keydown.esc="editingIntent = false"></textarea>
          <div class="si-actions">
            <button class="mini-btn" @click="saveIntent">完成</button>
            <button class="mini-btn" @click="editingIntent = false">取消</button>
          </div>
        </div>
      </div>

      <!-- 阶段标签 -->
      <div class="stage-tabs">
        <button :class="{ on: stage === 'first' }" @click="stage = 'first'">首帧</button>
        <button :class="{ on: stage === 'last' }" @click="stage = 'last'">尾帧</button>
        <button :class="{ on: stage === 'video' }" @click="stage = 'video'">视频</button>
      </div>

      <div class="wb-main" :class="{ narrow, 'preview-collapsed': !narrow && previewCollapsed }">
        <!-- 窄窗视图切换（参数/预览，不上下堆叠） -->
        <div v-if="narrow" class="view-switch">
          <button :class="{ on: narrowView === 'editor' }" @click="narrowView = 'editor'">参数</button>
          <button :class="{ on: narrowView === 'preview' }" @click="narrowView = 'preview'">预览</button>
        </div>

        <!-- 参数面板（主体，单一纵向滚动；核心字段始终可见） -->
        <div class="editor-panel" v-show="!narrow || narrowView === 'editor'">
          <div class="editor-scroll">
            <!-- 生产绑定（场景参考）：BOUND 生成走 Binding-aware 路径，READY 才可生成 -->
            <div class="binding-panel">
              <div class="bp-head" @click="bindingOpen = !bindingOpen">
                <span class="dot" :class="bindingDotClass"></span>
                <span class="bp-title">场景参考</span>
                <span class="bp-sum">{{ bindingSummary }}</span>
                <span class="spacer"></span>
                <svg class="bp-chev" :class="{ open: bindingOpen }" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9" /></svg>
              </div>
              <div v-if="bindingOpen" class="bp-body">
                <div v-if="bindingLoading" class="bp-muted">加载中…</div>

                <!-- 未绑定：说明 + 绑定表单 -->
                <template v-else-if="!binding">
                  <p class="bp-muted">未绑定——生成走原有流程。绑定后，本镜生成将以「已批准的场景实例视图 + 角色 Look」为生产参考（BOUND 不回退旧流程，未就绪时生成会被阻断）。</p>
                  <button v-if="!bindingFormOpen" class="mini-btn primary" :disabled="bindingBusy" @click="openBindingForm">绑定场景参考</button>
                  <template v-else>
                    <div v-if="bpInstances.length" class="bp-field">
                      <label>布景状态</label>
                      <select v-model="bpInstanceId" @change="onBpInstanceChange">
                        <option v-for="i in bpInstances" :key="i.scene_instance_id" :value="i.scene_instance_id">{{ bpInstanceLabel(i) }}</option>
                      </select>
                    </div>
                    <p v-else class="bp-warn">本项目还没有与故事段 {{ shot.scene_id }} 关联的 active 布景状态——先去「场景系统」创建并批准场景图。</p>
                    <div v-if="bpInstanceId && bpViews.length" class="bp-field">
                      <label>机位方向</label>
                      <select v-model="bpViewId" @change="onBpViewChange">
                        <option v-for="v in bpViews" :key="v.view_id" :value="v.view_id">
                          {{ v.label || v.view_id }}<template v-if="v.camera_zone">（{{ v.camera_zone }}<template v-if="v.look_direction"> · 看向 {{ v.look_direction }}</template>）</template>
                        </option>
                      </select>
                    </div>
                    <div v-if="bpViewId && bpApprovedSivs.length" class="bp-field">
                      <label>场景图（可选钉住；不选则自动解析该方向唯一已批准图）</label>
                      <select v-model="bpPinnedSivId">
                        <option value="">不钉住（自动解析）</option>
                        <option v-for="s in bpApprovedSivs" :key="s.scene_instance_view_id" :value="s.scene_instance_view_id">{{ s.label || s.scene_instance_view_id }}（已批准）</option>
                      </select>
                    </div>
                    <p v-else-if="bpViewId && !bpApprovedSivs.length" class="bp-warn">该方向还没有已批准的场景图——绑定会处于「未就绪」，生成会被阻断；建议先在场景系统批准。</p>
                    <div v-for="name in (shot.characters || [])" :key="name" class="bp-field">
                      <label>{{ name }} · Look（必选）</label>
                      <select v-model="bpLookByChar[name]">
                        <option v-if="!(bpLooksByChar[name] || []).length" value="">（无已批准 Look——去「生产资源」创建并批准）</option>
                        <option v-for="lk in (bpLooksByChar[name] || [])" :key="lk.id" :value="lk.id">{{ lk.name || lk.id }}</option>
                      </select>
                    </div>
                    <p v-if="!shot.characters?.length" class="bp-muted">本镜无出场角色（无需绑定 Look）。</p>
                    <div class="bp-actions">
                      <button class="mini-btn primary" :disabled="bindingBusy || !canCreateBinding" @click="createBinding">创建绑定</button>
                      <button class="mini-btn" :disabled="bindingBusy" @click="bindingFormOpen = false">取消</button>
                    </div>
                  </template>
                </template>

                <!-- 已绑定：摘要 + validity + 操作 -->
                <template v-else>
                  <div class="bp-bound">
                    <div class="bp-scene">
                      <div class="bp-thumb" :class="{ empty: !bpCoverUrl }">
                        <img v-if="bpCoverUrl" :src="bpCoverUrl" @error="bpCoverUrl = ''" />
                        <span v-else>无图</span>
                      </div>
                      <div class="bp-scene-info">
                        <div class="bp-line"><span class="k">场景</span>{{ bpBoundSceneText }}</div>
                        <div class="bp-line" v-for="cb in (binding.character_bindings || [])" :key="cb.character_name">
                          <span class="k">{{ cb.character_name }}</span>{{ bpLookLabel(cb.look_id) || cb.look_id }}
                        </div>
                        <div class="bp-line" v-if="!binding.character_bindings?.length && !binding.scene_binding"><span class="k">空绑定</span>仅占位（生成会被阻断）</div>
                      </div>
                    </div>
                    <div class="bp-validity" v-if="validity">
                      <div class="bp-v-line" :class="{ bad: validity.readiness === 'NOT_READY' }">
                        {{ validity.readiness === 'READY' ? '参考就绪——生成将使用上述绑定的生产参考'
                          : validity.readiness === 'NOT_READY' ? '未就绪——生成会被阻断（BOUND 绝不回退旧流程）'
                          : '状态未知' }}
                      </div>
                      <div class="bp-issue" v-for="(iss, i) in (validity.issues || [])" :key="i">· {{ bpIssueText(iss) }}</div>
                    </div>
                    <div class="bp-actions">
                      <button class="mini-btn" :disabled="bindingBusy" @click="refreshBinding"
                              title="接受当前依赖作为新的 freshness 基准（只刷新指纹，不改绑定意图；失效依赖会被拒绝）">刷新基准</button>
                      <button class="mini-btn danger" :disabled="bindingBusy" @click="unbindBinding">解除绑定</button>
                    </div>
                  </div>
                </template>
              </div>
            </div>

            <!-- inherit 接力面板（标准+专业都显示；仅首帧标签 + inherit 模式） -->
            <div v-if="stage === 'first' && shot.opening_source === 'inherit_previous'" class="inherit-panel" :class="continuityStatus.status">
              <div class="inherit-title">开场沿用上一镜末帧</div>
              <div class="inherit-source">来自 {{ shot.source_shot_id || '上一镜' }} 的视频末帧</div>
              <div v-if="continuityStatus.message" class="inherit-msg">{{ continuityStatus.message }}</div>
              <div class="inherit-actions">
                <button v-if="continuityStatus.status === 'stale'" class="mini-btn" @click="syncSource">更新为最新末帧</button>
                <button class="mini-btn" @click="setOpeningSource('independent')">解除接力</button>
                <button v-if="sourceShot && !sourceShot.video_end_frame?.url" class="mini-btn" @click="refreshVideoEnd">提取来源末帧</button>
              </div>
            </div>

            <!-- 标准模式：每个标签只显示一个主提示词编辑器（+ 尾帧/视频输入帧缩略图） -->
            <template v-if="mode === 'standard'">
              <!-- 尾帧标签：当前开场画面（effective first frame）缩略图 -->
              <div v-if="stage === 'last' && form.link_mode === 'keyframes'" class="eff-frame-preview">
                <span class="eff-label">当前开场画面</span>
                <div class="eff-thumb" :class="{ empty: !effectiveFirstFrameUrl }">
                  <img v-if="effectiveFirstFrameUrl" :src="effectiveFirstFrameUrl" />
                  <span v-else class="muted">开场画面未就绪</span>
                </div>
              </div>
              <!-- 视频标签：当前输入画面（首帧 -> 尾帧） -->
              <div v-if="stage === 'video'" class="eff-frame-preview video-input">
                <div class="eff-thumb small" :class="{ empty: !effectiveFirstFrameUrl }">
                  <img v-if="effectiveFirstFrameUrl" :src="effectiveFirstFrameUrl" />
                  <span v-else class="muted">未就绪</span>
                  <span class="eff-tag">首帧</span>
                </div>
                <span class="eff-arrow">→</span>
                <div v-if="form.link_mode === 'keyframes'" class="eff-thumb small" :class="{ empty: !lfUrl }">
                  <img v-if="lfUrl" :src="lfUrl" />
                  <span v-else class="muted">未生成</span>
                  <span class="eff-tag">尾帧</span>
                </div>
              </div>
              <!-- 视频标签：时长选择（标准模式用户自主设置；默认约 3 秒由后端兜底） -->
              <div v-if="stage === 'video'" class="duration-row">
                <span class="dur-label">视频时长</span>
                <select v-model="durationMode">
                  <option value="follow">默认（约 3 秒）</option>
                  <option value="3">约 3 秒</option>
                  <option value="5">约 5 秒</option>
                  <option value="10">约 10 秒</option>
                  <option value="18">约 18 秒</option>
                  <option value="custom">自定义</option>
                </select>
                <input v-if="durationMode === 'custom'" type="number" step="0.5" min="1" max="20" v-model.number="form.target_duration_s" class="dur-input" />
              </div>
              <!-- 主提示词编辑器（inherit 首帧模式下隐藏：不生成首帧，提示词无用途） -->
              <PromptEditor
                v-if="!(stage === 'first' && shot.opening_source === 'inherit_previous')"
                v-model="stagePromptModel"
                :label="stageLabel"
                :hint="stageHint"
                :placeholder="stagePlaceholder"
                :gen-busy="promptBusy[stage]"
                :opt-busy="optBusy[stage]"
                @generate="generatePrompt(stage)"
                @optimize="optimizePrompt(stage)"
              />
            </template>

            <!-- 专业模式：结构化字段 + 折叠区 + 最终提示词编辑器 + 更新最终提示词 -->
            <template v-else>
              <!-- 首帧核心字段 -->
              <template v-if="stage === 'first'">
                <div class="field"><label>起始画面描述</label>
                  <textarea v-model="form.description" rows="3" placeholder="地点、主体、画面中正在发生的状态"></textarea>
                </div>
                <div class="field"><label>主体起始状态</label>
                  <textarea v-model="form.opening_state" rows="2" placeholder="角色姿态/朝向/表情或物体初始状态"></textarea>
                </div>
                <div class="field-row">
                  <div class="field"><label>景别</label>
                    <select v-model="form.shot_size"><option v-for="o in SHOT_SIZES" :key="o.v" :value="o.v">{{ o.l }}</option></select>
                  </div>
                  <div class="field"><label>机位角度</label>
                    <select v-model="form.camera_angle"><option value="">（不指定）</option><option v-for="o in CAMERA_ANGLES" :key="o.v" :value="o.v">{{ o.l }}</option></select>
                  </div>
                </div>
                <div class="field"><label>光线与氛围</label>
                  <select v-model="form.lighting"><option value="">（不指定）</option><option v-for="o in LIGHTINGS" :key="o.v" :value="o.v">{{ o.l }}</option></select>
                </div>
              </template>

              <!-- 尾帧核心字段 -->
              <template v-else-if="stage === 'last'">
                <div v-if="form.link_mode !== 'keyframes'" class="muted pad">当前接续方式不需要尾帧。</div>
                <template v-else>
                  <div class="field"><label>结束画面</label>
                    <textarea v-model="form.last_frame_description" rows="3" placeholder="镜头结束时最终看到的画面"></textarea>
                  </div>
                  <div class="field"><label>主体结束状态</label>
                    <textarea v-model="form.end_state" rows="2" placeholder="人物/物体最终姿态、位置、方向、表情"></textarea>
                  </div>
                  <div class="field"><label>相对首帧的变化</label>
                    <textarea v-model="form.frame_change" rows="3" placeholder="例：机甲抬起右臂，身体后倾"></textarea>
                  </div>
                </template>
              </template>

              <!-- 视频核心字段 -->
              <template v-else>
                <div class="field"><label>主体动作</label>
                  <textarea v-model="form.motion" rows="3" placeholder="例：机甲短暂停顿后迅速抬起右臂挡在胸前"></textarea>
                </div>
                <div class="field-row">
                  <div class="field"><label>摄影机运动</label>
                    <select v-model="form.camera_movement"><option value="">（不指定）</option><option v-for="o in CAMERA_MOVEMENTS" :key="o.v" :value="o.v">{{ o.l }}</option></select>
                  </div>
                  <div class="field"><label>动作节奏</label>
                    <select v-model="form.motion_pace"><option value="">（不指定）</option><option v-for="o in MOTION_PACES" :key="o.v" :value="o.v">{{ o.l }}</option></select>
                  </div>
                </div>
                <div class="field"><label>镜头时长</label>
                  <select v-model="durationMode">
                    <option value="follow">默认 / 跟随配音</option><option value="3">约 3 秒</option><option value="5">约 5 秒</option><option value="10">约 10 秒</option><option value="18">约 18 秒</option><option value="custom">自定义</option>
                  </select>
                  <input v-if="durationMode === 'custom'" type="number" step="0.5" min="1" max="20" v-model.number="form.target_duration_s" class="dur-input" />
                </div>
                <div class="field"><label>台词（可选，本批不生成配音）</label>
                  <textarea v-model="form.dialogue" rows="2" placeholder="台词保留为数据，本批不进入配音流程"></textarea>
                </div>
              </template>

              <!-- 折叠区：一致性约束（尾帧）/ 保持稳定（视频） -->
              <Collapsible v-if="stage === 'last' && form.link_mode === 'keyframes'" title="一致性约束" :summary="preserveSummary">
                <div class="preserve-groups">
                  <div v-for="g in PRESERVE_GROUPS" :key="g.title" class="preserve-group">
                    <div class="pg-title">{{ g.title }}</div>
                    <div class="tile-grid">
                      <label v-for="o in g.opts" :key="o.v" class="tile">
                        <input type="checkbox" :checked="form.preserve_elements.includes(o.v)" @change="toggleList(form.preserve_elements, o.v)" />
                        <span class="tile-body"><span class="tile-label">{{ o.l }}</span><span class="tile-check">✓</span></span>
                      </label>
                    </div>
                  </div>
                </div>
              </Collapsible>
              <Collapsible v-if="stage === 'video'" title="保持稳定" :summary="videoPreserveSummary">
                <div class="tile-grid">
                  <label v-for="o in videoPreserveOptions" :key="o.v" class="tile">
                    <input type="checkbox" :checked="form.video_preserve_elements.includes(o.v)" @change="toggleList(form.video_preserve_elements, o.v)" />
                    <span class="tile-body"><span class="tile-label">{{ o.l }}</span><span class="tile-check">✓</span></span>
                  </label>
                </div>
              </Collapsible>

              <!-- 折叠区：专业控制 -->
              <Collapsible title="专业控制" :summary="profSummary">
                <template v-if="stage === 'first'">
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
                <template v-else-if="stage === 'last'">
                  <div class="field"><label>新增元素</label><input v-model="form.add_elements" placeholder="右侧爆炸火光" /></div>
                  <div class="field"><label>删除元素</label><input v-model="form.remove_elements" placeholder="地面碎石" /></div>
                  <div class="field"><label>结束构图 / 摄影机状态</label><input v-model="form.end_composition" /></div>
                  <div class="field"><label>自定义保持要求</label><input v-model="form.preserve_custom" /></div>
                </template>
                <template v-else>
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
              </Collapsible>

              <!-- 折叠区：最终提示词（编辑器 + 更新最终提示词按钮；按钮只在该阶段主动点击时更新，不因切换模式自动覆盖） -->
              <Collapsible title="最终提示词" :summary="promptSummary">
                <div class="prof-prompt-bar">
                  <button class="mini-btn" :disabled="promptBusy[stage]" @click="updateFinalPrompt(stage)">
                    {{ promptBusy[stage] ? '更新中…' : '更新最终提示词' }}
                  </button>
                  <span v-if="(stage === 'first' ? firstPromptStatus : stage === 'last' ? lastPromptStatus : videoPromptStatus) === 'stale'" class="stale-hint">结构字段已变化，建议更新</span>
                </div>
                <PromptEditor
                  v-model="stagePromptModel"
                  :label="stageLabel"
                  :hint="stageHint"
                  :placeholder="stagePlaceholder"
                  :gen-busy="promptBusy[stage]"
                  :opt-busy="optBusy[stage]"
                  @generate="generatePrompt(stage)"
                  @optimize="optimizePrompt(stage)"
                />
              </Collapsible>
            </template>
          </div>
        </div>

        <!-- 预览面板（可收起；窄窗随视图切换显示；主画布 + 缩略条） -->
        <div class="preview-panel" v-show="narrow ? narrowView === 'preview' : !previewCollapsed">
          <div class="preview-head">
            <span class="preview-title">{{ stage === 'first' ? '首帧预览' : stage === 'last' ? '尾帧预览' : '视频预览' }}</span>
            <button v-if="!narrow" class="mini-btn" @click="setPreviewCollapsed(true)" title="收起预览">收起 ◀</button>
          </div>
          <div class="preview-body">
            <div class="canvas" :style="previewBoxStyle" :class="{ empty: mainCanvasEmpty }">
              <video v-if="mainCanvasIsVideo" :src="mainCanvasVideoUrl" controls preload="metadata"></video>
              <img v-else-if="mainCanvasUrl" :src="mainCanvasUrl" />
              <span v-else class="canvas-empty">{{ stage === 'last' && !ffUrlReady ? '未选择首帧' : '尚未生成' }}</span>
              <div v-if="mainCanvasOverlay" class="canvas-overlay">{{ mainCanvasOverlay }}</div>
              <span v-if="!mainCanvasEmpty" class="canvas-label">{{ mainCanvasLabel }}</span>
              <span v-if="ffSlotRole" class="ff-role-badge" :class="ffSlotRole.key">{{ ffSlotRole.label }}</span>
            </div>
            <div v-if="thumbStrip.length" class="thumb-strip">
              <button v-for="(t, i) in thumbStrip" :key="t.cid || (t.label + i)" class="thumb" :class="{ sel: t.sel, ref: t.ref, cont: t.cont }" @click="chooseThumb(t)">
                <img v-if="t.url" :src="t.url" />
                <span v-else class="muted">?</span>
                <span class="thumb-tag">{{ t.label || ('v' + t.idx) }}</span>
              </button>
            </div>
            <div v-if="stage === 'video' && videoVersionsReversed.length" class="video-versions">
              <div v-for="(v, i) in videoVersionsReversed" :key="v.version_id" class="video-version" :class="{ final: v.version_id === shot.final_video }">
                <video :src="v.local ? vidUrl(v.local) + '#t=0.1' : ''" preload="metadata" class="vv-thumb" :class="{ previewing: previewVersionId === v.version_id }" @click="previewVersion(v.version_id)" title="点击预览"></video>
                <div v-if="v.input_visuals && v.input_visuals.length" class="vv-inputs">
                  <span class="vv-inputs-head" title="这是 provenance（该版本实际消费的全部输入事实），不是首帧槽">实际输入</span>
                  <div v-for="(iv, ii) in v.input_visuals" :key="ii" class="vv-input" :class="ivClass(iv)" :title="ivTitle(iv)">
                    <img v-if="iv.url" :src="ivSrc(iv.url)" />
                    <span class="iv-tag">{{ ivLabel(iv) }}</span>
                  </div>
                </div>
                <div class="vv-info">
                  <span class="vv-no">v{{ videoVersionsReversed.length - i }}</span>
                  <span class="vv-ret" v-if="v.seconds_returned">{{ v.seconds_returned }}s / {{ v.size_returned }}</span>
                  <span class="vv-final" v-if="v.version_id === shot.final_video">✓ 最终</span>
                  <button v-else class="mini-btn" @click="setFinalVideo(v.version_id)">设为最终</button>
                </div>
              </div>
            </div>
            <button v-if="ffNeedsRehost && stage === 'first'" class="mini-btn" @click="rehost">修复图片链接</button>
            <Collapsible v-if="hasHistory && stage !== 'video'" title="生成历史" :summary="historySummary">
              <div v-for="(b, bi) in batchesReversed" :key="b.batch_id" class="batch">
                <div class="batch-head" @click="toggleBatch(b.batch_id)">
                  <span class="batch-chevron">{{ isBatchExpanded(b.batch_id) ? '▾' : '▸' }}</span>
                  <span class="batch-title">批次 {{ batchesReversed.length - bi }} · {{ b.candidates.length }} 候选 · {{ b.status === 'done' ? '完成' : b.status }}</span>
                </div>
                <div v-if="isBatchExpanded(b.batch_id)" class="cands">
                  <div v-for="(c, i) in b.candidates" :key="c.candidate_id" class="cand" :class="{ sel: c.candidate_id === selectedCid }" :style="{ aspectRatio: previewRatio }" @click="chooseCand(c.candidate_id)">
                    <img v-if="c.local" :src="imgUrl(c.local)" /><SafeFrameOverlay v-if="stage === 'first'" /><span class="cand-idx">v{{ i + 1 }}</span>
                  </div>
                </div>
              </div>
            </Collapsible>
            <div v-if="stage === 'first' && !shot.first_frame_batches?.length" class="empty-state">根据镜头画面和角色参考生成两张起始画面候选。</div>
            <div v-if="stage === 'last' && form.link_mode === 'keyframes' && !shot.last_frame_batches?.length" class="empty-state">基于首帧生成尾帧画面。</div>
            <div v-if="stage === 'video' && !videoVersionsReversed.length" class="empty-state">根据输入帧、主体动作和运镜生成视频。</div>
          </div>
        </div>

        <!-- 预览收起后的打开按钮（桌面） -->
        <button v-if="!narrow && previewCollapsed" class="reopen-preview" @click="setPreviewCollapsed(false)" title="展开预览">▶ 预览</button>
      </div>

      <!-- 底部 action bar：保存状态 + 前置条件 + 主生成按钮（始终可见，不随滚动消失） -->
      <div class="stage-action-bar">
        <span class="save-status" :class="saveStatus">{{ saveStatusText || '自动保存' }}</span>
        <span class="pre-hint" v-if="stageAction.pre">⚠ {{ stageAction.pre }}</span>
        <button class="main-gen-btn" :disabled="stageAction.disabled" @click="stageAction.fn()">{{ stageAction.label }}</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch, onMounted, onUnmounted, nextTick } from 'vue';
import { apiClient } from '@/api';
import type { ShotData } from '@/api';
import { getImageUrl, getVideoUrl } from '@/utils/media';
import SafeFrameOverlay from './SafeFrameOverlay.vue';
import PromptEditor from './PromptEditor.vue';
import Collapsible from './Collapsible.vue';
import { useJobStore } from '@/store/jobs';
import { useTaskStream } from '@/composables/useTaskStream';
import { createDiscreteApi } from 'naive-ui';
import { discreteConfigProviderProps } from '@/styles/naiveTheme';

const { message, dialog } = createDiscreteApi(['message', 'dialog'], { configProviderProps: discreteConfigProviderProps });

const props = defineProps<{
  shot: ShotData; projectName: string;
  projectCharacters: Array<{ name: string; description_en: string; views: any }>;
  projectStyleLabel: string; projectRatio: string;
  sceneStory: string; sceneId: string;
  sceneShots: ShotData[];
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
// 尾帧"保持一致"分组（仅界面表达，复用 PRESERVE_OPTIONS 的值，不改后端字段顺序）
const PRESERVE_GROUPS = [
  { title: '角色与主体', keys: ['character identity', 'facial features', 'outfit and armor', 'main object shape'] },
  { title: '画面与镜头', keys: ['scene layout', 'camera angle', 'visual style'] },
].map(g => ({ ...g, opts: PRESERVE_OPTIONS.filter(o => g.keys.includes(o.v)) }));

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
// 预览框画幅（跟项目 aspect_ratio，16:9 / 9:16 / 1:1）
const previewRatio = computed(() => {
  const r = (props.projectRatio || '16:9').split(':').map(Number);
  return r.length === 2 && r[0] && r[1] ? `${r[0]} / ${r[1]}` : '16 / 9';
});
// 参考首帧预览框样式：横版限宽、竖版限高，保持画幅不溢出
const previewBoxStyle = computed(() => {
  const r = (props.projectRatio || '16:9').split(':').map(Number);
  const [w, h] = r.length === 2 && r[0] && r[1] ? r : [16, 9];
  return w >= h
    ? { aspectRatio: `${w} / ${h}`, width: '100%', maxWidth: '380px' }
    : { aspectRatio: `${w} / ${h}`, height: '260px', width: 'auto', maxWidth: '100%' };
});

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

// 提示词 v-model：get 显示 override||en（兼容旧 override 数据）；set 写 _en + 清 override
// （标准模式直接编辑三个提示词字段 prompt_en/last_frame_prompt_en/video_prompt_en，编辑即生效）
function promptModel(stage: Stage) {
  return computed({
    get: () => {
      const f = form;
      const override = stage === 'first' ? f.first_frame_prompt_override : stage === 'last' ? f.last_frame_prompt_override : f.video_prompt_override;
      const en = stage === 'first' ? f.prompt_en : stage === 'last' ? f.last_frame_prompt_en : f.video_prompt_en;
      return override || en || '';
    },
    set: (v: string) => {
      if (stage === 'first') { form.prompt_en = v; form.first_frame_prompt_override = ''; }
      else if (stage === 'last') { form.last_frame_prompt_en = v; form.last_frame_prompt_override = ''; }
      else { form.video_prompt_en = v; form.video_prompt_override = ''; }
    },
  });
}
const firstPromptModel = promptModel('first');
const lastPromptModel = promptModel('last');
const videoPromptModel = promptModel('video');
// 当前 stage 的提示词 v-model（标准/专业模式共用同一编辑器绑定）
const stagePromptModel = computed({
  get: () => stage.value === 'first' ? firstPromptModel.value : stage.value === 'last' ? lastPromptModel.value : videoPromptModel.value,
  set: (v: string) => {
    if (stage.value === 'first') firstPromptModel.value = v;
    else if (stage.value === 'last') lastPromptModel.value = v;
    else videoPromptModel.value = v;
  },
});
const stageLabel = computed(() => ({ first: '首帧提示词', last: '尾帧提示词', video: '视频提示词' } as const)[stage.value]);
const stageHint = computed(() => ({
  first: '描述镜头开始时看到的画面，包括人物、环境、构图和光线。',
  last: '描述镜头结束时的画面，以及相对开场画面发生了什么变化。',
  video: '描述人物或物体的动作、环境变化、镜头运动和时间过程。',
} as const)[stage.value]);
const stagePlaceholder = computed(() => ({
  first: '例：A young girl with silver hair standing on a mountain ridge at golden sunrise, medium shot, soft natural light',
  last: '例：The girl turns to face the camera, wind blowing her cloak, same ridge, golden sunrise',
  video: '例：The girl slowly turns her head, wind flutters her cloak, subtle camera push-in',
} as const)[stage.value]);

const promptBusy = reactive({ first: false, last: false, video: false });
const optBusy = reactive({ first: false, last: false, video: false });
function buildBrief(stage: Stage): string {
  const f = form;
  // 用中文标签 + 用户文本拼成中文简述，送文本模型译成英文提示词（专业模式"更新最终提示词"用）
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
// 本镜意图编辑（复用 form.description，watch(form) 自动保存）
const editingIntent = ref(false);
const intentDraft = ref('');
const intentTextarea = ref<HTMLTextAreaElement | null>(null);
function startEditIntent() {
  intentDraft.value = form.description || '';
  editingIntent.value = true;
  nextTick(() => intentTextarea.value?.focus());
}
function saveIntent() { form.description = intentDraft.value.trim(); editingIntent.value = false; }
function writePrompt(stage: Stage, en: string) {
  if (stage === 'first') { form.prompt_en = en; form.first_frame_prompt_override = ''; }
  else if (stage === 'last') { form.last_frame_prompt_en = en; form.last_frame_prompt_override = ''; }
  else { form.video_prompt_en = en; form.video_prompt_override = ''; }
}
// 根据上下文生成（调后端 AI 接口，只填文本不生成资产）
async function generatePrompt(stage: Stage) {
  promptBusy[stage] = true;
  try {
    const r = await apiClient.generatePromptContext(props.projectName, props.shot.shot_id, stage);
    const en = (r.prompt || '').trim();
    if (!en) { message.error('提示词生成返回空'); return; }
    // 首帧本镜意图为空时，后端可能返回 inferred_shot_intent -> 弹确认（采用并生成 / 修改）
    if (stage === 'first' && r.inferred_shot_intent) {
      dialog.info({
        title: 'AI 推断本镜意图',
        content: r.inferred_shot_intent,
        positiveText: '采用并生成提示词',
        negativeText: '修改',
        onPositiveClick: () => {
          form.description = r.inferred_shot_intent!;
          writePrompt(stage, en);
          message.success('提示词已生成，可手改');
        },
        onNegativeClick: () => {
          // 不写 prompt，打开本镜意图编辑器让用户改（预填推断结果）
          intentDraft.value = r.inferred_shot_intent || '';
          editingIntent.value = true;
        },
      });
      return;
    }
    writePrompt(stage, en);
    message.success('提示词已生成，可手改');
  } catch (e: any) { message.error(`提示词生成失败: ${e.message || e}`); }
  finally { promptBusy[stage] = false; }
}
// 优化提示词（以当前编辑器内容为核心，调后端 AI 接口，只覆盖文本不生成资产）
async function optimizePrompt(stage: Stage) {
  const current = stagePromptModel.value;
  if (!current.trim()) { message.info('请先填写或生成提示词'); return; }
  optBusy[stage] = true;
  try {
    const r = await apiClient.optimizePrompt(props.projectName, props.shot.shot_id, stage, current);
    const en = (r.prompt || '').trim();
    if (!en) { message.error('优化返回空'); return; }
    if (stage === 'first') { form.prompt_en = en; form.first_frame_prompt_override = ''; }
    else if (stage === 'last') { form.last_frame_prompt_en = en; form.last_frame_prompt_override = ''; }
    else { form.video_prompt_en = en; form.video_prompt_override = ''; }
    message.success('提示词已优化');
  } catch (e: any) { message.error(`优化失败: ${e.message || e}`); }
  finally { optBusy[stage] = false; }
}
// 专业模式"更新最终提示词"：按结构字段重新拼接 -> 译英文 -> 写 _en + sig（只在该按钮主动点击时更新）
async function updateFinalPrompt(stage: Stage) {
  const brief = buildBrief(stage);
  if (!brief.trim()) { message.info('请先填写该阶段的画面/动作字段'); return; }
  promptBusy[stage] = true;
  try {
    const r = await apiClient.translateText(brief);
    const en = (r.english || '').trim();
    if (!en) { message.error('提示词生成返回空'); return; }
    if (stage === 'first') { form.prompt_en = en; form.first_frame_prompt_override = ''; form.first_frame_prompt_sig = sigOf('first'); }
    else if (stage === 'last') { form.last_frame_prompt_en = en; form.last_frame_prompt_override = ''; form.last_frame_prompt_sig = sigOf('last'); }
    else { form.video_prompt_en = en; form.video_prompt_override = ''; form.video_prompt_sig = sigOf('video'); }
    message.success('最终提示词已更新');
  } catch (e: any) { message.error(`更新失败: ${e.message || e}`); }
  finally { promptBusy[stage] = false; }
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
const lfBatchesReversed = computed(() => [...(props.shot.last_frame_batches || [])].reverse());
// 当前 stage 的批次/选择指针（首帧/尾帧统一）
const batchesReversed = computed(() => stage.value === 'first' ? ffBatchesReversed.value : lfBatchesReversed.value);
const selectedCid = computed(() => stage.value === 'first' ? props.shot.selected_first_frame : props.shot.selected_last_frame);
const expandedBatches = ref<Set<string>>(new Set());
function isBatchExpanded(batchId: string): boolean {
  if (expandedBatches.value.size === 0) {
    const latest = batchesReversed.value[0];   // reversed，第一个是最新批次
    return latest?.batch_id === batchId;
  }
  return expandedBatches.value.has(batchId);
}
function toggleBatch(batchId: string) {
  if (expandedBatches.value.size === 0) {
    const latest = batchesReversed.value[0];
    if (latest) expandedBatches.value = new Set([latest.batch_id]);
  }
  const s = new Set(expandedBatches.value);
  if (s.has(batchId)) s.delete(batchId); else s.add(batchId);
  expandedBatches.value = s;
}
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
async function chooseLast(candidateId: string) {
  try { await apiClient.chooseLastFrame(props.projectName, props.shot.shot_id, candidateId); emit('refresh'); }
  catch (e: any) { message.error(`选定失败: ${e.message || e}`); }
}
// 历史/缩略条候选点选：按当前 stage 调 chooseFirst/chooseLast
async function chooseCand(candidateId: string) {
  if (stage.value === 'first') return chooseFirst(candidateId);
  if (stage.value === 'last') return chooseLast(candidateId);
}
function chooseThumb(t: any) {
  if (!t.cid) return;
  if (stage.value === 'first') chooseFirst(t.cid);
  else if (stage.value === 'last') chooseLast(t.cid);
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
const videoVersionsReversed = computed(() => [...(props.shot.video_versions || [])].filter(v => v.local).reverse());
const finalVideoVersion = computed(() => {
  const vid = props.shot.final_video; if (!vid) return null;
  return (props.shot.video_versions || []).find(v => v.version_id === vid) || null;
});
const videoBlockReason = computed(() => {
  if (finalVideoVersion.value?.local) return '';
  if (!props.shot.video?.num_frames && form.target_duration_s === null) return '';  // 时长由后端兜底（无配音无 target_duration_s -> 默认 3 秒），不再因无配音拦截
  // effective 首帧：inherit 用来源末帧 url；其余用本镜已选首帧 url（或待托管 local）
  const ffUsable = effectiveFirstFrameReady.value || (props.shot.opening_source !== 'inherit_previous' && ffNeedsRehost.value);
  if (!ffUsable) return props.shot.opening_source === 'inherit_previous' ? '接力来源末帧未就绪' : '请先生成并选择首帧';
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

// ---------- Phase 6A：镜头接力 / 开场来源 ----------
const OPENING_MODES = [
  { v: 'independent', l: '独立生成', d: '为这个镜头重新生成首帧。' },
  { v: 'reference_previous', l: '参考上一镜', d: '保留人物和环境连续性，同时允许换机位和构图。' },
  { v: 'inherit_previous', l: '沿用上一镜末帧', d: '直接从上一镜头结束画面继续。画面起点一致，后续运动仍由模型生成。' },
];
// 同场景上一镜（按 sceneShots 顺序）
const prevShot = computed<ShotData | null>(() => {
  const ids = props.sceneShots.map(s => s.shot_id);
  const i = ids.indexOf(props.shot.shot_id);
  if (i <= 0) return null;
  return props.sceneShots[i - 1] || null;
});
// 接力来源镜头（source_shot_id 指向，通常 = prevShot）
const sourceShot = computed<ShotData | null>(() => {
  if (!props.shot.source_shot_id) return null;
  return props.sceneShots.find(s => s.shot_id === props.shot.source_shot_id) || null;
});
// 上一镜是否就绪（有完成视频；inherit 还需末帧公网 URL）
const prevReady = computed(() => {
  const p = prevShot.value; if (!p) return false;
  if (!p.final_video || p.video?.status !== 'done') return false;
  return true;
});
const prevEndUrlReady = computed(() => !!prevShot.value?.video_end_frame?.url);
const openingSourceLabel = computed(() => OPENING_MODES.find(m => m.v === props.shot.opening_source)?.l || '独立生成');
// effective 首帧（与后端 _effective_first_frame_url 一致）：inherit -> 来源末帧 url；其余 -> 本镜已选首帧
const effectiveFirstFrameUrl = computed(() => {
  if (props.shot.opening_source === 'inherit_previous' && sourceShot.value) {
    return sourceShot.value.video_end_frame?.url || '';
  }
  return ffUrl.value;
});
const effectiveFirstFrameReady = computed(() => !!effectiveFirstFrameUrl.value);
// 接力状态（前端镜像后端 continuity_status，用于展示与操作）
const continuityStatus = computed<{ status: string; message: string }>(() => {
  const s = props.shot;
  if (s.opening_source === 'independent' || !s.source_shot_id) return { status: 'independent', message: '' };
  const prev = sourceShot.value;
  if (!prev) return { status: 'broken', message: '来源镜头已删除' };
  if (prev.scene_id !== s.scene_id) return { status: 'broken', message: '来源镜头不在同一场景' };
  if (!prev.final_video || prev.video?.status !== 'done') return { status: 'broken', message: '上一镜头未完成视频' };
  const end = prev.video_end_frame;
  if (s.opening_source === 'inherit_previous') {
    if (!end?.url) return { status: 'broken', message: '上一镜头末帧未就绪（无公网 URL）' };
  } else if (!(end?.status === 'done' && end?.local)) {
    return { status: 'broken', message: '上一镜头末帧未就绪' };
  }
  if (s.source_version !== prev.final_video) return { status: 'stale', message: '上一镜头已更新' };
  return { status: 'ready', message: '' };
});
async function setOpeningSource(source: string) {
  try { await apiClient.setOpeningSource(props.projectName, props.shot.shot_id, source); emit('refresh'); }
  catch (e: any) { message.error(`设置失败: ${e.message || e}`); }
  finally { openPopover.value = null; }
}
async function syncSource() {
  try { await apiClient.syncSource(props.projectName, props.shot.shot_id); message.success('已更新为最新末帧'); emit('refresh'); }
  catch (e: any) { message.error(`同步失败: ${e.message || e}`); }
}
async function refreshVideoEnd() {
  // inherit_previous: 末帧在来源镜头，对来源调 refresh（当前镜头无自己的视频末帧）
  const targetId = (props.shot.opening_source === 'inherit_previous' && props.shot.source_shot_id)
    ? props.shot.source_shot_id : props.shot.shot_id;
  try { await apiClient.refreshVideoEnd(props.projectName, targetId); message.success('末帧已重新提取'); emit('refresh'); }
  catch (e: any) { message.error(`提取失败: ${e.message || e}`); }
}

// ---------- 生产绑定（场景参考） ----------
// BOUND shot 的生成走 Binding-aware 路径（READY→Pack→Adapter；NOT_READY 结构化拒绝，
// 绝不回退 legacy——src/generation/image_adapter._gate_ready_pack）。本面板 = 绑定 /
// 解绑 / 刷新基准 + validity 展示；业务与校验收口在后端 src/shot_binding。
const binding = ref<any>(null);              // ShotProductionBinding（null = UNBOUND）
const bindingRevision = ref('');
const validity = ref<any>(null);
const bindingLoading = ref(true);
const bindingBusy = ref(false);
const bindingOpen = ref(false);
const bindingFormOpen = ref(false);

const bpInstanceId = ref('');
const bpViewId = ref('');
const bpPinnedSivId = ref('');
const bpLookByChar = ref<Record<string, string>>({});
const bpInstances = ref<any[]>([]);
const bpViews = ref<any[]>([]);
const bpApprovedSivs = ref<any[]>([]);
const bpLooksByChar = ref<Record<string, any[]>>({});
const bpLocations = ref<any[]>([]);
const bpCoverUrl = ref('');

const bindingDotClass = computed(() => {
  if (bindingLoading.value || !binding.value) return '';
  return validity.value?.readiness === 'READY' ? 'on' : 'fail';
});
const bindingSummary = computed(() => {
  if (bindingLoading.value) return '';
  if (!binding.value) return '未绑定';
  const r = validity.value?.readiness;
  return r === 'READY' ? '已绑定 · 参考就绪' : r === 'NOT_READY' ? '已绑定 · 未就绪' : '已绑定';
});
const canCreateBinding = computed(() => {
  if (!bpInstanceId.value || !bpViewId.value) return false;
  // cast 规则：binding 角色集合必须与 shot.characters 完全一致，每个角色都要有 Look
  for (const name of (props.shot.characters || [])) {
    if (!bpLookByChar.value[name]) return false;
  }
  return true;
});

const BINDING_ISSUE_LABELS: Record<string, string> = {
  fingerprint_mismatch: '依赖已变更（点「刷新基准」接受当前状态）',
  stale_dependency: '场景图被场景系统判定过期（重新生成/批准后刷新基准）',
  project_visual_profile_unspecified: '项目未设置视觉风格档案（bound 生成必需）',
  visual_profile_unreadable: '风格档案不可读',
  cast_mismatch: '镜头角色与绑定不一致（改角色后需重新绑定）',
  scene_mismatch: '镜头已换故事段，绑定失效（解除后重新绑定）',
  story_scene_mismatch: '布景状态与镜头故事段不一致',
  scene_instance_missing: '布景状态已删除',
  scene_instance_not_active: '布景状态非 active',
  location_view_missing: '机位方向已删除',
  scene_instance_view_missing: '场景图不存在（先在场景系统生成并批准）',
  scene_instance_view_not_approved: '场景图未批准',
  scene_instance_view_ambiguous: '场景图数据异常（同方向多个实体）',
  fingerprint_unavailable: '场景图缺批准基准指纹',
  look_missing: '绑定的 Look 已删除',
  look_not_approved: 'Look 无可用已批准参考',
  character_missing: '角色不存在',
  prop_missing: '绑定的道具已删除',
  prop_not_approved: '道具无可用已批准参考',
  composition_not_approved: '组合参考未批准',
  binding_conflict: '道具被组合与直绑同时声明',
  orphan_binding: '镜头已删除（孤儿绑定）',
};
function bpIssueText(iss: any): string {
  const label = BINDING_ISSUE_LABELS[String(iss?.reason)] || String(iss?.reason || '未知问题');
  const id = iss?.dependency_id && iss.dependency_id !== props.shot.shot_id ? ` [${iss.dependency_id}]` : '';
  const hint = iss?.detail?.hint ? `：${iss.detail.hint}` : '';
  return `${label}${id}${hint}`;
}
function bindingErrText(e: any): string {
  if (e && typeof e === 'object' && ('error' in e || 'detail' in e)) {
    return `[${(e as any).error || 'error'}] ${typeof (e as any).detail === 'string' ? (e as any).detail : JSON.stringify((e as any).detail)}`;
  }
  return String(e?.message || e);
}

function bpInstanceLabel(i: any): string {
  const loc = bpLocations.value.find(l => l.location_id === i.location_id);
  const tod = i.state?.time_of_day ? ` · ${i.state.time_of_day}` : '';
  return `${loc?.name || i.location_id} · ${i.story_scene_id || ''}${tod}`;
}
function bpLookLabel(lookId: string): string {
  for (const ls of Object.values(bpLooksByChar.value)) {
    const hit = (ls || []).find((l: any) => l.id === lookId);
    if (hit) return hit.name || lookId;
  }
  return '';
}

async function loadBindingState() {
  bindingLoading.value = true;
  try {
    const [r, v] = await Promise.all([
      apiClient.getShotBinding(props.projectName, props.shot.shot_id).catch(() => null),
      apiClient.getShotBindingValidity(props.projectName, props.shot.shot_id).catch((e: any) => ({
        readiness: 'UNKNOWN', issues: [{ reason: 'validity_unreadable', detail: { hint: bindingErrText(e) } }],
      })),
    ]);
    binding.value = r?.binding || null;
    bindingRevision.value = r?.binding_revision || '';
    validity.value = v;
    if (binding.value) {
      await loadBoundCover();
      // Look 名称显示（bound 摘要行用；失败静默——仅装饰）
      const names = (binding.value.character_bindings || []).map((cb: any) => cb.character_name);
      await Promise.all(names.map(async (n: string) => {
        if (bpLooksByChar.value[n]) return;
        try {
          const r2 = await apiClient.listLooks(props.projectName, n);
          bpLooksByChar.value[n] = (r2.looks || []).filter((l: any) => l.status === 'approved');
        } catch { bpLooksByChar.value[n] = []; }
      }));
    }
  } finally { bindingLoading.value = false; }
}
/** 已绑定时的场景图封面（最新已提交候选；失败静默——纯装饰） */
async function loadBoundCover() {
  bpCoverUrl.value = '';
  const sb = binding.value?.scene_binding;
  if (!sb) return;
  try {
    const target = sb.pinned_scene_instance_view_id
      || validity.value?.resolved_dependencies?.scene_instance_view_id;
    if (!target) return;
    const g = await apiClient.sceneInstanceViewGenerations(props.projectName, target, 4);
    const committed = (g.generation_records || []).filter((x: any) => x.status === 'committed');
    const f = (committed[committed.length - 1]?.candidate_files || [])[0];
    if (f) bpCoverUrl.value = `/projects/${encodeURIComponent(props.projectName)}/${String(f).replace(/^\/+/, '')}`;
  } catch { /* 封面失败不影响功能 */ }
}

async function openBindingForm() {
  bindingFormOpen.value = true;
  bindingBusy.value = true;
  try {
    const [insts, locs] = await Promise.all([
      apiClient.sceneListSceneInstances(props.projectName),
      apiClient.sceneListLocations(props.projectName).catch(() => ({ locations: [] })),
    ]);
    bpInstances.value = (insts.scene_instances || []).filter((i: any) =>
      i.status === 'active' && (!props.shot.scene_id || i.story_scene_id === props.shot.scene_id));
    bpLocations.value = locs.locations || [];
    if (bpInstances.value.length) {
      bpInstanceId.value = bpInstances.value[0].scene_instance_id;
      await onBpInstanceChange();
    }
    // cast 规则：shot.characters 每个角色都要绑定一个已批准 Look
    const names = props.shot.characters || [];
    const pairs = await Promise.all(names.map((n: string) =>
      apiClient.listLooks(props.projectName, n)
        .then((r: any) => [n, (r.looks || []).filter((l: any) => l.status === 'approved')] as const)
        .catch(() => [n, [] as any[]] as const)));
    bpLooksByChar.value = Object.fromEntries(pairs.map(([n, ls]) => [n, ls as any[]]));
    const picked: Record<string, string> = {};
    for (const [n, ls] of pairs) picked[n] = (ls as any[])[0]?.id || '';
    bpLookByChar.value = picked;
  } catch (e: any) {
    message.error(`加载绑定选项失败: ${bindingErrText(e)}`);
  } finally { bindingBusy.value = false; }
}
async function onBpInstanceChange() {
  bpViewId.value = ''; bpPinnedSivId.value = '';
  bpApprovedSivs.value = []; bpViews.value = [];
  const inst = bpInstances.value.find(i => i.scene_instance_id === bpInstanceId.value);
  if (!inst) return;
  try {
    const vs = await apiClient.sceneListViews(props.projectName, inst.location_id);
    bpViews.value = (vs.views || []).filter((v: any) => v.approval_status === 'approved');
    if (bpViews.value.length) {
      bpViewId.value = bpViews.value[0].view_id;
      await onBpViewChange();
    }
  } catch (e: any) { message.error(`加载机位失败: ${bindingErrText(e)}`); }
}
async function onBpViewChange() {
  bpPinnedSivId.value = '';
  bpApprovedSivs.value = [];
  if (!bpInstanceId.value || !bpViewId.value) return;
  try {
    const s = await apiClient.sceneListInstanceViews(props.projectName, bpInstanceId.value);
    bpApprovedSivs.value = (s.scene_instance_views || []).filter((x: any) =>
      x.location_view_id === bpViewId.value && x.approval_status === 'approved');
  } catch { /* 列表失败时允许不钉住创建 */ }
}

async function createBinding() {
  bindingBusy.value = true;
  try {
    const payload: any = {
      shot_id: props.shot.shot_id,
      scene_id: props.shot.scene_id,
      scene_binding: {
        scene_instance_id: bpInstanceId.value,
        location_view_id: bpViewId.value,
        ...(bpPinnedSivId.value ? { pinned_scene_instance_view_id: bpPinnedSivId.value } : {}),
      },
      character_bindings: (props.shot.characters || [])
        .filter((n: string) => bpLookByChar.value[n])
        .map((n: string) => ({ character_name: n, look_id: bpLookByChar.value[n] })),
      prop_bindings: [],
    };
    const r = await apiClient.createShotBinding(props.projectName, payload);
    message.success('已绑定场景参考（生成将使用绑定的生产参考）');
    bindingFormOpen.value = false;
    await reloadBindingAfterMutation(r);
  } catch (e: any) {
    message.error(`绑定失败: ${bindingErrText(e)}`);
  } finally { bindingBusy.value = false; }
}
async function refreshBinding() {
  bindingBusy.value = true;
  try {
    const r = await apiClient.refreshShotBinding(props.projectName, props.shot.shot_id, bindingRevision.value);
    message.success('已刷新 freshness 基准');
    await reloadBindingAfterMutation(r);
  } catch (e: any) {
    message.error(`刷新失败: ${bindingErrText(e)}`);
  } finally { bindingBusy.value = false; }
}
async function unbindBinding() {
  if (!window.confirm('解除本镜头的场景参考绑定？解除后生成恢复原有流程（绑定记录删除，可重新绑定）。')) return;
  bindingBusy.value = true;
  try {
    await apiClient.deleteShotBinding(props.projectName, props.shot.shot_id, bindingRevision.value);
    message.success('已解除绑定');
    binding.value = null;
    bindingRevision.value = '';
    validity.value = null;
    bpCoverUrl.value = '';
    emit('refresh');
  } catch (e: any) {
    message.error(`解除失败: ${bindingErrText(e)}`);
  } finally { bindingBusy.value = false; }
}
/** 变更后：以 receipt 回填 → 重读 validity → 刷新封面 → 通知父级刷新卡片角标 */
async function reloadBindingAfterMutation(r?: any) {
  if (r?.binding) {
    binding.value = r.binding;
    bindingRevision.value = r.binding_revision || '';
  } else {
    const rd = await apiClient.getShotBinding(props.projectName, props.shot.shot_id).catch(() => null);
    binding.value = rd?.binding || null;
    bindingRevision.value = rd?.binding_revision || '';
  }
  validity.value = await apiClient.getShotBindingValidity(props.projectName, props.shot.shot_id)
    .catch(() => null);
  if (binding.value) await loadBoundCover(); else bpCoverUrl.value = '';
  emit('refresh');
}

watch(() => props.shot.shot_id, () => {
  binding.value = null; validity.value = null; bindingRevision.value = '';
  bindingFormOpen.value = false; bindingOpen.value = false;
  loadBindingState();
});
loadBindingState();

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

// ---------- 渐进式 UI 状态（纯前端，不进项目数据） ----------
type Popover = 'chars' | 'audio' | 'link' | 'opening' | null;
const openPopover = ref<Popover>(null);
const storyExpanded = ref(false);
const previewCollapsed = ref(localStorage.getItem('wb_preview_collapsed') === '1');
function setPreviewCollapsed(v: boolean) { previewCollapsed.value = v; localStorage.setItem('wb_preview_collapsed', v ? '1' : '0'); }
const narrow = ref(false);
const narrowView = ref<'editor' | 'preview'>('editor');

const LINK_OPTIONS = [
  { v: 'i2v', l: '图生视频', d: '跨场景硬切，单图动画化' },
  { v: 'keyframes', l: '首尾帧过渡', d: '同场景接力，首帧+尾帧插值' },
  { v: 'none', l: '不接续', d: '各镜独立，共用一张参考图' },
];
const linkLabel = computed(() => LINK_OPTIONS.find(o => o.v === form.link_mode)?.l || '图生视频');

// 上下文摘要
const storySummary = computed(() => {
  const s = (props.sceneStory || '').trim();
  return s ? (s.slice(0, 22) + (s.length > 22 ? '…' : '')) : '未填写';
});
const charSummary = computed(() => form.characters.length ? form.characters.join('、') : '无角色');
const audioSummary = computed(() => {
  if (audioBusy.value) return '配音中…';
  if (props.shot.audio?.status === 'done' && props.shot.audio?.local) return `已配音 ${((props.shot.audio.duration_ms || 0) / 1000).toFixed(1)}s`;
  return form.dialogue ? '未配音' : '无台词';
});

// 折叠区状态摘要
const preserveSummary = computed(() => form.preserve_elements.length ? `已选 ${form.preserve_elements.length} 项` : '未设置');
const videoPreserveSummary = computed(() => form.video_preserve_elements.length ? `已选 ${form.video_preserve_elements.length} 项` : '未设置');
const profSummary = computed(() => hasProfessionalSettings.value ? '已设置' : '未设置');
const promptSummaryText = (s: PromptStatus) => ({ empty: '未生成', auto: '自动生成 · 已同步', manual: '已手动修改', stale: '待更新' } as const)[s];
const promptSummary = computed(() => promptSummaryText(promptStatusOf(stage.value)));
const historySummary = computed(() => {
  if (stage.value === 'first') return `${props.shot.first_frame_batches?.length || 0} 个批次`;
  if (stage.value === 'last') return `${props.shot.last_frame_batches?.length || 0} 个批次`;
  if (stage.value === 'video') return `${props.shot.video_versions?.length || 0} 个版本`;
  return '';
});
const hasHistory = computed(() => {
  if (stage.value === 'first') return (props.shot.first_frame_batches?.length || 0) > 0;
  if (stage.value === 'last') return (props.shot.last_frame_batches?.length || 0) > 0;
  if (stage.value === 'video') return (props.shot.video_versions?.length || 0) > 0;
  return false;
});

// 预览主画布 + 缩略条（不改动候选/指针逻辑，只读现有数据）
const latestFFBatch = computed(() => props.shot.first_frame_batches?.[props.shot.first_frame_batches.length - 1]);
const mainCanvasUrl = computed(() => {
  if (stage.value === 'first') return effectiveFirstFrameUrl.value;   // inherit -> 来源末帧；其余 -> 已选首帧
  if (stage.value === 'last') return lfUrl.value || (ffUrlReady.value ? ffUrl.value : '');
  return '';
});
const mainCanvasIsVideo = computed(() => stage.value === 'video' && !!(finalVideoVersion.value?.local || videoVersionsReversed.value[0]?.local));
const previewVersionId = ref<string | null>(null);
watch(stage, () => { previewVersionId.value = null; });
const mainCanvasVideoUrl = computed(() => {
  const pv = previewVersionId.value ? videoVersionsReversed.value.find(v => v.version_id === previewVersionId.value) : null;
  const v = pv || finalVideoVersion.value || videoVersionsReversed.value[0];
  return v?.local ? vidUrl(v.local) : '';
});
function previewVersion(vid: string) {
  previewVersionId.value = previewVersionId.value === vid ? null : vid;
}
const mainCanvasOverlay = computed(() => {
  if (stage.value === 'last' && !lfUrl.value && ffUrlReady.value) return '尾帧尚未生成';
  if (stage.value === 'first' && props.shot.opening_source === 'inherit_previous' && !effectiveFirstFrameUrl.value) return '来源末帧未就绪';
  return '';
});
const mainCanvasEmpty = computed(() => {
  if (stage.value === 'first') return !effectiveFirstFrameUrl.value;
  if (stage.value === 'last') return !mainCanvasUrl.value;
  if (stage.value === 'video') return !mainCanvasIsVideo.value;
  return true;
});
const mainCanvasLabel = computed(() => {
  if (stage.value === 'first') {
    if (props.shot.opening_source === 'inherit_previous' && sourceShot.value) return `来自 ${sourceShot.value.shot_id} 的视频末帧`;
    return '首帧';
  }
  if (stage.value === 'last') return lfUrl.value ? '尾帧' : '参考首帧';
  return '视频';
});

// Phase 39F（41.md §4）：首帧大图本体的最小语义角标——只看大槽位也不会把
// reference_only 误解成 frame 0。只依据事实表达：inherit=接力；最终版本
// input_visuals 与当前选中首帧候选（公网 URL/local）匹配 → 该条目角色；
// 无事实不显示（不发明状态）。
const ffSlotRole = computed(() => {
  if (stage.value !== 'first') return null;
  if (props.shot.opening_source === 'inherit_previous') return { key: 'relay', label: '接力' };
  const cid = props.shot.selected_first_frame;
  let cand: any = null;
  for (const b of props.shot.first_frame_batches || []) {
    const c = (b.candidates || []).find((x: any) => x.candidate_id === cid);
    if (c) { cand = c; break; }
  }
  const urls = new Set<string>();
  if (cand?.url) { urls.add(cand.url); urls.add(ivSrc(cand.url)); }
  if (cand?.local) { urls.add(cand.local); urls.add(imgUrl(cand.local)); }
  if (!urls.size) return null;
  const finalVv = (props.shot.video_versions || []).find((v: any) => v.version_id === props.shot.final_video);
  const iv = (finalVv?.input_visuals || []).find((e: any) => e.url && (urls.has(e.url) || urls.has(ivSrc(e.url))));
  if (!iv) return null;
  if (iv.semantic_role === 'reference_only') return { key: 'reference', label: '参考' };
  if (iv.semantic_role === 'relay_start_boundary') return { key: 'relay', label: '接力' };
  if (iv.semantic_role === 'hard_start_boundary') return { key: 'hard', label: '首帧' };
  return null;
});
const thumbStrip = computed(() => {
  if (stage.value === 'first') {
    // inherit_previous：开场=来源末帧（主画布显示），本镜候选不参与，放入历史区，缩略条留空
    if (props.shot.opening_source === 'inherit_previous') return [];
    const arr: any[] = [];
    // reference_previous：缩略条前置「上一镜参考」（来源末帧），与候选区分
    if (props.shot.opening_source === 'reference_previous' && sourceShot.value?.video_end_frame?.local) {
      arr.push({ url: imgUrl(sourceShot.value.video_end_frame.local), label: `${sourceShot.value.shot_id}末帧`, ref: true, cont: true });
    }
    for (const c of (latestFFBatch.value?.candidates || [])) {
      const i = (latestFFBatch.value?.candidates || []).indexOf(c);
      arr.push({ url: c.local ? imgUrl(c.local) : '', cid: c.candidate_id, idx: i + 1, sel: c.candidate_id === props.shot.selected_first_frame });
    }
    return arr;
  }
  if (stage.value === 'last') {
    const arr: any[] = [];
    if (ffUrl.value) arr.push({ url: ffUrl.value, label: '首帧', ref: true });
    for (const c of latestLFCands.value) arr.push({ url: c.local ? imgUrl(c.local) : '', label: '尾帧', cid: c.candidate_id, sel: c.candidate_id === props.shot.selected_last_frame });
    return arr;
  }
  return [];
});

// 阶段主操作（底部 action bar，单一主按钮，不随滚动消失）
const stageAction = computed(() => {
  // inherit_previous：开场沿用上一镜末帧，不生成首帧候选；主按钮改为 解除接力 / 更新来源
  if (stage.value === 'first' && props.shot.opening_source === 'inherit_previous') {
    const stale = continuityStatus.value.status === 'stale';
    return {
      label: stale ? '更新来源' : '解除接力',
      disabled: false,
      pre: `开场沿用 ${props.shot.source_shot_id || '上一镜'} 末帧`,
      fn: stale ? syncSource : () => setOpeningSource('independent'),
    };
  }
  if (stage.value === 'first') return { label: busy('first_frame') ? '生成中…' : '生成首帧（2 候选）', disabled: busy('first_frame'), pre: '', fn: genFirstFrame };
  if (stage.value === 'last') return {
    label: busy('last_frame') ? '生成中…' : '生成尾帧',
    disabled: !effectiveFirstFrameReady.value || busy('last_frame') || form.link_mode !== 'keyframes',
    pre: form.link_mode !== 'keyframes' ? '当前接续方式不需要尾帧' : (!effectiveFirstFrameReady.value ? '开场画面未就绪（请先生成并选择首帧，或来源末帧未就绪）' : ''),
    fn: genLastFrame,
  };
  return { label: busy('video') ? jobMsg('video') : '生成视频', disabled: !canGenVideo.value || busy('video'), pre: videoBlockReason.value, fn: genVideo };
});

// popover：同时只开一个；Escape/点外关闭；打开时聚焦首个可操作项；锚定到触发控件
const popoverRect = ref({ top: 0, left: 0 });
const popoverStyle = computed(() => ({
  top: popoverRect.value.top + 'px',
  left: Math.min(popoverRect.value.left, (typeof window !== 'undefined' ? window.innerWidth : 9999) - 380) + 'px',
}));
function togglePopover(p: Exclude<Popover, null>) {
  if (openPopover.value === p) { openPopover.value = null; return; }
  const el = document.querySelector<HTMLElement>(`[data-pop="${p}"]`);
  if (el) { const r = el.getBoundingClientRect(); popoverRect.value = { top: r.bottom + 6, left: r.left }; }
  openPopover.value = p;
}
function charRefUrl(name: string): string {
  const c = props.projectCharacters.find(x => x.name === name);
  const a = c?.views?.front as any;
  return a?.url || (a?.local ? imgUrl(a.local) : '');
}
function onPopoverKeydown(e: KeyboardEvent) { if (e.key === 'Escape') openPopover.value = null; }
watch(openPopover, async (v) => {
  if (v) { await nextTick(); document.querySelector<HTMLElement>('.popover-open [data-autofocus]')?.focus(); }
});

// 响应式：窄窗口用 参数/预览 视图切换（不上下堆叠）
function updateNarrow() { narrow.value = window.innerWidth < 920; }
onMounted(() => { updateNarrow(); window.addEventListener('resize', updateNarrow); });
onUnmounted(() => { window.removeEventListener('resize', updateNarrow); });

function imgUrl(p: string) { return getImageUrl(p); }
function vidUrl(p: string) { return getVideoUrl(p); }

// Phase 39D：每个视频版本的"生成输入"显示——首尾帧（可以是参考）。
// 参考图（reference_only）用琥珀色框标记，与硬边界（绿）和接力边界（蓝）区分。
// Phase 39F：strip 头标注 provenance 语义；character_look 用「人物参考」标签，
// 避免误读为占据首帧槽（41.md §4）。
function ivSrc(u: string) { return u && u.startsWith('http') ? u : imgUrl(u); }
function ivClass(iv: any) {
  return { refmark: iv.semantic_role === 'reference_only',
           relay: iv.semantic_role === 'relay_start_boundary' };
}
function ivLabel(iv: any) {
  if (iv.semantic_role === 'reference_only') {
    return iv.source_type === 'character_look' ? '人物参考' : '参考';
  }
  if (iv.semantic_role === 'relay_start_boundary') return '首·接力';
  if (iv.semantic_role === 'hard_end_boundary') return '尾帧';
  return '首帧';
}
function ivTitle(iv: any) {
  const role = iv.semantic_role === 'reference_only'
    ? (iv.source_type === 'character_look'
        ? '人物参考（provenance——非首帧槽）'
        : '参考（不钉死画面）')
    : iv.semantic_role === 'relay_start_boundary'
      ? '接力边界（上一镜真实尾帧）'
      : iv.semantic_role === 'hard_end_boundary' ? '硬尾帧边界' : '硬首帧边界';
  const src = iv.source_shot_id ? `，来源 ${iv.source_shot_id}` : '';
  return `${role}${src}`;
}
</script>

<style lang="scss" scoped>
.wb-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.6); z-index: 1500; display: flex; align-items: center; justify-content: center; }
.wb { width: 94vw; height: 92vh; max-width: 1700px; background: var(--bg-root); border: 1px solid var(--border); border-radius: 10px; display: flex; flex-direction: column; overflow: hidden; box-shadow: 0 20px 60px rgba(0,0,0,0.4); }

/* 顶部 */
.wb-head { display: flex; align-items: center; gap: 12px; padding: 10px 20px; border-bottom: 1px solid var(--border); flex-shrink: 0; }
.wb-title { font-size: 13px; font-weight: 600; color: var(--text-primary); font-family: monospace; }
.wb-badge { font-size: 11px; color: var(--text-muted); border: 1px solid var(--border); border-radius: 10px; padding: 2px 9px; white-space: nowrap; }
.mode-switch { display: flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; button { padding: 5px 13px; background: none; border: none; color: var(--text-muted); font-size: 12px; cursor: pointer; &.on { background: var(--accent); color: var(--accent-text); } &:not(.on):hover { color: var(--text-secondary); } } }
.prof-hint { font-size: 11px; color: #fbbf24; cursor: pointer; white-space: nowrap; }
.wb-task { font-size: 11px; color: var(--text-muted); white-space: nowrap; &.busy { color: #60a5fa; } }
.wb-close { margin-left: auto; background: none; border: none; color: var(--text-muted); font-size: 16px; cursor: pointer; padding: 4px; &:hover { color: var(--text-primary); } }

/* 上下文摘要条（紧凑） */
.ctx-bar { display: flex; align-items: center; gap: 6px; padding: 6px 20px; border-bottom: 1px solid var(--border); background: var(--bg-surface); flex-shrink: 0; flex-wrap: wrap; position: relative; }
.ctx-item { display: inline-flex; align-items: center; gap: 5px; padding: 4px 10px; background: none; border: 1px solid var(--border); border-radius: 14px; color: var(--text-secondary); font-size: 11px; cursor: pointer; white-space: nowrap; &:hover { border-color: var(--border-hover); color: var(--text-primary); } &.on { border-color: var(--accent); color: var(--text-primary); } &:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; } }
.ctx-k { color: var(--text-muted); }
.ctx-v { color: var(--text-secondary); max-width: 220px; overflow: hidden; text-overflow: ellipsis; }
.ctx-chev { color: var(--text-muted); font-size: 10px; }
.ctx-story-full { width: 100%; margin-top: 4px; padding: 8px 10px; background: var(--bg-root); border: 1px solid var(--border); border-radius: 6px; font-size: 12px; color: var(--text-secondary); line-height: 1.6; max-height: 120px; overflow-y: auto; }

/* popover */
.popover-backdrop { position: fixed; inset: 0; z-index: 1600; }
.popover { position: fixed; z-index: 1601; width: 340px; max-width: calc(100vw - 24px); max-height: 60vh; overflow-y: auto; background: var(--bg-surface); border: 1px solid var(--border-hover); border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.4); padding: 12px; }
.pop-head { font-size: 12px; font-weight: 600; color: var(--text-secondary); margin-bottom: 8px; }
.pop-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
.chip { padding: 5px 11px; border-radius: 14px; border: 1px solid var(--border); background: none; color: var(--text-secondary); font-size: 12px; cursor: pointer; &.on { background: var(--accent); border-color: var(--accent); color: var(--accent-text); } &:not(.on):hover { border-color: var(--border-hover); color: var(--text-primary); } }
.pop-refs { display: flex; flex-wrap: wrap; gap: 8px; padding-top: 8px; border-top: 1px solid var(--border); }
.pop-ref { display: flex; flex-direction: column; align-items: center; gap: 3px; img { width: 56px; height: 56px; object-fit: cover; border-radius: 4px; border: 1px solid var(--border); } .muted { font-size: 10px; } }
.pop-ref-name { font-size: 10px; color: var(--text-muted); max-width: 64px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pop-input { width: 100%; background: var(--bg-root); border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px; font-size: 13px; color: var(--text-primary); outline: none; resize: vertical; font-family: inherit; &:focus { border-color: var(--accent); } }
.pop-audio-row { display: flex; align-items: center; gap: 10px; margin-top: 10px; }
.audio-status { font-size: 11px; color: var(--text-secondary); }
.pop-list { display: flex; flex-direction: column; gap: 4px; }
.pop-opt { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; padding: 8px 10px; background: none; border: 1px solid var(--border); border-radius: 6px; cursor: pointer; text-align: left; &:hover:not(:disabled) { border-color: var(--border-hover); background: var(--bg-hover); } &.on { border-color: var(--accent); } &:disabled { opacity: 0.5; cursor: not-allowed; } }
.pop-opt-l { font-size: 12px; color: var(--text-primary); }
.pop-opt-d { font-size: 11px; color: var(--text-muted); }
.pop-opt-pre { font-size: 10px; color: #fbbf24; margin-top: 2px; }
.pop-continuity { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border); &.stale .pop-cont-msg { color: #fbbf24; } &.broken .pop-cont-msg { color: #f87171; } &.ready .pop-cont-msg { color: #4ade80; } }
.pop-cont-msg { font-size: 11px; color: var(--text-secondary); margin-bottom: 6px; }
.pop-cont-actions { display: flex; flex-wrap: wrap; gap: 6px; }

/* inherit_previous 接力面板（首帧标签） */
.inherit-panel { padding: 14px; background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 14px; &.stale { border-color: rgba(251,191,36,0.5); } &.broken { border-color: rgba(248,113,113,0.5); } }
.eff-frame-preview { margin-bottom: 14px; }
.eff-frame-preview .eff-label { display: block; font-size: 12px; color: var(--text-muted); margin-bottom: 6px; }
.eff-thumb { position: relative; width: 100%; aspect-ratio: 16/9; max-width: 380px; background: var(--bg-root); border: 1px solid var(--border); border-radius: 6px; overflow: hidden; display: flex; align-items: center; justify-content: center;
  img { width: 100%; height: 100%; object-fit: cover; }
  .muted { font-size: 12px; color: var(--text-faint); }
  &.empty { border-style: dashed; }
  &.small { width: 130px; aspect-ratio: 16/9; max-width: none; }
}
.eff-frame-preview.video-input { display: flex; align-items: center; gap: 10px; }
.eff-arrow { color: var(--text-muted); font-size: 16px; }
.eff-tag { position: absolute; bottom: 2px; right: 4px; font-size: 10px; padding: 1px 5px; border-radius: 3px; background: rgba(0,0,0,0.6); color: #fff; }
.prof-prompt-bar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.stale-hint { font-size: 11px; color: #fbbf24; }
.inherit-title { font-size: 13px; font-weight: 600; color: var(--text-primary); }
.inherit-source { font-size: 12px; color: var(--text-secondary); margin-top: 4px; }
.inherit-msg { font-size: 11px; color: #fbbf24; margin-top: 6px; }
.inherit-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }

/* 阶段标签 */
.stage-tabs { display: flex; gap: 2px; padding: 0 20px; border-bottom: 1px solid var(--border); flex-shrink: 0; button { padding: 10px 26px; border: none; background: none; color: var(--text-muted); font-size: 13px; cursor: pointer; border-bottom: 2px solid transparent; &.on { color: var(--text-primary); border-bottom-color: var(--accent); } &:not(.on):hover { color: var(--text-secondary); } } }
.shot-intent-bar { display: flex; align-items: flex-start; gap: 10px; padding: 8px 20px; border-bottom: 1px solid var(--border); background: var(--bg-surface); flex-shrink: 0; }
.si-label { font-size: 11px; color: var(--text-muted); white-space: nowrap; padding-top: 2px; flex-shrink: 0; }
.si-view { flex: 1; display: flex; align-items: center; gap: 10px; min-width: 0; }
.si-text { flex: 1; font-size: 12px; color: var(--text-secondary); line-height: 1.5; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; &.empty { color: var(--text-faint); font-style: italic; } }
.si-edit { flex: 1; display: flex; flex-direction: column; gap: 6px; }
.si-textarea { width: 100%; background: var(--bg-root); border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px; font-size: 12px; color: var(--text-primary); outline: none; resize: vertical; line-height: 1.5; font-family: inherit; min-height: 56px; &:focus { border-color: var(--accent); } }
.si-actions { display: flex; gap: 6px; }

/* 主体：参数 + 预览 grid；预览可收起 */
.wb-main { display: grid; grid-template-columns: minmax(540px, 1.3fr) minmax(340px, 0.7fr); flex: 1; min-height: 0; }
.wb-main.preview-collapsed { grid-template-columns: 1fr; }
.wb-main.narrow { display: flex; flex-direction: column; }

.view-switch { display: flex; gap: 4px; padding: 6px 20px; border-bottom: 1px solid var(--border); flex-shrink: 0; button { padding: 5px 16px; background: none; border: 1px solid var(--border); border-radius: 6px; color: var(--text-muted); font-size: 12px; cursor: pointer; &.on { background: var(--accent); color: var(--accent-text); border-color: var(--accent); } } }

.editor-panel { overflow-y: auto; border-right: 1px solid var(--border); }
.wb-main.preview-collapsed .editor-panel { border-right: none; }
.wb-main.narrow .editor-panel { border-right: none; border-bottom: 1px solid var(--border); flex: 1; }
.editor-scroll { padding: 16px 20px; }

/* 字段 */
.field { margin-bottom: 14px; label { display: block; font-size: 12px; font-weight: 500; color: var(--text-secondary); margin-bottom: 6px; white-space: nowrap; } }
.field input, .field select, .field textarea { width: 100%; background: var(--bg-surface); border: 1px solid var(--border); border-radius: 6px; padding: 9px 12px; font-size: 13px; color: var(--text-primary); outline: none; font-family: inherit; transition: border-color .12s; &:focus { border-color: var(--accent); } &::placeholder { color: var(--text-faint); } }
.field textarea { resize: vertical; line-height: 1.55; min-height: 64px; }
.field select { cursor: pointer; }
.field-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.seed-row { display: flex; gap: 8px; align-items: center; & > select { width: auto; flex: 0 0 auto; } & > input { width: auto; flex: 1 1 auto; min-width: 0; } }
.dur-input { margin-top: 8px; }
.duration-row { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; font-size: 12px; flex-wrap: wrap; }
.duration-row .dur-label { color: var(--text-muted); }
.duration-row select { padding: 4px 8px; border-radius: 4px; border: 1px solid var(--border); background: var(--bg-root); color: var(--text-secondary); font-size: 12px; }
.duration-row .dur-input { margin-top: 0; width: 80px; }

/* 保持一致 / 保持稳定：可选 tile */
.preserve-groups { display: flex; flex-direction: column; gap: 12px; }
.preserve-group { .pg-title { font-size: 11px; color: var(--text-muted); margin-bottom: 6px; } }
.tile-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.tile { display: block; position: relative; cursor: pointer; }
.tile input { position: absolute; opacity: 0; inset: 0; width: 100%; height: 100%; margin: 0; cursor: pointer; }
.tile-body { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 9px 12px; border: 1px solid var(--border); border-radius: 6px; background: transparent; color: var(--text-secondary); font-size: 13px; transition: border-color .12s, background .12s, color .12s; }
.tile:hover .tile-body { border-color: var(--border-hover); background: var(--bg-hover); color: var(--text-primary); }
.tile input:focus-visible + .tile-body { border-color: var(--accent); }
.tile input:checked + .tile-body { border-color: #4ade80; background: rgba(74,222,128,0.07); color: var(--text-primary); }
.tile-label { white-space: nowrap; }
.tile-check { opacity: 0; color: #4ade80; font-size: 13px; font-weight: 700; transition: opacity .12s; }
.tile input:checked + .tile-body .tile-check { opacity: 1; }

.muted { font-size: 11px; color: var(--text-faint); }
.pad { padding: 12px 0; }
.mini-btn { padding: 6px 12px; border-radius: 6px; border: 1px solid var(--border); background: var(--bg-surface); color: var(--text-secondary); font-size: 12px; cursor: pointer; white-space: nowrap; transition: border-color .12s, color .12s; &:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); } &:disabled { opacity: 0.5; cursor: not-allowed; } }

/* 预览面板 */
.preview-panel { overflow-y: auto; padding: 14px 18px; display: flex; flex-direction: column; gap: 12px; }
.wb-main.narrow .preview-panel { flex: 1; }
.preview-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding-bottom: 8px; border-bottom: 1px solid var(--border); }
.preview-title { font-size: 13px; font-weight: 600; color: var(--text-primary); }
.preview-body { display: flex; flex-direction: column; gap: 12px; }
.canvas { position: relative; border: 1px solid var(--border); border-radius: 8px; background: var(--bg-surface); overflow: hidden; display: flex; align-items: center; justify-content: center; align-self: flex-start; width: 100%; &.empty { min-height: 140px; } img, video { width: 100%; height: 100%; object-fit: contain; } video { background: #000; } }
.canvas-empty { font-size: 12px; color: var(--text-faint); padding: 24px; }
.canvas-overlay { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; background: rgba(0,0,0,0.45); color: #e5e5e5; font-size: 12px; }
.canvas-label { position: absolute; top: 6px; left: 8px; font-size: 10px; color: #fff; background: rgba(0,0,0,0.55); padding: 1px 6px; border-radius: 3px; }
.thumb-strip { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 4px; }
.thumb { position: relative; flex: 0 0 auto; width: 88px; height: 50px; border: 2px solid var(--border); border-radius: 5px; overflow: hidden; cursor: pointer; background: var(--bg-surface); padding: 0; &.sel { border-color: #4ade80; } &.ref { border-style: dashed; } &.cont { border-color: #60a5fa; border-style: dotted; } img { width: 100%; height: 100%; object-fit: cover; } .muted { display: flex; align-items: center; justify-content: center; width: 100%; height: 100%; } }
.thumb-tag { position: absolute; bottom: 0; left: 0; right: 0; font-size: 9px; color: #fff; background: rgba(0,0,0,0.55); text-align: center; padding: 1px; }
.video-versions { display: flex; flex-direction: column; gap: 6px; }
.video-version { display: flex; align-items: center; gap: 10px; padding: 7px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 12px; &.final { border-color: #4ade80; } }
.vv-thumb { width: 72px; aspect-ratio: 16/9; border-radius: 4px; object-fit: cover; background: var(--bg-root); flex-shrink: 0; cursor: pointer; &.previewing { outline: 2px solid var(--accent); outline-offset: 1px; } }
.vv-info { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; flex: 1; }
.vv-inputs { display: flex; gap: 5px; flex-shrink: 0; align-items: center; }
.vv-inputs-head { font-size: 9px; color: var(--text-muted); white-space: nowrap; cursor: help; }
.vv-input { position: relative; width: 52px; aspect-ratio: 16/9; border-radius: 4px; overflow: hidden; border: 2px solid #4ade80; flex-shrink: 0;
  img { width: 100%; height: 100%; object-fit: cover; display: block; }
  &.refmark { border-color: #fbbf24; }   // 参考图标记（琥珀）
  &.relay { border-color: #60a5fa; }      // 接力边界（蓝）
  .iv-tag { position: absolute; bottom: 0; left: 0; right: 0; font-size: 8px; color: #fff; background: rgba(0,0,0,0.55); text-align: center; padding: 0 1px; } }
.ff-role-badge { position: absolute; top: 6px; right: 8px; font-size: 10px; padding: 1px 7px; border-radius: 9px; border: 1px solid; background: rgba(0,0,0,0.55); pointer-events: none;
  &.reference { color: #fbbf24; border-color: #fbbf24; }   // 参考（琥珀）
  &.relay { color: #60a5fa; border-color: #60a5fa; }       // 接力（蓝）
  &.hard { color: #34d399; border-color: #34d399; } }      // 硬边界（绿）
.vv-no { font-size: 11px; font-weight: 600; color: var(--text-secondary); font-family: monospace; }
.vv-ret { color: var(--text-muted); }
.vv-final { color: #4ade80; font-weight: 600; }

/* 生成历史批次（首帧） */
.batch { margin-bottom: 8px; }
.batch-head { display: flex; align-items: center; gap: 6px; padding: 7px 10px; background: var(--bg-surface); border: 1px solid var(--border); border-radius: 6px; cursor: pointer; &:hover { background: var(--bg-hover); } }
.batch-chevron { font-size: 11px; color: var(--text-muted); }
.batch-title { font-size: 12px; color: var(--text-secondary); }
.cands { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 8px; margin-top: 8px; }
.cand { position: relative; border-radius: 6px; overflow: hidden; cursor: pointer; border: 2px solid transparent; background: var(--bg-surface); &.sel { border-color: #4ade80; } img { width: 100%; height: 100%; object-fit: cover; } }
.cand-idx { position: absolute; top: 3px; left: 4px; font-size: 10px; color: #fff; background: rgba(0,0,0,0.55); padding: 0 4px; border-radius: 2px; }
.empty-state { font-size: 12px; color: var(--text-faint); padding: 16px; background: var(--bg-surface); border: 1px dashed var(--border); border-radius: 8px; line-height: 1.6; }

.reopen-preview { align-self: flex-start; padding: 8px 12px; margin: 14px 18px; border: 1px solid var(--border); border-radius: 6px; background: var(--bg-surface); color: var(--text-secondary); font-size: 12px; cursor: pointer; &:hover { border-color: var(--accent); color: var(--accent); } }

/* 底部 action bar（始终可见） */
.stage-action-bar { display: flex; align-items: center; gap: 14px; padding: 10px 20px; border-top: 1px solid var(--border); background: var(--bg-surface); flex-shrink: 0; }
.save-status { font-size: 12px; color: var(--text-muted); &.saved { color: #4ade80; } &.failed { color: #f87171; } &.saving { color: var(--text-secondary); } }
.pre-hint { font-size: 12px; color: #fbbf24; }
.main-gen-btn { margin-left: auto; padding: 9px 24px; border-radius: 6px; border: none; background: var(--accent); color: var(--accent-text); font-size: 13px; font-weight: 600; cursor: pointer; white-space: nowrap; &:hover:not(:disabled) { background: var(--accent-hover); } &:disabled { opacity: 0.5; cursor: not-allowed; } }

/* 生产绑定（场景参考） */
.binding-panel { border: 1px solid var(--border); border-radius: 8px; background: var(--bg-surface); margin-bottom: 12px; }
.bp-head { display: flex; align-items: center; gap: 8px; padding: 9px 12px; cursor: pointer; &:hover { background: var(--bg-hover); border-radius: 8px; } }
.bp-title { font-size: 12px; font-weight: 600; color: var(--text-primary); }
.bp-sum { font-size: 11px; color: var(--text-muted); }
.bp-chev { color: var(--text-faint); transition: transform 0.15s ease; &.open { transform: rotate(180deg); } }
.bp-body { padding: 4px 12px 12px; border-top: 1px solid var(--border); }
.bp-muted { font-size: 12px; color: var(--text-muted); line-height: 1.7; margin: 8px 0; }
.bp-warn { font-size: 12px; color: var(--text-secondary); background: var(--bg-root); border: 1px dashed var(--border); border-radius: 6px; padding: 7px 10px; line-height: 1.6; margin: 8px 0; }
.bp-field { display: flex; flex-direction: column; gap: 4px; margin: 8px 0;
  label { font-size: 11px; color: var(--text-muted); }
  select { background: var(--bg-root); border: 1px solid var(--border); border-radius: 5px; color: var(--text-primary); font-size: 12px; padding: 6px 8px; font-family: inherit;
    &:focus { outline: none; border-color: var(--border-hover); } } }
.bp-actions { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
.mini-btn.primary { border-color: var(--accent); color: var(--accent); &:hover:not(:disabled) { background: var(--accent); color: var(--accent-text); } }
.mini-btn.danger { color: #f87171; &:hover:not(:disabled) { border-color: #f87171; color: #f87171; } }
.bp-bound { display: flex; flex-direction: column; gap: 10px; padding-top: 8px; }
.bp-scene { display: flex; gap: 10px; align-items: flex-start; }
.bp-thumb { width: 96px; aspect-ratio: 16 / 9; flex-shrink: 0; border-radius: 5px; overflow: hidden; border: 1px solid var(--border); background: #000;
  img { display: block; width: 100%; height: 100%; object-fit: cover; }
  &.empty { display: flex; align-items: center; justify-content: center; font-size: 11px; color: var(--text-faint); } }
.bp-scene-info { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.bp-line { font-size: 12px; color: var(--text-secondary); line-height: 1.5;
  .k { display: inline-block; min-width: 42px; font-size: 11px; color: var(--text-faint); } }
.bp-validity { border-top: 1px dashed var(--border); padding-top: 8px; }
.bp-v-line { font-size: 12px; color: var(--text-secondary);
  &.bad { color: #f87171; } }
.bp-issue { font-size: 11px; color: var(--text-muted); line-height: 1.6; margin-top: 3px; }

/* 低高度：压缩非必要 padding，action bar 始终可见 */
@media (max-height: 720px) {
  .wb-head { padding: 7px 20px; }
  .ctx-bar { padding: 4px 20px; }
  .stage-tabs button { padding: 7px 26px; }
  .editor-scroll { padding: 12px 20px; }
}
/* 窄窗口：参数/预览视图切换 */
@media (max-width: 920px) {
  .wb { width: 100vw; height: 100vh; max-width: none; border-radius: 0; border: none; }
  .field-row { grid-template-columns: 1fr; }
}
</style>
