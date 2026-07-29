import { ref, watch } from 'vue';

type Theme = 'dark' | 'light';

const theme = ref<Theme>((localStorage.getItem('theme') as Theme) || 'dark');

export function useTheme() {
  const toggleTheme = () => {
    theme.value = theme.value === 'dark' ? 'light' : 'dark';
  };

  const setTheme = (t: Theme) => {
    theme.value = t;
  };

  watch(theme, (val) => {
    document.documentElement.setAttribute('data-theme', val);
    localStorage.setItem('theme', val);
  }, { immediate: true });

  return {
    theme,
    toggleTheme,
    setTheme
  };
}
