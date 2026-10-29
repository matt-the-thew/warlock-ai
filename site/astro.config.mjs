// @ts-check
import { defineConfig } from 'astro/config';

import react from '@astrojs/react';

import tailwindcss from '@tailwindcss/vite';

// https://astro.build/config
export default defineConfig({
  integrations: [react()],

  vite: {
    plugins: [tailwindcss()],
    server: {
      // The Python API (`uv run dnd-rag serve`).
      proxy: { '/api': 'http://127.0.0.1:8000' }
    }
  }
});
