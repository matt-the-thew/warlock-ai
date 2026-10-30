// @ts-check
import { defineConfig } from 'astro/config';

import react from '@astrojs/react';
import clerk from '@clerk/astro';

import tailwindcss from '@tailwindcss/vite';

// https://astro.build/config
export default defineConfig({
  // Clerk runs entirely in the browser here: the site stays static output, and
  // the Python API verifies the session token Chat sends with each question.
  integrations: [react(), clerk()],

  vite: {
    plugins: [tailwindcss()],
    // Read the repo root's .env, which the API shares, for
    // PUBLIC_CLERK_PUBLISHABLE_KEY. Only PUBLIC_* values reach the browser.
    envDir: '..',
    server: {
      // The Python API (`uv run dnd-rag serve`).
      proxy: { '/api': 'http://127.0.0.1:8000' }
    }
  }
});
