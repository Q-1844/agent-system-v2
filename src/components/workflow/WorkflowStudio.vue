<template>
  <div class="wf-studio">
    <!-- ============ 顶部工具条 ============ -->
    <header class="wf-topbar">
      <h2 class="wf-title">工作流</h2>
      <input
        v-model="draft.name"
        class="wf-name"
        placeholder="工作流名称"
        spellcheck="false"
        @input="markDirty"
      />
      <span class="wf-chip" :class="saveTone">
        <span class="dot" />{{ saveText }}
      </span>
      <span v-if="backendOnline === false" class="wf-chip chip-warn"
            title="后端未连接：注册项以仓库快照展示，无法核对 Agent 停用状态">
        后端未连接
      </span>

      <span class="wf-spacer" />

      <div class="wf-legend" title="两类连线的图例">
        <span class="lg-item"><span class="lg-line exec" />执行依赖</span>
        <span class="lg-item"><span class="lg-line bind" />Skill 绑定</span>
      </div>

      <button class="btn btn-outline btn-sm" :class="{ 'check-bad': validation.errors > 0, 'check-ok': validation.errors === 0 && validation.warnings === 0 }"
              @click="runCheckManually">
        结构检查{{ checkLabel }}
      </button>
      <button class="btn btn-quiet btn-sm assistant-toggle" :class="{ 'assistant-on': assistantOpen }"
              title="搭建助手：按你的描述自动搭建工作流草稿"
              @click="assistantOpen = !assistantOpen">
        搭建助手
      </button>
      <button class="btn btn-quiet btn-sm" @click="openTemplates">从模板新建</button>
      <button class="btn btn-quiet btn-sm" @click="loadSample">示例工作流</button>
      <button class="btn btn-quiet btn-sm" @click="exportJson">导出 JSON</button>
      <button class="btn btn-primary btn-sm" @click="saveNow">保存草稿</button>
      <button class="btn btn-outline btn-sm" :disabled="starting || backendOnline !== true"
              :title="runBtnTitle" @click="startRun">
        <span class="run-full">{{ starting ? '启动中…' : '运行' }}</span>
        <span class="run-short" title="运行">运行</span>
      </button>
      <button v-if="activeRun" class="btn btn-outline btn-sm"
              :class="{ 'assistant-on': runPanelOpen }" @click="toggleRunPanel">
        运行状态<span v-if="runBadge" class="run-badge">{{ runBadge }}</span>
      </button>
    </header>

    <div class="wf-main">
      <!-- ============ 左：组件库 ============ -->
      <aside class="wf-lib">
        <input v-model="libSearch" class="input lib-search" placeholder="搜索组件…" spellcheck="false" />
        <div class="lib-scroll">
          <div class="lib-group">
            <div class="lib-group-title">Agent 组件<span class="cnt">{{ filteredAgents.length }}</span></div>
            <div
              v-for="a in filteredAgents"
              :key="a.id"
              class="lib-item"
              draggable="true"
              :title="`${a.description}\n来源：${a.source}`"
              @dragstart="onLibDragStart($event, { kind: 'agent', refId: a.id })"
              @click="addNodeFromLib({ kind: 'agent', refId: a.id })"
            >
              <div class="li-row">
                <span class="li-name">{{ a.name }}</span>
                <span class="li-id mono-num">{{ a.id }}</span>
              </div>
              <div class="li-sub">{{ a.role }}</div>
            </div>
          </div>

          <div class="lib-group">
            <div class="lib-group-title">Skill 组件<span class="cnt">{{ filteredSkills.length }}</span></div>
            <div
              v-for="s in filteredSkills"
              :key="s.id"
              class="lib-item lib-skill"
              draggable="true"
              :title="`${s.description}\n来源：${s.manifestSource}`"
              @dragstart="onLibDragStart($event, { kind: 'skill', refId: s.id })"
              @click="addNodeFromLib({ kind: 'skill', refId: s.id })"
            >
              <div class="li-row">
                <span class="li-name">{{ s.name }}</span>
                <span class="li-id mono-num">v{{ s.version }}</span>
              </div>
              <div class="li-sub mono-num">{{ s.id }}</div>
            </div>
            <div class="lib-hint">Skill 需用绑定线连到 Agent，才会被该 Agent 使用</div>
          </div>

          <div class="lib-group">
            <div class="lib-group-title">人工审核<span class="cnt">1</span></div>
            <div
              class="lib-item lib-review"
              title="在 Agent 之间插入人工审核；可连续放置两道表达「先审一次，再审一次」"
              @click="addNodeFromLib({ kind: 'human_review' })"
            >
              <div class="li-row"><span class="li-name">人工审核</span></div>
              <div class="li-sub">通过 → 继续；驳回 → 停止并留意见</div>
            </div>
          </div>

          <div class="lib-group">
            <div class="lib-group-title">边界提示<span class="cnt">2</span></div>
            <div class="lib-boundary-row">
              <button class="btn btn-outline btn-sm" @click="addNodeFromLib({ kind: 'boundary_start' })">▶ 开始</button>
              <button class="btn btn-outline btn-sm" @click="addNodeFromLib({ kind: 'boundary_end' })">■ 结束</button>
            </div>
            <div class="lib-hint">开始 / 结束只是画布边界提示，不算组件类型</div>
          </div>
        </div>
        <div class="lib-foot">注册项来源：src/agents/registry.py · src/agents/skill_system/builtin（快照，详见契约为档）</div>
      </aside>

      <!-- ============ 中：画布 ============ -->
      <section
        ref="canvasWrap"
        class="wf-canvas-wrap"
        :class="{ 'drag-over': draggingOver }"
        @dragover.prevent
        @dragenter.prevent="onDragEnterWrap"
        @dragleave="onDragLeaveWrap"
        @drop="onDrop"
      >
        <VueFlow
          :id="WF_FLOW_ID"
          :nodes="flowNodes"
          :edges="flowEdges"
          :delete-key-code="null"
          :min-zoom="0.25"
          :max-zoom="2"
          :connection-radius="30"
          class="wf-flow"
          @nodes-change="onNodesChange"
          @connect="onConnect"
          @node-click="onNodeClick"
          @edge-click="onEdgeClick"
          @edge-double-click="onEdgeDoubleClick"
          @pane-click="clearSelection"
        >
          <Background :gap="20" :size="1.4" pattern-color="#808080" />
          <template #node-wfAgent="p"><AgentNode v-bind="p" /></template>
          <template #node-wfSkill="p"><SkillNode v-bind="p" /></template>
          <template #node-wfReview="p"><ReviewNode v-bind="p" /></template>
          <template #node-wfBoundary="p"><BoundaryNode v-bind="p" /></template>
          <template #edge-wfEdge="p"><WfEdge v-bind="p" /></template>
        </VueFlow>

        <div class="wf-canvas-controls">
          <button class="ctrl-btn" title="缩小" @click="zoomOut()">−</button>
          <span class="ctrl-zoom mono-num">{{ zoomPct }}%</span>
          <button class="ctrl-btn" title="放大" @click="zoomIn()">+</button>
          <button class="ctrl-btn" title="适应画布" @click="fitToContent()">⤢</button>
        </div>

        <div v-if="!draft.nodes.length" class="wf-empty-guide">
          <p>画布为空。从左侧组件库添加 Agent / Skill / 人工审核，</p>
          <p>或 <a class="guide-link" @click.prevent="loadSample">载入示例工作流</a>，看一条由真实注册项组成的链。</p>
        </div>
      </section>

      <!-- ============ 右：配置面板 ============ -->
      <aside class="wf-panel">
        <!-- 运行视图（run 面板开关打开时优先） -->
        <template v-if="panelMode === 'run'">
          <WorkflowRunPanel
            :run="activeRun"
            :get-ref-id="getRefId"
            @decide="onDecideReview"
            @stop="onStopRun"
            @continue="onContinueRun"
            @locate="locateNode"
          />
        </template>

        <!-- 概览（未选中） -->
        <template v-else-if="panelMode === 'overview'">
          <div class="panel-head">
            <span class="panel-title">工作流概览</span>
          </div>
          <div class="panel-body">
            <div class="kv"><span class="k">项目</span><span class="v">{{ projectName }}</span></div>
            <div class="kv"><span class="k">工作流</span><span class="v">{{ draft.name || '未命名' }}</span></div>
            <div class="kv"><span class="k">标识</span><span class="v mono-num">{{ draft.workflowId }}</span></div>
            <div class="stat-row">
              <span class="stat"><b>{{ countBy('agent') }}</b>Agent</span>
              <span class="stat"><b>{{ countBy('skill') }}</b>Skill</span>
              <span class="stat"><b>{{ countBy('human_review') }}</b>人工审核</span>
              <span class="stat"><b>{{ execCount }}</b>执行线</span>
              <span class="stat"><b>{{ bindCount }}</b>绑定线</span>
            </div>

            <div class="sec-label">结构检查</div>
            <div v-if="!validation.issues.length" class="check-ok-line">✓ 未发现问题</div>
            <div v-else class="issue-list">
              <button
                v-for="(iss, i) in validation.issues"
                :key="i"
                class="issue"
                :class="iss.level"
                @click="jumpToIssue(iss)"
              >
                <span class="iss-badge">{{ iss.level === 'error' ? '✕' : '⚠' }}</span>
                <span class="iss-text">{{ iss.message }}</span>
              </button>
            </div>

            <div class="sec-label">本页语义</div>
            <p class="sem-note">
              草稿保存到<b>后端</b>（带版本冲突保护，浏览器留离线副本）；「运行」按保存的
              定义启动一条真实生产链（后端顺序调度，文本调用开启思考）。执行依赖为无环
              顺序图；人工审核「通过→继续、驳回→停止」；资产批准/选片/定版仍走项目页正式入口。
            </p>
            <button class="btn btn-quiet btn-sm danger-link" @click="clearCanvas">新建空白工作流</button>
          </div>
        </template>

        <!-- Agent 配置 -->
        <template v-else-if="panelMode === 'agent' && selectedAgentNode">
          <div class="panel-head">
            <span class="panel-title">{{ selAgent?.name ?? selectedAgentNode.refId }}</span>
            <span class="panel-id mono-num">{{ selectedAgentNode.refId }}</span>
          </div>
          <div class="panel-body">
            <p v-if="!selAgent" class="unset-hint">引用失效：注册表中找不到该 Agent。请删除该节点或改用正式 Agent。</p>
            <template v-else>
              <div class="kv"><span class="k">角色</span><span class="v">{{ selAgent.role }}</span></div>
              <p class="p-desc">{{ selAgent.description }}</p>
              <div class="kv"><span class="k">读取</span><span class="v">{{ selAgent.readScopes.join(' / ') }}</span></div>
              <div class="kv"><span class="k">写入</span><span class="v">{{ selAgent.writeScopes.join(' / ') }}</span></div>
              <div class="kv"><span class="k">产物</span><span class="v">{{ selAgent.artifacts.join('；') }}</span></div>
              <div class="kv"><span class="k">注册依据</span><span class="v mono-num tiny">{{ selAgent.source }}</span></div>

              <div v-if="selectedAgentNode && isAgentDisabled(selectedAgentNode.refId)" class="unset-hint warn">
                ◌ 后端已停用该 Agent（settings.enabled=false）：画布可编辑，运行不可用。
              </div>

              <div class="sec-label">任务目标与范围（运行必需）</div>
              <textarea
                :value="selectedAgentNode.config.objective ?? ''"
                class="input obj-input"
                rows="3"
                placeholder="本节点要做什么、范围是什么（例如：为本项目编写两段小故事）。启动前必填。"
                spellcheck="false"
                @input="onObjectiveInput"
              />
              <p class="sem-note tiny">
                留空时草稿可保存，但「运行」会被后端阻止并指出缺项。
                上游节点的产物会作为交接包自动传给本节点，无需在此粘贴内容。
              </p>

              <!-- Phase 47 B批：产物要求（目标级完成校验契约） -->
              <div class="sec-label">产物要求（完成判定）</div>
              <div class="kv">
                <span class="k">完成策略</span>
                <select class="input tpl-input" :value="selectedAgentNode.config.completion?.strategy ?? 'require_new'" @change="onStrategyChange">
                  <option value="require_new">require_new（本 attempt 必须有新产物）</option>
                  <option value="allow_verified_reuse">allow_verified_reuse（允许服务端核验既有产物）</option>
                </select>
              </div>
              <div v-for="(spec, i) in (selectedAgentNode.config.completion?.outputs || [])" :key="i" class="spec-row">
                <select class="input spec-kind" :value="spec.kind" @change="onSpecKind(i, $event)">
                  <option v-for="k in COMPLETION_KINDS" :key="k" :value="k">{{ k }}</option>
                </select>
                <input class="input spec-count" type="number" min="1" :value="spec.minCount ?? 1" @change="onSpecCount(i, $event)" title="最少数量" />
                <input class="input spec-id" :value="spec.id ?? ''" placeholder="实体 ID（可选）" @change="onSpecId(i, $event)" />
                <button class="btn btn-quiet btn-sm danger-link spec-del" @click="removeOutputSpec(i)">删</button>
                <div class="spec-flags">
                  <label v-for="f in COMPLETION_FLAGS" :key="f.key" class="spec-flag tiny">
                    <input type="checkbox" :checked="specFlag(spec, f.key)" @change="onSpecFlag(i, f.key, $event)" />{{ f.label }}
                  </label>
                </div>
              </div>
              <button class="btn btn-quiet btn-sm" @click="addOutputSpec">+ 添加产物要求</button>
              <p class="sem-note tiny">
                产物要求逐项按项目真实事实核对（存在性/图像/审批/可播放）；
                默认 require_new 不接受旧素材冒充新产物。留空 = 只按回执域校验（Phase 46 语义）。
              </p>

              <div class="sec-label">工具面</div>
              <div class="tool-block-head">
                <span>核心工具 · 模块自带必有</span>
                <span class="tool-count">{{ selAgent.tools.length }}</span>
              </div>
              <div class="tool-list core">
                <div v-for="t in selAgent.tools" :key="t" class="tool-item core">
                  <span class="core-tick">✓</span>
                  <span class="mono-num">{{ t }}</span>
                </div>
              </div>
              <div class="tool-block-head extra">
                <span>额外工具 · 可选</span>
              </div>
              <p class="unset-hint">暂无额外工具。系统后续注册新工具后，可在此为该 Agent 勾选启用。</p>
              <p class="sem-note tiny">核心工具由注册表定义、随 Agent 必带；额外工具仅记录在前端草稿，不扩大权限、不创建新工具类型。</p>
            </template>
            <button class="btn btn-outline btn-sm danger-link" @click="removeNode(selectedAgentNode!.id)">删除此节点</button>
          </div>
        </template>

        <!-- Skill 配置 -->
        <template v-else-if="panelMode === 'skill' && selectedSkillNode">
          <div class="panel-head">
            <span class="panel-title">{{ selSkill?.name ?? selectedSkillNode.refId }}</span>
            <span class="panel-id mono-num">v{{ selectedSkillNode.refVersion }}</span>
          </div>
          <div class="panel-body">
            <p v-if="!selSkill" class="unset-hint">引用失效：正式 Skill 目录中找不到该 Skill。</p>
            <template v-else>
              <div class="chip-row">
                <span class="wf-chip chip-blue">正式 Skill</span>
                <span class="wf-chip mono-num">{{ selSkill.source }}</span>
                <span class="wf-chip">{{ selSkill.status }}</span>
              </div>
              <p class="p-desc">{{ selSkill.description }}</p>
              <div class="kv"><span class="k">适用</span><span class="v">{{ selSkill.applicability }}</span></div>
              <div v-if="selSkill.dependsOn.length" class="kv">
                <span class="k">依赖</span>
                <span class="v mono-num">{{ selSkill.dependsOn.join('、') }}</span>
              </div>
              <div class="kv"><span class="k">清单来源</span><span class="v mono-num tiny">{{ selSkill.manifestSource }}</span></div>

              <div class="sec-label">官方绑定（skill.json）</div>
              <div class="bind-table">
                <div v-for="b in selSkill.bindings" :key="b.agentId" class="bind-row">
                  <span class="bind-agent">{{ getAgent(b.agentId)?.name ?? b.agentId }}</span>
                  <span class="bind-role">{{ b.role }}</span>
                  <span class="mono-num bind-pri">P{{ b.routingPriority }}</span>
                </div>
              </div>
              <p class="sem-note tiny">Skill 绑定给 Agent 使用，不表示 Skill 会先独立运行或生成产物。</p>
            </template>
            <button class="btn btn-outline btn-sm danger-link" @click="removeNode(selectedSkillNode!.id)">删除此节点</button>
          </div>
        </template>

        <!-- 人工审核配置 -->
        <template v-else-if="panelMode === 'review' && selectedReviewNode">
          <div class="panel-head">
            <span class="panel-title">人工审核</span>
            <span class="panel-id">可连续放置两道</span>
          </div>
          <div class="panel-body">
            <div class="field">
              <span class="f-k">显示名</span>
              <input v-model="reviewLabel" class="input" placeholder="如：场景参考审定" spellcheck="false" />
            </div>

            <div class="field">
              <span class="f-k">审核对象 / 产物</span>
              <template v-if="reviewUpstreamOptions.length">
                <select v-model="reviewTargetSource" class="input">
                  <option v-for="opt in reviewUpstreamOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
                  <option value="__custom__">自定义…</option>
                </select>
                <select v-if="reviewTargetSource !== '__custom__' && reviewArtifactOptions.length"
                        v-model="reviewTargetArtifact" class="input">
                  <option v-for="opt in reviewArtifactOptions" :key="opt" :value="opt">{{ opt }}</option>
                </select>
                <input v-else v-model="reviewTargetArtifact" class="input" placeholder="审核对象（草稿自由填写）" spellcheck="false" />
              </template>
              <template v-else>
                <input v-model="reviewTargetArtifact" class="input" placeholder="审核对象（尚无上游连线，自由填写）" spellcheck="false" />
                <span class="unset-hint">⚠ 无上游执行依赖：结构检查会提示「审核节点没有上游产物」</span>
              </template>
            </div>

            <div class="field">
              <span class="f-k">审核说明</span>
              <textarea v-model="reviewInstructions" class="input" rows="3" placeholder="审核要点、退回标准…" />
            </div>
            <div class="field">
              <span class="f-k">审核者</span>
              <input v-model="reviewReviewer" class="input" placeholder="如：制作人（草稿自由填写，仓库无审核者名册）" spellcheck="false" />
            </div>

            <!-- Phase 47 §4.6：批准并选用目标（运行期在审核卡片从真实候选中选定） -->
            <div class="field">
              <span class="f-k">通过时需选用</span>
              <select class="input" :value="reviewSelectionKind" @change="onReviewSelectionKind($event)">
                <option value="">不要求（仅确认/意见）</option>
                <option value="video_version">视频版本（shot final_video）</option>
                <option value="sequence_render">成片 render（final_render）</option>
                <option value="first_frame">分镜首帧候选</option>
                <option value="look_reference">Look 参考图批准</option>
                <option value="prop_reference">道具参考图批准</option>
              </select>
            </div>
            <div v-if="reviewSelectionKind" class="field">
              <span class="f-k">选用对象 ID</span>
              <input v-model="reviewSelectionEntity" class="input mono-num"
                     placeholder="shotId / sequenceId / lookId（运行期也可在卡片从候选选择）" spellcheck="false" />
            </div>

            <div class="sec-label">首版语义</div>
            <p class="sem-note">
              通过 → 沿后续执行依赖继续；驳回 → 停止并留下意见，等待人工修改工作流或重新发起。
              <b>自动返工循环与暂停中的后端任务尚未实现（待后端定义）。</b>
            </p>
            <button class="btn btn-outline btn-sm danger-link" @click="removeNode(selectedReviewNode!.id)">删除此节点</button>
          </div>
        </template>

        <!-- 边界配置 -->
        <template v-else-if="panelMode === 'boundary' && selectedBoundaryNode">
          <div class="panel-head">
            <span class="panel-title">{{ selectedBoundaryNode.type === 'boundary_start' ? '开始（边界）' : '结束（边界）' }}</span>
          </div>
          <div class="panel-body">
            <p class="sem-note">开始 / 结束只是画布边界提示，不是组件类型，不参与执行语义。</p>
            <button class="btn btn-outline btn-sm danger-link" @click="removeNode(selectedBoundaryNode!.id)">删除此节点</button>
          </div>
        </template>

        <!-- 连线信息 -->
        <template v-else-if="panelMode === 'edge' && selectedEdge">
          <div class="panel-head">
            <span class="panel-title">{{ selectedEdge.type === 'skill_binding' ? 'Skill 绑定' : '执行依赖' }}</span>
            <span class="wf-chip" :class="selectedEdge.type === 'skill_binding' ? 'chip-blue' : ''">
              {{ selectedEdge.type === 'skill_binding' ? '绑定线' : '执行线' }}
            </span>
          </div>
          <div class="panel-body">
            <div class="kv">
              <span class="k">方向</span>
              <span class="v">{{ edgeSourceTitle }} <b class="arrow">→</b> {{ edgeTargetTitle }}</span>
            </div>
            <div class="kv">
              <span class="k">端口</span>
              <span class="v mono-num">{{ portLabel(selectedEdge.sourcePort) }} → {{ portLabel(selectedEdge.targetPort) }}</span>
            </div>

            <template v-if="selectedEdge.type === 'skill_binding'">
              <div class="kv">
                <span class="k">版本</span>
                <span class="v mono-num">v{{ selectedEdge.skillVersion ?? '?' }}</span>
              </div>
              <p class="sem-note">表示该 Agent 使用该 Skill 的指定版本；不表示 Skill 要先独立运行。</p>
            </template>
            <template v-else-if="selectedEdge.contract">
              <div class="chip-row">
                <span class="wf-chip" :class="contractChipClass">
                  {{ contractText }}
                </span>
              </div>
              <div class="sec-label">上游产物</div>
              <ul class="artifact-list">
                <li v-for="(a, i) in selectedEdge.contract.upstreamArtifacts" :key="i">{{ a }}</li>
              </ul>
              <div class="sec-label">下游读取</div>
              <ul class="artifact-list">
                <li v-for="(a, i) in selectedEdge.contract.downstreamReads" :key="i">{{ a }}</li>
              </ul>
              <div class="sec-label">依据</div>
              <p class="evidence mono-num">{{ selectedEdge.contract.evidence }}</p>
            </template>

            <button class="btn btn-outline btn-sm danger-link" @click="removeEdge(selectedEdge!.id)">删除连线</button>
          </div>
        </template>
      </aside>

      <!-- ============ 右：搭建助手（对话框）============ -->
      <WorkflowAgentPanel v-if="assistantOpen" :draft="draft" :controller="wfController" :backend-online="backendOnline" :producer-model="producerModel" />

      <!-- ============ Phase 47 C批：从模板新建（弹层）============ -->
      <div v-if="templatesOpen" class="tpl-overlay" @click.self="templatesOpen = false">
        <div class="tpl-modal">
          <div class="tpl-head">
            <span class="tpl-title">从模板新建工作流</span>
            <button class="btn btn-quiet btn-sm" @click="templatesOpen = false">关闭</button>
          </div>
          <p class="sem-note tiny">
            复制模板产生<b>新工作流身份</b>（保留模板版本来源）；创建后可改目标、节点顺序、
            Skill 绑定与审核次数再保存。模板不触发任何生产任务，只有「运行」派发。
          </p>
          <div class="tpl-list">
            <div v-for="t in templates" :key="t.id" class="tpl-item" :class="{ active: tplActive?.id === t.id }" @click="tplActive = t">
              <div class="tpl-name">{{ t.name }}</div>
              <div class="tpl-desc tiny">{{ t.description }}</div>
              <div class="tpl-io tiny muted">需要：{{ (t.requiredInputs || []).join('、') || '—' }}</div>
            </div>
          </div>
          <div v-if="tplActive" class="tpl-params">
            <div class="sec-label">参数（{{ tplActive.id }}）</div>
            <div v-for="p in tplActive.params || []" :key="p.key" class="kv">
              <span class="k">{{ p.label }}</span>
              <input v-model="tplParamDraft[p.key]" class="input tpl-input" :placeholder="p.required ? '必填' : '可选'" />
            </div>
            <div class="kv">
              <span class="k">工作流标识</span>
              <input v-model="tplWorkflowId" class="input tpl-input mono-num" placeholder="wf_自定义标识" />
            </div>
            <button class="btn btn-primary btn-sm tpl-create" :disabled="tplCreating" @click="createFromTemplate">
              {{ tplCreating ? '创建中…' : '创建并载入画布' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, provide, reactive, ref } from 'vue';
import { VueFlow, useVueFlow, MarkerType } from '@vue-flow/core';
import type { Connection, EdgeChange, NodeChange } from '@vue-flow/core';
// vue-flow 基础布局样式（.vue-flow__node 绝对定位 / handle 定位 / viewport 层级）
import '@vue-flow/core/dist/style.css';
import { Background } from '@vue-flow/background';
import { createDiscreteApi } from 'naive-ui';
import { discreteConfigProviderProps } from '@/styles/naiveTheme';
import { apiClient } from '@/api';

import { WORKFLOW_AGENTS, WORKFLOW_SKILLS, getAgent, getSkill } from './catalog';
import type { AgentCatalogEntry, SkillCatalogEntry } from './catalog';
import { executionContract } from './contracts';
import { validateDraft, connectionRule } from './validation';
import { loadDraft, saveDraft, newEmptyDraft } from './storage';
import { buildSampleDraft } from './sample';
import {
  makeNode, genId,
} from './types';
import type { ValidationIssue, WorkflowDraft, WorkflowEdge, WorkflowNode, WorkflowNodeType } from './types';
import AgentNode from './nodes/AgentNode.vue';
import SkillNode from './nodes/SkillNode.vue';
import ReviewNode from './nodes/ReviewNode.vue';
import BoundaryNode from './nodes/BoundaryNode.vue';
import WfEdge from './edges/WfEdge.vue';
import WorkflowAgentPanel from './WorkflowAgentPanel.vue';
import WorkflowRunPanel from './WorkflowRunPanel.vue';
import { shortAgentName, shortSkillName } from './assistant';
import type { WorkflowAssistantController, ApplyResult } from './assistant';
import type { WorkflowRun, WorkflowReviewRequest, ValidationIssue2 } from '@/api';

const { message, dialog } = createDiscreteApi(['message', 'dialog'], {
  configProviderProps: discreteConfigProviderProps,
});

const props = defineProps<{ projectName: string }>();

// 显式 store id：<VueFlow> 子组件按 id 从 storage 认领同一个实例。
// 隐式 adopt（provide/inject）在部分加载时序下会让父级拿到永远未初始化的孤儿实例
// ——表现为 zoomIn/setTransform/screenToFlowCoordinate 全部静默无效（真机复现过）。
const WF_FLOW_ID = 'wf-studio';
const {
  zoomIn, zoomOut, setTransform, screenToFlowCoordinate, vueFlowRef, viewport: vp, dimensions,
} = useVueFlow(WF_FLOW_ID);

// 自研「适应画布」：vue-flow 内置 fitView 在受控节点场景下会因 store 尺寸状态
// 静默失败，这里直接用 DOM 实测包围盒 + setTransform，结果确定可复现。
function fitToContent(duration = 220) {
  const el = vueFlowRef.value as HTMLElement | null;
  if (!el) return;
  const cont = el.getBoundingClientRect();
  const z = vp.value?.zoom ?? 1;
  const vx = vp.value?.x ?? 0;
  const vy = vp.value?.y ?? 0;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  let any = false;
  el.querySelectorAll<HTMLElement>('.vue-flow__node').forEach((n) => {
    const r = n.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const fx = (r.left - cont.left - vx) / z;
    const fy = (r.top - cont.top - vy) / z;
    minX = Math.min(minX, fx);
    minY = Math.min(minY, fy);
    maxX = Math.max(maxX, fx + r.width / z);
    maxY = Math.max(maxY, fy + r.height / z);
    any = true;
  });
  if (!any) return;
  const pad = 56;
  const bw = Math.max(1, maxX - minX);
  const bh = Math.max(1, maxY - minY);
  const zoom = Math.max(0.25, Math.min(1.5, Math.min((cont.width - pad * 2) / bw, (cont.height - pad * 2) / bh)));
  const tx = cont.width / 2 - ((minX + maxX) / 2) * zoom;
  const ty = cont.height / 2 - ((minY + maxY) / 2) * zoom;
  setTransform({ x: tx, y: ty, zoom });  // duration>0 的 d3 过渡在部分场景静默失效，用立即变换
}

/** 把画布视图居中到某个流程坐标点（定位校验问题用） */
function centerOnFlowPoint(x: number, y: number, zoom?: number) {
  const el = vueFlowRef.value as HTMLElement | null;
  if (!el) return;
  const cont = el.getBoundingClientRect();
  const z = zoom ?? (vp.value?.zoom ?? 1);
  setTransform({ x: cont.width / 2 - x * z, y: cont.height / 2 - y * z, zoom: z });
}

// ============ 草稿状态 ============
const draft = ref<WorkflowDraft>(newEmptyDraft(props.projectName, '未命名工作流'));
const dirty = ref(false);
const savedAt = ref<string | null>(null);
const libSearch = ref('');
const canvasWrap = ref<HTMLElement | null>(null);

const selectedNodeId = ref<string | null>(null);
const selectedEdgeId = ref<string | null>(null);

const backendOnline = ref<boolean | null>(null);
const disabledAgents = ref<Set<string>>(new Set());
/** Producer 当前解析到的文本模型（透明度展示用，来自既有 listAgents 数据） */
const producerModel = ref<string | null>(null);

// ============ Phase 46：后端保存 / 运行状态 ============
/** 服务端定义 revision（保存与启动的冲突保护凭据）；null=后端尚无该定义 */
const serverRevision = ref<number | null>(null);
/** 后端保存状态：idle=本地改动未同步 | saved=后端已保存 | conflict=revision 冲突 | error */
const backendSaveState = ref<'idle' | 'saved' | 'conflict' | 'error' | 'offline'>('idle');
const conflictServerRevision = ref<number | null>(null);
const starting = ref(false);
const activeRun = ref<WorkflowRun | null>(null);
const runPanelOpen = ref(false);
const runPollTimer = ref<number | null>(null);
const runEventsSeq = ref(0);
/** 后端校验返回的启动阻塞项（node/edge 定位；与前端体检并存，运行前权威） */
const backendBlockers = ref<ValidationIssue2[]>([]);

const validation = computed(() => validateDraft(draft.value));

function markDirty() {
  dirty.value = true;
  if (backendSaveState.value === 'saved') backendSaveState.value = 'idle';
}

// ============ 生命周期 ============
onMounted(() => {
  const restored = loadDraft(props.projectName);
  if (restored) {
    draft.value = restored;
    dirty.value = false;
    savedAt.value = restored.updatedAt ?? null;
    scheduleFitView();
  }
  // 后台页签加载时 ResizeObserver 不投递（无渲染帧），dimensions 停在 0 会让
  // 缩放/适配/落点换算整体静默失效——挂载后分几档兜底实测视口尺寸。
  // （vue-flow 的 dimensions 是 ref；这里只在该写的时候写，不与 ResizeObserver 抢。）
  const patchDimensions = () => {
    try {
      const el = vueFlowRef.value as HTMLElement | null;
      if (!el) return;
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0 && !(dimensions.value?.width > 0)) {
        dimensions.value = { width: r.width, height: r.height };
        scheduleFitView(); // 挂载早期的自动适配因 dimensions=0 而空转，这里补一次
      }
    } catch {
      /* 视口未就绪时静默，下一档重试 */
    }
  };
  [600, 1500, 3000, 6000].forEach((ms) => window.setTimeout(patchDimensions, ms));
  // 后台页签被 Chrome 强节流时上面的定时器可能迟迟不跑：用户任何一次真实
  // 交互（按下/滚轮/拖入）必然发生在渲染帧里，当场补测一次，确定性兜底。
  patchDimensions();
  nextTick(() => {
    const wrap = canvasWrap.value;
    if (!wrap) return;
    for (const evt of ['pointerdown', 'wheel', 'dragenter'] as const) {
      wrap.addEventListener(evt, patchDimensions, { capture: true, passive: true });
    }
  });
  // 后端在线核对：拿到 Agent 停用状态（可选能力，失败不影响草稿编辑）
  apiClient
    .listAgents()
    .then((list) => {
      backendOnline.value = true;
      disabledAgents.value = new Set(list.filter((a) => !a.enabled).map((a) => a.id));
      const producer = list.find((a) => a.id === 'producer');
      producerModel.value = producer?.model_profile?.reasoning_model ?? null;
      // Phase 46：加载服务端定义（revision 冲突保护基线）。
      // 本地已有草稿时保持本地为工作副本（旧草稿只在用户主动保存/运行时导入，
      // 不被后端静默覆盖）；仅当本地为空时采用服务端版本。
      apiClient
        .getWorkflowDefinition(props.projectName, draft.value.workflowId)
        .then((doc) => {
          serverRevision.value = doc.revision;
          backendSaveState.value = dirty.value ? 'idle' : 'saved';
          if (!draft.value.nodes.length && (doc.nodes?.length || doc.edges?.length)) {
            draft.value = {
              ...newEmptyDraft(props.projectName, doc.name || '未命名工作流'),
              createdAt: doc.createdAt ?? new Date().toISOString(),
              nodes: doc.nodes || [],
              edges: doc.edges || [],
            };
            dirty.value = false;
            savedAt.value = doc.updatedAt ?? null;
            scheduleFitView();
          }
        })
        .catch(() => {
          /* 服务端无定义（404）或失败：保持本地草稿与 idle 状态 */
        });
      // 恢复观察：找本项目非终态 run（刷新后从后端找回，不依赖 localStorage）
      apiClient
        .listWorkflowRuns(props.projectName)
        .then(({ runs }) => {
          const active = (runs || []).find(
            (r: any) => r.status && !['failed', 'stopped', 'rejected', 'completed'].includes(r.status)
          );
          if (active?.runId) {
            refreshRun(active.runId);
            runPanelOpen.value = true;
          }
        })
        .catch(() => {});
    })
    .catch(() => {
      backendOnline.value = false;
      backendSaveState.value = 'offline';
    });

  window.addEventListener('beforeunload', onBeforeUnload);
});
onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', onBeforeUnload);
  stopPolling();   // 关页不取消任务（后端继续按真实状态运行）；仅停止前端观察
});
function onBeforeUnload(e: BeforeUnloadEvent) {
  if (dirty.value) {
    e.preventDefault();
    e.returnValue = '';
  }
}

