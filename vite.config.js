import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Relative asset paths, so the build works from a GitHub Pages project URL
  // (https://<user>.github.io/<repo>/) as well as from a domain root.
  base: './',
})
