/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Kickbase-inspired palette (for Tailwind utilities)
        kb: {
          black: '#0a0a0a',
          dark: '#141414',
          card: '#1a1a1a',
          border: '#2a2a2a',
          'grey-dark': '#3a3a3a',
          grey: '#6b7280',
          'grey-light': '#a8adb4',
          white: '#f5f5f5',
          red: '#e11d48',
          'red-dark': '#be123c',
          purple: '#9747FF',
        },
        // Semantic colors
        success: {
          400: '#4ade80',
          500: '#22c55e',
          600: '#16a34a',
        },
        danger: {
          400: '#f87171',
          500: '#ef4444',
          600: '#dc2626',
        },
        warning: {
          400: '#fbbf24',
          500: '#f59e0b',
          600: '#d97706',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
