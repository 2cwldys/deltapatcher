/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./src/renderer/index.html', './src/renderer/src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // the icon's colours: a dark slate tile, a teal delta, an amber blob
        ink: {
          950: '#0b0f15',
          900: '#0f141c',
          850: '#131a24',
          800: '#18202c',
          700: '#222c3b',
          600: '#2d394b',
          500: '#3c4a5f',
          400: '#5b6a80',
          300: '#8593a8'
        },
        delta: { DEFAULT: '#19b3a0', light: '#3fd1bd', dark: '#128a7c' },
        blob: { DEFAULT: '#f2a33a', light: '#f7c06f', dark: '#c47f22' }
      },
      fontFamily: {
        sans: ['"Segoe UI"', 'Inter', 'system-ui', '-apple-system', 'Ubuntu', 'Roboto', 'sans-serif'],
        mono: ['"Cascadia Mono"', 'Consolas', '"SF Mono"', 'Menlo', '"DejaVu Sans Mono"', 'monospace']
      }
    }
  },
  plugins: []
}