// ============ 画布映射（draft → vue-flow）============
function vueType(t: WorkflowNodeType): string {
  switch (t) {
    case 'agent': return 'wfAgent';
    case 'skill': return 'wfSkill';
    case 'human_review': return 'wfReview';
    default: return 'wfBoundary';
  }
}

const issuesByNode = computed(() => {
  const m = new Map<string, ValidationIssue[]>();
  for (const iss of validation.value.issues) {
    if (!iss.nodeId) continue;
    if (!m.has(iss.nodeId)) m.set(iss.nodeId, []);
    m.get(iss.nodeId)!.push(iss);
  }
  return m;
});
const issuesByEdge = computed(() => {
  const m = new Map<string, ValidationIssue[]>();
  for (const iss of validation.value.issues) {
    if (!iss.edgeId) continue;
    if (!m.has(iss.edgeId)) m.set(iss.edgeId, []);
    m.get(iss.edgeId)!.push(iss);
  }
  return m;
});

function boundSkillCountOf(nodeId: string): number {
  return draft.value.edges.filter((e) => e.type === 'skill_binding' && e.target === nodeId).length;
}
function boundTargetsOf(nodeId: string): string[] {
  return draft.value.edges
    .filter((e) => e.type === 'skill_binding' && e.source === nodeId)
    .map((e) => {
      const t = draft.value.nodes.find((n) => n.id === e.target);
      return (t && getAgent(t.refId)?.name) || t?.refId || '未知';
    });
}
function upstreamAgentNameOf(nodeId: string): string | undefined {
  const e = draft.value.edges.find((x) => x.type === 'execution' && x.target === nodeId);
  if (!e) return undefined;
  const src = draft.value.nodes.find((n) => n.id === e.source);
  if (!src) return undefined;
  return src.label || (src.type === 'agent' ? getAgent(src.refId)?.name : undefined);
}
function isAgentDisabled(refId: string | null): boolean {
  return !!refId && disabledAgents.value.has(refId);
}

