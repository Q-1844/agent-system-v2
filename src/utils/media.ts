/**
 * 媒体文件 URL 工具
 * 统一处理后端返回的项目路径 -> 可访问 URL
 * 图片走 /api/image（带CORS+缓存），视频/音频走 /projects 静态服务
 */
const API_BASE = 'http://localhost:8000';

export function mediaUrl(path: string, type: 'image' | 'video' | 'audio' = 'image'): string {
  if (!path) return '';
  const normalized = path.replace(/\\/g, '/');
  const match = normalized.match(/projects\/([^/]+)\/output\/([^/]+)\/(.+)$/);
  if (match) {
    const [, project, category, filename] = match;
    if (type === 'image') {
      return `${API_BASE}/api/image/${encodeURIComponent(project)}/${category}/${encodeURIComponent(filename)}`;
    }
    return `${API_BASE}/projects/${encodeURIComponent(project)}/output/${category}/${encodeURIComponent(filename)}`;
  }
  const cleanPath = normalized.replace(/^.*projects\//, 'projects/');
  return `${API_BASE}/${cleanPath}`;
}

export const getImageUrl = (p: string) => mediaUrl(p, 'image');
export const getVideoUrl = (p: string) => mediaUrl(p, 'video');
export const getAudioUrl = (p: string) => mediaUrl(p, 'audio');
