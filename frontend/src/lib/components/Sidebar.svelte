<script lang="ts">
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { authStore } from '#lib/stores/auth.svelte';

  const navItems = [
    { href: '/',         label: 'Dashboard',         icon: '⬛' },
    { href: '/channels', label: 'YouTube Channels',   icon: '📺' },
    { href: '/library',  label: 'Video Library',      icon: '🎬' },
    { href: '/instagram',label: 'Instagram Account',  icon: '📸' },
    { href: '/queue',    label: 'Publishing Queue',   icon: '⏱' },
    { href: '/history',  label: 'Publishing History', icon: '📋' },
    { href: '/settings', label: 'Settings',           icon: '⚙️' },
  ];

  let currentPath = $derived(page.url.pathname);
</script>

<aside class="w-64 min-h-screen flex flex-col bg-surface-card border-r border-surface-border flex-shrink-0">
  <!-- Logo -->
  <div class="p-5 border-b border-surface-border">
    <div class="flex items-center gap-3">
      <div class="w-9 h-9 rounded-lg bg-ig-gradient flex items-center justify-center text-white font-bold text-lg">A</div>
      <div>
        <div class="font-bold text-white font-headline text-sm">Adoraxe</div>
        <div class="text-xs text-zinc-500">Reel Automation</div>
      </div>
    </div>
  </div>

  <!-- Navigation -->
  <nav class="flex-1 p-3 space-y-1">
    {#each navItems as item}
      <a
        href={item.href}
        class="nav-item {currentPath === item.href || (item.href !== '/' && currentPath.startsWith(item.href)) ? 'active' : ''}"
      >
        <span class="text-base">{item.icon}</span>
        <span>{item.label}</span>
      </a>
    {/each}
  </nav>

  <!-- Footer -->
  <div class="p-4 border-t border-surface-border flex items-center justify-between text-xs text-zinc-500">
    <span>Adoraxe v1.0.0</span>
    <button
      type="button"
      class="text-[11px] text-zinc-400 hover:text-red-400 flex items-center gap-1 transition-colors px-1.5 py-0.5 rounded hover:bg-surface-elevated cursor-pointer"
      onclick={() => authStore.logout()}
      title="Lock portal and clear administrator session"
    >
      <span>🔒 Lock</span>
    </button>
  </div>
</aside>
