import { sveltekit } from '@sveltejs/kit/vite';
import adapter from '@sveltejs/adapter-auto';
import { defineConfig } from 'vite';

export default defineConfig({
	envPrefix: ['VITE_', 'PUBLIC_'],
	plugins: [
		sveltekit({
			adapter: adapter()
		})
	],
	server: {
		port: 5173,
		proxy: {
			'/api': {
				target: process.env.VITE_BACKEND_URL || 'http://localhost:8000',
				changeOrigin: true,
			},
		},
	},
});