const flowNodes = computed(() =>
  draft.value.nodes.map((n) => {
    const issues = issuesByNode.value.get(n.id) ?? [];
    let data: Record<string, unknown>;
    if (n.type === 'agent') {
      data = {
        node: n,
        hasError: issues.some((i) => i.level === 'error'),
        errorCount: issues.length,
        errorTitles: issues.map((i) => i.message),
        unavailable: isAgentDisabled(n.refId),
        boundSkillCount: boundSkillCountOf(n.id),
      };
    } else if (n.type === 'skill') {
      data = {
        node: n,
        hasError: issues.some((i) => i.level === 'error'),
        errorCount: issues.length,
        errorTitles: issues.map((i) => i.message),
        boundTargets: boundTargetsOf(n.id),
      };
    } else if (n.type === 'human_review') {
      const reviewIdx = draft.value.nodes
        .filter((x) => x.type === 'human_review')
        .findIndex((x) => x.id === n.id);
      data = {
        node: n,
        hasError: issues.some((i) => i.level === 'error'),
        errorCount: issues.length,
        errorTitles: issues.map((i) => i.message),
        reviewSeq: reviewIdx + 1,
        upstreamAgentName: upstreamAgentNameOf(n.id),
      };
    } else {
      data = { kind: n.type === 'boundary_start' ? 'start' : 'end' };
    }
    return { id: n.id, type: vueType(n.type), position: { ...n.position }, data };
  })
);

const flowEdges = computed(() =>
  draft.value.edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    sourceHandle: e.sourcePort,
    targetHandle: e.targetPort,
    type: 'wfEdge',
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: e.type === 'skill_binding' ? '#60a5fa' : '#7a7a7a',
      width: 16,
      height: 16,
    },
    data: {
      edgeType: e.type,
      contractStatus: e.contract?.status,
      skillVersion: e.skillVersion ?? null,
    },
  }))
);

function onNodesChange(changes: NodeChange[]) {
  let moved = false;
  for (const c of changes) {
    if (c.type === 'position' && c.position) {
      const n = draft.value.nodes.find((x) => x.id === c.id);
      if (n && (n.position.x !== c.position.x || n.position.y !== c.position.y)) {
        n.position = { ...c.position };
        moved = true;
      }
    }
  }
  if (moved) markDirty();
}

// ============ 连线 ============
function onConnect(conn: Connection) {
  const check = connectionRule(
    draft.value,
    conn.source,
    conn.target,
    conn.sourceHandle ?? 'out',
    conn.targetHandle ?? 'in'
  );
  if (!check.ok) {
    message.error(check.reason ?? '不允许的连接', { duration: 4200 });
    return;
  }
  const srcNode = draft.value.nodes.find((n) => n.id === conn.source);
  const dstNode = draft.value.nodes.find((n) => n.id === conn.target);
  if (!srcNode || !dstNode) return;
  const isBinding = srcNode.type === 'skill';
  const edge: WorkflowEdge = {
    id: genId('e'),
    type: isBinding ? 'skill_binding' : 'execution',
    source: conn.source,
    target: conn.target,
    sourcePort: (conn.sourceHandle ?? 'out') as WorkflowEdge['sourcePort'],
    targetPort: (conn.targetHandle ?? 'in') as WorkflowEdge['targetPort'],
  };
  if (isBinding) {
    edge.skillVersion = srcNode.refVersion ?? null;
    edge.contract = null;
  } else {
    edge.contract = executionContract(
      srcNode.type === 'agent' ? srcNode.refId : null,
      dstNode.type === 'agent' ? dstNode.refId : null,
      srcNode.type === 'human_review' || dstNode.type === 'human_review',
      dstNode.type === 'human_review'
    );
  }
  draft.value.edges.push(edge);
  markDirty();
  message.success(
    isBinding
      ? `已绑定：${getSkill(srcNode.refId)?.name ?? ''} → ${getAgent(dstNode.refId)?.name ?? ''}`
      : '已连接执行依赖',
    { duration: 1800 }
  );
}

// ============ 画布交互 ============
function onNodeClick(e: { node: { id: string } }) {
  selectedNodeId.value = e.node.id;
  selectedEdgeId.value = null;
}
function onEdgeClick(e: { edge: { id: string } }) {
  selectedEdgeId.value = e.edge.id;
  selectedNodeId.value = null;
}
function clearSelection() {
  selectedNodeId.value = null;
  selectedEdgeId.value = null;
}

// ============ 组件库 → 画布 ============
interface LibItem {
  kind: 'agent' | 'skill' | 'human_review' | 'boundary_start' | 'boundary_end';
  refId?: string;
}

