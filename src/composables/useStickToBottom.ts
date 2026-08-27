/**
 * 智能贴底滚动（AI-7D-R 体验收口）。
 *
 * 需求：
 *  - 打开会话后自动定位到最新消息。
 *  - 新消息出现时：用户原本接近底部 → 自动跟随滚到底；
 *    用户正在查看历史（已向上滚动）→ 不强制拉到底。
 *  - 窗口/容器尺寸变化时，若仍贴底则保持贴底。
 *
 * 不绑定具体消息数组：调用方在消息变化/会话切换时调用 pinToBottom() 或
 * observe(messagesRef)。距离阈值内（NEAR_BOTTOM_PX）视为「贴底」。
 */
import { ref, nextTick, onBeforeUnmount, type Ref } from 'vue';

const NEAR_BOTTOM_PX = 48;

export function useStickToBottom(
  elRef: Ref<HTMLElement | null>,
  opts: { nearBottomPx?: number } = {},
) {
  const threshold = opts.nearBottomPx ?? NEAR_BOTTOM_PX;
  // 用户是否「贴底」（据此决定是否跟随新消息）。初始 true：首次加载自动滚到最新。
  const stuck = ref(true);

  function isNearBottom(): boolean {
    const el = elRef.value;
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight <= threshold;
  }

  function scrollToBottom(smooth = false) {
    const el = elRef.value;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
  }

  /** 会话切换/首次打开：无条件跳最新，并重新贴底。 */
  async function pinToBottom() {
    stuck.value = true;
    await nextTick();
    scrollToBottom(false);
  }

  /** 新消息到达：仅当用户原本贴底才跟随。 */
  async function followIfStuck() {
    await nextTick();
    if (stuck.value) scrollToBottom(false);
  }

  function onScroll() {
    stuck.value = isNearBottom();
  }

  function bind() {
    elRef.value?.addEventListener('scroll', onScroll, { passive: true });
  }
  function unbind() {
    elRef.value?.removeEventListener('scroll', onScroll);
  }

  onBeforeUnmount(unbind);

  return {
    stuck, isNearBottom, scrollToBottom, pinToBottom, followIfStuck, bind, unbind,
  };
}
