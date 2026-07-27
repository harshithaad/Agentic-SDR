import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  resolve: {
    // The dev server may be launched through the D:\sdrapp junction
    // (space-free path); without this Vite realpaths files outside the root.
    preserveSymlinks: true,
  },
  server: {
    fs: {
      allow: ['D:/sdrapp/frontend', 'D:/Salesforce Compass Program/agentic-sdr/frontend'],
    },
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
