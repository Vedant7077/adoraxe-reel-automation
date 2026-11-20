<script lang="ts">
  import '../app.css';
  import { onMount } from 'svelte';
  import Sidebar from '#lib/components/Sidebar.svelte';
  import AdminLogin from '#lib/components/AdminLogin.svelte';
  import { authStore } from '#lib/stores/auth.svelte';
  import type { LayoutProps } from './$types';

  let { children }: LayoutProps = $props();

  onMount(async () => {
    await authStore.init();
  });
</script>

<svelte:head>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin="">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <title>Adoraxe – Reel Automation</title>
</svelte:head>

{#if authStore.isChecking}
  <div class="min-h-screen w-full flex items-center justify-center bg-surface">
    <div class="flex flex-col items-center gap-3">
      <div class="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin"></div>
      <p class="text-xs text-zinc-400 font-medium font-headline">Verifying security session…</p>
    </div>
  </div>
{:else if !authStore.isAuthenticated}
  <AdminLogin />
{:else}
  <div class="flex h-screen overflow-hidden bg-surface">
    <Sidebar />
    <main class="flex-1 overflow-y-auto">
      <div class="p-6 md:p-8">
        {@render children()}
      </div>
    </main>
  </div>
{/if}
