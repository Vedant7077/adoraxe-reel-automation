<script lang="ts">
  import { onMount } from 'svelte';
  import { getHealth } from '#lib/api/client';

  let health = $state<any>(null);
  let loading = $state(true);
  let demoMode = $state(true);
  let backendUrl = $state('');
  let frontendUrl = $state('');

  onMount(async () => {
    backendUrl = import.meta.env.VITE_BACKEND_URL || (import.meta.env as any).PUBLIC_API_URL || 'http://localhost:8000';
    frontendUrl = window.location.origin;
    try {
      health = await getHealth();
      demoMode = health.demo_mode;
    } catch (e) { health = null; }
    finally { loading = false; }
  });

  const services = [
    { key: 'database',               label: 'PostgreSQL Database',      icon: '🗄' },
    { key: 'ffmpeg',                 label: 'FFmpeg Video Processor',   icon: '🎞' },
    { key: 'cloudinary_configured',  label: 'Cloudinary CDN',           icon: '☁️' },
    { key: 'instagram_configured',   label: 'Instagram Graph API',      icon: '📸' },
  ];
</script>

<div>
  <div class="page-header">
    <h1 class="page-title">Settings</h1>
    <p class="page-subtitle">System health, service status, and application configuration.</p>
  </div>

  <!-- System Status -->
  <div class="card mb-6">
    <div class="flex items-center justify-between mb-4">
      <h2 class="text-sm font-semibold text-zinc-300">System Status</h2>
      <button class="text-xs text-primary hover:underline" onclick={() => location.reload()}>🔄 Refresh</button>
    </div>

    <!-- Backend -->
    <div class="p-3 rounded-lg border border-surface-border mb-3 flex items-center gap-3">
      <span class="w-2 h-2 rounded-full {health ? 'bg-emerald-400' : 'bg-red-400'} flex-shrink-0 animate-pulse"></span>
      <div class="flex-1">
        <p class="text-sm text-white font-medium">Backend API (FastAPI)</p>
        <p class="text-xs text-zinc-500">{backendUrl}/api/v1/health</p>
      </div>
      <span class="badge {health ? 'badge-published' : 'badge-failed'}">{health ? 'Online' : 'Offline'}</span>
    </div>

    {#if loading}
      {#each services as s}
        <div class="p-3 rounded-lg border border-surface-border mb-2 animate-pulse flex gap-3">
          <div class="w-2 h-2 rounded-full bg-zinc-600 flex-shrink-0 mt-1.5"></div>
          <div class="h-4 bg-surface-elevated rounded flex-1"></div>
        </div>
      {/each}
    {:else if health}
      {#each services as s}
        {@const ok = health[s.key]}
        <div class="p-3 rounded-lg border border-surface-border mb-2 flex items-center gap-3">
          <span class="w-2 h-2 rounded-full {ok ? 'bg-emerald-400' : 'bg-amber-400'} flex-shrink-0 animate-pulse"></span>
          <div class="flex-1">
            <p class="text-sm text-white">{s.icon} {s.label}</p>
            {#if !ok}<p class="text-xs text-amber-400">Not configured — set environment variables in .env</p>{/if}
          </div>
          <span class="badge {ok ? 'badge-published' : 'badge-pending'}">{ok ? 'OK' : 'Not Configured'}</span>
        </div>
      {/each}
    {:else}
      <p class="text-xs text-red-400 text-center py-4">Cannot connect to backend. Is it running?</p>
    {/if}
  </div>

  <!-- App Settings -->
  <div class="card mb-6">
    <h2 class="text-sm font-semibold text-zinc-300 mb-4">Application Configuration</h2>

    <div class="space-y-4">
      <!-- Demo Mode -->
      <div class="p-3 rounded-lg bg-surface-elevated border border-surface-border">
        <div class="flex items-center justify-between">
          <div>
            <p class="text-sm font-medium text-white">DEMO_MODE</p>
            <p class="text-xs text-zinc-500 mt-0.5">Shows sample data and simulates publishing without real API calls.</p>
          </div>
          <span class="badge {demoMode ? 'badge-pending' : 'badge-published'}">{demoMode ? 'Enabled' : 'Disabled'}</span>
        </div>
        <p class="text-xs text-zinc-600 mt-2">Change in <code class="text-amber-300">.env</code>: set <code class="text-amber-300">DEMO_MODE=false</code></p>
      </div>

      <!-- URLs -->
      <div>
        <p class="text-xs text-zinc-500 mb-1 font-medium">Backend URL</p>
        <input type="text" value={backendUrl} readonly class="input text-xs text-zinc-400 cursor-default"/>
      </div>
      <div>
        <p class="text-xs text-zinc-500 mb-1 font-medium">Frontend URL</p>
        <input type="text" value={frontendUrl} readonly class="input text-xs text-zinc-400 cursor-default"/>
      </div>
    </div>
  </div>

  <!-- About -->
  <div class="card text-xs text-zinc-500">
    <h2 class="text-sm font-semibold text-zinc-300 mb-3">About</h2>
    <div class="grid grid-cols-2 gap-2">
      <div><span class="text-zinc-400">Version</span><br/>{health?.version || '1.0.0'}</div>
      <div><span class="text-zinc-400">Stack</span><br/>SvelteKit + FastAPI</div>
      <div><span class="text-zinc-400">Database</span><br/>PostgreSQL 16 (Docker)</div>
      <div><span class="text-zinc-400">Instagram API</span><br/>Meta Graph API v21.0</div>
    </div>
    <div class="flex flex-wrap gap-2 mt-3">
      {#each ['Python 3.13', 'Node.js 22', 'FFmpeg 9.0.2', 'PostgreSQL 16', 'SQLAlchemy 2'] as chip}
        <span class="px-2 py-1 rounded bg-surface-elevated border border-surface-border">{chip}</span>
      {/each}
    </div>
  </div>
</div>
