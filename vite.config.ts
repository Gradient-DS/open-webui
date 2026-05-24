import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

import { viteStaticCopy } from 'vite-plugin-static-copy';

export default defineConfig({
	plugins: [
		sveltekit(),
		viteStaticCopy({
			targets: [
				{
					src: 'node_modules/onnxruntime-web/dist/*.jsep.*',

					dest: 'wasm'
				}
			]
		})
	],
	define: {
		APP_VERSION: JSON.stringify(process.env.npm_package_version),
		APP_BUILD_HASH: JSON.stringify(process.env.APP_BUILD_HASH || 'dev-build')
	},
	server: {
		// Proxy target is env-driven so soev's multi-stack dev workflow
		// (stack-N runs OWUI BE on an allocated port like 18180) works
		// without a config fork. Defaults to 8080 — the standard
		// `open-webui dev` port — for non-multi-stack invocations.
		proxy: (() => {
			const target = `http://localhost:${process.env.OWUI_BE_PORT || '8080'}`;
			return {
				'/api': target,
				'/ollama': target,
				'/openai': target,
				'/oauth': target,
				'/static': target
			};
		})()
	},
	build: {
		sourcemap: true
	},
	worker: {
		format: 'es'
	},
	esbuild: {
		pure: process.env.ENV === 'dev' ? [] : ['console.log', 'console.debug', 'console.error']
	}
});
