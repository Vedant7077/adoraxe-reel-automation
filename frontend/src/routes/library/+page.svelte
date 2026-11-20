<script lang="ts">
  import { onMount } from 'svelte';
  import { getVideos } from '#lib/api/client';
  import { goto } from '$app/navigation';

  let videos = $state<any[]>([]);
  let total = $state(0);
  let page = $state(1);
  let pageSize = $state(24);
  let totalPages = $state(1);
  let loading = $state(true);
  let search = $state('');
  let statusFilter = $state('');
  let selected = $state(new Set<string>());

  const statusFilters = [
    { value: '', label: 'All' },
    { value: 'IMPORTED', label: 'Imported' },
    { value: 'PROCESSED', label: 'Processed' },
    { value: 'READY', label: 'Ready' },
    { value: 'FAILED', label: 'Failed' },
  ];

  onMount(() => loadVideos());

  async function loadVideos() {
    loading = true;
    try {
      const res = await getVideos({ page, page_size: pageSize, status: statusFilter || undefined, search: search || undefined });
      videos = res.items || [];
      total = res.total || 0;
      totalPages = res.total_pages || 1;
    } catch (e) { videos = []; }
    finally { loading = false; }
  }

  function toggleSelect(id: string) {
    if (selected.has(id)) selected.delete(id);
    else selected.add(id);
    selected = new Set(selected);
  }

  function formatDuration(secs: number | null): string {
    if (!secs) return '—';
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    const s = secs % 60;
    return h > 0 ? `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`
                 : `${m}:${String(s).padStart(2,'0')}`;
  }

  function formatViews(n: number | null): string {
    if (!n) return '—';
    if (n >= 1_000_000) return (n/1_000_000).toFixed(1) + 'M';
    if (n >= 1_000)     return (n/1_000).toFixed(0) + 'K';
    return String(n);
  }

  function statusBadge(s: string) {
    const m: Record<string,string> = {
      IMPORTED:'badge-imported', PROCESSED:'badge-processed',
      READY:'badge-ready', FAILED:'badge-failed', PROCESSING:'badge-processing'
    };
    return 'badge ' + (m[s] || 'badge-simulated');
  }

  let debounceTimer: ReturnType<typeof setTimeout>;
  function onSearch() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => { page = 1; loadVideos(); }, 400);
  }
</script>

<div>
  <div class="page-header flex items-start justify-between">
    <div>
      <h1 class="page-title">Video Library <span class="ml-2 badge badge-processed">{total}</span></h1>
      <p class="page-subtitle">Browse and manage imported videos ready for Reel processing.</p>
    </div>
    <a href="/channels" class="btn-secondary text-xs">+ Import from YouTube</a>
  </div>

  <!-- Controls -->
  <div class="flex flex-col sm:flex-row gap-3 mb-4">
    <input
      type="text"
      bind:value={search}
      oninput={onSearch}
      placeholder="Search by title or channel…"
      class="input flex-1"
    />
    <div class="flex gap-2">
      {#each statusFilters as f}
        <button
          class="px-3 py-1.5 rounded-lg text-xs font-medium transition-colors {statusFilter === f.value ? 'bg-primary text-white' : 'bg-surface-card border border-surface-border text-zinc-400 hover:text-white'}"
          onclick={() => { statusFilter = f.value; page = 1; loadVideos(); }}
        >{f.label}</button>
      {/each}
    </div>
  </div>

  <!-- Bulk toolbar -->
  {#if selected.size > 0}
    <div class="flex items-center gap-3 mb-4 p-3 rounded-lg bg-primary/10 border border-primary/30">
      <span class="text-sm font-medium text-primary">{selected.size} selected</span>
      <button class="btn-primary text-xs py-1.5">Queue for Publishing</button>
      <button class="text-xs text-zinc-400 hover:text-white" onclick={() => selected = new Set()}>Deselect All</button>
    </div>
  {/if}

  <!-- Grid -->
  {#if loading}
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
      {#each Array(6) as _}
        <div class="card animate-pulse">
          <div class="aspect-video bg-surface-elevated rounded-lg mb-3"></div>
          <div class="h-4 bg-surface-elevated rounded mb-2 w-3/4"></div>
          <div class="h-3 bg-surface-elevated rounded w-1/2"></div>
        </div>
      {/each}
    </div>
  {:else if videos.length === 0}
    <div class="card py-16 text-center text-zinc-500">
      <p class="text-4xl mb-3">🎬</p>
      <p class="text-sm font-medium">No videos found</p>
      <p class="text-xs mt-1">Import a YouTube channel to populate your library.</p>
      <a href="/channels" class="btn-primary text-xs mt-4 inline-flex">Connect YouTube Channel</a>
    </div>
  {:else}
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
      {#each videos as video}
        <div class="card cursor-pointer group relative {selected.has(video.id) ? 'border-primary ring-1 ring-primary/30' : ''}"
             onclick={() => goto(`/library/${video.id}`)}>
          <!-- Checkbox -->
          <button
            class="absolute top-3 right-3 z-10 w-5 h-5 rounded border border-surface-border flex items-center justify-center transition-colors {selected.has(video.id) ? 'bg-primary border-primary' : 'bg-surface hover:border-primary'}"
            onclick={(e) => { e.stopPropagation(); toggleSelect(video.id); }}
          >{#if selected.has(video.id)}<span class="text-white text-xs">✓</span>{/if}</button>

          <!-- Thumbnail -->
          <div class="aspect-video rounded-lg overflow-hidden bg-surface-elevated mb-3 relative">
            {#if video.thumbnail_url}
              <img src={video.thumbnail_url} alt={video.title} class="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"/>
            {:else}
              <div class="w-full h-full flex items-center justify-center text-4xl">🎬</div>
            {/if}
            {#if video.duration_seconds}
              <span class="absolute bottom-2 right-2 bg-black/80 text-white text-xs px-1.5 py-0.5 rounded">
                {formatDuration(video.duration_seconds)}
              </span>
            {/if}
          </div>

          <!-- Info -->
          <h3 class="text-sm font-semibold text-white line-clamp-2 mb-1">{video.title}</h3>
          {#if video.channel_name}
            <p class="text-xs text-zinc-500 mb-2">{video.channel_name}</p>
          {/if}
          <div class="flex items-center justify-between">
            <div class="flex gap-3 text-xs text-zinc-500">
              {#if video.view_count}<span>👁 {formatViews(video.view_count)}</span>{/if}
            </div>
            <span class={statusBadge(video.status)}>{video.status}</span>
          </div>
        </div>
      {/each}
    </div>
  {/if}

  <!-- Pagination -->
  {#if totalPages > 1}
    <div class="flex items-center justify-between mt-6">
      <span class="text-xs text-zinc-500">Page {page} of {totalPages} · {total} videos</span>
      <div class="flex gap-2">
        <button class="btn-secondary text-xs py-1.5" disabled={page === 1} onclick={() => { page--; loadVideos(); }}>← Prev</button>
        <button class="btn-secondary text-xs py-1.5" disabled={page === totalPages} onclick={() => { page++; loadVideos(); }}>Next →</button>
      </div>
    </div>
  {/if}
</div>
