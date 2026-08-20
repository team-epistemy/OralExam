import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ mode }) => ({
  plugins: [react(), tailwindcss()],
  // The AWS image bakes the SPA under /app/ (served by FastAPI StaticFiles), so a
  // production build defaults to that base. Vercel serves the SPA from the domain
  // root, so its build sets VITE_BASE_PATH=/ to override. Keep local dev at /.
  base: process.env.VITE_BASE_PATH ?? (mode === 'production' ? '/app/' : '/'),
}))
