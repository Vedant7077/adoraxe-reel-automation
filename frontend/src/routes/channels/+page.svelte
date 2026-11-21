<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { importChannel, getChannels, getChannelVideos } from '#lib/api/client';
  import { formatLocalDate } from '#lib/utils/formatters';

  let channelUrl = $state('');
  let importing = $state(false);
  let importError = $state('');
  let channels = $state<any[]>([]);
  let loading = $state(true);
  let previewChannel = $state<any>(null);

  // Selected channel videos state
  let selectedChannel = $state<any>(null);
  let selectedChannelId = $state<string | null>(null);
  let previewVideos = $state<any[]>([]);
  let loadingVideos = $state(false);
  let videoError = $state('');
  let videoTotal = $state(0);

  onMount(async () => {
    await loadChannels();
  });

  async function loadChannels() {
    loading = true;
    try {
      channels = await getChannels();
      // Auto-load videos for the first channel if none is currently selected
      if (channels.length > 0 && !selectedChannelId) {
        await viewVideos(channels[0], false);
      }
    } catch (e) {
      channels = [];
    } finally {
      loading = false;
    }
  }

  async function handleImport() {
    if (!channelUrl.trim()) return;
    importing = true;
    importError = '';
    previewChannel = null;
    try {
      const ch = await importChannel(channelUrl.trim());
      previewChannel = ch;
      channelUrl = '';
      await loadChannels();
      // Auto-load videos for the newly imported channel
      if (ch?.id) {
        await viewVideos(ch, true);
      }
    } catch (e: any) {
      importError = e.message || 'Failed to fetch channel';
    } finally {
      importing = false;
    }
  }

  async function viewVideos(channel: any, shouldScroll = false) {
    selectedChannel = channel;
    selectedChannelId = channel.id;
    loadingVideos = true;
    videoError = '';
    previewVideos = [];
    try {
      const res = await getChannelVideos(channel.id);
      previewVideos = res.videos || [];
      videoTotal = res.total ?? previewVideos.length;
      if (shouldScroll && typeof document !== 'undefined') {
        setTimeout(() => {
          document.getElementById('channel-videos-section')?.scrollIntoView({ behavior: 'smooth' });
        }, 100);
      }
    } catch (e: any) {
      videoError = e.message || 'Failed to fetch channel videos';
      previewVideos = [];
    } finally {
      loadingVideos = false;
    }
  }

  function formatDuration(secs: number | null): string {
    if (!secs) return '—';
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    const s = secs % 60;
    return h > 0
      ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
      : `${m}:${String(s).padStart(2, '0')}`;
  }

  function formatViews(n: number | null): string {
    if (!n) return '—';
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
    if (n >= 1_000) return (n / 1_000).toFixed(0) + 'K';
    return String(n);
  }

  function statusBadge(s: string) {
    const m: Record<string, string> = {
      IMPORTED: 'badge-imported',
      PROCESSED: 'badge-processed',
      READY: 'badge-ready',
      FAILED: 'badge-failed',
      PROCESSING: 'badge-processing',
    };
    return 'badge ' + (m[s] || 'badge-simulated');
  }
</script>

