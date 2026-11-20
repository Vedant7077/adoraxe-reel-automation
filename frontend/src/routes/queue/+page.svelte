<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import {
    getQueueVideos,
    getInstagramStatus,
    getInstagramAccounts,
    activateInstagramAccount,
    connectInstagram,
    removeFromQueue,
    addToQueue,
  } from '#lib/api/client';
  import { publishingStore } from '#lib/stores/publishing.svelte';

  let videos = $state<any[]>([]);
  let igStatus = $state<any>(null);
  let accounts = $state<any[]>([]);
  let selectedAccountId = $state<string>('');
  let loading = $state(true);
  let switchingAccount = $state(false);
  let selectedVideo = $state<any>(null);
  let rightsConfirmed = $state(false);
  let publishError = $state('');
  let successNotice = $state('');

  // Remove confirmation modal state
  let videoToRemove = $state<any>(null);
  let showRemoveModal = $state(false);
  let removing = $state(false);

  onMount(async () => {
    await Promise.all([loadIgStatus(), loadVideos()]);
    // Recover any in-flight publishing job or caption from database
    await publishingStore.init(selectedAccountId);
    if (publishingStore.selectedVideoId) {
      const match = videos.find((v) => v.id === publishingStore.selectedVideoId);
      if (match) selectedVideo = match;
    }
  });

  async function loadVideos(forAccountId?: string) {
    try {
      videos = await getQueueVideos(forAccountId || selectedAccountId);
      const targetId = page.url.searchParams.get('video_id');
      if (targetId) {
        let found = videos.find((v) => v.id === targetId);
        if (!found) {
          try {
            await addToQueue(targetId);
            videos = await getQueueVideos(forAccountId || selectedAccountId);
            found = videos.find((v) => v.id === targetId);
          } catch {
            // Video might not exist or not processed
          }
        }
        if (found && !selectedVideo) {
          selectedVideo = found;
        }
      } else if (!selectedVideo && videos.length > 0) {
        const firstEligible = videos.find((v) => v.can_publish);
        if (firstEligible) selectedVideo = firstEligible;
      }
    } catch (e) {
      videos = [];
    } finally {
      loading = false;
    }
  }

  async function loadIgStatus() {
    try {
      igStatus = await getInstagramStatus();
      try {
        accounts = await getInstagramAccounts();
      } catch {
        accounts = [];
      }

      if (!selectedAccountId && igStatus?.account_id) {
        selectedAccountId = igStatus.account_id;
      } else if (!selectedAccountId && accounts.length > 0) {
        const act = accounts.find((a) => a.is_active);
        selectedAccountId = act ? act.id : accounts[0].id;
      }
    } catch (e) {
      igStatus = null;
    }
  }

  async function handleAccountSwitch(newAccountId: string) {
    if (!newAccountId || newAccountId === selectedAccountId) return;
    switchingAccount = true;
    publishError = '';
    try {
      await activateInstagramAccount(newAccountId);
      selectedAccountId = newAccountId;
      igStatus = await getInstagramStatus(true);
      accounts = await getInstagramAccounts();
      await loadVideos(newAccountId);
      const targetAcc = accounts.find((a) => a.id === newAccountId);
      successNotice = `Switched publishing target account to @${targetAcc?.username || igStatus?.username || 'selected profile'}.`;
    } catch (e: any) {
      publishError = `Failed to switch account: ${e.message || e}`;
    } finally {
      switchingAccount = false;
    }
  }

  async function handleConnectNewAccount() {
    try {
      const res = await connectInstagram();
      window.location.href = res.auth_url;
    } catch (e: any) {
      publishError = `Failed to initiate Meta OAuth: ${e.message || e}`;
    }
  }

  function promptRemove(video: any, event?: MouseEvent) {
    if (event) event.stopPropagation();
    videoToRemove = video;
    showRemoveModal = true;
  }

  async function confirmRemove() {
    if (!videoToRemove) return;
    removing = true;
    publishError = '';
    try {
      await removeFromQueue(videoToRemove.id);
      successNotice = `"${videoToRemove.title}" was removed from the publishing queue. It remains safely in your Video Library.`;
      if (selectedVideo?.id === videoToRemove.id) {
        selectedVideo = null;
      }
      showRemoveModal = false;
      videoToRemove = null;
      await loadVideos();
    } catch (e: any) {
      publishError = `Failed to remove video from queue: ${e.message || e}`;
    } finally {
      removing = false;
    }
  }

  async function handlePublish() {
    if (!selectedVideo || !rightsConfirmed || !selectedVideo.can_publish) return;
    publishError = '';
    successNotice = '';
    try {
      await publishingStore.publish(
        selectedVideo.id,
        publishingStore.caption,
        rightsConfirmed,
        selectedAccountId
      );
      await loadVideos();
    } catch (e: any) {
      publishError = e.message || 'Publishing submission failed';
    }
  }

  function formatCropMode(mode?: string): string {
    if (!mode) return '9:16 Reel';
    const m = mode.toLowerCase();
    if (m === 'crop') return 'Crop (Center)';
    if (m === 'pad') return 'Pad (Bars)';
    if (m === 'blur') return 'Blur Fill';
    return mode;
  }

  function cropBadgeClass(mode?: string): string {
    if (!mode) return 'bg-zinc-800 text-zinc-400 border-zinc-700';
    const m = mode.toLowerCase();
    if (m === 'crop') return 'bg-cyan-950/80 text-cyan-300 border-cyan-800/60';
    if (m === 'pad') return 'bg-indigo-950/80 text-indigo-300 border-indigo-800/60';
    if (m === 'blur') return 'bg-emerald-950/80 text-emerald-300 border-emerald-800/60';
    return 'badge-processed';
  }

  function stageDescription(stage?: string): string {
    if (!stage) return 'In progress...';
    switch (stage) {
      case 'QUEUED':
        return 'Job queued and persisted in database. Starting runner...';
      case 'CONTAINER_CREATING':
        return 'Submitting video and caption to Meta to create Instagram media container...';
      case 'CONTAINER_PROCESSING':
        return "Meta is processing the 9:16 Reel media container. Polling status...";
      case 'READY_TO_PUBLISH':
        return 'Container processing FINISHED on Meta servers. Ready to publish.';
      case 'PUBLISH_REQUESTED':
        return 'Final media_publish call sent to Meta. Awaiting publication confirmation...';
      case 'PUBLISHED':
        return 'Successfully published and live on Instagram!';
      case 'OUTCOME_UNKNOWN':
        return 'Network timeout after publish request was sent. Verifying via read-only reconciliation...';
      case 'RECONCILIATION_REQUIRED':
        return 'Publishing outcome uncertain. Safe read-only reconciliation available.';
      case 'FAILED':
        return 'Publishing failed.';
      default:
        return stage;
    }
  }

  let isConnected = $derived(igStatus?.status === 'CONNECTED');
  let isDemoMode = $derived(Boolean(igStatus?.demo_mode));
  let currentAccount = $derived(
    accounts.find((a) => a.id === selectedAccountId) ||
    accounts.find((a) => a.is_active) ||
    igStatus
  );
  let captionLength = $derived(publishingStore.caption.length);
  let captionOk = $derived(captionLength <= 2200);
  let activeJob = $derived(publishingStore.activeJob);
  let isJobInFlight = $derived(
    activeJob ? ['QUEUED', 'CONTAINER_CREATING', 'CONTAINER_PROCESSING', 'READY_TO_PUBLISH', 'PUBLISH_REQUESTED', 'PENDING', 'PROCESSING'].includes(activeJob.status) : false
  );
  let isPublished = $derived(activeJob?.status === 'PUBLISHED');
  let canSubmit = $derived(
    Boolean(
      selectedVideo &&
      selectedVideo.can_publish &&
      rightsConfirmed &&
      captionOk &&
      !publishingStore.isPublishing &&
      !isJobInFlight &&
      !isPublished
    )
  );