/** 背景点距 20px，落点/点击落位统一吸附，和网格视觉对齐 */
const GRID = 20;
const snapGrid = (v: number) => Math.round(v / GRID) * GRID;
/** 各类型节点的半宽/半高（落点居中到光标） */
const HALF_SIZE: Record<LibItem['kind'], [number, number]> = {
  agent: [116, 48],
  skill: [112, 44],
  human_review: [112, 48],
  boundary_start: [60, 18],
  boundary_end: [60, 18],
};

function libItemLabel(item: LibItem): string {
  if (item.kind === 'agent') return getAgent(item.refId)?.name ?? 'Agent';
  if (item.kind === 'skill') return getSkill(item.refId)?.name ?? 'Skill';
  if (item.kind === 'human_review') return '人工审核';
  return item.kind === 'boundary_start' ? '开始' : '结束';
}

function onLibDragStart(evt: DragEvent, item: LibItem) {
  evt.dataTransfer?.setData('application/x-wf-item', JSON.stringify(item));
  if (!evt.dataTransfer) return;
  evt.dataTransfer.effectAllowed = 'copy';
  // 自定义拖拽幽灵：跟随光标的小徽章（默认是整张卡的半透明镜像，大且糊）
  const ghost = document.createElement('div');
  ghost.textContent = libItemLabel(item);
  Object.assign(ghost.style, {
    position: 'fixed',
    top: '-200px',
    left: '-200px',
    zIndex: '9999',
    padding: '5px 12px',
    borderRadius: '999px',
    border: '1px solid rgba(96, 165, 250, 0.55)',
    background: 'var(--bg-surface, #1a1a1a)',
    color: 'var(--text-primary, #e5e5e5)',
    fontSize: '12px',
    fontWeight: '500',
    whiteSpace: 'nowrap',
    pointerEvents: 'none',
    boxShadow: '0 6px 18px rgba(0, 0, 0, 0.45)',
  } as CSSStyleDeclaration);
  document.body.appendChild(ghost);
  evt.dataTransfer.setDragImage(ghost, 18, 16);
  window.setTimeout(() => ghost.remove(), 0);
}

let dragEnterCount = 0;
const draggingOver = ref(false);
function onDragEnterWrap() {
  dragEnterCount++;
  draggingOver.value = true;
}
function onDragLeaveWrap() {
  dragEnterCount = Math.max(0, dragEnterCount - 1);
  if (!dragEnterCount) draggingOver.value = false;
}

function onDrop(evt: DragEvent) {
  dragEnterCount = 0;
  draggingOver.value = false;
  const raw = evt.dataTransfer?.getData('application/x-wf-item');
  if (!raw) return;
  try {
    const item = JSON.parse(raw) as LibItem;
    const pos = screenToFlowCoordinate({ x: evt.clientX, y: evt.clientY });
    const [hw, hh] = HALF_SIZE[item.kind] ?? [110, 40];
    addNode(item, { x: snapGrid(pos.x - hw), y: snapGrid(pos.y - hh) });
  } catch {
    /* 忽略非法拖拽数据 */
  }
}

function addNodeFromLib(item: LibItem) {
  // 点击添加：落到当前视口中心（吸附网格），连续点击按序错位避免叠放
  const rect = (vueFlowRef.value as HTMLElement | null)?.getBoundingClientRect();
  let pos = { x: 160, y: 160 };
  if (rect) {
    const c = screenToFlowCoordinate({
      x: rect.left + rect.width * 0.5,
      y: rect.top + rect.height * 0.5,
    });
    const jitter = draft.value.nodes.length % 5;
    const [hw, hh] = HALF_SIZE[item.kind] ?? [110, 40];
    pos = { x: snapGrid(c.x - hw + jitter * 40), y: snapGrid(c.y - hh + jitter * 32) };
  }
  addNode(item, pos);
}

function addNode(item: LibItem, position: { x: number; y: number }) {
  let node: WorkflowNode | null = null;
  if (item.kind === 'agent') {
    const a = getAgent(item.refId);
    if (!a) {
      message.error('未找到该 Agent');
      return;
    }
    node = makeNode({ type: 'agent', refId: a.id, position, config: { selectedTools: [] } });
  } else if (item.kind === 'skill') {
    const s = getSkill(item.refId);
    if (!s) {
      message.error('未找到该 Skill');
      return;
    }
    node = makeNode({ type: 'skill', refId: s.id, refVersion: s.version, position });
  } else if (item.kind === 'human_review') {
    node = makeNode({ type: 'human_review', position, config: {} });
  } else if (item.kind === 'boundary_start') {
    if (draft.value.nodes.some((n) => n.type === 'boundary_start')) {
      message.warning('开始（边界）最多一个');
      return;
    }
    node = makeNode({ type: 'boundary_start', position });
  } else if (item.kind === 'boundary_end') {
    if (draft.value.nodes.some((n) => n.type === 'boundary_end')) {
      message.warning('结束（边界）最多一个');
      return;
    }
    node = makeNode({ type: 'boundary_end', position });
  }
  if (!node) return;
  draft.value.nodes.push(node);
  markDirty();
  selectedNodeId.value = node.id;
  selectedEdgeId.value = null;
}

function removeNode(id: string) {
  draft.value.nodes = draft.value.nodes.filter((n) => n.id !== id);
  draft.value.edges = draft.value.edges.filter((e) => e.source !== id && e.target !== id);
  if (selectedNodeId.value === id) clearSelection();
  markDirty();
}
function removeEdge(id: string) {
  draft.value.edges = draft.value.edges.filter((e) => e.id !== id);
  if (selectedEdgeId.value === id) clearSelection();
  markDirty();
  message.info('连线已删除，依赖提示已更新', { duration: 1600 });
}
/** 连线双击 = 快捷删除（与面板「删除连线」等效） */
function onEdgeDoubleClick(e: { edge: { id: string } }) {
  if (e?.edge?.id) removeEdge(e.edge.id);
}

// ============ 右侧面板 ============
const panelMode = computed<'run' | 'overview' | 'agent' | 'skill' | 'review' | 'boundary' | 'edge'>(() => {
  if (runPanelOpen.value) return 'run';   // 运行视图优先（草稿与运行分离：编辑面板暂被遮盖，可再点关闭）
  if (selectedEdgeId.value) return 'edge';
  const n = draft.value.nodes.find((x) => x.id === selectedNodeId.value);
  if (!n) return 'overview';
  if (n.type === 'agent') return 'agent';
  if (n.type === 'skill') return 'skill';
  if (n.type === 'human_review') return 'review';
  return 'boundary';
});

const selectedAgentNode = computed(() =>
  panelMode.value === 'agent'
    ? (draft.value.nodes.find((n) => n.id === selectedNodeId.value) ?? null)
    : null
);
const selectedSkillNode = computed(() =>
  panelMode.value === 'skill'
    ? (draft.value.nodes.find((n) => n.id === selectedNodeId.value) ?? null)
    : null
);
const selectedReviewNode = computed(() =>
  panelMode.value === 'review'
    ? (draft.value.nodes.find((n) => n.id === selectedNodeId.value) ?? null)
    : null
);
const selectedBoundaryNode = computed(() =>
  panelMode.value === 'boundary'
    ? (draft.value.nodes.find((n) => n.id === selectedNodeId.value) ?? null)
    : null
);
const selectedEdge = computed(
  () => draft.value.edges.find((e) => e.id === selectedEdgeId.value) ?? null
);

const selAgent = computed(() => getAgent(selectedAgentNode.value?.refId));
const selSkill = computed(() => getSkill(selectedSkillNode.value?.refId));

// 工具语义（2026-10-03 用户定义）：核心工具=注册表自带必有（不可勾选）；
// 额外工具=以后系统扩展后在草稿里勾选启用（config.selectedTools，当前恒空）。

// —— 人工审核字段双向 ——
const reviewLabel = computed({
  get: () => selectedReviewNode.value?.label ?? '',
  set: (v: string) => {
    const n = selectedReviewNode.value;
    if (n) {
      n.label = v || null;
      markDirty();
    }
  },
});
const reviewInstructions = computed({
  get: () => selectedReviewNode.value?.config.instructions ?? '',
  set: (v: string) => {
    const n = selectedReviewNode.value;
    if (n) {
      n.config.instructions = v;
      markDirty();
    }
  },
});
const reviewReviewer = computed({
  get: () => selectedReviewNode.value?.config.reviewer ?? '',
  set: (v: string) => {
    const n = selectedReviewNode.value;
    if (n) {
      n.config.reviewer = v;
      markDirty();
    }
  },
});
// —— Phase 47 §4.6：审核选用目标（selection）——
const reviewSelectionKind = computed(() => selectedReviewNode.value?.config.selection?.kind ?? '');
const reviewSelectionEntity = computed({
  get: () => selectedReviewNode.value?.config.selection?.entityId ?? '',
  set: (v: string) => {
    const n = selectedReviewNode.value;
    if (n && n.config.selection) {
      n.config.selection.entityId = v;
      markDirty();
    }
  },
});
function onReviewSelectionKind(e: Event) {
  const n = selectedReviewNode.value;
  if (!n) return;
  const kind = (e.target as HTMLSelectElement).value;
  if (!kind) delete n.config.selection;
  else n.config.selection = { kind, entityId: n.config.selection?.entityId ?? '' };
  markDirty();
}

/** 审核对象来源候选：所有执行入线的 source 节点 */
const reviewUpstreamOptions = computed(() => {
  const n = selectedReviewNode.value;
  if (!n) return [];
  return draft.value.edges
    .filter((e) => e.type === 'execution' && e.target === n.id)
    .map((e) => {
      const src = draft.value.nodes.find((x) => x.id === e.source);
      if (!src) return null;
      const title =
        src.label || (src.type === 'agent' ? getAgent(src.refId)?.name : undefined) ||
        (src.type === 'human_review' ? '上一道审核' : '开始');
      return { value: src.id, label: title };
    })
    .filter((x): x is { value: string; label: string } => !!x);
});

const reviewTargetSource = computed({
  get: () => {
    const n = selectedReviewNode.value;
    if (!n) return '';
    const src = n.config.targetSourceNodeId;
    if (src && reviewUpstreamOptions.value.some((o) => o.value === src)) return src;
    return reviewUpstreamOptions.value.length ? '__custom__' : '__custom__';
  },
  set: (v: string) => {
    const n = selectedReviewNode.value;
    if (!n) return;
    if (v === '__custom__') {
      n.config.targetSourceNodeId = null;
    } else {
      n.config.targetSourceNodeId = v;
      const src = draft.value.nodes.find((x) => x.id === v);
      const a = src && src.type === 'agent' ? getAgent(src.refId) : undefined;
      n.config.targetArtifact = a?.artifacts[0] ?? '上一道审核通过后的后续产物';
    }
    markDirty();
  },
});

const reviewArtifactOptions = computed(() => {
  const n = selectedReviewNode.value;
  if (!n) return [];
  const srcId = n.config.targetSourceNodeId;
  if (!srcId) return [];
  const src = draft.value.nodes.find((x) => x.id === srcId);
  if (!src) return [];
  if (src.type === 'agent') return getAgent(src.refId)?.artifacts ?? [];
  if (src.type === 'human_review') return ['上一道审核通过后的后续产物'];
  return [];
});

const reviewTargetArtifact = computed({
  get: () => selectedReviewNode.value?.config.targetArtifact ?? '',
  set: (v: string) => {
    const n = selectedReviewNode.value;
    if (n) {
      n.config.targetArtifact = v;
      markDirty();
    }
  },
});

// —— Agent 节点任务目标（运行必需字段；§4.1）——
function onObjectiveInput(e: Event) {
  const n = selectedAgentNode.value;
  if (!n) return;
  n.config.objective = (e.target as HTMLTextAreaElement).value;
  markDirty();
}

// —— Phase 47 B批：产物要求（completion 契约）编辑 ——
const COMPLETION_KINDS = [
  'scene', 'story', 'character', 'look', 'clothing', 'prop', 'composition',
  'reference_asset', 'location', 'location_view', 'scene_instance',
  'scene_instance_view', 'shot', 'video_version', 'sequence',
];
const COMPLETION_FLAGS = [
  { key: 'withImage', label: '需有图/文件' },
  { key: 'withApprovedRef', label: '需已批准' },
  { key: 'playable', label: '需可播放' },
  { key: 'withCompletedRender', label: '需完成渲染' },
];

function ensureCompletion(n: WorkflowNode): Record<string, any> {
  if (!n.config.completion || typeof n.config.completion !== 'object') {
    n.config.completion = { strategy: 'require_new', outputs: [] };
  }
  return n.config.completion;
}
function onStrategyChange(e: Event) {
  const n = selectedAgentNode.value;
  if (!n) return;
  ensureCompletion(n).strategy = (e.target as HTMLSelectElement).value;
  markDirty();
}
function addOutputSpec() {
  const n = selectedAgentNode.value;
  if (!n) return;
  const c = ensureCompletion(n);
  c.outputs = c.outputs || [];
  c.outputs.push({ kind: 'scene', minCount: 1 });
  markDirty();
}
function removeOutputSpec(i: number) {
  const n = selectedAgentNode.value;
  if (!n) return;
  const c = n.config.completion;
  if (!c) return;
  (c.outputs || []).splice(i, 1);
  markDirty();
}
function onSpecKind(i: number, e: Event) {
  const outs = selectedAgentNode.value?.config.completion?.outputs;
  if (outs && outs[i]) { outs[i].kind = (e.target as HTMLSelectElement).value; markDirty(); }
}
function onSpecCount(i: number, e: Event) {
  const outs = selectedAgentNode.value?.config.completion?.outputs;
  if (outs && outs[i]) {
    const v = parseInt((e.target as HTMLInputElement).value, 10);
    outs[i].minCount = Number.isFinite(v) && v >= 1 ? v : 1;
    markDirty();
  }
}
function onSpecFlag(i: number, key: string, e: Event) {
  const outs = selectedAgentNode.value?.config.completion?.outputs;
  if (outs && outs[i]) {
    const checked = (e.target as HTMLInputElement).checked;
    const spec = outs[i] as Record<string, any>;
    if (checked) spec[key] = true;
    else delete spec[key];
    markDirty();
  }
}
function specFlag(spec: Record<string, any>, key: string): boolean {
  return !!spec[key];
}
function onSpecId(i: number, e: Event) {
  const outs = selectedAgentNode.value?.config.completion?.outputs;
  if (outs && outs[i]) {
    const v = (e.target as HTMLInputElement).value.trim();
    if (v) outs[i].id = v;
    else delete outs[i].id;
    markDirty();
  }
}

