/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        'bg-base':        '#0d1117',
        'bg-sidebar':     '#0a0d12',
        'bg-card':        '#161b22',
        'bg-card2':       '#1c2128',
        'border-default': '#30363d',
        'accent':         '#58a6ff',
        'accent2':        '#7c3aed',
        'success':        '#3fb950',
        'warning':        '#e3b341',
        'danger':         '#f85149',
        'text-main':      '#c9d1d9',
        'text-muted':     '#8b949e',
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'monospace'],
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      },
    },
  },
  plugins: [],
}