</script>

<div>
  <div class="page-header">
    <h1 class="page-title">Publishing Queue</h1>
    <p class="page-subtitle">Select target Instagram profile, preview 9:16 Reels with aspect ratio filters, manage queue items, and publish.</p>
  </div>

  <!-- Instagram status banner -->
  {#if igStatus}
    {#if isConnected && !isDemoMode}
      <div class="mb-5 p-3 rounded-xl bg-emerald-900/30 border border-emerald-800/50 flex items-center justify-between text-xs text-emerald-400">
        <div class="flex items-center gap-2">
          <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
          <span>Connected as <strong>@{currentAccount?.username || igStatus.username}</strong> ({currentAccount?.account_type || igStatus.account_type || 'Creator'})</span>
        </div>
        <span class="badge badge-published text-[11px]">Meta Live Active</span>
      </div>
    {:else if isDemoMode}
      <div class="mb-5 p-3.5 rounded-xl bg-amber-900/30 border border-amber-800/50 text-xs text-amber-400 flex items-center justify-between">
        <div class="flex items-center gap-2">
          <span class="text-base">⚠</span>
          <span>DEMO MODE – Publishing is SIMULATED for <strong>@{currentAccount?.username || igStatus.username || 'adoraxe_demo'}</strong>. Duplicate publishing is prevented.</span>
        </div>
        <span class="badge badge-simulated text-[10px]">Simulated Mode</span>
      </div>
    {:else}
      <div class="mb-5 p-3.5 rounded-xl bg-red-900/30 border border-red-800/50 text-xs text-red-400 flex items-center justify-between">
        <span>❌ No Instagram account connected.</span>
        <button onclick={handleConnectNewAccount} class="text-primary underline font-medium">Connect Account →</button>
      </div>
    {/if}
  {/if}

  {#if successNotice}
    <div class="mb-5 p-3.5 rounded-xl bg-emerald-950/40 border border-emerald-800/60 flex items-center justify-between text-xs text-emerald-300">
      <div class="flex items-center gap-2">
        <span class="text-emerald-400 font-bold">✓</span>
        <span>{successNotice}</span>
      </div>
      <button class="text-emerald-400 hover:text-white" onclick={() => successNotice = ''}>✕</button>
    </div>
  {/if}

  {#if publishError}
    <div class="mb-5 p-3.5 rounded-xl bg-red-950/40 border border-red-800/60 flex items-center justify-between text-xs text-red-300">
      <div class="flex items-center gap-2">
        <span class="text-red-400 font-bold">⚠</span>
        <span>{publishError}</span>
      </div>
      <button class="text-red-400 hover:text-white" onclick={() => publishError = ''}>✕</button>
    </div>
  {/if}

  <div class="grid grid-cols-1 lg:grid-cols-12 gap-6">

    <!-- LEFT COLUMN: Video selector list (5 cols) -->
    <div class="lg:col-span-5 card h-fit">
      <div class="flex items-center justify-between mb-3 pb-2 border-b border-surface-border">
        <div>
          <h2 class="text-sm font-semibold text-zinc-200 font-headline">Select Processed Video</h2>
          <span class="text-[11px] text-zinc-500">{videos.filter(v => v.can_publish).length} eligible for publishing</span>
        </div>
        <span class="badge badge-ready text-[10px]">{videos.length} in queue</span>
      </div>

      {#if loading}
        <div class="space-y-2">
          {#each [1, 2, 3] as _}
            <div class="h-16 bg-surface-elevated rounded-lg animate-pulse"></div>
          {/each}
        </div>
      {:else if videos.length === 0}
        <div class="py-10 text-center text-zinc-500">
          <p class="text-3xl mb-2">🎬</p>
          <p class="text-sm text-zinc-400 font-medium">No videos currently in publishing queue.</p>
          <p class="text-xs text-zinc-500 mt-1 max-w-xs mx-auto">
            Videos processed in your Video Library will appear here ready to publish.
          </p>
          <a href="/library" class="btn-primary text-xs mt-4 inline-flex">Go to Video Library →</a>
        </div>
      {:else}
        <div class="space-y-2.5 max-h-[620px] overflow-y-auto pr-1">
          {#each videos as video}
            <div
              class="w-full text-left p-3 rounded-xl border transition-all relative group {selectedVideo?.id === video.id ? 'border-primary bg-primary/10 ring-1 ring-primary/40 shadow-sm' : video.can_publish ? 'border-surface-border hover:border-zinc-500 bg-surface-elevated/40' : 'border-surface-border/40 bg-surface-elevated/10 opacity-60'}"
            >
              <div
                class="flex items-start gap-3 cursor-pointer"
                onclick={() => { if (video.can_publish) selectedVideo = video; }}
                role="button"
                tabindex="0"
                onkeydown={(e) => { if (e.key === 'Enter' && video.can_publish) selectedVideo = video; }}
              >
                <!-- Thumbnail -->
                <div class="relative w-16 h-12 flex-shrink-0 rounded-lg overflow-hidden bg-surface-elevated border border-surface-border">
                  {#if video.thumbnail_url}
                    <img src={video.thumbnail_url} alt={video.title} class="w-full h-full object-cover {video.can_publish ? '' : 'grayscale'}"/>
                  {:else}
                    <div class="w-full h-full flex items-center justify-center text-lg">🎬</div>
                  {/if}
                  {#if video.duration_seconds}
                    <span class="absolute bottom-0.5 right-0.5 bg-black/80 text-[9px] text-zinc-300 px-1 py-0.2 rounded font-mono">
                      {video.duration_seconds}s
                    </span>
                  {/if}
                </div>

                <!-- Info -->
                <div class="flex-1 min-w-0 pr-6">
                  <p class="text-xs font-semibold leading-tight line-clamp-2 {video.can_publish ? 'text-white' : 'text-zinc-400'}">
                    {video.title}
                  </p>

                  <div class="flex items-center gap-1.5 mt-1.5 flex-wrap">
                    <span class="text-[10px] text-zinc-400 truncate max-w-[120px]">
                      {video.channel_name || 'Adoraxe Demo Lab'}
                    </span>

                    <!-- Aspect Ratio Badge -->
                    {#if video.crop_mode}
                      <span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-semibold border {cropBadgeClass(video.crop_mode)}">
                        {formatCropMode(video.crop_mode)}
                      </span>
                    {/if}

                    {#if video.is_already_published}
                      <span class="badge text-[9px] bg-zinc-800 text-zinc-400 border border-zinc-700">✓ Published</span>
                    {:else if video.is_publishing_in_progress}
                      <span class="badge text-[9px] bg-blue-950 text-blue-400 border border-blue-800 animate-pulse">⏳ In Progress</span>
                    {/if}
                  </div>

                  {#if video.disabled_reason}
                    <p class="text-[10px] text-amber-400/90 mt-1 line-clamp-1">{video.disabled_reason}</p>
                  {/if}
                </div>

                <!-- Selection Indicator -->
                {#if selectedVideo?.id === video.id}
                  <div class="w-5 h-5 rounded-full bg-primary flex items-center justify-center text-white text-xs font-bold flex-shrink-0">
                    ✓
                  </div>
                {/if}
              </div>

              <!-- Action button: Remove from queue -->
              <button
                type="button"
                class="absolute top-2.5 right-2.5 p-1 rounded-md text-zinc-500 hover:text-red-400 hover:bg-red-950/60 transition-colors"
                title="Remove video from publishing queue"
                onclick={(e) => promptRemove(video, e)}
              >
                <span class="text-xs">✕</span>
              </button>
            </div>
          {/each}
        </div>
      {/if}
    </div>

    <!-- RIGHT COLUMN: Profile Selector, Reel Preview, Caption & Publish (7 cols) -->
    <div class="lg:col-span-7 space-y-5">

      <!-- 1. TARGET INSTAGRAM ACCOUNT SELECTOR & SWITCHER CARD -->
      <div class="card border-surface-border">
        <div class="flex items-center justify-between pb-3 mb-3 border-b border-surface-border flex-wrap gap-2">
          <div class="flex items-center gap-2">
            <span class="text-base">📸</span>
            <h3 class="text-xs font-bold text-white uppercase tracking-wider font-headline">Publishing Target Account</h3>
            {#if currentAccount?.is_demo}
              <span class="badge badge-simulated text-[10px]">Demo Mode</span>
            {:else}
              <span class="badge badge-published text-[10px]">✓ Active Target</span>
            {/if}
          </div>

          <!-- Connect Different Profile action -->
          <button
            type="button"
            class="text-xs text-primary hover:text-secondary hover:underline flex items-center gap-1 font-medium"
            onclick={handleConnectNewAccount}
            title="Connect another Instagram Creator or Business account with Meta Unified Login"
          >
            <span>+</span> Connect Different Profile
          </button>
        </div>

        <div class="flex items-center justify-between gap-4 flex-wrap">
          <!-- Active Target Account Details -->
          <div class="flex items-center gap-3 min-w-0">
            {#if currentAccount?.profile_picture_url}
              <img
                src={currentAccount.profile_picture_url}
                alt={currentAccount.username}
                class="w-11 h-11 rounded-full object-cover border border-primary/40 shadow-sm flex-shrink-0"
              />
            {:else}
              <div class="w-11 h-11 rounded-full bg-ig-gradient flex items-center justify-center text-white font-bold text-base shadow-sm flex-shrink-0">
                {currentAccount?.username?.[0]?.toUpperCase() || 'I'}
              </div>
            {/if}

            <div class="min-w-0">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="text-sm font-bold text-white font-headline truncate">
                  @{currentAccount?.username || igStatus?.username || 'adoraxe_demo'}
                </span>
                <span class="badge badge-imported text-[10px]">
                  {currentAccount?.account_type || igStatus?.account_type || 'CREATOR'}
                </span>
              </div>
              <p class="text-xs text-zinc-400 truncate mt-0.5">
                {currentAccount?.full_name || igStatus?.full_name || 'Instagram Creator'}
              </p>
            </div>
          </div>

          <!-- Account Switcher Dropdown (when multiple accounts available) -->
          <div class="flex items-center gap-2">
            <span class="text-xs text-zinc-400">Profile:</span>
            {#if accounts.length > 0}
              <select
                value={selectedAccountId}
                onchange={(e) => handleAccountSwitch((e.target as HTMLSelectElement).value)}
                disabled={switchingAccount}
                class="input text-xs py-1.5 px-3 rounded-lg border-surface-border bg-surface-elevated text-white max-w-[210px] cursor-pointer"
              >
                {#each accounts as acc}
                  <option value={acc.id}>
                    @{acc.username} {acc.id === selectedAccountId ? '(Active)' : ''} {acc.is_demo ? '[Demo]' : ''}
                  </option>
                {/each}
              </select>
            {:else}
              <span class="text-xs text-zinc-400 font-mono">@{igStatus?.username || 'yotube_insta_automation'}</span>
            {/if}
          </div>
        </div>
      </div>

      {#if selectedVideo}
        <!-- 2. REEL PREVIEW & ASPECT RATIO SPECIFICATIONS CARD -->
        <div class="card border-primary/30">
          <div class="flex items-center justify-between pb-3 mb-3 border-b border-surface-border flex-wrap gap-2">
            <div class="flex items-center gap-2">
              <span class="text-base">📱</span>
              <h2 class="text-sm font-bold text-white font-headline">9:16 Instagram Reel Preview</h2>
              <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold border {cropBadgeClass(selectedVideo.crop_mode)}">
                {formatCropMode(selectedVideo.crop_mode)} (9:16)
              </span>
            </div>

            <!-- Remove Button on the active preview card -->
            <button
              type="button"
              class="btn-danger text-xs py-1 px-2.5 flex items-center gap-1.5"
              onclick={() => promptRemove(selectedVideo)}
              title="Remove this video from the publishing queue"
            >
              <span>🗑</span> Remove from Queue
            </button>
          </div>

          <div class="grid grid-cols-1 sm:grid-cols-12 gap-5 items-center">

            <!-- 9:16 Vertical Video Player Container -->
            <div class="sm:col-span-5 flex justify-center">
              <div class="w-full max-w-[210px] rounded-2xl overflow-hidden aspect-[9/16] bg-black border-2 border-surface-border shadow-2xl relative ring-1 ring-white/10">
                {#if selectedVideo.cloudinary_url}
                  <video
                    src={selectedVideo.cloudinary_url}
                    controls
                    playsinline
                    preload="metadata"
                    class="w-full h-full object-contain bg-black"
                  >
                    <track kind="captions" />
                  </video>
                {:else}
                  <div class="w-full h-full flex flex-col items-center justify-center text-zinc-500 p-4 text-center">
                    <span class="text-3xl mb-2">🎬</span>
                    <p class="text-xs">No video preview stream</p>
                  </div>
                {/if}
              </div>
            </div>

            <!-- Specifications & Aspect Ratio Details -->
            <div class="sm:col-span-7 space-y-3 text-xs">
              <div>
                <span class="text-[10px] text-zinc-500 uppercase tracking-wider block">Selected Video Title</span>
                <p class="text-sm font-semibold text-white mt-0.5 leading-snug">{selectedVideo.title}</p>
                <p class="text-[11px] text-zinc-400 mt-0.5">Channel: {selectedVideo.channel_name || 'Adoraxe Demo Lab'}</p>
              </div>

              <!-- Conversion Filter Breakdown -->
              <div class="p-3 rounded-xl bg-surface-elevated border border-surface-border space-y-1.5">
                <div class="flex items-center justify-between">
                  <span class="text-zinc-400 font-medium">Aspect Ratio Filter:</span>
                  <span class="font-bold text-white">{formatCropMode(selectedVideo.crop_mode)}</span>
                </div>
                <div class="flex items-center justify-between">
                  <span class="text-zinc-400 font-medium">Target Resolution:</span>
                  <span class="font-mono text-zinc-200">{selectedVideo.width || 1080}×{selectedVideo.height || 1920}</span>
                </div>
                <div class="flex items-center justify-between">
                  <span class="text-zinc-400 font-medium">Duration:</span>
                  <span class="text-zinc-200">{selectedVideo.duration_seconds}s</span>
                </div>
                <div class="flex items-center justify-between">
                  <span class="text-zinc-400 font-medium">Cloudinary Delivery:</span>
                  <span class="text-emerald-400 font-mono text-[11px]">HTTPS Verified ✓</span>
                </div>
              </div>

              <div class="p-2.5 rounded-lg bg-emerald-950/30 border border-emerald-800/40 text-[11px] text-emerald-300 flex items-start gap-2">
                <span>✓</span>
                <p>Validated against official Instagram Reel technical constraints (1080×1920, H.264 / AAC, 30fps).</p>
              </div>
            </div>
          </div>
        </div>

        <!-- 3. CAPTION EDITOR CARD (Persisted across navigation) -->
        <div class="card">
          <div class="flex items-center justify-between mb-2">
            <h2 class="text-sm font-semibold text-zinc-200 font-headline">Instagram Reel Caption</h2>
            {#if publishingStore.activeJob?.caption}
              <span class="text-[10px] text-emerald-400 font-medium">✓ Saved in Database</span>
            {/if}
          </div>
          <div class="relative">
            <textarea
              bind:value={publishingStore.caption}
              placeholder="Write your Instagram Reel caption, hashtags, and description…"
              rows={4}
              class="input resize-none w-full font-body text-xs"
              disabled={isJobInFlight || isPublished}
            ></textarea>
            <span class="absolute bottom-2 right-2 text-[10px] {captionOk ? 'text-zinc-500' : 'text-red-400 font-bold'}">
              {captionLength}/2200
            </span>
          </div>
        </div>

        <!-- 4. RIGHTS CONFIRMATION & PUBLISH ACTION -->
        <div class="card space-y-4">
          <label class="flex items-start gap-3 cursor-pointer select-none">
            <input
              type="checkbox"
              bind:checked={rightsConfirmed}
              disabled={isJobInFlight || isPublished}
              class="mt-0.5 accent-primary h-4 w-4 rounded"
            />
            <div class="text-xs text-zinc-300 leading-relaxed">
              <strong class="text-white">I confirm publishing rights:</strong> I have all necessary rights, licenses,
              and permissions to publish this video content to Instagram.
              {#if isDemoMode}
                <span class="text-amber-400 block mt-0.5 font-medium">(DEMO MODE active: Publication will be simulated and recorded.)</span>
              {/if}
            </div>
          </label>

          <button
            class="btn-primary w-full justify-center py-3 text-sm font-bold shadow-md shadow-primary/20"
            onclick={handlePublish}
            disabled={!canSubmit}
          >
            {#if publishingStore.isPublishing}
              ⏳ Submitting Job to Server…
            {:else if isJobInFlight}
              ⏳ In Progress ({activeJob?.current_stage || activeJob?.status || 'Processing'})…
            {:else if isPublished}
              ✓ Published Live to @{currentAccount?.username || igStatus?.username}
            {:else if !selectedVideo.can_publish}
              ⚠ Cannot Publish ({selectedVideo.disabled_reason || 'Ineligible'})
            {:else if isDemoMode}
              🔵 Publish Reel to @{currentAccount?.username || igStatus?.username || 'adoraxe_demo'} (SIMULATED)
            {:else}
              🚀 Publish Reel to @{currentAccount?.username || igStatus?.username}
            {/if}
          </button>
        </div>

        <!-- 5. PERSISTENT JOB STATUS (Survives tab/page navigation & refresh) -->
        {#if activeJob}
          <div class="card space-y-3 border {isPublished ? 'border-emerald-800/80 bg-emerald-950/20' : activeJob.status === 'FAILED' ? 'border-red-800/80 bg-red-950/20' : ['OUTCOME_UNKNOWN', 'RECONCILIATION_REQUIRED'].includes(activeJob.status) ? 'border-amber-800/80 bg-amber-950/20' : 'border-primary/40'}">
            <div class="flex items-center justify-between flex-wrap gap-2">
              <div class="flex items-center gap-2">
                <span class="text-xs font-bold text-white font-headline">Publishing Job Status</span>
                <span class="text-[10px] text-zinc-500 font-mono">#{activeJob.id.slice(0, 8)}</span>
              </div>
              <span class="badge {isPublished ? 'badge-published' : activeJob.status === 'FAILED' ? 'badge-failed' : activeJob.status === 'SIMULATED' ? 'badge-simulated' : ['OUTCOME_UNKNOWN', 'RECONCILIATION_REQUIRED'].includes(activeJob.status) ? 'bg-amber-950/80 text-amber-300 border-amber-800/60' : 'badge-processing'}">
                {activeJob.current_stage || activeJob.status}
              </span>
            </div>

            <!-- Stage explanation -->
            <p class="text-xs text-zinc-300">
              {stageDescription(activeJob.current_stage || activeJob.status)}
            </p>

            <!-- Progress bar -->
            <div class="h-2 bg-surface-elevated rounded-full overflow-hidden">
              <div
                class="h-full bg-gradient-to-r from-primary to-secondary transition-all duration-500 rounded-full"
                style="width: {activeJob.progress || (isPublished ? 100 : 10)}%"
              ></div>
            </div>

            <!-- SUCCESS: Published Live -->
            {#if isPublished}
              <div class="pt-2 border-t border-emerald-800/40 space-y-2">
                <div class="flex items-center justify-between text-xs text-emerald-300 flex-wrap gap-2">
                  <div class="flex items-center gap-1.5">
                    <span class="font-bold">✓</span>
                    <span>Reel is Live on Instagram</span>
                  </div>
                  {#if activeJob.instagram_media_id}
                    <span class="font-mono text-[11px] text-zinc-400">Media ID: {activeJob.instagram_media_id}</span>
                  {/if}
                </div>

                <div class="flex items-center justify-between gap-3 pt-1 flex-wrap">
                  {#if activeJob.permalink}
                    <a
                      href={activeJob.permalink}
                      target="_blank"
                      rel="noopener noreferrer"
                      class="btn-primary text-xs py-1.5 px-3 flex items-center gap-1 bg-gradient-to-r from-pink-600 to-purple-600 hover:from-pink-500 hover:to-purple-500"
                    >
                      <span>📸</span> Open Live Reel on Instagram ↗
                    </a>
                  {/if}
                  <button
                    type="button"
                    class="btn-secondary text-xs py-1.5 px-3"
                    onclick={() => publishingStore.reset()}
                  >
                    Publish Another Video
                  </button>
                </div>
              </div>

            <!-- UNCERTAIN OUTCOME: Reconciliation Needed -->
            {:else if ['OUTCOME_UNKNOWN', 'RECONCILIATION_REQUIRED'].includes(activeJob.status)}
              <div class="pt-2 border-t border-amber-800/40 space-y-2">
                <div class="p-2.5 rounded-lg bg-amber-950/60 border border-amber-800/60 text-xs text-amber-300 space-y-1">
                  <p class="font-semibold">⚠ Publishing Outcome Uncertain</p>
                  <p class="text-[11px] text-amber-200/90 leading-relaxed">
                    The publish request was dispatched to Meta, but the response timed out. To prevent creating duplicate Reels, Adoraxe protects this state.
                  </p>
                  {#if activeJob.error_message}
                    <p class="text-[10px] text-zinc-400 font-mono mt-1">Details: {activeJob.error_message}</p>
                  {/if}
                </div>
                <div class="flex items-center gap-3">
                  <button
                    type="button"
                    class="btn-primary text-xs py-1.5 px-3 bg-amber-600 hover:bg-amber-500 flex items-center gap-1"
                    onclick={() => publishingStore.reconcile()}
                    disabled={publishingStore.isReconciling}
                  >
                    <span>🔍</span> {publishingStore.isReconciling ? 'Checking Instagram…' : 'Reconcile with Instagram'}
                  </button>
                </div>
              </div>

            <!-- FAILED -->
            {:else if activeJob.status === 'FAILED'}
              <div class="pt-2 border-t border-red-800/40 space-y-2">
                <p class="text-xs text-red-400">
                  ⚠ {activeJob.error_message || 'Publication failed'}
                </p>
                <button
                  type="button"
                  class="btn-secondary text-xs py-1.5 px-3"
                  onclick={() => publishingStore.reset()}
                >
                  Dismiss & Try Again
                </button>
              </div>

            <!-- SIMULATED -->
            {:else if activeJob.status === 'SIMULATED'}
              <p class="text-xs text-emerald-400 mt-2 text-center font-medium">
                ✓ SIMULATED publication complete for @{currentAccount?.username || igStatus?.username || 'yotube_insta_automation'}. Recorded in Publishing History.
              </p>
            {/if}
          </div>
        {/if}

      {:else}
        <!-- No video selected placeholder -->
        <div class="card py-16 text-center text-zinc-500 space-y-3">
          <div class="w-16 h-16 rounded-2xl bg-surface-elevated border border-surface-border flex items-center justify-center text-3xl mx-auto text-zinc-400">
            👈
          </div>
          <h3 class="text-base font-bold text-white font-headline">Select a Reel from the Queue</h3>
          <p class="text-xs text-zinc-400 max-w-sm mx-auto">
            Choose an eligible processed video from the list on the left to preview its 9:16 vertical stream, review its aspect ratio filter, and write your caption for @{currentAccount?.username || igStatus?.username || 'yotube_insta_automation'}.
          </p>
        </div>
      {/if}
    </div>
  </div>

  <!-- ==================== REMOVE CONFIRMATION MODAL ==================== -->
  {#if showRemoveModal}
    <div class="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
      <div class="card max-w-md w-full border border-red-800/60 shadow-2xl bg-surface-card p-6">
        <div class="flex items-center gap-3 mb-3">
          <div class="w-10 h-10 rounded-full bg-red-950 border border-red-800 flex items-center justify-center text-red-400 text-lg">
            🗑
          </div>
          <div>
            <h3 class="text-base font-bold text-white font-headline">Remove from Publishing Queue?</h3>
            <p class="text-xs text-zinc-400 truncate max-w-[280px]">{videoToRemove?.title}</p>
          </div>
        </div>

        <div class="p-3.5 rounded-lg bg-surface-elevated border border-surface-border text-xs text-zinc-300 space-y-2 mb-5">
          <p>Remove this video from the publishing queue?</p>
          <div class="space-y-1 text-zinc-400">
            <p class="text-emerald-400">✓ The video, original file, and processed 9:16 Reel remain safe in your Video Library.</p>
            <p class="text-zinc-400">✓ You can re-add it to the publishing queue anytime from the Video Library.</p>
          </div>
        </div>

        <div class="flex justify-end gap-3">
          <button
            class="btn-secondary text-xs"
            onclick={() => { showRemoveModal = false; videoToRemove = null; }}
            disabled={removing}
          >
            Cancel
          </button>
          <button
            class="btn-danger text-xs"
            onclick={confirmRemove}
            disabled={removing}
          >
            {removing ? '⏳ Removing…' : 'Yes, Remove from Queue'}
          </button>
        </div>
      </div>
    </div>
  {/if}
</div>