// —— 连线面板 ——
function nodeTitle(id: string): string {
  const n = draft.value.nodes.find((x) => x.id === id);
  if (!n) return '未知节点';
  if (n.label) return n.label;
  if (n.type === 'agent') return getAgent(n.refId)?.name ?? String(n.refId);
  if (n.type === 'skill') return getSkill(n.refId)?.name ?? String(n.refId);
  if (n.type === 'human_review') return '人工审核';
  if (n.type === 'boundary_start') return '开始';
  return '结束';
}
const edgeSourceTitle = computed(() =>
  selectedEdge.value ? nodeTitle(selectedEdge.value.source) : ''
);
const edgeTargetTitle = computed(() =>
  selectedEdge.value ? nodeTitle(selectedEdge.value.target) : ''
);
function portLabel(p: WorkflowEdge['sourcePort']): string {
  return p === 'bind' ? 'bind（Skill 绑定）' : p === 'in' ? 'in（执行输入）' : 'out（执行输出）';
}
const contractChipClass = computed(() => {
  const s = selectedEdge.value?.contract?.status;
  return s === 'verified' ? 'chip-ok' : s === 'pending_backend' ? 'chip-warn' : '';
});
const contractText = computed(() => {
  const s = selectedEdge.value?.contract?.status;
  return s === 'verified'
    ? '✓ 已核实产物契约'
    : s === 'pending_backend'
      ? '待后端定义（未找到逐对产物契约）'
      : '人工审核语义（首版）';
});

// —— 概览 ——
function countBy(t: WorkflowNodeType): number {
  return draft.value.nodes.filter((n) => n.type === t).length;
}
const execCount = computed(() => draft.value.edges.filter((e) => e.type === 'execution').length);
const bindCount = computed(() => draft.value.edges.filter((e) => e.type === 'skill_binding').length);

function jumpToIssue(iss: ValidationIssue) {
  if (iss.nodeId) {
    selectedNodeId.value = iss.nodeId;
    selectedEdgeId.value = null;
    const n = draft.value.nodes.find((x) => x.id === iss.nodeId);
    if (n) centerOnFlowPoint(n.position.x + 116, n.position.y + 70, 0.9);
  } else if (iss.edgeId) {
    selectedEdgeId.value = iss.edgeId;
    selectedNodeId.value = null;
  }
}

// ============ 组件库过滤 ============
const filteredAgents = computed<AgentCatalogEntry[]>(() => {
  const q = libSearch.value.trim().toLowerCase();
  if (!q) return WORKFLOW_AGENTS;
  return WORKFLOW_AGENTS.filter(
    (a) =>
      a.name.toLowerCase().includes(q) ||
      a.id.includes(q) ||
      a.role.toLowerCase().includes(q) ||
      a.description.toLowerCase().includes(q)
  );
});
const filteredSkills = computed<SkillCatalogEntry[]>(() => {
  const q = libSearch.value.trim().toLowerCase();
  if (!q) return WORKFLOW_SKILLS;
  return WORKFLOW_SKILLS.filter(
    (s) =>
      s.name.toLowerCase().includes(q) ||
      s.id.includes(q) ||
      s.description.toLowerCase().includes(q)
  );
});

// ============ 保存 / 导出 / 检查 / 示例 ============
const saveText = computed(() => {
  if (backendSaveState.value === 'conflict') return '保存冲突';
  if (backendSaveState.value === 'offline') return dirty.value ? '未保存（后端未连接）' : '草稿（后端未连接）';
  if (dirty.value) return backendSaveState.value === 'saved' ? '本地有未同步更改' : '未保存更改';
  return savedAt.value
    ? `已保存${backendSaveState.value === 'saved' ? '（后端）' : ''} · ${formatTime(savedAt.value)}`
    : '草稿（未保存）';
});
const saveTone = computed(() => {
  if (backendSaveState.value === 'conflict' || backendSaveState.value === 'error') return 'chip-warn';
  if (dirty.value) return 'chip-warn';
  return 'chip-ok';
});
function formatTime(iso: string): string {
  const d = new Date(iso);
  const p = (x: number) => String(x).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}`;
}
const checkLabel = computed(() => {
  const v = validation.value;
  if (v.errors) return ` · ${v.errors} 错${v.warnings ? ` ${v.warnings} 警` : ''}`;
  if (v.warnings) return ` · ${v.warnings} 警`;
  return ' · 通过';
});

function runCheckManually() {
  const r = validateDraft(draft.value);
  if (!r.issues.length) {
    message.success('结构检查通过：无错误、无警告', { duration: 2400 });
  } else {
    clearSelection();
    message.warning(
      `结构检查：${r.errors} 个错误、${r.warnings} 个警告（右侧面板可定位）`,
      { duration: 3200 }
    );
  }
}

function saveNow(): Promise<boolean> {
  // 保存：后端为权威（带 revision 冲突保护），localStorage 恒为离线副本。
  // 保存前运行前端结构检查（错误不阻止保存草稿，但明确报告）。
  const r = validateDraft(draft.value);
  saveDraft(draft.value);
  const payload = { ...draft.value, updatedAt: new Date().toISOString() };
  if (backendOnline.value !== true) {
    dirty.value = false;
    savedAt.value = payload.updatedAt;
    backendSaveState.value = 'offline';
    message.warning('后端未连接：草稿仅保存到浏览器（不会同步到服务端）', { duration: 3200 });
    return Promise.resolve(false);
  }
  return apiClient
    .saveWorkflowDefinition(props.projectName, draft.value.workflowId, payload, serverRevision.value)
    .then((res) => {
      serverRevision.value = res.revision;
      savedAt.value = res.updatedAt;
      dirty.value = false;
      backendSaveState.value = 'saved';
      if (res.validation?.errors?.length) {
        message.warning(
          `已保存到后端，但存在 ${res.validation.errors.length} 个结构错误（启动会被阻止；点「结构检查」定位）`,
          { duration: 4200 }
        );
      } else {
        message.success('已保存到后端（含离线副本）', { duration: 2200 });
      }
      return true;
    })
    .catch((err: any) => {
      const detail = String(err?.message || err || '');
      if (detail.includes('revision_conflict')) {
        backendSaveState.value = 'conflict';
        conflictServerRevision.value = null;
        apiClient
          .getWorkflowDefinition(props.projectName, draft.value.workflowId)
          .then((doc) => { conflictServerRevision.value = doc.revision; })
          .catch(() => {});
        dialog.warning({
          title: '保存冲突',
          content: '服务端已有更新的版本（另一处已保存）。你的本地修改不会覆盖它。可选择：'
            + '「加载服务端版本」（丢弃本地未同步修改）或「保持本地」（稍后自行处理）。',
          positiveText: '加载服务端版本',
          negativeText: '保持本地',
          onPositiveClick: () => {
            apiClient
              .getWorkflowDefinition(props.projectName, draft.value.workflowId)
              .then((doc) => {
                draft.value = {
                  ...newEmptyDraft(props.projectName, doc.name || '未命名工作流'),
                  nodes: doc.nodes || [],
                  edges: doc.edges || [],
                };
                serverRevision.value = doc.revision;
                dirty.value = false;
                savedAt.value = doc.updatedAt ?? null;
                backendSaveState.value = 'saved';
                scheduleFitView();
                message.success('已加载服务端版本', { duration: 2200 });
              })
              .catch(() => message.error('加载服务端版本失败'));
          },
        });
      } else {
        backendSaveState.value = 'error';
        message.error(`后端保存失败：${detail.slice(0, 160)}（浏览器副本已保存）`, { duration: 4200 });
      }
      return false;
    });
}

function exportJson() {
  const r = validateDraft(draft.value);
  const payload = {
    ...draft.value,
    updatedAt: new Date().toISOString(),
    validation: r,
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  const safeName = (draft.value.name || 'draft').replace(/[\\/:*?"<>|]/g, '_');
  a.href = url;
  a.download = `workflow-${props.projectName}-${safeName}.json`;
  a.click();
  URL.revokeObjectURL(url);
  if (r.errors) {
    message.warning(`JSON 已导出（含 ${r.errors} 个结构错误的检查结果）`, { duration: 3200 });
  } else {
    message.success('JSON 已导出（含结构检查结果）', { duration: 2400 });
  }
}

// ============ Phase 46：运行（真实后端执行） ============
const runBtnTitle = computed(() => {
  if (backendOnline.value !== true) return '后端未连接，无法运行';
  if (starting.value) return '正在启动…';
  return '保存草稿并启动一条真实生产链（后端顺序调度）';
});
const runBadge = computed(() => {
  const st = activeRun.value?.status;
  if (st === 'waiting_human') return ' · 待审核';
  if (st === 'needs_reconciliation') return ' · 需核对';
  if (st === 'running') return ' · 运行中';
  return '';
});
/** 已知非终态 run 的节点 refId（面板与画布定位用） */
function getRefId(nodeId: string): string | null {
  return activeRun.value?.nodes?.[nodeId]?.refId ?? null;
}
function toggleRunPanel() {
  runPanelOpen.value = !runPanelOpen.value;
}

async function startRun() {
  if (starting.value) return;
  starting.value = true;
  try {
    // 先保存（带 revision 冲突保护）；保存失败/冲突时不启动（run 必须基于已确认定义）
    const saved = await saveNow();
    if (!saved) {
      message.warning('未保存到后端，已取消启动（运行基于服务端已确认的定义）', { duration: 3600 });
      return;
    }
    const idemKey = `run-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    const { run, reused } = await apiClient.startWorkflowRun(
      props.projectName, draft.value.workflowId,
      { idempotencyKey: idemKey, expectedRevision: serverRevision.value, saveIfChanged: false }
    );
    activeRun.value = run;
    runPanelOpen.value = true;
    runEventsSeq.value = 0;
    startPolling();
    message.success(reused ? `已存在相同启动键的 run：${run.runId}（未重复创建）`
                           : `run 已启动：${run.runId}`, { duration: 3200 });
  } catch (err: any) {
    const detail = String(err?.message || err || '');
    if (detail.includes('start_blocked')) {
      message.error(`启动被阻止：${detail.replace('[start_blocked] ', '').slice(0, 300)}`, { duration: 6000 });
    } else if (detail.includes('revision_conflict')) {
      backendSaveState.value = 'conflict';
      message.error('定义版本冲突：请先处理保存冲突再运行', { duration: 4200 });
    } else {
      message.error(`启动失败：${detail.slice(0, 260)}`, { duration: 5200 });
    }
  } finally {
    starting.value = false;
  }
}

function startPolling() {
  stopPolling();
  // 轮询 run 状态（§8.3：无可靠事件流时轮询即可；后台标签页激活时靠 navigate 重拉）
  runPollTimer.value = window.setInterval(refreshActiveRun, 2500);
  refreshActiveRun();
}
function stopPolling() {
  if (runPollTimer.value != null) {
    window.clearInterval(runPollTimer.value);
    runPollTimer.value = null;
  }
}
async function refreshActiveRun() {
  const rid = activeRun.value?.runId;
  if (!rid) return;
  try {
    const run = await apiClient.getWorkflowRun(props.projectName, rid);
    const prev = activeRun.value;
    activeRun.value = run;
    // 终态后降频（保留面板可查，但停止轮询新变化以外的负担）
    if (['failed', 'stopped', 'rejected', 'completed'].includes(run.status)) {
      stopPolling();
    }
    if (prev && run.updatedAt !== prev.updatedAt) {
      // 状态变化可见性：轻提示（不打断）
      if (run.status !== prev.status) {
        if (run.status === 'waiting_human') message.info('流程等待人工审核（右侧面板可决策）', { duration: 3000 });
        else if (run.status === 'completed') message.success('工作流全部节点完成', { duration: 3200 });
        else if (run.status === 'failed') message.error(`流程失败：${run.nodes?.[run.plan?.order?.[run.cursor]]?.error || '见运行面板'}`, { duration: 4200 });
      }
    }
  } catch {
    /* 观察连接问题不打断本地；下一轮重试 */
  }
}
async function refreshRun(runId: string) {
  try {
    activeRun.value = await apiClient.getWorkflowRun(props.projectName, runId);
    runPanelOpen.value = true;
    startPolling();
  } catch {
    message.warning(`恢复观察 run ${runId} 失败`, { duration: 3000 });
  }
}

