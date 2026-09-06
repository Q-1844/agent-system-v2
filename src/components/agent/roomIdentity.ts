/**
 * Production Room · Agent 视觉身份（Phase 3）。
 * 纯前端展示层映射（图标/配色/中文名）——协作事实仍全部来自后端投影，
 * 这里不承载任何状态语义。user 也纳入以便气泡/roster 统一渲染。
 */
export interface RoomIdentity {
  name: string;
  emoji: string;
  color: string;       // 头像底色（深浅主题均可读的中饱和色）
  role: string;
}

export const ROOM_IDENTITY: Record<string, RoomIdentity> = {
  user:               { name: '用户',               emoji: '👤', color: '#64748b', role: '' },
  producer:           { name: 'Producer',           emoji: '🎛️', color: '#3b82f6', role: '总制片 · 协调者' },
  script_writer:      { name: 'Script Writer',      emoji: '✍️', color: '#22a06b', role: '编剧 · 故事层' },
  character_designer: { name: 'Character Designer', emoji: '🎨', color: '#c2559d', role: '角色设计 · 视觉身份' },
  director:           { name: 'Director',           emoji: '🎥', color: '#c78a1d', role: '导演 · 分镜' },
};

export function identityOf(agentId: string | null | undefined): RoomIdentity {
  return ROOM_IDENTITY[agentId || ''] ?? { name: agentId || '未知', emoji: '🤖', color: '#6b7280', role: '' };
}

/** roster/状态徽标的中文标签（状态值由后端投影给出，真实事实） */
export function statusLabel(status: string | null | undefined, notCalled?: boolean): string {
  if (notCalled && (!status || status === 'idle')) return '本轮未参与';
  switch (status) {
    case 'working': return '工作中';
    case 'completed': return '已完成';
    case 'cancelled': return '已停止';
    case 'failed': return '失败';
    case 'interrupted': return '已中断';
    case 'not_configured': return '未配置';
    case 'running': return '工作中';
    case 'stopped': return '已停止';
    case 'dispatched': return '已委派';
    case 'idle': return '空闲';
    default: return status || '空闲';
  }
}

/** 结果卡事实徽标（§二十四：轻量 footer，不展示 receipt JSON） */
export function changedRefsText(refs?: { scenes?: string[]; shots?: string[]; characters?: string[] }): string {
  if (!refs) return '';
  const parts: string[] = [];
  if (refs.scenes?.length) parts.push(`Scenes: ${refs.scenes.join(', ')}`);
  if (refs.shots?.length) parts.push(`Shots: ${refs.shots.join(', ')}`);
  if (refs.characters?.length) parts.push(`角色: ${refs.characters.join(', ')}`);
  return parts.join(' · ');
}
