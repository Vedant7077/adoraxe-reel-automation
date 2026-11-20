/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/**/*.{html,js,svelte,ts}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Adoraxe Brand
        primary: {
          DEFAULT: '#E1306C',
          50:  '#FEE8F0',
          100: '#FDD0E1',
          200: '#FBA1C3',
          300: '#F872A5',
          400: '#F54387',
          500: '#E1306C',
          600: '#B82658',
          700: '#8F1D44',
          800: '#661330',
          900: '#3D0A1C',
        },
        secondary: '#F77737',
        tertiary: '#833AB4',
        // Dark theme surfaces
        surface: {
          DEFAULT: '#0D0D1A',
          card:    '#161625',
          elevated:'#1E1E32',
          border:  '#25253A',
        },
      },
      fontFamily: {
        headline: ['"Plus Jakarta Sans"', 'sans-serif'],
        body:     ['Inter', 'sans-serif'],
      },
      backgroundImage: {
        'ig-gradient': 'linear-gradient(135deg, #E1306C 0%, #F77737 50%, #833AB4 100%)',
        'ig-gradient-h': 'linear-gradient(90deg, #E1306C 0%, #F77737 100%)',
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      },
    },
  },
  plugins: [],
};
