<script lang="ts">
	export let className = 'w-4 h-4';
</script>

<!-- Generic single-color "knowledge / service desk" glyph for TOPdesk. Drawn
     with `currentColor` so it inherits the surrounding text colour and themes
     cleanly in light/dark mode (TOPdesk's real wordmark is multi-colour and
     not redistributable as a path). -->
<svg
	xmlns="http://www.w3.org/2000/svg"
	viewBox="0 0 24 24"
	class={className}
	fill="none"
	stroke="currentColor"
	stroke-width="2"
	stroke-linecap="round"
	stroke-linejoin="round"
	aria-label="TOPdesk"
	role="img"
>
	<rect x="3" y="4" width="18" height="16" rx="2" />
	<path d="M3 9h18" />
	<path d="M7 13h6" />
	<path d="M7 16.5h4" />
</svg>
