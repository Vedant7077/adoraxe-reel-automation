<script lang="ts">
  import { onMount } from 'svelte';
  import { getHealth, importChannel, getHistory, getVideos } from '#lib/api/client';
  import { formatLocalDateTime } from '#lib/utils/formatters';

  let health = $state<any>(null);
  let channelUrl = $state('');
  let importing = $state(false);
  let importError = $state('');
  let importSuccess = $state('');
  let recentActivity = $state<any[]>([]);
  let loadingActivity = $state(true);
  let totalVideos = $state(0);
  let totalPublished = $state(0);
  let processingJobs = $state(0);
  let failedJobs = $state(0);

  onMount(async () => {
    await refreshDashboard();
  });

  async function refreshDashboard() {
    try { health = await getHealth(); } catch { health = null; }
    try {
      const vids = await getVideos({ page: 1, page_size: 1 });
      totalVideos = vids.total || 0;
    } catch { totalVideos = 0; }

    try {
      const hist = await getHistory(1, 20);
      recentActivity = (hist.items || []).slice(0, 5);
      totalPublished = (hist.items || []).filter((i: any) => i.status === 'PUBLISHED').length;
      failedJobs = (hist.items || []).filter((i: any) => i.status === 'FAILED').length;
      processingJobs = (hist.items || []).filter((i: any) => i.status === 'PROCESSING' || i.status === 'PENDING').length;
    } catch { recentActivity = []; }
    finally { loadingActivity = false; }
  }

  async function handleImport() {
    if (!channelUrl.trim()) return;
    importing = true;
    importError = '';
    importSuccess = '';
    try {
      const ch = await importChannel(channelUrl.trim());
      importSuccess = `✓ Successfully imported "${ch.name}"! Available in Video Library.`;
      channelUrl = '';
      await refreshDashboard();
    } catch (e: any) {
      importError = e.message || 'Failed to import YouTube content';
    } finally {
      importing = false;
    }
  }

  function statusClass(s: string) {
    const map: Record<string,string> = {
      PUBLISHED:'badge-published', PROCESSING:'badge-processing',
      PENDING:'badge-pending', FAILED:'badge-failed', SIMULATED:'badge-simulated'
    };
    return 'badge ' + (map[s] || 'badge-simulated');
  }
</script>

