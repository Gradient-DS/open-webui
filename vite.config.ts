import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

import { viteStaticCopy } from 'vite-plugin-static-copy';

// [Gradient] An allocated dev-stack port takes precedence over the upstream URL override.
const backendTarget = process.env.OWUI_BE_PORT
	? `http://localhost:${process.env.OWUI_BE_PORT}`
	: process.env.WEBUI_BACKEND_URL || 'http://localhost:8080';

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
			const target = backendTarget;
			return {
				'/api': target,
				'/ollama': target,
				'/openai': target,
				'/oauth': target,
				'/static': target,
				// Socket.IO is mounted at /ws/socket.io on the BE
				// (`backend/open_webui/main.py` app.mount('/ws', socket_app)).
				// [Gradient] constants.ts uses relative URLs. Vite proxies HTTP and
				// WebSocket traffic to OWUI_BE_PORT (or the backend URL/default above).
				// ws: true forwards the Socket.IO HTTP-101 upgrade handshake.
				'/ws': { target, ws: true }
			};
		})()
		// NOTE: a previous `watch.ignored: ['**/.worktrees/**']` was removed
		// here. The intent was to suppress HMR for sibling worktrees when a
		// dev server runs from the repo root, but chokidar matches the
		// absolute file path — so inside a worktree the pattern matches every
		// own file too and silently kills HMR (vite serves the cached transform
		// indefinitely). The kickoff_features workflow already runs one dev
		// server per worktree from its own cwd, so cross-worktree noise is not
		// a real concern. If you ever run a single dev server from the parent
		// repo root with multiple worktrees underneath, re-introduce a
		// per-invocation guard rather than a blanket glob.
	},
	build: {
		sourcemap: true
	},
	worker: {
		format: 'es'
	},
	esbuild: {
		pure: process.env.ENV === 'dev' ? [] : ['console.log', 'console.debug', 'console.error']
	},
	test: {
		exclude: ['**/node_modules/**', '**/.worktrees/**']
	}
});
