import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 3458,
    host: '0.0.0.0',
    proxy: {
      '/gold': {
        target: 'http://100.119.161.65:8099',
        changeOrigin: true,
      },
    },
  },
  preview: {
    port: 3458,
    host: '0.0.0.0',
  },
})