<div>
  <div class="page-header flex items-start justify-between">
    <div>
      <h1 class="page-title">Dashboard Overview</h1>
      <p class="page-subtitle">Automate, slice, and publish YouTube videos directly into viral Instagram Reels.</p>
    </div>
    {#if health}
      <div class="flex items-center gap-2 text-xs">
        <span class="w-2 h-2 rounded-full {health.status === 'ok' ? 'bg-emerald-400' : 'bg-red-400'} animate-pulse"></span>
        <span class="text-zinc-400">Backend {health.status === 'ok' ? 'connected' : 'offline'}</span>
        {#if health.demo_mode}<span class="badge badge-pending">DEMO MODE</span>{/if}
      </div>
    {/if}
  </div>

  <!-- Stat Cards -->
  <div class="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
    {#each [
      { label: 'Imported Videos',  value: totalVideos || '0',    icon: '🎬', color: 'border-t-blue-500',   sub: 'In video library' },
      { label: 'Published Reels',  value: totalPublished || '0', icon: '📱', color: 'border-t-emerald-500', sub: 'Successfully posted' },
      { label: 'Processing Jobs',  value: processingJobs || '0', icon: '⚙️', color: 'border-t-blue-400',   sub: 'In progress' },
      { label: 'Failed Jobs',      value: failedJobs || '0',     icon: '⚠️', color: 'border-t-red-500',    sub: 'Need attention' },
    ] as stat}
      <div class="card border-t-2 {stat.color}">
        <div class="flex items-start justify-between">
          <div>
            <p class="text-xs text-zinc-500 mb-1">{stat.label}</p>
            <p class="text-3xl font-bold text-white font-headline">{stat.value}</p>
            <p class="text-xs text-zinc-600 mt-1">{stat.sub}</p>
          </div>
          <span class="text-2xl opacity-70">{stat.icon}</span>
        </div>
      </div>
    {/each}
  </div>

  <!-- Quick Ingest -->
  <div class="card mb-8 border border-surface-border hover:border-primary/30 transition-colors">
    <div class="flex items-start justify-between mb-2">
      <div>
        <h2 class="text-sm font-semibold text-zinc-200">Quick Ingest from YouTube</h2>
        <p class="text-xs text-zinc-400 mt-0.5">
          Paste any YouTube Channel URL, @creator handle, or direct video link.
        </p>
      </div>
      <span class="badge badge-imported text-xs">Channels • Handles • Video URLs</span>
    </div>

    <div class="flex gap-3 mt-3">
      <input
        type="url"
        bind:value={channelUrl}
        placeholder="e.g. https://youtu.be/Vmo4SakRHnI, https://youtube.com/@mkbhd, or @channelname"
        class="input flex-1"
        disabled={importing}
        onkeydown={(e) => { if (e.key === 'Enter') handleImport(); }}
      />
      <button class="btn-primary" onclick={handleImport} disabled={importing}>
        {importing ? '⏳ Fetching…' : '▶ Fetch Videos'}
      </button>
    </div>

    <div class="mt-2 flex items-center gap-2 text-xs text-zinc-500">
      <span>Supported formats:</span>
      <code class="text-zinc-400 font-mono text-[11px]">https://youtu.be/...</code>
      <span>•</span>
      <code class="text-zinc-400 font-mono text-[11px]">https://youtube.com/watch?v=...</code>
      <span>•</span>
      <code class="text-zinc-400 font-mono text-[11px]">@handle</code>
    </div>

    {#if importError}
      <div class="mt-3 p-3 rounded-lg bg-red-900/40 border border-red-800/50">
        <p class="text-xs text-red-400">⚠ {importError}</p>
      </div>
    {/if}

    {#if importSuccess}
      <div class="mt-3 p-3 rounded-lg bg-emerald-900/40 border border-emerald-800/50 flex items-center justify-between">
        <p class="text-xs text-emerald-400">{importSuccess}</p>
        <a href="/library" class="text-xs text-primary font-semibold hover:underline">Go to Library →</a>
      </div>
    {/if}
  </div>

  <!-- Recent Activity -->
  <div class="card">
    <div class="flex items-center justify-between mb-5">
      <h2 class="text-sm font-semibold text-zinc-200">Recent Activity</h2>
      <a href="/history" class="text-xs text-primary hover:underline">View Full History →</a>
    </div>

    {#if loadingActivity}
      <div class="space-y-3">
        {#each [1,2,3] as _}
          <div class="h-12 bg-surface-elevated rounded-lg animate-pulse"></div>
        {/each}
      </div>
    {:else if recentActivity.length === 0}
      <div class="py-12 text-center text-zinc-500">
        <p class="text-5xl mb-4">🚀</p>
        <p class="text-sm font-medium text-zinc-400">No activity yet</p>
        <p class="text-xs mt-1">Import a YouTube channel or video link above to get started.</p>
        <div class="mt-5 space-y-2 text-left max-w-xs mx-auto">
          {#each [
            ['1', 'Import a YouTube channel, @handle, or video link'],
            ['2', 'Select and inspect videos in Video Library'],
            ['3', 'Process to 9:16 Reel format with FFmpeg'],
            ['4', 'Publish or simulate publish to Instagram'],
          ] as [n, s]}
            <div class="flex items-center gap-2 text-xs text-zinc-600">
              <span class="w-5 h-5 rounded-full border border-surface-border flex items-center justify-center flex-shrink-0">{n}</span>
              {s}
            </div>
          {/each}
        </div>
      </div>
    {:else}
      <div class="space-y-0">
        {#each recentActivity as item}
          <div class="flex items-center gap-4 py-3 border-b border-surface-border last:border-0">
            <div class="flex-1 min-w-0">
              <p class="text-sm text-white truncate font-medium">{item.video_title}</p>
              <p class="text-xs text-zinc-500 flex items-center gap-2">
                <span>{item.instagram_username ? '@' + item.instagram_username : 'Demo'}</span>
                <span>•</span>
                <span>{formatLocalDateTime(item.published_at || item.created_at)}</span>
              </p>
            </div>
            <span class={statusClass(item.status)}>{item.status}</span>
          </div>
        {/each}
      </div>
    {/if}
  </div>
</div>