<div>
  <div class="page-header">
    <h1 class="page-title">YouTube Channels</h1>
    <p class="page-subtitle">Connect YouTube channels to auto-ingest videos for Reel transformation.</p>
  </div>

  <!-- URL Input -->
  <div class="card mb-6">
    <label for="channel-url-input" class="block text-xs font-semibold text-zinc-400 mb-2 uppercase tracking-wide">YouTube Channel URL, @handle, or Video Link</label>
    <div class="flex gap-3">
      <div class="relative flex-1">
        <span class="absolute left-3 top-1/2 -translate-y-1/2 text-red-500">▶</span>
        <input
          id="channel-url-input"
          type="url"
          bind:value={channelUrl}
          placeholder="e.g. https://youtu.be/Vmo4SakRHnI, https://www.youtube.com/@mkbhd, or @channelname"
          class="input pl-9"
          disabled={importing}
          onkeydown={(e) => e.key === 'Enter' && handleImport()}
        />
      </div>
      <button class="btn-primary" onclick={handleImport} disabled={importing}>
        {importing ? '⏳ Fetching…' : '▶ Fetch Videos'}
      </button>
    </div>
    <div class="mt-2 flex items-center gap-2 text-xs text-zinc-500">
      <span>Supports:</span>
      <code class="text-zinc-400 font-mono text-[11px]">https://youtu.be/...</code>
      <span>•</span>
      <code class="text-zinc-400 font-mono text-[11px]">https://youtube.com/watch?v=...</code>
      <span>•</span>
      <code class="text-zinc-400 font-mono text-[11px]">@creator</code>
    </div>
    {#if importError}
      <div class="mt-3 p-3 rounded-lg bg-red-900/40 border border-red-800/50">
        <p class="text-xs text-red-400">⚠ {importError}</p>
        <p class="text-xs text-zinc-500 mt-1">Check the URL format or try a different channel handle.</p>
      </div>
    {/if}
  </div>

  <!-- Import Loading Skeleton -->
  {#if importing}
    <div class="card mb-6 animate-pulse">
      <div class="flex gap-4">
        <div class="w-16 h-16 rounded-full bg-surface-elevated"></div>
        <div class="flex-1 space-y-2">
          <div class="h-4 bg-surface-elevated rounded w-1/3"></div>
          <div class="h-3 bg-surface-elevated rounded w-1/4"></div>
          <div class="h-3 bg-surface-elevated rounded w-1/2"></div>
        </div>
      </div>
    </div>
  {:else if previewChannel}
    <div class="card mb-6 border border-primary/30">
      <div class="flex items-start gap-4">
        {#if previewChannel.thumbnail_url}
          <img src={previewChannel.thumbnail_url} alt={previewChannel.name} class="w-16 h-16 rounded-full object-cover"/>
        {:else}
          <div class="w-16 h-16 rounded-full bg-ig-gradient flex items-center justify-center text-white font-bold text-xl">
            {previewChannel.name[0]}
          </div>
        {/if}
        <div class="flex-1">
          <h3 class="font-semibold text-white">{previewChannel.name}</h3>
          {#if previewChannel.handle}<p class="text-xs text-zinc-400">{previewChannel.handle}</p>{/if}
          <div class="flex gap-4 mt-2 text-xs text-zinc-400">
            {#if previewChannel.subscriber_count}
              <span>👥 {(previewChannel.subscriber_count / 1_000_000).toFixed(1)}M subscribers</span>
            {/if}
            {#if previewChannel.video_count}
              <span>🎬 {previewChannel.video_count} videos</span>
            {/if}
          </div>
          {#if previewChannel.description}
            <p class="text-xs text-zinc-500 mt-2 line-clamp-2">{previewChannel.description}</p>
          {/if}
        </div>
        <button
          class="btn-secondary text-xs"
          disabled={loadingVideos && selectedChannelId === previewChannel.id}
          onclick={() => viewVideos(previewChannel)}
        >
          {loadingVideos && selectedChannelId === previewChannel.id ? '⏳ Loading…' : 'View Videos'}
        </button>
      </div>
    </div>
  {/if}

  <!-- Connected Channels -->
  <div class="flex items-center justify-between mb-3">
    <h2 class="text-sm font-semibold text-zinc-400 uppercase tracking-wide">Connected Channels</h2>
    <a href="/library" class="text-xs text-primary hover:underline">Browse All Videos in Video Library →</a>
  </div>
  {#if loading}
    <div class="space-y-3">
      {#each [1,2] as _}
        <div class="h-20 card animate-pulse"></div>
      {/each}
    </div>
  {:else if channels.length === 0}
    <div class="card py-14 text-center text-zinc-500">
      <p class="text-4xl mb-3">📭</p>
      <p class="text-sm font-medium">No channels added yet</p>
      <p class="text-xs mt-1">Paste a YouTube channel URL above to get started.</p>
    </div>
  {:else}
    <div class="space-y-3 mb-8">
      {#each channels as channel}
        <div
          class="card flex items-center gap-4 transition-all cursor-pointer {selectedChannelId === channel.id ? 'border-primary/60 ring-1 ring-primary/40 bg-surface-elevated/40' : 'hover:border-zinc-700'}"
          onclick={() => viewVideos(channel, true)}
        >
          {#if channel.thumbnail_url}
            <img src={channel.thumbnail_url} alt={channel.name} class="w-12 h-12 rounded-full object-cover flex-shrink-0"/>
          {:else}
            <div class="w-12 h-12 rounded-full bg-ig-gradient flex items-center justify-center text-white font-bold flex-shrink-0">
              {channel.name[0]}
            </div>
          {/if}
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2">
              <p class="font-semibold text-sm text-white truncate">{channel.name}</p>
              <span class="badge badge-imported text-xs">{channel.is_active ? 'Active' : 'Paused'}</span>
            </div>
            <div class="flex gap-3 text-xs text-zinc-500 mt-0.5">
              {#if channel.subscriber_count}<span>👥 {(channel.subscriber_count/1_000_000).toFixed(1)}M</span>{/if}
              {#if channel.video_count}<span>🎬 {channel.video_count} videos</span>{/if}
              {#if channel.last_synced_at}<span>🔄 {formatLocalDate(channel.last_synced_at)}</span>{/if}
            </div>
          </div>
          <button
            class="btn-secondary text-xs"
            disabled={loadingVideos && selectedChannelId === channel.id}
            onclick={(e) => { e.stopPropagation(); viewVideos(channel, true); }}
          >
            {loadingVideos && selectedChannelId === channel.id
              ? '⏳ Loading…'
              : (selectedChannelId === channel.id ? 'Viewing Videos ↓' : 'View Videos →')}
          </button>
        </div>
      {/each}
    </div>
  {/if}

  <!-- Channel Videos Gallery Section -->
  {#if selectedChannel}
    <div id="channel-videos-section" class="border-t border-surface-border pt-6 mt-6">
      <div class="flex items-center justify-between mb-4">
        <div>
          <h2 class="text-lg font-semibold text-white flex items-center gap-2">
            <span>Videos from</span>
            <span class="text-primary">{selectedChannel.name}</span>
            {#if previewVideos.length > 0}
              <span class="badge badge-processed ml-2">{previewVideos.length} loaded</span>
            {/if}
          </h2>
          <p class="text-xs text-zinc-400 mt-0.5">
            Click any video to inspect metadata, preview media, or process into a 9:16 Reel.
          </p>
        </div>
        <button
          class="btn-secondary text-xs"
          disabled={loadingVideos}
          onclick={() => viewVideos(selectedChannel)}
        >
          {loadingVideos ? '⏳ Fetching…' : '🔄 Refresh Videos'}
        </button>
      </div>

      {#if loadingVideos}
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {#each Array(6) as _}
            <div class="card animate-pulse">
              <div class="aspect-video bg-surface-elevated rounded-lg mb-3"></div>
              <div class="h-4 bg-surface-elevated rounded mb-2 w-3/4"></div>
              <div class="h-3 bg-surface-elevated rounded w-1/2"></div>
            </div>
          {/each}
        </div>
      {:else if videoError}
        <div class="p-4 rounded-xl bg-red-900/30 border border-red-800/50">
          <p class="text-sm font-medium text-red-400">⚠ Failed to load videos</p>
          <p class="text-xs text-zinc-400 mt-1">{videoError}</p>
          <button
            class="btn-secondary text-xs mt-3"
            onclick={() => viewVideos(selectedChannel)}
          >
            Try Again
          </button>
        </div>
      {:else if previewVideos.length === 0}
        <div class="card py-12 text-center text-zinc-500">
          <p class="text-3xl mb-2">🎬</p>
          <p class="text-sm font-medium">No videos found for this channel</p>
          <p class="text-xs mt-1">Click "Refresh Videos" to scrape YouTube for latest uploads.</p>
          <button
            class="btn-secondary text-xs mt-4 inline-flex"
            onclick={() => viewVideos(selectedChannel)}
          >
            Scrape YouTube Now
          </button>
        </div>
      {:else}
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {#each previewVideos as video}
            <div
              class="card cursor-pointer group hover:border-primary/50 transition-colors"
              onclick={() => goto(`/library/${video.id}`)}
            >
              <!-- Thumbnail -->
              <div class="aspect-video rounded-lg overflow-hidden bg-surface-elevated mb-3 relative">
                {#if video.thumbnail_url}
                  <img
                    src={video.thumbnail_url}
                    alt={video.title}
                    class="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                    loading="lazy"
                  />
                {:else}
                  <div class="w-full h-full flex items-center justify-center text-4xl">🎬</div>
                {/if}
                {#if video.duration_seconds}
                  <span class="absolute bottom-2 right-2 bg-black/80 text-white text-xs px-1.5 py-0.5 rounded font-mono">
                    {formatDuration(video.duration_seconds)}
                  </span>
                {/if}
              </div>

              <!-- Info -->
              <h3 class="text-sm font-semibold text-white line-clamp-2 mb-1 group-hover:text-primary transition-colors">
                {video.title}
              </h3>
              <div class="flex items-center justify-between text-xs text-zinc-500 mt-2">
                <span>👁 {formatViews(video.view_count)} views</span>
                <span class={statusBadge(video.status)}>{video.status}</span>
              </div>
            </div>
          {/each}
        </div>
      {/if}
    </div>
  {/if}
</div>
