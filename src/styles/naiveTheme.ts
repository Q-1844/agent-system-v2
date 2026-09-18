import { darkTheme } from 'naive-ui';
import type { GlobalThemeOverrides } from 'naive-ui';

/**
 * 全局黑白灰主题覆盖，与 src/styles/global.scss 中的 CSS 变量保持一致。
 * 供 createDiscreteApi（message / dialog）以及后续的 n-config-provider 统一使用，
 * 避免 Naive UI 默认绿色主色与黑白灰风格冲突。
 */
export const naiveThemeOverrides: GlobalThemeOverrides = {
  common: {
    fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    primaryColor: '#e5e5e5',
    primaryColorHover: '#ffffff',
    primaryColorPressed: '#c4c4c4',
    primaryColorSuppl: '#e5e5e5',
    bodyColor: '#111111',
    cardColor: '#1a1a1a',
    modalColor: '#1a1a1a',
    popoverColor: '#1a1a1a',
    inputColor: '#1a1a1a',
    actionColor: '#222222',
    hoverColor: '#222222',
    borderColor: '#2a2a2a',
    dividerColor: '#222222',
    textColorBase: '#e5e5e5',
    textColor1: '#e5e5e5',
    textColor2: '#999999',
    textColor3: '#666666',
    borderRadius: '6px',
    borderRadiusSmall: '4px',
    successColor: '#22c55e',
    successColorHover: '#4ade80',
    successColorPressed: '#16a34a',
    warningColor: '#eab308',
    warningColorHover: '#facc15',
    warningColorPressed: '#ca8a04',
    errorColor: '#ef4444',
    errorColorHover: '#f87171',
    errorColorPressed: '#dc2626'
  },
  Button: {
    textColorPrimary: '#111111',
    textColorHoverPrimary: '#111111',
    textColorPressedPrimary: '#111111',
    textColorFocusPrimary: '#111111'
  },
  Dialog: {
    color: '#1a1a1a',
    textColor: '#999999',
    titleTextColor: '#e5e5e5'
  },
  Message: {
    color: '#1f1f1f',
    textColor: '#e5e5e5'
  }
};

/** createDiscreteApi 统一配置（强制暗色 + 黑白灰覆盖） */
export const discreteConfigProviderProps = {
  theme: darkTheme,
  themeOverrides: naiveThemeOverrides
};
