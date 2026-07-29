import { defineStore } from 'pinia';
import { ref, reactive, computed } from 'vue';
import { apiClient } from '@/api';
import type { Scene, Character, TaskStatus } from '@/api';
import { createDiscreteApi, darkTheme } from 'naive-ui';

// 全局通知（Naive UI discrete message，支持队列/手动关闭，替代单 ref 通知）
const { message: naiveMessage } = createDiscreteApi(['message'], {
  configProviderProps: { theme: darkTheme },
});

// 默认mock数据
const defaultCharacters: Character[] = [
  {
    name: '小星',
    description: 'a young girl with silver hair, blue eyes, wearing a white cloak, gentle expression',
    reference_images: {}
  },
  {
    name: '暗影',
    description: 'a tall figure in black armor, glowing red eyes, menacing presence',
    reference_images: {}
  }
];

const defaultScenes: Scene[] = [
  {
    id: 1,
    description: '小星站在山顶，俯瞰云海，晨光洒落',
    characters: ['小星'],
    camera: 'wide shot, from behind',
    lighting: 'golden sunrise',
    motion: 'hair and cloak flowing in the wind',
    dialogue: '新的一天开始了。',
    status: 'completed',
    score: { total: 8.5, character: 9, style: 8, composition: 8, clarity: 9 }
  },
  {
    id: 2,
    description: '暗影从云层中浮现，红眼闪烁',
    characters: ['暗影'],
    camera: 'low angle, dramatic',
    lighting: 'dark clouds, red glow',
    motion: 'slowly emerging from mist',
    dialogue: '你逃不掉的。',
    status: 'completed',
    score: { total: 8.2, character: 8, style: 8, composition: 9, clarity: 8 }
  },
  {
    id: 3,
    description: '两人对峙，风暴骤起',
    characters: ['小星', '暗影'],
    camera: 'medium shot, side view',
    lighting: 'lightning flashes',
    motion: 'wind intensifying, hair and clothes blowing',
    dialogue: '我不会屈服！',
    status: 'in_progress',
    score: { total: 8.5, character: 9, style: 8, composition: 8, clarity: 9 }
  },
  {
    id: 4,
    description: '小星举起法杖，光芒四射',
    characters: ['小星'],
    camera: 'close-up, dramatic',
    lighting: 'bright white light',
    motion: 'magic energy gathering',
    status: 'pending'
  },
  {
    id: 5,
    description: '暗影被光芒击中，开始消散',
    characters: ['暗影'],
    camera: 'medium shot',
    lighting: 'light vs darkness',
    motion: 'shadow dissolving',
    status: 'pending'
  },
  {
    id: 6,
    description: '云海恢复平静，阳光普照',
    characters: [],
    camera: 'wide shot, aerial',
    lighting: 'warm golden light',
    motion: 'clouds parting',
    status: 'pending'
  },
  {
    id: 7,
    description: '小星微笑，转身离开',
    characters: ['小星'],
    camera: 'medium shot, from front',
    lighting: 'soft backlight',
    motion: 'gentle turn and walk',
    status: 'pending'
  },
  {
    id: 8,
    description: '远景，小星走向远方的光芒',
    characters: ['小星'],
    camera: 'wide shot, from behind',
    lighting: 'ethereal glow',
    motion: 'walking into distance',
    status: 'pending'
  }
];

