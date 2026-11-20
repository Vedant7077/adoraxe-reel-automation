<script lang="ts">
  import { onMount } from 'svelte';
  import { getHistory, reconcilePublishJob } from '#lib/api/client';
  import { formatLocalDateTime } from '#lib/utils/formatters';

  let history = $state<any[]>([]);
  let total = $state(0);
  let page = $state(1);
  let loading = $state(true);
  let statusFilter = $state('');
  let reconcilingId = $state<string | null>(null);
  let reconcileNotice = $state('');

  const filters = [
    { value: '', label: 'All' },
    { value: 'PUBLISHED', label: 'Published' },
    { value: 'SIMULATED', label: 'Simulated' },
    { value: 'FAILED', label: 'Failed' },
  ];

  onMount(() => loadHistory());

  async function loadHistory() {
    loading = true;
    try {
      const res = await getHistory(page, 20, statusFilter || undefined);
      history = res.items || [];
      total = res.total || 0;
    } catch (e) {
      history = [];
    } finally {
      loading = false;
    }
  }

  async function handleReconcile(jobId: string) {
    if (!jobId) return;
    reconcilingId = jobId;
    reconcileNotice = '';
    try {
      const res = await reconcilePublishJob(jobId);
      reconcileNotice = res.message || 'Reconciliation finished';
      await loadHistory();
    } catch (e: any) {
      reconcileNotice = `Reconciliation error: ${e.message || e}`;
    } finally {
      reconcilingId = null;
    }
  }

  function statusClass(s: string) {
    const status = (s || '').toUpperCase();
    if (status === 'PUBLISHED') return 'badge badge-published';
    if (status === 'SIMULATED') return 'badge badge-simulated';
    if (status === 'FAILED') return 'badge badge-failed';
    if (['OUTCOME_UNKNOWN', 'RECONCILIATION_REQUIRED'].includes(status)) {
      return 'badge bg-amber-950/80 text-amber-300 border border-amber-800/60';
    }
    return 'badge badge-processing';
  }

  function formatDate(d: string | null): string {
    return formatLocalDateTime(d);
  }
</script>

<div>
  <div class="page-header flex items-start justify-between flex-wrap gap-3">
    <div>
      <h1 class="page-title">Publishing History</h1>
      <p class="page-subtitle">Track all Reel publishing jobs, Instagram permalinks, and verification outcomes.</p>
    </div>
    <div class="flex gap-2 flex-wrap">
      {#each filters as f}
        <button
          class="px-3 py-1.5 rounded-lg text-xs font-medium transition-colors {statusFilter === f.value ? 'bg-primary text-white' : 'bg-surface-card border border-surface-border text-zinc-400 hover:text-white'}"
          onclick={() => { statusFilter = f.value; page = 1; loadHistory(); }}
        >{f.label}</button>
      {/each}
      <button
        class="px-3 py-1.5 rounded-lg text-xs font-medium bg-surface-card border border-surface-border text-zinc-400 hover:text-white transition-colors"
        onclick={() => loadHistory()}
        title="Refresh history"
      >↻ Refresh</button>
    </div>
  </div>

  {#if reconcileNotice}
    <div class="mb-4 p-3 rounded-xl bg-surface-card border border-primary/40 flex items-center justify-between text-xs text-white">
      <span>{reconcileNotice}</span>
      <button class="text-zinc-400 hover:text-white" onclick={() => reconcileNotice = ''}>✕</button>
    </div>
  {/if}

  {#if loading}
    <div class="card animate-pulse space-y-3">
      {#each [1, 2, 3, 4] as _}
        <div class="h-14 bg-surface-elevated rounded-lg"></div>
      {/each}
    </div>
  {:else if history.length === 0}
    <div class="card py-16 text-center text-zinc-500">
      <p class="text-4xl mb-3">📋</p>
      <p class="text-sm font-medium">No publishing history found</p>
      <p class="text-xs mt-1">Published Reels will appear here with live Instagram permalinks and media IDs.</p>
    </div>
  {:else}
    <div class="card overflow-hidden p-0">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-surface-border">
            <th class="text-left text-xs text-zinc-500 font-medium px-5 py-3">Video Title</th>
            <th class="text-left text-xs text-zinc-500 font-medium px-5 py-3 hidden sm:table-cell">Account</th>
            <th class="text-left text-xs text-zinc-500 font-medium px-5 py-3 hidden md:table-cell">Date</th>
            <th class="text-left text-xs text-zinc-500 font-medium px-5 py-3">Status</th>
            <th class="text-left text-xs text-zinc-500 font-medium px-5 py-3 hidden lg:table-cell">Media ID</th>
            <th class="text-right text-xs text-zinc-500 font-medium px-5 py-3">Action</th>
          </tr>
        </thead>
        <tbody>
          {#each history as item}
            <tr class="border-b border-surface-border last:border-0 hover:bg-surface-elevated/50 transition-colors">
              <td class="px-5 py-3">
                <p class="text-white text-xs font-medium line-clamp-1">{item.video_title}</p>
                {#if item.error_message}
                  <p class="text-red-400 text-xs line-clamp-1 mt-0.5">⚠ {item.error_message}</p>
                {/if}
              </td>
              <td class="px-5 py-3 hidden sm:table-cell text-xs text-zinc-400">
                {item.instagram_username ? '@' + item.instagram_username : '—'}
              </td>
              <td class="px-5 py-3 hidden md:table-cell text-xs text-zinc-400 font-mono text-[11px]">
                {formatDate(item.published_at || item.created_at)}
              </td>
              <td class="px-5 py-3">
                <span class={statusClass(item.status)}>{item.status}</span>
              </td>
              <td class="px-5 py-3 hidden lg:table-cell">
                {#if item.instagram_media_id}
                  <code class="text-xs text-zinc-400 font-mono">{item.instagram_media_id.slice(0, 16)}…</code>
                {:else}
                  <span class="text-xs text-zinc-600">—</span>
                {/if}
              </td>
              <td class="px-5 py-3 text-right">
                {#if item.permalink}
                  <a
                    href={item.permalink}
                    target="_blank"
                    rel="noopener noreferrer"
                    class="text-xs text-primary hover:text-secondary hover:underline font-medium inline-flex items-center gap-1"
                  >
                    <span>📸</span> Open Reel ↗
                  </a>
                {:else if ['OUTCOME_UNKNOWN', 'RECONCILIATION_REQUIRED'].includes(item.status)}
                  <button
                    type="button"
                    class="btn-primary text-[11px] py-1 px-2.5 bg-amber-600 hover:bg-amber-500"
                    onclick={() => handleReconcile(item.publishing_job_id)}
                    disabled={reconcilingId === item.publishing_job_id}
                  >
                    {reconcilingId === item.publishing_job_id ? 'Checking…' : '🔍 Reconcile'}
                  </button>
                {:else}
                  <span class="text-xs text-zinc-600">—</span>
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>