async function onDecideReview(rv: WorkflowReviewRequest, decision: 'approve' | 'reject', note: string,
                              opts?: { withSelection?: boolean; selectionOverride?: { kind: string; entityId: string; versionId: string } | null }) {
  if (!activeRun.value) return;
  try {
    const out = await apiClient.decideWorkflowReview(
      props.projectName, activeRun.value.runId, rv.requestId, decision, note,
      rv.artifactFingerprint,
      opts?.withSelection ?? false,
      opts?.selectionOverride ?? null);
    if (out?.alreadyDecided) {
      message.warning(`该审核已被处理过（决策：${out.decision === 'approve' ? '通过' : '驳回'}）`, { duration: 3200 });
    } else if (decision === 'approve' && opts?.withSelection) {
      message.success(out?.receipt?.withSelection
        ? '已通过并选用：指针已变更，下一节点开始' : '已通过', { duration: 3200 });
    } else {
      message.success(decision === 'approve' ? '已通过：下一节点开始' : '已驳回：流程停止', { duration: 2800 });
    }
    await refreshActiveRun();
  } catch (err: any) {
    const detail = String(err?.message || err || '');
    if (detail.includes('review_object_changed')) {
      message.error('审核对象已变化（待审产物内容与打开时不一致），请刷新后复核', { duration: 4200 });
    } else if (detail.includes('selection_required')) {
      message.error('本审核要求明确选用：请选择精确版本后点「通过并选用」', { duration: 4200 });
    } else {
      message.error(`审核提交失败：${detail.slice(0, 200)}`, { duration: 4200 });
    }
    await refreshActiveRun();
  }
}

async function onStopRun() {
  if (!activeRun.value) return;
  try {
    await apiClient.stopWorkflowRun(props.projectName, activeRun.value.runId);
    message.success('已请求停止：不再派发下游；在途任务按真实能力收尾', { duration: 3600 });
    await refreshActiveRun();
  } catch (err: any) {
    message.error(`停止失败：${String(err?.message || err).slice(0, 200)}`, { duration: 3600 });
  }
}

async function onContinueRun(nodeId: string | null) {
  if (!activeRun.value) return;
  try {
    const { run } = await apiClient.continueWorkflowRun(
      props.projectName, activeRun.value.runId, nodeId);
    activeRun.value = run;
    startPolling();
    message.success('已显式继续：从当前节点重新派发（新尝试）', { duration: 3000 });
  } catch (err: any) {
    const detail = String(err?.message || err || '');
    if (detail.includes('waiting_human')) {
      message.warning('流程在等待人工审核：请先在运行面板完成审核决策', { duration: 3600 });
    } else {
      message.error(`继续失败：${detail.slice(0, 200)}`, { duration: 3600 });
    }
  }
}

function locateNode(nodeId: string) {
  runPanelOpen.value = false;
  const n = draft.value.nodes.find((x) => x.id === nodeId);
  if (n) {
    selectedNodeId.value = nodeId;
    selectedEdgeId.value = null;
  } else {
    message.info('该节点不在当前草稿中（运行使用的是启动时的冻结定义）', { duration: 3200 });
  }
}

// fitView 需要节点尺寸就绪；节点刚注入时未必完成布局，重试几次
function scheduleFitView() {
  const delays = [80, 300, 700, 1400];
  for (const d of delays) {
    setTimeout(() => {
      if (!draft.value.nodes.length) return;
      fitToContent();
    }, d);
  }
}

function loadSample() {
  if (draft.value.nodes.length && dirty.value) {
    dialog.warning({
      title: '载入示例工作流',
      content: '当前画布有未保存的更改，载入示例将覆盖它们（可先「保存草稿」）。确定继续？',
      positiveText: '覆盖并载入示例',
      negativeText: '取消',
      onPositiveClick: () => applySample(),
    });
  } else {
    applySample();
  }
}
function applySample() {
  draft.value = buildSampleDraft(props.projectName);
  dirty.value = true;
  clearSelection();
  scheduleFitView();
  message.success('示例已载入：全部条目来自真实注册表（Agent×5 · Skill×1 · 人工审核×2）', {
    duration: 3600,
  });
}

function clearCanvas() {
  dialog.warning({
    title: '新建空白工作流',
    content: '将清空当前画布（浏览器草稿需重新保存后才生效）。确定继续？',
    positiveText: '清空',
    negativeText: '取消',
    onPositiveClick: () => {
      draft.value = newEmptyDraft(props.projectName, '未命名工作流');
      dirty.value = true;
      clearSelection();
    },
  });
}

// ============ Phase 47 C批：从模板新建 ============
const templatesOpen = ref(false);
const templates = ref<any[]>([]);
const tplActive = ref<any>(null);
const tplParamDraft = reactive<Record<string, string>>({});
const tplWorkflowId = ref('');
const tplCreating = ref(false);

async function openTemplates() {
  templatesOpen.value = true;
  if (!templates.value.length) {
    try {
      const out = await apiClient.listWorkflowTemplates(props.projectName);
      templates.value = out.templates || [];
    } catch (err: any) {
      message.error(`模板列表加载失败：${String(err?.message || err).slice(0, 160)}`, { duration: 3600 });
    }
  }
}

async function createFromTemplate() {
  if (!tplActive.value || tplCreating.value) return;
  const wid = (tplWorkflowId.value || '').trim() ||
    `wf_${tplActive.value.id.toLowerCase()}_${Date.now().toString(36)}`;
  const params: Record<string, any> = {};
  for (const [k, v] of Object.entries(tplParamDraft)) {
    if (String(v).trim() !== '') params[k] = v;
  }
  tplCreating.value = true;
  try {
    const out = await apiClient.createWorkflowFromTemplate(
      props.projectName, tplActive.value.id, wid, params);
    const doc = out?.definition;
    if (!out?.validation?.ok) {
      message.warning('模板定义已保存（含校验问题，可修正后再运行）', { duration: 3600 });
    }
    if (doc) {
      draft.value = {
        ...newEmptyDraft(props.projectName, doc.name || tplActive.value.name),
        workflowId: wid,
        createdAt: doc.createdAt ?? new Date().toISOString(),
        nodes: doc.nodes || [],
        edges: doc.edges || [],
      };
      serverRevision.value = doc.revision ?? 1;
      dirty.value = false;
      savedAt.value = doc.updatedAt ?? null;
      clearSelection();
      scheduleFitView();
    }
    templatesOpen.value = false;
    message.success(`已从模板 ${tplActive.value.id} 创建新工作流（标识 ${wid}，revision ${out?.revision}）`,
      { duration: 4200 });
  } catch (err: any) {
    message.error(`模板创建失败：${String(err?.message || err).slice(0, 200)}`, { duration: 4200 });
  } finally {
    tplCreating.value = false;
  }
}

const zoomPct = computed(() => Math.round((vp.value?.zoom ?? 1) * 100));

// ============ 搭建助手（右侧对话框）============
const assistantOpen = ref(window.innerWidth >= 1280);

function findAgentNodeByRef(refId: string): WorkflowNode | undefined {
  return draft.value.nodes.find((n) => n.type === 'agent' && n.refId === refId);
}
function findSkillNodeByRef(refId: string): WorkflowNode | undefined {
  return draft.value.nodes.find((n) => n.type === 'skill' && n.refId === refId);
}

/** 沿执行边从 startId 正向收集可抵达的审核（不穿过其它 Agent/边界）。
 *  「A 之后的审核」按图拓扑找，节点被拖到任何 x 位置都不影响定位。 */
function reviewsReachableFrom(startId: string, stopAtId?: string): WorkflowNode[] {
  const byId = new Map(draft.value.nodes.map((n) => [n.id, n]));
  const found: WorkflowNode[] = [];
  const seen = new Set<string>([startId]);
  const queue = [startId];
  while (queue.length) {
    const cur = queue.shift()!;
    for (const e of draft.value.edges) {
      if (e.type !== 'execution' || e.source !== cur || seen.has(e.target)) continue;
      if (stopAtId && e.target === stopAtId) continue;
      const n = byId.get(e.target);
      if (!n) continue;
      if (n.type === 'human_review') { found.push(n); queue.push(n.id); }
      // Agent / 边界是别的站点：不穿过、不收集
    }
  }
  return found;
}

/** 反向：收集所有能沿执行边抵达 targetId 的审核（「B 之前的审核」） */
function reviewsReachingInto(targetId: string): WorkflowNode[] {
  const byId = new Map(draft.value.nodes.map((n) => [n.id, n]));
  const found: WorkflowNode[] = [];
  const seen = new Set<string>([targetId]);
  const queue = [targetId];
  while (queue.length) {
    const cur = queue.shift()!;
    for (const e of draft.value.edges) {
      if (e.type !== 'execution' || e.target !== cur || seen.has(e.source)) continue;
      const n = byId.get(e.source);
      if (!n) continue;
      if (n.type === 'human_review') { found.push(n); queue.push(n.id); }
      // 不穿过其它 Agent/边界
    }
  }
  return found;
}

/** 图定位优先，找不到时回退 x 坐标窗口（覆盖尚未连线的游离审核） */
function locateReviews(afterId?: string, beforeId?: string): WorkflowNode[] {
  let graph: WorkflowNode[] = [];
  if (afterId && beforeId) graph = reviewsReachableFrom(afterId, beforeId);
  else if (afterId) graph = reviewsReachableFrom(afterId);
  else if (beforeId) graph = reviewsReachingInto(beforeId);
  if (graph.length) return graph;
  // 回退：x 窗口（含游离未连线的审核）
  const afterNode = afterId ? draft.value.nodes.find((n) => n.id === afterId) : undefined;
  const beforeNode = beforeId ? draft.value.nodes.find((n) => n.id === beforeId) : undefined;
  let targets = draft.value.nodes.filter((n) => n.type === 'human_review');
  if (afterNode) targets = targets.filter((n) => n.position.x > afterNode.position.x);
  if (beforeNode) targets = targets.filter((n) => n.position.x < beforeNode.position.x);
  return targets.sort((a, b) => a.position.x - b.position.x);
}

/** 静默连线（不走 toast；重复边视为 skipped 而非失败） */
function connectNodes(
  sourceId: string,
  targetId: string,
  kind: 'execution' | 'skill_binding',
  sourcePort: 'out' | 'bind' = kind === 'skill_binding' ? 'bind' : 'out',
  targetPort: 'in' | 'bind' = kind === 'skill_binding' ? 'bind' : 'in'
): ApplyResult & { skipped?: boolean } {
  const check = connectionRule(draft.value, sourceId, targetId, sourcePort, targetPort);
  if (!check.ok) {
    const dup = /已存在/.test(check.reason ?? '');
    return dup
      ? { ok: true, skipped: true }
      : { ok: false, reason: check.reason };
  }
  const src = draft.value.nodes.find((n) => n.id === sourceId);
  const dst = draft.value.nodes.find((n) => n.id === targetId);
  if (!src || !dst) return { ok: false, reason: '节点不存在' };
  const edge: WorkflowEdge = {
    id: genId('e'),
    type: kind,
    source: sourceId,
    target: targetId,
    sourcePort,
    targetPort,
  };
  if (kind === 'skill_binding') {
    edge.skillVersion = src.refVersion ?? null;
    edge.contract = null;
  } else {
    edge.contract = executionContract(
      src.type === 'agent' ? src.refId : null,
      dst.type === 'agent' ? dst.refId : null,
      src.type === 'human_review' || dst.type === 'human_review',
      dst.type === 'human_review'
    );
  }
  draft.value.edges.push(edge);
  markDirty();
  return { ok: true };
}

/** 为新节点找一排空闲 y（与现有节点保持距离） */
function findFreeY(x: number, height = 0): number {
  const base = 280;
  for (let row = 0; row < 6; row++) {
    const y = base + row * 240;
    const clash = draft.value.nodes.some(
      (n) => Math.abs(n.position.x - x) < 150 && Math.abs(n.position.y + height / 2 - y) < 140
    );
    if (!clash) return y;
  }
  return base + 6 * 240;
}

function createAgentSilent(refId: string, x?: number, y?: number): ApplyResult & { nodeId?: string } {
  const a = getAgent(refId);
  if (!a) return { ok: false, reason: `未找到 Agent：${refId}` };
  const node = makeNode({
    type: 'agent', refId,
    position: { x: x ?? 160, y: y ?? findFreeY(x ?? 160) },
    config: { selectedTools: [] },
  });
  draft.value.nodes.push(node);
  markDirty();
  return { ok: true, nodeId: node.id };
}

/** A 是否已能沿执行依赖到达 B（幂等搭链用：可达则不必再连） */
function execReachable(from: string, to: string): boolean {
  const adj = new Map<string, string[]>();
  for (const e of draft.value.edges) {
    if (e.type !== 'execution') continue;
    if (!adj.has(e.source)) adj.set(e.source, []);
    adj.get(e.source)!.push(e.target);
  }
  const seen = new Set<string>([from]);
  const queue = [from];
  while (queue.length) {
    const cur = queue.shift()!;
    if (cur === to) return true;
    for (const next of adj.get(cur) ?? []) {
      if (!seen.has(next)) { seen.add(next); queue.push(next); }
    }
  }
  return false;
}