export const useProjectStore = defineStore('project', () => {
  // 项目信息
  const projectName = ref('我的动画短片');
  const style = ref('anime, soft lighting, cinematic composition');
  const size = ref('1152x768');

  // 角色列表（初始空，避免 mock 数据闪烁；由 loadProject 加载真实数据）
  const characters = ref<Character[]>([]);

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
        // 更新缓存
        if (!characterCache.value[charName]) {
          characterCache.value[charName] = { description, views: {} };
        }
        characterCache.value[charName].views[viewKey] = {
          raw: r.raw_base64,
          seg: r.seg_base64 || undefined,
        };
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
    // 更新 busy 状态（使用 spread 确保响应式）
    const currentState = generatingState[charName] || { busy: {}, busySeg: {}, steps: [] };
    generatingState[charName] = {
      ...currentState,
      busySeg: { ...currentState.busySeg, [viewKey]: true },
    };

    try {
      const r = await apiClient.segmentImage(imageBase64);

      if (r.success && characterCache.value[charName]?.views[viewKey]) {
        characterCache.value[charName].views[viewKey].seg = r.seg_base64;
      }

      return r;
    } finally {
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

  // 场景列表（初始空，由 loadProject 加载）
  const scenes = ref<Scene[]>([]);

  // 当前场景索引
  const currentSceneIndex = ref(0);

  // 通知消息
  const notification = ref({ show: false, message: '', type: 'info' as 'info' | 'success' | 'error' });

  // 任务状态
  const taskStatus = ref<TaskStatus>({
    task_id: '',
    status: 'idle',
    progress: {},
    message: '',
  });

  // 统计数据
  const stats = computed(() => ({
    total: scenes.value.length,
    completed: scenes.value.filter((s) => s.status === 'completed').length,
    inProgress: scenes.value.filter((s) => s.status === 'in_progress').length,
    pending: scenes.value.filter((s) => s.status === 'pending').length,
  }));

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

  // 加载项目数据
  async function loadProject(name: string) {
    try {
      console.log('[Store] 开始加载项目:', name);
      projectName.value = name;

      // 从API加载角色
      const chars = await apiClient.getProjectCharacters(name);
      console.log('[Store] API返回角色:', JSON.stringify(chars));
      if (chars.length > 0) {
        characters.value = chars;
        console.log('[Store] 已加载角色:', chars.length, '个');
        // 打印每个角色的图片路径
        chars.forEach(c => {
          console.log(`[Store] 角色 ${c.name} 参考图:`, JSON.stringify(c.reference_images));
        });
      } else {
        console.log('[Store] API返回空角色列表，使用默认数据');
      }

      // 从API加载场景
      const sceneList = await apiClient.getProjectScenes(name);
      console.log('[Store] API返回场景:', JSON.stringify(sceneList));
      if (sceneList.length > 0) {
        scenes.value = sceneList;
        console.log('[Store] 已加载场景:', sceneList.length, '个');
        // 打印每个场景的图片路径
        sceneList.forEach(s => {
          console.log(`[Store] 场景 ${s.id} 状态: ${s.status}, 图片: ${s.image_path || '无'}`);
        });
      } else {
        console.log('[Store] API返回空场景列表，使用默认数据');
      }
    } catch (error) {
      console.error('[Store] API加载失败，使用默认数据:', error);
    }
  }

  // 运行工作流
  async function runWorkflow(scriptContent: string, mode: string = 'interactive') {
    try {
      const result = await apiClient.runWorkflow(
        projectName.value,
        scriptContent,
        mode
      );
      taskStatus.value = {
        task_id: result.task_id,
        status: 'running',
        progress: {},
        message: '工作流已启动',
      };
      showNotification('工作流已启动', 'success');

      // 开始轮询任务状态
      pollTaskStatus(result.task_id);
    } catch (error) {
      console.error('启动工作流失败:', error);
      showNotification(`启动失败: ${error}`, 'error');
      // 模拟运行状态
      taskStatus.value = {
        task_id: 'mock_task',
        status: 'running',
        progress: {},
        message: '正在生成...',
      };

      // 模拟完成
      setTimeout(() => {
        taskStatus.value = {
          task_id: 'mock_task',
          status: 'completed',
          progress: {},
          message: '生成完成',
        };
        showNotification('生成完成（模拟）', 'success');
      }, 3000);
    }
  }

  // 轮询任务状态
  async function pollTaskStatus(taskId: string) {
    const poll = async () => {
      try {
        const status = await apiClient.getTaskStatus(taskId);
        taskStatus.value = status;

        if (status.status === 'running') {
          setTimeout(poll, 2000);
        } else if (status.status === 'completed') {
          showNotification('工作流执行完成！', 'success');
          await loadProject(projectName.value);
        } else if (status.status === 'failed') {
          showNotification(`工作流执行失败: ${status.message}`, 'error');
        }
      } catch (error) {
        console.error('获取任务状态失败:', error);
      }
    };

    await poll();
  }

  // 刷新任务状态
  async function refreshTaskStatus() {
    try {
      const status = await apiClient.getCurrentTask();
      taskStatus.value = status;
    } catch (error) {
      console.log('后端未连接，使用默认状态');
    }
  }

  // ============ 场景操作 ============

  // 确认场景
  async function confirmScene(sceneId: number) {
    try {
      await apiClient.confirmScene(projectName.value, sceneId);
      // 更新本地状态
      const scene = scenes.value.find(s => s.id === sceneId);
      if (scene) {
        scene.status = 'confirmed';
      }
      showNotification(`场景 ${sceneId} 已确认`, 'success');
    } catch (error) {
      console.error('确认场景失败:', error);
      showNotification(`确认失败: ${error}`, 'error');
    }
  }

  // 重新生成场景
  async function regenerateScene(sceneId: number) {
    try {
      await apiClient.regenerateScene(projectName.value, sceneId);
      // 更新本地状态
      const scene = scenes.value.find(s => s.id === sceneId);
      if (scene) {
        scene.status = 'pending';
        scene.image_path = undefined;
        scene.score = undefined;
      }
      showNotification(`场景 ${sceneId} 已重置，将重新生成`, 'info');
    } catch (error) {
      console.error('重置场景失败:', error);
      showNotification(`重置失败: ${error}`, 'error');
    }
  }

  // 跳过场景
  function skipScene(sceneId: number) {
    const scene = scenes.value.find(s => s.id === sceneId);
    if (scene) {
      scene.status = 'skipped';
      showNotification(`场景 ${sceneId} 已跳过`, 'info');
    }
  }

  // 暂停工作流
  async function pauseWorkflow() {
    // 暂停功能 - 标记为暂停状态
    if (taskStatus.value.status === 'running') {
      taskStatus.value = {
        ...taskStatus.value,
        message: '已暂停',
      };
      showNotification('工作流已暂停', 'info');
    }
  }

  // ============ 角色操作 ============

  // 重新生成角色
  async function regenerateCharacter(characterName: string) {
    try {
      await apiClient.regenerateCharacter(projectName.value, characterName);
      // 清除本地角色参考图
      const char = characters.value.find(c => c.name === characterName);
      if (char) {
        char.reference_images = {};
      }
      showNotification(`角色 "${characterName}" 已重置`, 'success');
    } catch (error) {
      console.error('重置角色失败:', error);
      showNotification(`重置失败: ${error}`, 'error');
    }
  }

  // 编辑角色（打开编辑对话框的标记）
  const editingCharacter = ref<string | null>(null);

  function editCharacter(characterName: string) {
    editingCharacter.value = characterName;
  }

  // ============ 动画操作 ============

  // 重新生成动画
  async function regenerateAnimation(fromScene: number, toScene: number) {
    try {
      await apiClient.regenerateAnimation(projectName.value, fromScene, toScene);
      showNotification(`过渡动画 ${fromScene}→${toScene} 已重置`, 'success');
    } catch (error) {
      console.error('重置动画失败:', error);
      showNotification(`重置失败: ${error}`, 'error');
    }
  }

  // ============ 音频操作 ============

  // 生成所有音频
  async function generateAllAudio() {
    try {
      const result: any = await apiClient.generateAllAudio(projectName.value);
      if (result.task_id) {
        showNotification(result.message, 'info');
        const poll = async () => {
          try {
            const s: any = await apiClient.getTaskStatus(result.task_id);
            if (s.status === 'running' || s.status === 'pending') {
              setTimeout(poll, 1500);
            } else if (s.status === 'completed') {
              showNotification('全部音频生成完成', 'success');
              await loadProject(projectName.value);
            } else if (s.status === 'failed') {
              showNotification(`音频生成失败: ${s.message || ''}`, 'error');
            }
          } catch (e) { /* ignore */ }
        };
        await poll();
      } else {
        showNotification(result.message, 'info');
      }
    } catch (error: any) {
      console.error('生成音频失败:', error);
      showNotification(`生成失败: ${error.message}`, 'error');
    }
  }

  // 重新生成单个音频（支持音色/语速，真正调用 Edge TTS）
  async function regenerateAudio(sceneId: number, voice: string = 'default', speed: number = 1.0) {
    try {
      const result: any = await apiClient.generateSceneAudio(projectName.value, sceneId, { voice, speed });
      showNotification(result.message, 'info');
      const poll = async () => {
        try {
          const s: any = await apiClient.getTaskStatus(result.task_id);
          if (s.status === 'running' || s.status === 'pending') {
            setTimeout(poll, 1500);
          } else if (s.status === 'completed') {
            showNotification(`场景 ${sceneId} 音频生成完成`, 'success');
            await loadProject(projectName.value);
          } else if (s.status === 'failed') {
            showNotification(`音频生成失败: ${s.message || ''}`, 'error');
          }
        } catch (e) { /* ignore */ }
      };
      await poll();
    } catch (error: any) {
      console.error('生成音频失败:', error);
      showNotification(`生成失败: ${error.message}`, 'error');
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
    currentSceneIndex,
    taskStatus,
    stats,
    notification,
    editingCharacter,

    // 角色保存系统
    savedCharacters,
    currentCharacterId,
    characterCache,
    generatingState,

    // 方法
    loadProject,
    runWorkflow,
    refreshTaskStatus,
    showNotification,

    // 场景操作
    confirmScene,
    regenerateScene,
    skipScene,
    pauseWorkflow,

    // 角色操作
    regenerateCharacter,
    editCharacter,

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

    // 动画操作
    regenerateAnimation,

    // 音频操作
    generateAllAudio,
    regenerateAudio,

    // 设置操作
    updateSettings,
  };
});
