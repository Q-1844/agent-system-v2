import { defineStore } from 'pinia';
import { ref, reactive } from 'vue';
import { apiClient } from '@/api';
import type { Scene, Character } from '@/api';
import { createDiscreteApi } from 'naive-ui';
import { discreteConfigProviderProps } from '@/styles/naiveTheme';

// 全局通知（Naive UI discrete message，支持队列/手动关闭，替代单 ref 通知）
const { message: naiveMessage } = createDiscreteApi(['message'], {
  configProviderProps: discreteConfigProviderProps,
});

export const useProjectStore = defineStore('project', () => {
  // 项目信息
  const projectName = ref('我的动画短片');
  const style = ref('anime, soft lighting, cinematic composition');
  const size = ref('1152x768');

  // ============ AI-7C-H K3：选中项目身份持久化（F5 恢复）============
  // 只持久化项目身份/导航态（localStorage 允许）；session/execution 真相仍在后端。
  const SELECTED_PROJECT_KEY = 'agnes:project:selected:v1';
  // identityReady：项目身份解析完成（恢复或确认默认）。Agent/ChangeSession 的后端
  // 真相恢复必须 await 它——保证 F5 后恢复的是正确项目的会话（K3 验收要求）。
  let _resolveIdentity: () => void = () => {};
  const identityReady = new Promise<void>((resolve) => { _resolveIdentity = resolve; });
  const identityRestored = ref(false);

  function _persistSelected(name: string) {
    try { localStorage.setItem(SELECTED_PROJECT_KEY, name); } catch { /* ignore */ }
  }

  function _readSelected(): string | null {
    try { return localStorage.getItem(SELECTED_PROJECT_KEY); } catch { return null; }
  }

  function _clearSelected() {
    try { localStorage.removeItem(SELECTED_PROJECT_KEY); } catch { /* ignore */ }
  }

  /** K3：启动恢复选中项目。读 localStorage -> 对照后端项目列表 -> 有效则恢复，
   * 无效/后端不可达则回退默认（单次尝试，无崩溃循环）。完成后 resolve identityReady。 */
  async function restoreSelectedProject() {
    if (identityRestored.value) return;
    const stored = _readSelected();
    if (stored && stored !== projectName.value) {
      try {
        const projects = await apiClient.listProjects();
        if (projects.includes(stored)) {
          await loadProject(stored);
        } else {
          // 项目已删除/失效：清除记录，保持默认，不崩溃
          _clearSelected();
        }
      } catch {
        // 后端暂不可达：保持默认身份，不清记录（下次刷新可再试）；不循环
      }
    }
    identityRestored.value = true;
    _resolveIdentity();
  }

  // 角色列表（初始空；阶段 5 loadProject 不再调旧 getProjectCharacters，由各页面本地操作）
  const characters = ref<Character[]>([]);
  // 场景列表（初始空；script-editor 本地编辑，saveProjectScript 保存到 parsed_script.json）
  const scenes = ref<Scene[]>([]);

  // ============ 角色保存系统 ============

  // 已保存的角色列表
  const savedCharacters = ref<Array<{
    id: string;
    name: string;
    description: string;
    created_at: string;
    updated_at: string;
    has_views: {
      front: { raw: boolean; seg: boolean };
      side: { raw: boolean; seg: boolean };
      back: { raw: boolean; seg: boolean };
    };
  }>>([]);

  // 当前选中的角色ID（用于保存）
  const currentCharacterId = ref<string | null>(null);

  // 角色生成状态缓存（解决切换丢失问题）
  const characterCache = ref<Record<string, {
    description: string;
    views: {
      front?: { raw?: string; seg?: string };
      side?: { raw?: string; seg?: string };
      back?: { raw?: string; seg?: string };
    };
  }>>({});

  // 生成中的状态跟踪（用于进度持久化）- 使用 reactive 确保深层响应
  const generatingState = reactive<Record<string, {
    busy: Record<string, boolean>;
    busySeg: Record<string, boolean>;
    steps: any[];
  }>>({});

  // 加载已保存角色列表
  async function loadSavedCharacters() {
    try {
      const result = await apiClient.listSavedCharacters();
      savedCharacters.value = result.characters;
      console.log('[Store] 已加载保存角色:', result.characters.length, '个');
    } catch (error) {
      console.error('[Store] 加载保存角色失败:', error);
    }
  }

  // 保存当前角色
  async function saveCurrentCharacter(name: string, description: string, views: {
    front?: { raw?: string; seg?: string };
    side?: { raw?: string; seg?: string };
    back?: { raw?: string; seg?: string };
  }): Promise<boolean> {
    try {
      const result = await apiClient.saveCharacter({ name, description, views });
      if (result.success) {
        currentCharacterId.value = result.character_id;
        await loadSavedCharacters();
        showNotification(`角色 "${name}" 已保存`, 'success');
        return true;
      }
      return false;
    } catch (error) {
      console.error('[Store] 保存角色失败:', error);
      showNotification('保存失败', 'error');
      return false;
    }
  }

  // 加载已保存的角色
  async function loadSavedCharacter(characterId: string): Promise<{
    name: string;
    description: string;
    views: {
      front?: { raw?: string; seg?: string };
      side?: { raw?: string; seg?: string };
      back?: { raw?: string; seg?: string };
    };
  } | null> {
    try {
      const result = await apiClient.loadCharacter(characterId);
      if (result.success) {
        currentCharacterId.value = characterId;
        return result.character;
      }
      return null;
    } catch (error) {
      console.error('[Store] 加载角色失败:', error);
      showNotification('加载失败', 'error');
      return null;
    }
  }

  // 删除已保存的角色
  async function deleteSavedCharacter(characterId: string): Promise<boolean> {
    try {
      const result = await apiClient.deleteCharacter(characterId);
      if (result.success) {
        await loadSavedCharacters();
        showNotification('角色已删除', 'success');
        return true;
      }
      return false;
    } catch (error) {
      console.error('[Store] 删除角色失败:', error);
      showNotification('删除失败', 'error');
      return false;
    }
  }

  // 更新角色信息
  async function updateCharacterInfo(characterId: string, name?: string, description?: string): Promise<boolean> {
    try {
      const result = await apiClient.updateCharacter(characterId, { name, description });
      if (result.success) {
        await loadSavedCharacters();
        showNotification('角色信息已更新', 'success');
        return true;
      }
      return false;
    } catch (error) {
      console.error('[Store] 更新角色失败:', error);
      showNotification('更新失败', 'error');
      return false;
    }
  }

  // 缓存角色生成状态
  function cacheCharacterState(charName: string, description: string, views: {
    front?: { raw?: string; seg?: string };
    side?: { raw?: string; seg?: string };
    back?: { raw?: string; seg?: string };
  }) {
    characterCache.value[charName] = { description, views };
  }

  // 获取角色缓存
  function getCharacterCache(charName: string) {
    return characterCache.value[charName] || null;
  }

  // 更新生成状态
  function updateGeneratingState(charName: string, state: {
    busy?: Record<string, boolean>;
    busySeg?: Record<string, boolean>;
    steps?: any[];
  }) {
    if (!generatingState[charName]) {
      generatingState[charName] = { busy: {}, busySeg: {}, steps: [] };
    }
    if (state.busy) generatingState[charName].busy = state.busy;
    if (state.busySeg) generatingState[charName].busySeg = state.busySeg;
    if (state.steps) generatingState[charName].steps = state.steps;
  }

  // 获取生成状态
  function getGeneratingState(charName: string) {
    return generatingState[charName] || null;
  }

  // 清除生成状态
  function clearGeneratingState(charName: string) {
    delete generatingState[charName];
  }

  // 在 Store 中执行生成请求（防止组件销毁时丢失）
  async function generateView(charName: string, description: string, viewKey: string): Promise<{
    success: boolean;
    raw_base64?: string;
    seg_base64?: string;
    metadata?: any;
  }> {
    const proj = projectName.value;   // 捕获：生成开始时的项目（切项目后结果仍写回它）
    // 更新 busy 状态（使用 spread 确保响应式）
    const currentState = generatingState[charName] || { busy: {}, busySeg: {}, steps: [] };
    generatingState[charName] = {
      ...currentState,
      busy: { ...currentState.busy, [viewKey]: true },
    };

    try {
      const r = await apiClient.generateCharacterView({
        description,
        view: viewKey,
        style: 'anime',
      });

      if (r.success) {
        // 仅当仍在原项目时更新当前项目缓存（切项目后不污染新项目，GPT §7/§8）
        if (proj === projectName.value) {
          if (!characterCache.value[charName]) {
            characterCache.value[charName] = { description, views: {} };
          }
          characterCache.value[charName].views[viewKey] = {
            raw: r.raw_base64,
            seg: r.seg_base64 || undefined,
          };
        }
        // auto-bind 到捕获项目（始终写回原项目，不再依赖"绑定到项目"按钮，GPT §6）
        await persistCharacterViews(proj, charName, description, {
          [viewKey]: { raw: r.raw_base64, seg: r.seg_base64 || undefined },
        });
      }

      return r;
    } finally {
      // 清除 busy 状态
      const state = generatingState[charName];
      if (state) {
        generatingState[charName] = {
          ...state,
          busy: { ...state.busy, [viewKey]: false },
        };
      }
    }
  }

  // 在 Store 中执行抠图请求
  async function segmentView(charName: string, imageBase64: string, viewKey: string): Promise<{
    success: boolean;
    seg_base64?: string;
    metadata?: any;
  }> {
    const proj = projectName.value;   // 捕获：抠图开始时的项目（切项目后仍写回它）
    // 更新 busy 状态（使用 spread 确保响应式）
    const currentState = generatingState[charName] || { busy: {}, busySeg: {}, steps: [] };
    generatingState[charName] = {
      ...currentState,
      busySeg: { ...currentState.busySeg, [viewKey]: true },
    };

    try {
      const r = await apiClient.segmentImage(imageBase64);

      if (r.success && r.seg_base64) {
        // auto-bind 该视角原图+新抠图到捕获项目（合并，不覆盖其他视角，补"单独抠图不落盘"缺口）
        const ok = await persistCharacterViews(proj, charName, "", {
          [viewKey]: { raw: imageBase64, seg: r.seg_base64 },
        });
        if (!ok) showNotification('抠图已完成，但保存到项目失败', 'error');
        // 仅当仍在原项目时更新当前项目缓存
        if (proj === projectName.value && characterCache.value[charName]?.views[viewKey]) {
          characterCache.value[charName].views[viewKey].seg = r.seg_base64;
        }
      }

      return r;
    } finally {
      if (proj === projectName.value) {
        // 清除 busy 状态
        const state = generatingState[charName];
        if (state) {
          generatingState[charName] = {
            ...state,
            busySeg: { ...state.busySeg, [viewKey]: false },
          };
        }
      }
    }
  }

  // 通知消息（settings 页面仍用 ref 显示；showNotification 优先 naiveMessage，失败回退此 ref）
  const notification = ref({ show: false, message: '', type: 'info' as 'info' | 'success' | 'error' });

  // 显示通知（Naive UI message，支持队列/手动关闭）
  function showNotification(message: string, type: 'info' | 'success' | 'error' = 'info') {
    try {
      if (type === 'success') naiveMessage.success(message);
      else if (type === 'error') naiveMessage.error(message);
      else naiveMessage.info(message);
    } catch (e) {
      // 回退到旧通知
      notification.value = { show: true, message, type };
      setTimeout(() => { notification.value.show = false; }, 3000);
    }
  }

  // 加载项目：设置 projectName + 清空项目级角色数据（项目隔离：切项目不串）。
  // 角色列表由 loadProjectCharacters 按需加载（角色页 onMounted/watch 触发）。
  // K3：显式切换/创建项目时持久化身份（F5 恢复用）。
  async function loadProject(name: string) {
    projectName.value = name;
    _persistSelected(name);
    characters.value = [];
    characterCache.value = {};
    Object.keys(generatingState).forEach(k => { delete generatingState[k]; });
    currentCharacterId.value = null;
  }

  // 加载当前项目的角色（v2 project.json）。带 token 防竞态：切项目后旧响应不覆盖新。
  let _loadSeq = 0;
  async function loadProjectCharacters(name: string) {
    if (!name) return;
    const mySeq = ++_loadSeq;
    try {
      const result = await apiClient.getProjectCharactersFull(name);
      if (mySeq !== _loadSeq) return;   // 旧响应，丢弃（用户已切到别的项目）
      const list = (result.characters || []).map((c: any) => ({
        name: c.name, description: c.description_en || '', reference_images: {},
      }));
      characters.value = list;
      // 用项目里的 views 填充缓存：刷新后三视图仍可显示，状态(完成 N/3)正确
      for (const c of result.characters || []) {
        const views: any = {};
        for (const [v, data] of Object.entries(c.views || {})) {
          views[v] = { raw: (data as any).raw || undefined, seg: (data as any).seg || undefined };
        }
        characterCache.value[c.name] = { description: c.description_en || '', views };
      }
    } catch (e) {
      console.error('[Store] 加载项目角色失败', e);
      if (mySeq === _loadSeq) characters.value = [];
    }
  }

  // 把角色三视图落盘到指定项目（auto-bind）。proj 是生成开始时捕获的项目，
  // 即便用户中途切到别的项目，结果仍写回原项目（GPT §7）。
  // 返回 boolean 供调用方判断是否需要给"保存失败"提示。
  async function persistCharacterViews(proj: string, name: string, description: string, views: any): Promise<boolean> {
    try {
      await apiClient.bindCharacterToProject(proj, { name, description, views });
      return true;
    } catch (e: any) {
      console.error('[Store] 角色落盘失败', e);
      return false;
    }
  }

  // ============ 设置操作 ============

  // 更新设置
  async function updateSettings(newSettings: Record<string, any>) {
    try {
      await apiClient.updateSettings(newSettings);
      if (newSettings.projectName) projectName.value = newSettings.projectName;
      if (newSettings.style) style.value = newSettings.style;
      if (newSettings.size) size.value = newSettings.size;
      showNotification('设置已保存', 'success');
    } catch (error) {
      console.error('保存设置失败:', error);
      showNotification(`保存失败: ${error}`, 'error');
    }
  }

  return {
    // 状态
    projectName,
    style,
    size,
    characters,
    scenes,
    notification,
    identityReady,
    identityRestored,

    // 角色保存系统
    savedCharacters,
    currentCharacterId,
    characterCache,
    generatingState,

    // 方法
    loadProject,
    restoreSelectedProject,
    loadProjectCharacters,
    persistCharacterViews,
    showNotification,

    // 角色保存系统方法
    loadSavedCharacters,
    saveCurrentCharacter,
    loadSavedCharacter,
    deleteSavedCharacter,
    updateCharacterInfo,
    cacheCharacterState,
    getCharacterCache,
    updateGeneratingState,
    getGeneratingState,
    clearGeneratingState,
    generateView,
    segmentView,

    // 设置操作
    updateSettings,
  };
});