const wfController: WorkflowAssistantController = {
  clearAll() {
    draft.value = newEmptyDraft(props.projectName, draft.value.name);
    dirty.value = true;
    clearSelection();
  },
  loadSample() {
    applySample();
  },
  runCheck() {
    return validateDraft(draft.value);
  },
  addAgent(refId) {
    const existing = findAgentNodeByRef(refId);
    if (existing) return { ok: true, nodeId: existing.id, detail: '画布上已有该 Agent 的节点，直接复用' };
    return createAgentSilent(refId);
  },
  addSkill(refId) {
    const s = getSkill(refId);
    if (!s) return { ok: false, reason: `未找到 Skill：${refId}` };
    const existing = findSkillNodeByRef(refId);
    if (existing) return { ok: true, nodeId: existing.id, detail: '画布上已有该 Skill 的节点，直接复用' };
    const node = makeNode({
      type: 'skill', refId, refVersion: s.version,
      position: { x: 420, y: findFreeY(420) + 220 },
    });
    draft.value.nodes.push(node);
    markDirty();
    return { ok: true, nodeId: node.id };
  },
  addReview(label) {
    const node = makeNode({
      type: 'human_review', label: label ?? null,
      position: { x: 640, y: findFreeY(640) },
      config: {},
    });
    draft.value.nodes.push(node);
    markDirty();
    return { ok: true, nodeId: node.id };
  },
  addBoundary(which) {
    const type = which === 'start' ? 'boundary_start' : 'boundary_end';
    if (draft.value.nodes.some((n) => n.type === type)) {
      return { ok: false, reason: `${which === 'start' ? '开始' : '结束'}（边界）已存在，最多一个` };
    }
    const node = makeNode({ type, position: { x: which === 'start' ? -40 : 1240, y: 300 } });
    draft.value.nodes.push(node);
    markDirty();
    return { ok: true };
  },
  chain(refIds, withBoundary) {
    let created = 0;
    // 空数组：把画布上现有 Agent/审核按 x 序串起来
    const ids: string[] = refIds.length
      ? refIds.map((r) => {
          const found = findAgentNodeByRef(r);
          if (found) return found.id;
          const res = createAgentSilent(r, 160 + created * 280);
          if (!res.ok) return '';
          created++;
          return res.nodeId!;
        }).filter(Boolean)
      : draft.value.nodes
          .filter((n) => n.type === 'agent' || n.type === 'human_review')
          .sort((a, b) => a.position.x - b.position.x)
          .map((n) => n.id);
    if (ids.length < 2) {
      return { ok: false, created: 0, connected: 0, reason: refIds.length ? '链条至少需要两个有效 Agent' : '画布上可串联的 Agent/审核不足两个' };
    }
    let connected = 0;
    let failed: string | undefined;
    for (let i = 0; i + 1 < ids.length; i++) {
      // 幂等：A 已能沿执行依赖到达 B（含经审核中转）则跳过，避免重复搭链产生平行边
      if (execReachable(ids[i], ids[i + 1])) continue;
      const r = connectNodes(ids[i], ids[i + 1], 'execution');
      if (!r.ok) { failed = r.reason; break; }
      if (!r.skipped) connected++;
    }
    if (failed) return { ok: false, created, connected, reason: failed, detail: `已连 ${connected} 条后中断` };
    // 边界：开始→首节点，末节点→结束
    if (withBoundary) {
      const first = draft.value.nodes.find((n) => n.id === ids[0]);
      const last = draft.value.nodes.find((n) => n.id === ids[ids.length - 1]);
      if (first && !draft.value.edges.some((e) => e.type === 'execution' && e.target === first.id)) {
        let start = draft.value.nodes.find((n) => n.type === 'boundary_start');
        if (!start) {
          start = makeNode({ type: 'boundary_start', position: { x: first.position.x - 200, y: first.position.y } });
          draft.value.nodes.push(start);
          created++;
        }
        connectNodes(start.id, first.id, 'execution');
      }
      if (last && !draft.value.edges.some((e) => e.type === 'execution' && e.source === last.id)) {
        let end = draft.value.nodes.find((n) => n.type === 'boundary_end');
        if (!end) {
          end = makeNode({ type: 'boundary_end', position: { x: last.position.x + 240, y: last.position.y } });
          draft.value.nodes.push(end);
          created++;
        }
        connectNodes(last.id, end.id, 'execution');
      }
    }
    markDirty();
    return { ok: true, created, connected };
  },
  insertReviews(afterRef, beforeRef, count, label) {
    const afterNode = findAgentNodeByRef(afterRef);
    if (!afterNode) return { ok: false, reason: `画布上没有「${getAgent(afterRef)?.name ?? afterRef}」的节点，先把它加进画布` };
    const beforeNode = beforeRef ? findAgentNodeByRef(beforeRef) : undefined;
    if (beforeRef && !beforeNode) return { ok: false, reason: `画布上没有「${getAgent(beforeRef)?.name ?? beforeRef}」的节点` };
    // 插入点：before 节点，或 after 当前执行出线的目标
    let targetNode = beforeNode;
    if (!targetNode) {
      const outEdge = draft.value.edges.find((e) => e.type === 'execution' && e.source === afterNode.id);
      targetNode = outEdge ? draft.value.nodes.find((n) => n.id === outEdge.target) : undefined;
      if (targetNode && (targetNode.type === 'boundary_end' || targetNode.type === 'human_review' || targetNode.type === 'agent')) {
        // 可插入；先摘掉 after→target 的旧边
        draft.value.edges = draft.value.edges.filter((e) => !(e.type === 'execution' && e.source === afterNode.id && e.target === targetNode!.id));
      } else {
        targetNode = undefined;
      }
    } else {
      // after→before 旧边（如存在）摘除
      draft.value.edges = draft.value.edges.filter((e) => !(e.type === 'execution' && e.source === afterNode.id && e.target === targetNode!.id));
    }
    // 摆放：after 与 target 之间（无 target 则排在其右侧）
    const x0 = afterNode.position.x;
    const x1 = targetNode?.position.x ?? afterNode.position.x + 280 * (count + 1);
    const y = targetNode ? Math.min(afterNode.position.y, targetNode.position.y) - 200 : afterNode.position.y - 200;
    const ids: string[] = [];
    let lastFailure: string | undefined;
    for (let i = 0; i < count; i++) {
      const node = makeNode({
        type: 'human_review',
        label: count > 1 ? `${label ?? '人工审核'} #${i + 1}` : label ?? null,
        position: { x: Math.round(x0 + ((x1 - x0) * (i + 1)) / (count + 1)), y },
        config: {
          targetSourceNodeId: null,
          targetArtifact: afterNode.refId ? (getAgent(afterNode.refId)?.artifacts[0] ?? '') : '',
        },
      });
      draft.value.nodes.push(node);
      ids.push(node.id);
    }
    // 连线：after→r1→…→rN→target
    const seq = [afterNode.id, ...ids, ...(targetNode ? [targetNode.id] : [])];
    let connected = 0;
    for (let i = 0; i + 1 < seq.length; i++) {
      const r = connectNodes(seq[i], seq[i + 1], 'execution');
      if (!r.ok) { lastFailure = r.reason; break; }
      if (!r.skipped) connected++;
    }
    markDirty();
    if (lastFailure) return { ok: false, reason: lastFailure, detail: `审核节点已加入但连线中断（已连 ${connected} 条）` };
    return { ok: true, nodeId: ids[ids.length - 1], detail: `新建 ${count} 道审核 / ${connected} 条连线` };
  },
  bind(skillRefId, agentRefId) {
    const agentNode = findAgentNodeByRef(agentRefId)
      ?? (() => { const r = createAgentSilent(agentRefId); return r.ok ? draft.value.nodes.find((n) => n.id === r.nodeId) : undefined; })();
    if (!agentNode) return { ok: false, reason: `无法创建 Agent 节点：${agentRefId}` };
    const skill = getSkill(skillRefId);
    if (!skill) return { ok: false, reason: `未找到 Skill：${skillRefId}` };
    let skillNode = findSkillNodeByRef(skillRefId);
    if (!skillNode) {
      const r = wfController.addSkill(skillRefId);
      if (!r.ok) return r;
      skillNode = draft.value.nodes.find((n) => n.id === r.nodeId);
    }
    if (!skillNode) return { ok: false, reason: 'Skill 节点创建失败' };
    const r = connectNodes(skillNode.id, agentNode.id, 'skill_binding');
    if (!r.ok) return r;
    return {
      ok: true,
      detail: r.skipped
        ? '该绑定已存在'
        : `已绑定 ${skill.name} v${skill.version} → ${getAgent(agentRefId)?.name ?? agentRefId}`,
    };
  },
  // —— 删除 / 解绑 / 断开 / 填内容 / 只读汇报 ——
  removeAgent(refId) {
    const node = findAgentNodeByRef(refId);
    if (!node) return { ok: false, reason: `画布上没有${shortAgentName(refId)}的节点` };
    removeNode(node.id);
    return { ok: true };
  },
  removeSkill(refId) {
    const node = findSkillNodeByRef(refId);
    if (!node) return { ok: false, reason: `画布上没有「${shortSkillName(refId)}」的节点` };
    removeNode(node.id);
    return { ok: true };
  },
  removeReviews(afterRef, beforeRef) {
    const reviews = draft.value.nodes.filter((n) => n.type === 'human_review');
    if (!reviews.length) return { ok: false, removed: 0, reason: '画布上没有人工审核' };
    const afterNode = afterRef ? findAgentNodeByRef(afterRef) : undefined;
    const beforeNode = beforeRef ? findAgentNodeByRef(beforeRef) : undefined;
    if (afterRef && !afterNode) return { ok: false, removed: 0, reason: `画布上没有${shortAgentName(afterRef)}的节点，无法定位审核` };
    if (beforeRef && !beforeNode) return { ok: false, removed: 0, reason: `画布上没有${shortAgentName(beforeRef)}的节点，无法定位审核` };
    const targets = locateReviews(afterNode?.id, beforeNode?.id);
    if (!targets.length) return { ok: false, removed: 0, reason: '该范围内没有找到审核' };
    for (const n of targets) removeNode(n.id);
    return { ok: true, removed: targets.length };
  },
  removeBoundary(which) {
    const type = which === 'start' ? 'boundary_start' : 'boundary_end';
    const node = draft.value.nodes.find((n) => n.type === type);
    if (!node) return { ok: false, reason: `画布上没有${which === 'start' ? '开始' : '结束'}边界` };
    removeNode(node.id);
    return { ok: true };
  },
  unbind(skillRefId, agentRefId) {
    const skillNode = findSkillNodeByRef(skillRefId);
    if (!skillNode) return { ok: false, removed: 0, reason: `画布上没有「${shortSkillName(skillRefId)}」的节点` };
    const agentNode = agentRefId ? findAgentNodeByRef(agentRefId) : undefined;
    if (agentRefId && !agentNode) return { ok: false, removed: 0, reason: `画布上没有${shortAgentName(agentRefId)}的节点` };
    const before = draft.value.edges.length;
    draft.value.edges = draft.value.edges.filter((e) => {
      if (e.type !== 'skill_binding' || e.source !== skillNode.id) return true;
      if (agentNode && e.target !== agentNode.id) return true;
      return false;
    });
    const removed = before - draft.value.edges.length;
    if (!removed) return { ok: false, removed: 0, reason: agentNode ? '该绑定不存在' : '该 Skill 没有任何绑定' };
    markDirty();
    return { ok: true, removed };
  },
  disconnect(fromRef, toRef) {
    const fromNode = findAgentNodeByRef(fromRef);
    const toNode = findAgentNodeByRef(toRef);
    if (!fromNode || !toNode) {
      const missing = !fromNode ? shortAgentName(fromRef) : shortAgentName(toRef);
      return { ok: false, reason: `画布上没有${missing}的节点` };
    }
    const before = draft.value.edges.length;
    draft.value.edges = draft.value.edges.filter((e) => !(e.type === 'execution' && e.source === fromNode.id && e.target === toNode.id));
    const removed = before - draft.value.edges.length;
    if (!removed) return { ok: false, reason: `${shortAgentName(fromRef)}和${shortAgentName(toRef)}之间没有直接连线` };
    markDirty();
    return { ok: true, detail: `已断开 ${removed} 条直接连线` };
  },
  fillReview(match, patch) {
    const reviews = draft.value.nodes.filter((n) => n.type === 'human_review');
    if (!reviews.length) return { ok: false, reason: '画布上还没有人工审核，先插入一道' };
    let target = reviews.slice().sort((a, b) => a.position.x - b.position.x)[0];
    if (match.afterRef) {
      const afterNode = findAgentNodeByRef(match.afterRef);
      if (!afterNode) return { ok: false, reason: `画布上没有${shortAgentName(match.afterRef)}的节点，无法定位审核` };
      const located = locateReviews(afterNode.id);
      if (!located.length) return { ok: false, reason: `没有找到${shortAgentName(match.afterRef)}之后的审核` };
      target = located[0];
    }
    if (patch.label !== undefined) target.label = patch.label;
    if (patch.instructions !== undefined) target.config = { ...target.config, instructions: patch.instructions };
    if (patch.reviewer !== undefined) target.config = { ...target.config, reviewer: patch.reviewer };
    markDirty();
    return { ok: true, detail: `已更新${target.label ? `「${target.label}」` : '人工审核'}的内容` };
  },
  outline() {
    const chainNodes = draft.value.nodes
      .filter((n) => n.type === 'agent' || n.type === 'human_review')
      .sort((a, b) => a.position.x - b.position.x);
    if (!chainNodes.length) return '画布现在是空的。可以试试「搭一条从剧本到成片的标准链」。';
    const skillNameById = new Map(
      draft.value.nodes.filter((n) => n.type === 'skill').map((n) => [n.id, shortSkillName(n.refId)]),
    );
    const bindsByAgent = new Map<string, string[]>();
    for (const e of draft.value.edges) {
      if (e.type !== 'skill_binding') continue;
      const name = skillNameById.get(e.source);
      if (name) bindsByAgent.set(e.target, [...(bindsByAgent.get(e.target) ?? []), name]);
    }
    const lines: string[] = ['当前画布（从左到右）：'];
    chainNodes.forEach((n, i) => {
      if (n.type === 'agent') {
        const binds = bindsByAgent.get(n.id);
        lines.push(`${i + 1}. ${shortAgentName(n.refId)}${binds?.length ? `（绑定：${binds.join('、')}）` : ''}`);
      } else {
        const who = n.config.reviewer ? ` · 审核者：${n.config.reviewer}` : '';
        const note = n.config.instructions ? ` · 说明：${n.config.instructions}` : '';
        lines.push(`${i + 1}. 人工审核${n.label ? `「${n.label}」` : ''}${who}${note}`);
      }
    });
    const skills = draft.value.nodes.filter((n) => n.type === 'skill').length;
    const start = draft.value.nodes.some((n) => n.type === 'boundary_start');
    const end = draft.value.nodes.some((n) => n.type === 'boundary_end');
    lines.push(
      `另有 Skill 节点 ${skills} 个；边界：${start ? '有开始' : '无开始'}/${end ? '有结束' : '无结束'}；`
      + `连线：执行 ${draft.value.edges.filter((e) => e.type === 'execution').length} 条、绑定 ${draft.value.edges.filter((e) => e.type === 'skill_binding').length} 条。`,
    );
    return lines.join('\n');
  },
  removeNodeById(nodeId) { removeNode(nodeId); },
  removeEdgeById(edgeId) { removeEdge(edgeId); },
  layoutRefit() {
    scheduleFitView();
  },
};
// 画布节点上的快捷 ✕ 通过注入拿到控制器（避免节点组件层层透传事件）
provide('wfController', wfController);
</script>

<style lang="scss" scoped>
.wf-studio {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--bg-root);
  min-width: 0;
}

/* ---------- 顶栏 ---------- */
.wf-topbar {
  height: 52px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 16px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-root);
  overflow-x: auto;
  overflow-y: hidden;

  &::-webkit-scrollbar { height: 0; }
}
.wf-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
}
.wf-name {
  width: 200px;
  height: 30px;
  padding: 0 10px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text-primary);
  font-size: 13px;
  outline: none;

  &:focus { border-color: var(--border-hover); }
}
.wf-spacer { flex: 1; min-width: 8px; }

.wf-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  line-height: 20px;
  padding: 0 8px;
  border-radius: 10px;
  border: 1px solid var(--border);
  color: var(--text-muted);
  white-space: nowrap;

  .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--text-faint);
    flex-shrink: 0;
  }
  &.chip-warn { color: var(--warning); border-color: rgba(234, 179, 8, 0.35);
    .dot { background: var(--warning); } }
  &.chip-ok { color: #4ade80; border-color: rgba(74, 222, 128, 0.3);
    .dot { background: #4ade80; } }
  &.chip-blue { color: #60a5fa; border-color: rgba(96, 165, 250, 0.35); }
}

.check-ok { border-color: rgba(74, 222, 128, 0.35) !important; color: #4ade80 !important; }
.check-bad { border-color: rgba(239, 68, 68, 0.45) !important; color: var(--error) !important; }
.run-disabled { opacity: 0.55; }
.assistant-toggle.assistant-on {
  color: var(--text-primary);
  background: var(--bg-hover);
}
.run-short { display: none; }

.wf-legend {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 11px;
  color: var(--text-muted);
  white-space: nowrap;

  .lg-item { display: inline-flex; align-items: center; gap: 5px; }
  .lg-line {
    display: inline-block;
    width: 22px;
    height: 0;
    &.exec { border-top: 2px solid #7a7a7a; }
    &.bind { border-top: 2px dashed #60a5fa; }
  }
}

/* ---------- 主区 ---------- */
.wf-main {
  flex: 1;
  display: flex;
  min-height: 0;
}

/* ---------- 左：组件库 ---------- */
.wf-lib {
  width: 226px;
  flex-shrink: 0;
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--bg-root);
}
.lib-search {
  margin: 10px 10px 6px;
  width: auto;
}
.input {
  height: 30px;
  padding: 0 10px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text-primary);
  font-size: 12px;
  outline: none;
  width: 100%;

  &:focus { border-color: var(--border-hover); }
  &::placeholder { color: var(--text-faint); }
}
textarea.input { height: auto; padding: 7px 10px; resize: vertical; font-family: inherit; line-height: 1.5; }
select.input { appearance: auto; }

.lib-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 0 10px 10px;
}
.lib-group { margin-top: 10px; }
.lib-group-title {
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.06em;
  color: var(--text-faint);
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 2px 6px;

  .cnt {
    font-size: 10px;
    color: var(--text-faint);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0 5px;
    line-height: 14px;
  }
}
.lib-item {
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-surface);
  padding: 7px 9px;
  margin-bottom: 6px;
  cursor: grab;
  transition: border-color 0.15s ease, background-color 0.15s ease, transform 0.12s ease, box-shadow 0.12s ease;

  &:hover {
    border-color: var(--border-hover);
    background: var(--bg-hover);
    transform: translateY(-1px);
    box-shadow: 0 3px 10px rgba(0, 0, 0, 0.3);
  }
  &:active {
    cursor: grabbing;
    transform: translateY(0) scale(0.99);
    box-shadow: none;
  }
}
.lib-skill { border-color: rgba(96, 165, 250, 0.25); }
.lib-review { border-color: rgba(234, 179, 8, 0.25); }
.li-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}
.li-name {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.li-id {
  font-size: 10px;
  color: var(--text-faint);
  flex-shrink: 0;
}
.li-sub {
  font-size: 10px;
  color: var(--text-muted);
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.lib-hint {
  font-size: 10px;
  color: var(--text-faint);
  line-height: 1.5;
  padding: 2px 2px 0;
}
.lib-boundary-row {
  display: flex;
  gap: 6px;
  margin-bottom: 4px;

  .btn { flex: 1; }
}
.lib-foot {
  flex-shrink: 0;
  border-top: 1px solid var(--border);
  padding: 8px 10px;
  font-size: 10px;
  color: var(--text-faint);
  line-height: 1.5;
}

/* ---------- 中：画布 ---------- */
.wf-canvas-wrap {
  flex: 1;
  position: relative;
  min-width: 0;
  background: var(--bg-root);

  /* 拖拽组件悬停在画布上时的落点提示 */
  &.drag-over::after {
    content: '';
    position: absolute;
    inset: 6px;
    pointer-events: none;
    border: 1.5px dashed rgba(96, 165, 250, 0.5);
    border-radius: 10px;
    background: rgba(96, 165, 250, 0.045);
  }
}
.wf-flow { width: 100%; height: 100%; }

/* 背景点阵颜色随主题（SVG 属性不支持 CSS var，经样式表覆盖） */
.wf-flow :deep(.vue-flow__background circle) {
  fill: var(--border-hover);
}

.wf-canvas-controls {
  position: absolute;
  right: 14px;
  bottom: 14px;
  z-index: 5;
  display: flex;
  align-items: center;
  gap: 2px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 3px;
}
.ctrl-btn {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  border-radius: 4px;
  color: var(--text-muted);
  font-size: 13px;
  cursor: pointer;

  &:hover { background: var(--bg-hover); color: var(--text-primary); }
}
.ctrl-zoom {
  font-size: 11px;
  color: var(--text-muted);
  padding: 0 4px;
  min-width: 38px;
  text-align: center;
}

.wf-empty-guide {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  pointer-events: none;
  font-size: 13px;
  color: var(--text-muted);
  text-align: center;

  p { margin: 0; }
  .guide-link {
    pointer-events: auto;
    color: var(--text-primary);
    text-decoration: underline;
    cursor: pointer;
  }
}

/* ---------- 右：配置面板 ---------- */
.wf-panel {
  width: 288px;
  flex-shrink: 0;
  border-left: 1px solid var(--border);
  background: var(--bg-root);
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow-y: auto;
}
.panel-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px 10px;
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  background: var(--bg-root);
  z-index: 2;

  .panel-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .panel-id {
    font-size: 10px;
    color: var(--text-faint);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 1px 5px;
    margin-left: auto;
    flex-shrink: 0;
  }
}
.panel-body {
  padding: 12px 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 9px;
}
.kv {
  display: flex;
  gap: 8px;
  align-items: baseline;

  .k {
    width: 56px;
    flex-shrink: 0;
    font-size: 11px;
    color: var(--text-faint);
  }
  .v {
    font-size: 12px;
    color: var(--text-secondary);
    min-width: 0;
    word-break: break-all;

    &.tiny { font-size: 10px; color: var(--text-faint); }
  }
  .arrow { color: var(--text-muted); }
}
.p-desc {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-secondary);
}
.chip-row { display: flex; gap: 6px; flex-wrap: wrap; }
.sec-label {
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.06em;
  color: var(--text-faint);
  padding-top: 6px;
  border-top: 1px solid var(--border);
  margin-top: 4px;
}
.stat-row {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  padding: 2px 0;

  .stat {
    font-size: 11px;
    color: var(--text-muted);

    b {
      font-size: 14px;
      color: var(--text-primary);
      margin-right: 4px;
      font-variant-numeric: tabular-nums;
    }
  }
}
.check-ok-line {
  font-size: 12px;
  color: #4ade80;
}
.issue-list {
  display: flex;
  flex-direction: column;
  gap: 5px;
  max-height: 260px;
  overflow-y: auto;
}
.issue {
  display: flex;
  gap: 7px;
  align-items: flex-start;
  text-align: left;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 7px 9px;
  cursor: pointer;
  transition: border-color 0.15s ease;

  &:hover { border-color: var(--border-hover); }

  .iss-badge { font-size: 11px; line-height: 1.5; flex-shrink: 0; }
  .iss-text { font-size: 11px; line-height: 1.5; color: var(--text-secondary); }

  &.error .iss-badge { color: var(--error); }
  &.warning .iss-badge { color: var(--warning); }
}
.sem-note {
  margin: 0;
  font-size: 11px;
  line-height: 1.7;
  color: var(--text-muted);

  b { color: var(--text-secondary); }
  &.tiny { font-size: 10px; }
}
.obj-input {
  width: 100%;
  resize: vertical;
  font-size: 12px;
  line-height: 1.6;
}
// Phase 47：产物要求编辑行
.spec-row {
  display: grid; grid-template-columns: 1fr 56px 1fr auto; gap: 5px; align-items: center;
  padding: 6px 7px; border: 1px solid var(--wf-border); border-radius: 7px;
  .spec-kind, .spec-count, .spec-id { font-size: 12px; padding: 4px 6px; }
  .spec-flags { grid-column: 1 / -1; display: flex; gap: 9px; flex-wrap: wrap;
    .spec-flag { display: flex; align-items: center; gap: 3px; cursor: pointer;
      input { accent-color: #60a5fa; } } }
}
.danger-link {
  align-self: flex-start;

  &:hover:not(:disabled) { color: var(--error); border-color: rgba(239, 68, 68, 0.4); }
}
.unset-hint {
  margin: 0;
  font-size: 11px;
  line-height: 1.6;
  color: var(--text-faint);

  &.warn { color: var(--warning); }
}

.tool-block-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 11px;
  color: var(--text-secondary);
  padding: 2px 0 6px;

  &.extra { padding-top: 10px; }
  .tool-count {
    font-size: 10px;
    color: var(--text-faint);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0 6px;
    line-height: 15px;
  }
}
.tool-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 240px;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
  background: var(--bg-surface);
}
.tool-item {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 11px;
  color: var(--text-secondary);
  cursor: default;
  padding: 2px 0;

  span { word-break: break-all; }

  .core-tick {
    flex-shrink: 0;
    font-size: 10px;
    line-height: 14px;
    width: 14px;
    text-align: center;
    color: #4ade80;
  }
}

.bind-table {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.bind-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  padding: 5px 8px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 6px;

  .bind-agent { color: var(--text-primary); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .bind-role { color: #60a5fa; }
  .bind-pri { color: var(--text-faint); }
}

.field {
  display: flex;
  flex-direction: column;
  gap: 5px;

  .f-k { font-size: 11px; color: var(--text-faint); }
}

.artifact-list {
  margin: 0;
  padding-left: 16px;
  display: flex;
  flex-direction: column;
  gap: 3px;

  li { font-size: 11px; line-height: 1.6; color: var(--text-secondary); word-break: break-all; }
}
.evidence {
  margin: 0;
  font-size: 10px;
  line-height: 1.6;
  color: var(--text-faint);
  word-break: break-all;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 7px 9px;
}

/* ---------- 按钮（与 scene-system 同规格）---------- */
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

/* ---------- 响应式（窄窗口）---------- */
@media (max-width: 1080px) {
  .wf-lib { width: 190px; }
  .wf-panel { width: 250px; }
  .wf-chat { width: 252px; }
  .wf-name { width: 140px; }
  .wf-legend { display: none; }
  // 顶栏允许换行，避免按钮被视口截断
  .wf-topbar {
    height: auto;
    min-height: 52px;
    flex-wrap: wrap;
    padding: 6px 16px;
    row-gap: 6px;
    overflow: visible;
  }
  .run-full { display: none; }
  .run-short { display: inline; }
}
@media (max-width: 860px) {
  .wf-lib { width: 168px; }
  .wf-panel { width: 224px; }
}

// ============ Phase 47 C批：模板弹层 ============
.tpl-overlay {
  position: fixed; inset: 0; z-index: 90;
  background: rgba(0, 0, 0, 0.55);
  display: flex; align-items: center; justify-content: center;
}
.tpl-modal {
  width: min(720px, 92vw); max-height: 84vh; overflow-y: auto;
  background: var(--bg, #171717); border: 1px solid var(--wf-border, #2a2a2a);
  border-radius: 12px; padding: 14px 16px; display: flex; flex-direction: column; gap: 10px;
}
.tpl-head { display: flex; align-items: center; justify-content: space-between; }
.tpl-title { font-size: 15px; font-weight: 600; }
.tpl-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.tpl-item {
  border: 1px solid var(--wf-border, #2a2a2a); border-radius: 8px; padding: 8px 10px;
  cursor: pointer; display: flex; flex-direction: column; gap: 3px;
  &:hover { border-color: #555; }
  &.active { border-color: #60a5fa; background: rgba(96, 165, 250, 0.08); }
  .tpl-name { font-size: 13px; font-weight: 600; }
  .tpl-desc { color: #c9c9c9; }
  .tpl-io { color: #9a9a9a; }
}
.tpl-params {
  border-top: 1px solid var(--wf-border, #2a2a2a); padding-top: 10px;
  display: flex; flex-direction: column; gap: 6px;
  .tpl-input { flex: 1; }
  .tpl-create { align-self: flex-start; }
}
.muted { color: #9a9a9a; }
</style>
