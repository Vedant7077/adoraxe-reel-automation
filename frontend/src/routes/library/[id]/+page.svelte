<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import {
    getVideo,
    uploadVideo,
    processVideo,
    getJobStatus,
    getVideoStreamUrl,
    retrieveMedia,
    getYtDlpStatus,
  } from '#lib/api/client';
  import { goto } from '$app/navigation';

  let video = $state<any>(null);
  let loading = $state(true);
  let error = $state('');

  // Alerts
  let uploadSuccessMsg = $state('');
  let uploadErrorMsg = $state('');
  let retrievalSuccessMsg = $state('');
  let retrievalErrorMsg = $state('');
  let retrievalErrorAction = $state('');
  let lastErrorCode = $state('');

  // Remote Media Retrieval State
  let directMediaUrl = $state('');
  let autoProcessOnRetrieve = $state(false);
  let retrieving = $state(false);
  let showUrlInput = $state(false);

  // yt-dlp Retrieval State
  let ytdlpStatus = $state<any>(null);
  let ytdlpRightsConfirmed = $state(false);
  let autoProcessOnYtDlp = $state(false);
  let retrievingYtDlp = $state(false);
  let showYtDlpSection = $state(false);
  let retrievalStatus = $state<'IDLE' | 'DOWNLOAD_STARTED' | 'DOWNLOADED' | 'REUSED' | 'FAILED'>('IDLE');

  // Job & Upload State
  let jobId = $state<string | null>(null);
  let jobStatus = $state<any>(null);
  let polling = $state(false);
  let uploadInput = $state<HTMLInputElement>();
  let uploading = $state(false);
  let cropMode = $state<'blur' | 'pad' | 'crop'>('blur');
  let viewMode = $state<'compare' | 'original' | 'reel'>('compare');

  let videoId = $derived(page.params.id || '');
  let originalVideoVersion = $state(Date.now());
  let processedVideoVersion = $state(Date.now());

  let originalAsset = $derived(
    video?.assets?.find((a: any) => !a.is_processed) || null
  );
  let processedAsset = $derived(
    video?.assets?.find((a: any) => a.is_processed) || null
  );

  let originalVideoUrl = $derived.by(() => {
    if (!video?.has_uploaded_video && !originalAsset) return null;
    // Prefer direct verified Cloudinary HTTPS URL for HTML5 video playback without 401; fallback to local stream
    const cld = originalAsset?.cloudinary_url || (video?.original_video_url?.startsWith('http') ? video.original_video_url : null);
    if (cld && (cld.startsWith('https://') || cld.startsWith('http://'))) {
      return cld;
    }
    return `${getVideoStreamUrl(videoId, 'original')}?t=${originalVideoVersion}`;
  });

  let processedVideoUrl = $derived.by(() => {
    if (!video?.has_processed_reel && !processedAsset) return null;
    // Prefer direct verified Cloudinary HTTPS URL for HTML5 video playback without 401; fallback to local stream
    const cld = processedAsset?.cloudinary_url || (video?.processed_video_url?.startsWith('http') ? video.processed_video_url : null);
    if (cld && (cld.startsWith('https://') || cld.startsWith('http://'))) {
      return cld;
    }
    return `${getVideoStreamUrl(videoId, 'processed')}?t=${processedVideoVersion}`;
  });

  let activeAsset = $derived(
    video?.active_asset || originalAsset || (video?.assets && video.assets.length > 0 ? video.assets[0] : null)
  );
  let processedCropMode = $derived(
    video?.crop_mode || processedAsset?.crop_mode || null
  );

  function formatCropModeLabel(mode: string | null | undefined): string {
    if (!mode) return '9:16 Reel';
    switch (mode.toLowerCase()) {
      case 'blur':
        return 'Blur Fill';
      case 'pad':
        return 'Pad (Bars)';
      case 'crop':
        return 'Crop (Center)';
      default:
        return mode;
    }
  }

  function formatCropModeBadge(mode: string | null | undefined): string {
    if (!mode) return '';
    switch (mode.toLowerCase()) {
      case 'blur':
        return '✨ Blur Fill';
      case 'pad':
        return '⬛ Pad (Bars)';
      case 'crop':
        return '✂ Crop Center';
      default:
        return mode.toUpperCase();
    }
  }

  onMount(async () => {
    await Promise.all([loadVideo(), loadYtDlpStatus()]);
  });

  async function loadVideo() {
    try {
      video = await getVideo(videoId);
    } catch (e: any) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  async function loadYtDlpStatus() {
    try {
      ytdlpStatus = await getYtDlpStatus();
    } catch (e: any) {
      console.warn('Could not load yt-dlp status:', e);
    }
  }

  async function handleYtDlpRetrieve() {
    if (!ytdlpRightsConfirmed) {
      retrievalStatus = 'FAILED';
      lastErrorCode = 'DOWNLOAD_NOT_AUTHORIZED';
      retrievalErrorMsg = 'You must confirm content ownership or authorization to retrieve this video.';
      retrievalErrorAction = 'Please check the authorization confirmation box below before proceeding.';
      return;
    }
    retrievalErrorMsg = '';
    retrievalErrorAction = '';
    lastErrorCode = '';
    retrievalSuccessMsg = '';
    uploadErrorMsg = '';
    uploadSuccessMsg = '';
    retrievingYtDlp = true;
    retrievalStatus = 'DOWNLOAD_STARTED';
    try {
      const res = await retrieveMedia(videoId, {
        useYtDlp: true,
        rightsConfirmed: true,
        autoProcess: autoProcessOnYtDlp,
        cropMode: cropMode,
      });
      retrievalStatus = res.status === 'REUSED' ? 'REUSED' : 'DOWNLOADED';
      retrievalSuccessMsg = `✓ [${retrievalStatus}] ${res.message} (${res.width || '—'}×${res.height || '—'}, ${formatDuration(res.duration_seconds)})`;
      await loadVideo();
      if (res.job_id) {
        jobId = res.job_id;
        polling = true;
        pollJob();
      }
    } catch (e: any) {
      retrievalStatus = 'FAILED';
      lastErrorCode = e.code || 'FAILED';
      retrievalErrorMsg = e.message || 'YouTube media retrieval failed';
      retrievalErrorAction = e.action || '';
    } finally {
      retrievingYtDlp = false;
    }
  }

  async function handleRetrieveMedia() {
    retrievalErrorMsg = '';
    retrievalErrorAction = '';
    lastErrorCode = '';
    retrievalSuccessMsg = '';
    uploadErrorMsg = '';
    uploadSuccessMsg = '';
    retrieving = true;
    try {
      const res = await retrieveMedia(
        videoId,
        directMediaUrl.trim() || undefined,
        autoProcessOnRetrieve,
        cropMode
      );
      retrievalSuccessMsg = `✓ ${res.message} (${res.width || '—'}×${res.height || '—'}, ${formatDuration(res.duration_seconds)})`;
      directMediaUrl = '';
      showUrlInput = false;
      await loadVideo();
      if (res.job_id) {
        jobId = res.job_id;
        polling = true;
        pollJob();
      }
    } catch (e: any) {
      lastErrorCode = e.code || 'FAILED';
      retrievalErrorMsg = e.message || 'Media retrieval failed';
      retrievalErrorAction = e.action || '';
    } finally {
      retrieving = false;
    }
  }

  async function handleUpload(e: Event) {
    const input = e.target as HTMLInputElement;
    if (!input.files?.length) return;
    const file = input.files[0];
    uploading = true;
    uploadErrorMsg = '';
    uploadSuccessMsg = '';
    retrievalErrorMsg = '';
    retrievalSuccessMsg = '';
    try {
      await uploadVideo(file, videoId);
      uploadSuccessMsg = `✓ Successfully uploaded "${file.name}". Original preview is now playable.`;
      await loadVideo();
      input.value = '';
    } catch (e: any) {
      uploadErrorMsg = e.message || 'Upload failed';
    } finally {
      uploading = false;
    }
  }

  async function handleProcess() {
    uploadErrorMsg = '';
    uploadSuccessMsg = '';
    retrievalErrorMsg = '';
    retrievalSuccessMsg = '';
    try {
      const res = await processVideo(videoId, cropMode);
      jobId = res.job_id;
      polling = true;
      pollJob();
    } catch (e: any) {
      error = e.message;
    }
  }

  async function pollJob() {
    if (!jobId || !polling) return;
    try {
      jobStatus = await getJobStatus(jobId);
      if (jobStatus.status === 'FINISHED' || jobStatus.status === 'FAILED') {
        polling = false;
        if (jobStatus.status === 'FINISHED') {
          processedVideoVersion = Date.now();
          await loadVideo();
          uploadSuccessMsg = '✓ 9:16 Reel processed successfully! Processed video is now playable.';
        } else if (jobStatus.status === 'FAILED') {
          await loadVideo();
          uploadErrorMsg = `Processing failed: ${jobStatus.error_message || 'Unknown error'}`;
        }
      } else {
        setTimeout(pollJob, 2000);
      }
    } catch (e) {
      polling = false;
    }
  }

  function formatDuration(secs: number | null): string {
    if (!secs) return '—';
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    const s = secs % 60;
    return h > 0 ? `${h}h ${m}m ${s}s` : `${m}m ${s}s`;
  }

  function formatBytes(bytes: number | null): string {
    if (!bytes) return '—';
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
</script>

<div>
  <!-- Back navigation -->
  <button class="text-xs text-zinc-400 hover:text-white mb-6 flex items-center gap-1 transition-colors" onclick={() => goto('/library')}>
    ← Back to Video Library
  </button>

  {#if loading}
    <div class="card animate-pulse space-y-4">
      <div class="aspect-video bg-surface-elevated rounded-xl"></div>
      <div class="h-6 bg-surface-elevated rounded w-2/3"></div>
      <div class="h-4 bg-surface-elevated rounded w-1/3"></div>
    </div>
  {:else if error}
    <div class="card border-red-800/50 bg-red-900/20">
      <p class="text-red-400 text-sm">⚠ {error}</p>
      <button class="btn-secondary text-xs mt-3" onclick={() => goto('/library')}>← Back to Library</button>
    </div>
  {:else if video}
    <div class="grid lg:grid-cols-5 gap-6">
      <!-- Left Column: Video Players + Comparison + Metadata (3 Cols) -->
      <div class="lg:col-span-3 space-y-6">

        <!-- Top Alerts for Retrieval / Upload / Processing -->
        {#if retrievalSuccessMsg}
          <div class="p-3 rounded-lg bg-emerald-950/40 border border-emerald-800/50 flex items-center justify-between text-xs text-emerald-300">
            <span>{retrievalSuccessMsg}</span>
            <button class="text-emerald-400 hover:text-white" onclick={() => retrievalSuccessMsg = ''}>✕</button>
          </div>
        {/if}
        {#if retrievalErrorMsg}
          <div class="p-3.5 rounded-lg bg-red-950/50 border border-red-800/60 space-y-2 text-xs text-red-200">
            <div class="flex items-center justify-between font-semibold text-red-300">
              <span class="flex items-center gap-1.5">
                <span>⚠</span> Retrieval Notice
                {#if lastErrorCode}
                  <span class="px-1.5 py-0.5 rounded font-mono text-[10px] bg-red-900/60 text-red-300 border border-red-700/50">
                    {lastErrorCode}
                  </span>
                {/if}
              </span>
              <button class="text-red-400 hover:text-white" onclick={() => retrievalErrorMsg = ''}>✕</button>
            </div>
            <p class="text-red-300/90 leading-relaxed">{retrievalErrorMsg}</p>
            {#if retrievalErrorAction}
              <div class="p-2 rounded bg-red-900/30 border border-red-800/40 text-red-300 font-mono text-[11px]">
                {retrievalErrorAction}
              </div>
            {/if}
            <div class="pt-1.5 flex items-center justify-between border-t border-red-900/50">
              <p class="text-zinc-400 text-[11px]">
                Fallback: Upload the video file directly or supply an authorized direct MP4/MOV URL.
              </p>
              <button
                type="button"
                onclick={() => uploadInput?.click()}
                class="px-2.5 py-1 text-xs rounded bg-surface hover:bg-surface-elevated text-white border border-zinc-700 hover:border-zinc-500 transition-colors flex items-center gap-1 font-medium"
              >
                <span>📁</span> Use Manual File Upload
              </button>
            </div>
          </div>
        {/if}
        {#if uploadSuccessMsg}
          <div class="p-3 rounded-lg bg-emerald-950/40 border border-emerald-800/50 flex items-center justify-between text-xs text-emerald-300">
            <span>{uploadSuccessMsg}</span>
            <button class="text-emerald-400 hover:text-white" onclick={() => uploadSuccessMsg = ''}>✕</button>
          </div>
        {/if}
        {#if uploadErrorMsg}
          <div class="p-3 rounded-lg bg-red-950/40 border border-red-800/50 flex items-center justify-between text-xs text-red-300">
            <span>⚠ {uploadErrorMsg}</span>
            <button class="text-red-400 hover:text-white" onclick={() => uploadErrorMsg = ''}>✕</button>
          </div>
        {/if}

        <!-- Video Player Section Header & View Mode Switcher -->
        <div class="card p-4">
          <div class="flex flex-wrap items-center justify-between gap-3 mb-4 pb-3 border-b border-surface-border">
            <div>
              <h2 class="text-sm font-semibold text-white flex items-center gap-2">
                <span>📹</span> Video Playback & Comparison
              </h2>
              <p class="text-xs text-zinc-400 mt-0.5">
                Inspect and compare the original source video with the 9:16 Instagram Reel.
              </p>
            </div>
            <!-- View Mode Switcher -->
            <div class="inline-flex rounded-lg bg-surface-elevated p-1 border border-surface-border text-xs">
              <button
                class="px-2.5 py-1 rounded-md transition-colors {viewMode === 'compare' ? 'bg-primary text-white font-medium shadow-sm' : 'text-zinc-400 hover:text-white'}"
                onclick={() => viewMode = 'compare'}>
                Side-by-Side
              </button>
              <button
                class="px-2.5 py-1 rounded-md transition-colors {viewMode === 'original' ? 'bg-primary text-white font-medium shadow-sm' : 'text-zinc-400 hover:text-white'}"
                onclick={() => viewMode = 'original'}>
                Original (16:9)
              </button>
              <button
                class="px-2.5 py-1 rounded-md transition-colors {viewMode === 'reel' ? 'bg-primary text-white font-medium shadow-sm' : 'text-zinc-400 hover:text-white'}"
                onclick={() => viewMode = 'reel'}>
                Reel (9:16)
              </button>
            </div>
          </div>

          <!-- Mode: Compare (Side-by-Side) -->
          {#if viewMode === 'compare'}
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4 items-start">
              <!-- Left: Original Source Video -->
              <div class="space-y-2">
                <div class="flex items-center justify-between text-xs px-1">
                  <span class="font-medium text-zinc-300 flex items-center gap-1.5">
                    <span>🎬</span> Original Source
                  </span>
                  {#if video.has_uploaded_video}
                    <span class="badge badge-published text-[10px]">Playable</span>
                  {:else}
                    <span class="badge text-[10px] bg-zinc-800 text-zinc-400">Media Not Acquired</span>
                  {/if}
                </div>

                {#if video.has_uploaded_video && originalVideoUrl}
                  <div class="rounded-xl overflow-hidden bg-black border border-surface-border aspect-video flex items-center justify-center relative shadow-lg">
                    {#key originalVideoUrl}
                      <video
                        controls
                        playsinline
                        preload="metadata"
                        class="w-full h-full object-contain"
                        src={originalVideoUrl}
                      >
                        <track kind="captions" />
                      </video>
                    {/key}
                  </div>
                  <div class="p-2.5 rounded-lg bg-surface-elevated/70 border border-surface-border text-xs space-y-1">
                    <div class="flex items-center justify-between text-zinc-400">
                      <span class="text-white font-medium truncate max-w-[150px]">{activeAsset?.original_filename || 'original.mp4'}</span>
                      <span class="text-zinc-500">{activeAsset?.codec || 'H.264'}</span>
                    </div>
                    <div class="flex items-center justify-between text-zinc-500 text-[11px]">
                      <span>{activeAsset?.width && activeAsset?.height ? `${activeAsset.width}×${activeAsset.height}` : 'Source Resolution'}</span>
                      <span>{formatBytes(activeAsset?.file_size_bytes)}</span>
                    </div>
                  </div>
                {:else}
                  <div class="rounded-xl border-2 border-dashed border-surface-border bg-surface-elevated/40 p-6 flex flex-col items-center justify-center text-center aspect-video">
                    <span class="text-3xl mb-2">📁</span>
                    <h3 class="text-xs font-semibold text-white">Media Not Acquired Yet</h3>
                    <p class="text-[11px] text-zinc-500 mt-1 max-w-[240px]">Acquire authorized media via yt-dlp stream, direct URL, or file upload in the pipeline panel.</p>
                    <div class="flex flex-wrap gap-1.5 mt-3 justify-center">
                      <button class="btn-primary text-xs py-1 px-2" onclick={() => showYtDlpSection = true}>
                        ▶ yt-dlp
                      </button>
                      <button class="btn-secondary text-xs py-1 px-2" onclick={() => showUrlInput = true}>
                        🌐 Direct URL
                      </button>
                      <button class="btn-secondary text-xs py-1 px-2" onclick={() => uploadInput?.click()}>
                        📁 Upload
                      </button>
                    </div>
                  </div>
                {/if}
              </div>

              <!-- Right: Processed 9:16 Reel -->
              <div class="space-y-2">
                <div class="flex items-center justify-between text-xs px-1">
                  <span class="font-medium text-zinc-300 flex items-center gap-1.5">
                    <span>📱</span> Processed 9:16 Reel
                  </span>
                  {#if video.has_processed_reel}
                    <div class="flex items-center gap-1.5">
                      {#if processedCropMode}
                        <span class="badge text-[10px] bg-primary/20 text-primary border border-primary/30 font-medium">
                          {formatCropModeBadge(processedCropMode)}
                        </span>
                      {/if}
                      <span class="badge badge-published text-[10px]">
                        {processedAsset?.width && processedAsset?.height ? `${processedAsset.width}×${processedAsset.height}` : '1080×1920'}
                      </span>
                    </div>
                  {:else if polling}
                    <span class="badge text-[10px] bg-blue-900/40 text-blue-300 border border-blue-700 animate-pulse">Processing</span>
                  {:else}
                    <span class="badge text-[10px] bg-zinc-800 text-zinc-400">Not Generated</span>
                  {/if}
                </div>

                {#if video.has_processed_reel && processedVideoUrl}
                  <div class="rounded-xl overflow-hidden bg-black border border-primary/40 aspect-[9/16] max-h-[340px] mx-auto flex items-center justify-center relative shadow-2xl ring-1 ring-primary/20">
                    {#key processedVideoUrl}
                      <video
                        controls
                        playsinline
                        preload="metadata"
                        class="w-full h-full object-contain"
                        src={processedVideoUrl}
                      >
                        <track kind="captions" />
                      </video>
                    {/key}
                  </div>
                  <div class="p-2.5 rounded-lg bg-surface-elevated/70 border border-surface-border text-xs space-y-1">
                    <div class="flex items-center justify-between text-zinc-400">
                      <span class="text-emerald-400 font-medium">✓ Instagram Reel Ready</span>
                      <span class="text-zinc-300 font-medium">{formatCropModeLabel(processedCropMode)} (9:16)</span>
                    </div>
                    <div class="flex items-center justify-between text-zinc-500 text-[11px]">
                      <span>{processedAsset?.width && processedAsset?.height ? `${processedAsset.width}×${processedAsset.height}` : '1080×1920'} • 30fps</span>
                      <span>H.264 / AAC</span>
                    </div>
                  </div>
                {:else if polling}
                  <div class="rounded-xl border border-primary/30 bg-surface-elevated/60 p-6 flex flex-col items-center justify-center text-center aspect-[9/16] max-h-[340px] mx-auto">
                    <div class="w-8 h-8 rounded-full border-2 border-primary border-t-transparent animate-spin mb-3"></div>
                    <h3 class="text-xs font-semibold text-white">Converting Reel...</h3>
                    <p class="text-[11px] text-zinc-400 mt-1">Applying 9:16 crop with FFmpeg</p>
                    <span class="text-xs font-mono text-primary mt-2">{jobStatus?.progress || 0}%</span>
                  </div>
                {:else}
                  <div class="rounded-xl border-2 border-dashed border-surface-border bg-surface-elevated/40 p-6 flex flex-col items-center justify-center text-center aspect-[9/16] max-h-[340px] mx-auto">
                    <span class="text-2xl mb-2">⚡</span>
                    <h3 class="text-xs font-semibold text-white">Reel Not Generated Yet</h3>
                    <p class="text-[11px] text-zinc-500 mt-1 max-w-[180px]">Select crop mode and click "Process as Reel" to convert.</p>
                    {#if video.has_uploaded_video}
                      <button class="btn-primary text-xs mt-3 flex items-center gap-1.5" onclick={handleProcess}>
                        ⚡ Process Now
                      </button>
                    {/if}
                  </div>
                {/if}
              </div>
            </div>

          <!-- Mode: Original Video Full Width -->
          {:else if viewMode === 'original'}
            <div class="space-y-3">
              <div class="flex items-center justify-between text-xs">
                <span class="font-medium text-zinc-300">Original Source Video (Full Width)</span>
                {#if video.has_uploaded_video}
                  <span class="badge badge-published text-[10px]">Playable</span>
                {/if}
              </div>
              {#if video.has_uploaded_video && originalVideoUrl}
                <div class="rounded-xl overflow-hidden bg-black border border-surface-border aspect-video flex items-center justify-center relative shadow-lg">
                  {#key originalVideoUrl}
                    <video
                      controls
                      playsinline
                      preload="metadata"
                      class="w-full h-full object-contain"
                      src={originalVideoUrl}
                    >
                      <track kind="captions" />
                    </video>
                  {/key}
                </div>
              {:else}
                <div class="rounded-xl border-2 border-dashed border-surface-border bg-surface-elevated/40 p-10 flex flex-col items-center justify-center text-center aspect-video">
                  <span class="text-4xl mb-3">📁</span>
                  <h3 class="text-sm font-semibold text-white">Original Video File Not Acquired</h3>
                  <p class="text-xs text-zinc-500 mt-1 max-w-sm">Retrieve authorized media or upload a video file in the pipeline panel to stream and preview.</p>
                  <div class="flex gap-3 mt-4">
                    <button class="btn-primary text-xs flex items-center gap-1.5" onclick={() => showUrlInput = true}>
                      🌐 Retrieve Remote Media
                    </button>
                    <button class="btn-secondary text-xs flex items-center gap-2" onclick={() => uploadInput?.click()}>
                      📁 Upload Video File
                    </button>
                  </div>
                </div>
              {/if}
            </div>

          <!-- Mode: Processed Reel Full Height -->
          {:else if viewMode === 'reel'}
            <div class="space-y-3">
              <div class="flex items-center justify-between text-xs">
                <span class="font-medium text-zinc-300">9:16 Vertical Instagram Reel</span>
                {#if video.has_processed_reel}
                  <div class="flex items-center gap-1.5">
                    {#if processedCropMode}
                      <span class="badge text-[10px] bg-primary/20 text-primary border border-primary/30 font-medium">
                        {formatCropModeBadge(processedCropMode)}
                      </span>
                    {/if}
                    <span class="badge badge-published text-[10px]">
                      {processedAsset?.width && processedAsset?.height ? `${processedAsset.width}×${processedAsset.height}` : '1080×1920'} • 30fps
                    </span>
                  </div>
                {/if}
              </div>
              {#if video.has_processed_reel && processedVideoUrl}
                <div class="rounded-2xl overflow-hidden bg-black border border-primary/40 aspect-[9/16] max-h-[500px] mx-auto flex items-center justify-center relative shadow-2xl ring-1 ring-primary/20">
                  {#key processedVideoUrl}
                    <video
                      controls
                      playsinline
                      preload="metadata"
                      class="w-full h-full object-contain"
                      src={processedVideoUrl}
                    >
                      <track kind="captions" />
                    </video>
                  {/key}
                </div>
              {:else}
                <div class="rounded-2xl border-2 border-dashed border-surface-border bg-surface-elevated/40 p-10 flex flex-col items-center justify-center text-center aspect-[9/16] max-h-[460px] mx-auto">
                  <span class="text-4xl mb-3">⚡</span>
                  <h3 class="text-sm font-semibold text-white">Reel Not Generated</h3>
                  <p class="text-xs text-zinc-500 mt-1 max-w-xs">Convert this video into 9:16 format with FFmpeg to enable Reel playback.</p>
                  {#if video.has_uploaded_video}
                    <button class="btn-primary text-xs mt-4" onclick={handleProcess}>⚡ Process as Reel</button>
                  {/if}
                </div>
              {/if}
            </div>
          {/if}
        </div>

        <!-- Title & Metadata Info -->
        <div>
          <h1 class="text-xl font-bold text-white font-headline">{video.title}</h1>
          {#if video.channel_name}
            <p class="text-sm text-zinc-400 mt-1">{video.channel_name}</p>
          {/if}
          <div class="flex items-center gap-2 mt-2 flex-wrap">
            <span class="badge {video.status === 'PROCESSED' ? 'badge-processed' : video.status === 'IMPORTED' ? 'badge-imported' : 'badge-simulated'}">{video.status}</span>
            {#if video.has_uploaded_video}
              <span class="badge badge-published">Source Media Ready</span>
            {:else}
              <span class="badge bg-amber-950/70 border border-amber-800/60 text-amber-300 text-xs">Media Required</span>
            {/if}
            {#if video.has_processed_reel}
              <span class="badge badge-published">
                9:16 Reel Ready {#if processedCropMode}• {formatCropModeLabel(processedCropMode)}{/if}
              </span>
            {/if}
          </div>
        </div>

        <!-- Technical Metadata Grid -->
        <div class="grid grid-cols-2 sm:grid-cols-5 gap-3">
          {#each [
            { label: 'Duration', value: formatDuration(video.duration_seconds || activeAsset?.duration_seconds) },
            { label: 'View Count', value: video.view_count ? (video.view_count/1_000).toFixed(0)+'K' : '—' },
            { label: 'Source Specs', value: activeAsset?.width ? `${activeAsset.width}×${activeAsset.height}` : 'Pending Acquisition' },
            { label: 'Reel Filter / Aspect', value: processedCropMode ? `${formatCropModeLabel(processedCropMode)} (9:16)` : (video.has_processed_reel ? '9:16 Vertical' : 'Not Processed') },
            { label: 'File Size', value: formatBytes((processedAsset || activeAsset)?.file_size_bytes) },
          ] as meta}
            <div class="card-elevated p-3">
              <p class="text-[11px] text-zinc-500 uppercase tracking-wider">{meta.label}</p>
              <p class="text-sm text-white font-medium mt-0.5 truncate">{meta.value}</p>
            </div>
          {/each}
        </div>

        <!-- YouTube Thumbnail Artwork (Static Reference) -->
        <div class="card p-4">
          <div class="flex items-center justify-between mb-2">
            <div>
              <h3 class="text-xs font-semibold uppercase tracking-wider text-zinc-300">YouTube Thumbnail Artwork</h3>
              <p class="text-[11px] text-zinc-500">Static artwork from YouTube metadata (reference only, not playable media)</p>
            </div>
            <span class="text-[11px] text-zinc-500 font-mono">ID: {video.youtube_video_id}</span>
          </div>
          {#if video.thumbnail_url}
            <div class="aspect-video max-h-48 rounded-lg overflow-hidden bg-black border border-surface-border">
              <img src={video.thumbnail_url} alt={video.title} class="w-full h-full object-cover"/>
            </div>
          {:else}
            <div class="p-4 rounded-lg bg-surface-elevated text-xs text-zinc-500 text-center">
              No thumbnail artwork available
            </div>
          {/if}
        </div>

        <!-- Description Accordion -->
        {#if video.description}
          <details class="card p-4">
            <summary class="text-sm font-medium text-zinc-300 cursor-pointer">Description</summary>
            <p class="text-xs text-zinc-500 mt-2 whitespace-pre-wrap leading-relaxed">{video.description}</p>
          </details>
        {/if}
      </div>

      <!-- Right Column: Processing Pipeline & Publishing Controls (2 Cols) -->
      <div class="lg:col-span-2 space-y-4">
        <div class="card p-5">
          <h2 class="text-sm font-semibold text-zinc-300 mb-4 flex items-center gap-2">
            <span>⚙️</span> Processing Pipeline
          </h2>

          <!-- Step 1: Authorized Video Media Acquisition -->
          <div class="mb-5 p-3.5 rounded-lg bg-surface-elevated border border-surface-border space-y-3">
            <div class="flex items-center justify-between">
              <p class="text-xs text-zinc-300 font-medium">1. Video Media Acquisition</p>
              {#if video.has_uploaded_video}
                <span class="text-[10px] text-emerald-400 bg-emerald-950/60 px-2 py-0.5 rounded border border-emerald-800">Media Ready</span>
              {:else}
                <span class="text-[10px] text-amber-400 bg-amber-950/60 px-2 py-0.5 rounded border border-amber-800">Media Required</span>
              {/if}
            </div>
            <p class="text-[11px] text-zinc-400 leading-relaxed">
              Acquire authorized media to enable playback and 9:16 Reel conversion. Supports direct HTTP chunked streaming or manual upload.
            </p>

            <!-- Method A: Remote Direct Media URL -->
            <div class="pt-1 border-t border-surface-border/60">
              <div class="flex items-center justify-between mb-2">
                <span class="text-[11px] font-semibold text-zinc-300 uppercase tracking-wider">Method A: Authorized Remote URL</span>
                <button
                  class="text-[11px] text-primary hover:underline"
                  onclick={() => showUrlInput = !showUrlInput}
                >
                  {showUrlInput ? 'Hide' : 'Enter URL →'}
                </button>
              </div>

              {#if showUrlInput}
                <div class="space-y-2.5 p-3 rounded-lg bg-surface border border-surface-border/80 text-xs">
                  <div>
                    <label for="direct-media-input" class="block text-[11px] text-zinc-400 mb-1 font-medium">Direct MP4 / MOV URL</label>
                    <input
                      id="direct-media-input"
                      type="url"
                      bind:value={directMediaUrl}
                      placeholder="https://storage.example.com/video.mp4"
                      class="input text-xs w-full py-1.5"
                      disabled={retrieving}
                    />
                  </div>
                  <label class="flex items-center gap-2 cursor-pointer text-[11px] text-zinc-300">
                    <input type="checkbox" bind:checked={autoProcessOnRetrieve} class="rounded border-zinc-700 text-primary" />
                    <span>Auto-convert to 9:16 Reel upon download</span>
                  </label>
                  <button
                    class="btn-primary text-xs w-full py-2 flex items-center justify-center gap-2"
                    onclick={handleRetrieveMedia}
                    disabled={retrieving}
                  >
                    <span>🌐</span>
                    <span>{retrieving ? 'Streaming Chunked Download…' : 'Retrieve Remote Media'}</span>
                  </button>
                  <p class="text-[10px] text-zinc-500 leading-tight">
                    * YouTube watch URLs cannot be directly downloaded without bypassing access controls. Please supply an authorized direct MP4 stream or use file upload.
                  </p>
                </div>
              {:else}
                <button
                  class="btn-secondary text-xs w-full py-1.5 flex items-center justify-center gap-1.5"
                  onclick={() => showUrlInput = true}
                >
                  <span>🌐</span> Retrieve from Authorized URL
                </button>
              {/if}
            </div>

            <!-- Method B: Manual File Upload -->
            <div class="pt-2 border-t border-surface-border/60">
              <span class="text-[11px] font-semibold text-zinc-300 uppercase tracking-wider block mb-2">Method B: Manual File Upload</span>
              <input type="file" accept="video/mp4,video/quicktime,video/webm" class="hidden" bind:this={uploadInput} onchange={handleUpload}/>
              <button
                class="btn-secondary text-xs w-full flex items-center justify-center gap-2 py-1.5"
                onclick={() => uploadInput?.click()}
                disabled={uploading}
              >
                <span>📁</span> {uploading ? 'Uploading Video File...' : video.has_uploaded_video ? 'Replace Video File' : 'Upload Video File (MP4/MOV)'}
              </button>
              {#if activeAsset?.original_filename}
                <p class="text-[10px] text-zinc-400 truncate mt-1">Current asset: {activeAsset.original_filename}</p>
              {/if}
            </div>

            <!-- Method C: Authorized YouTube Media Retrieval (yt-dlp) -->
            <div class="pt-2 border-t border-surface-border/60">
              <div class="flex items-center justify-between mb-2">
                <span class="text-[11px] font-semibold text-zinc-300 uppercase tracking-wider flex items-center gap-1.5">
                  <span>Method C: YouTube Media (yt-dlp)</span>
                  {#if ytdlpStatus?.enabled}
                    <span class="badge text-[9px] bg-emerald-950/60 text-emerald-400 border border-emerald-800">Enabled</span>
                  {:else}
                    <span class="badge text-[9px] bg-amber-950/60 text-amber-400 border border-amber-800">Disabled</span>
                  {/if}
                </span>
                <button
                  class="text-[11px] text-primary hover:underline"
                  onclick={() => showYtDlpSection = !showYtDlpSection}
                >
                  {showYtDlpSection ? 'Hide' : 'Options →'}
                </button>
              </div>

              {#if showYtDlpSection}
                <div class="space-y-2.5 p-3 rounded-lg bg-surface border border-surface-border/80 text-xs">
                  <!-- System Status Pill -->
                  <div class="flex flex-wrap items-center gap-1.5 text-[10px] text-zinc-400 p-2 rounded bg-surface-elevated/70 border border-surface-border/60">
                    <span class="font-medium text-zinc-300">yt-dlp {ytdlpStatus?.ytdlp_version || '2026.10.26'}</span>
                    <span>•</span>
                    <span>EJS {ytdlpStatus?.ytdlp_ejs_version || '0.8.0'}</span>
                    <span>•</span>
                    <span class={ytdlpStatus?.js_runtime_supported === false ? 'text-amber-400 font-medium' : 'text-zinc-300'}>
                      {ytdlpStatus?.js_runtime || 'Node.js'}
                    </span>
                    <span>•</span>
                    <span>FFmpeg {ytdlpStatus?.ffmpeg_available ? '✓' : '✗'}</span>
                  </div>

                  {#if !ytdlpStatus?.enabled}
                    <div class="p-2.5 rounded bg-amber-950/40 border border-amber-800/60 text-amber-300 text-[11px] leading-tight">
                      ⚠️ <strong>yt-dlp Retrieval Disabled:</strong> Set <code>YTDLP_ENABLED=true</code> in your environment configuration to activate direct stream retrieval.
                    </div>
                  {:else if ytdlpStatus?.js_runtime_supported === false}
                    <div class="p-2.5 rounded bg-amber-950/40 border border-amber-800/60 text-amber-300 text-[11px] leading-tight">
                      ⚠️ <strong>Unsupported JavaScript Runtime:</strong> yt-dlp requires Node.js v22 or newer (detected: <code>{ytdlpStatus?.js_runtime || 'unsupported'}</code>). Ensure the backend container is running the updated Dockerfile.
                    </div>
                  {/if}

                  <!-- Live Retrieval Status Badge -->
                  {#if retrievalStatus !== 'IDLE'}
                    <div class="flex items-center justify-between p-2 rounded bg-surface-elevated text-xs border border-surface-border">
                      <span class="text-zinc-400">Status:</span>
                      {#if retrievalStatus === 'DOWNLOAD_STARTED'}
                        <span class="badge text-[10px] bg-blue-900/60 text-blue-300 border border-blue-700 animate-pulse font-mono">DOWNLOAD_STARTED</span>
                      {:else if retrievalStatus === 'DOWNLOADED'}
                        <span class="badge text-[10px] bg-emerald-950/60 text-emerald-300 border border-emerald-800 font-mono">DOWNLOADED</span>
                      {:else if retrievalStatus === 'REUSED'}
                        <span class="badge text-[10px] bg-amber-950/60 text-amber-300 border border-amber-800 font-mono">REUSED</span>
                      {:else if retrievalStatus === 'FAILED'}
                        <span class="badge text-[10px] bg-red-950/60 text-red-300 border border-red-800 font-mono">FAILED</span>
                      {/if}
                    </div>
                  {/if}

                  <label class="flex items-start gap-2 cursor-pointer text-[11px] text-zinc-300 leading-snug">
                    <input
                      type="checkbox"
                      bind:checked={ytdlpRightsConfirmed}
                      class="rounded border-zinc-700 text-primary mt-0.5"
                    />
                    <span>I confirm content ownership or authorization to download and repurpose this video. <span class="text-amber-400 font-semibold">*Required</span></span>
                  </label>

                  <label class="flex items-center gap-2 cursor-pointer text-[11px] text-zinc-300">
                    <input
                      type="checkbox"
                      bind:checked={autoProcessOnYtDlp}
                      class="rounded border-zinc-700 text-primary"
                    />
                    <span>Auto-convert to 9:16 Reel upon download</span>
                  </label>

                  <button
                    class="btn-primary text-xs w-full py-2 flex items-center justify-center gap-2"
                    onclick={handleYtDlpRetrieve}
                    disabled={retrievingYtDlp || !ytdlpRightsConfirmed || !ytdlpStatus?.enabled}
                  >
                    <span>▶</span>
                    <span>{retrievingYtDlp ? 'Retrieving Stream via yt-dlp…' : 'Retrieve Authorized YouTube Media'}</span>
                  </button>

                  <p class="text-[10px] text-zinc-500 leading-tight">
                    * Format: <code>bv*+ba/b</code> merged into MP4 via FFmpeg (max 2 GB). Strictly no cookies, no CAPTCHA bypass, no DRM circumvention.
                  </p>
                </div>
              {:else}
                <button
                  class="btn-secondary text-xs w-full py-1.5 flex items-center justify-center gap-1.5"
                  onclick={() => showYtDlpSection = true}
                >
                  <span>▶</span> Retrieve via yt-dlp
                </button>
              {/if}
            </div>
          </div>

          <!-- Step 2: Aspect Ratio & Conversion -->
          <div class="mb-5 space-y-2">
            <div class="flex items-center justify-between">
              <p class="text-xs text-zinc-300 font-medium">2. Aspect Ratio Conversion</p>
              <span class="text-[10px] text-emerald-400 font-medium">9:16 Vertical</span>
            </div>
            <div class="grid grid-cols-3 gap-1.5">
              {#each [
                ['blur', 'Blur Fill', '✨ Full'],
                ['pad', 'Pad (Bars)', '⬛ Full'],
                ['crop', 'Crop', '✂ Center']
              ] as [mode, label, badge]}
                <button
                  class="py-2.5 px-1.5 text-xs rounded-lg border text-center transition-colors flex flex-col items-center justify-center gap-0.5 {cropMode === mode ? 'bg-primary/20 border-primary text-primary font-medium shadow-sm' : 'border-surface-border text-zinc-400 hover:border-zinc-600'}"
                  onclick={() => cropMode = mode as 'blur'|'pad'|'crop'}
                >
                  <span class="font-medium text-[11px] leading-tight">{label}</span>
                  <span class="text-[9px] text-zinc-500">{badge}</span>
                </button>
              {/each}
            </div>
            <p class="text-[11px] text-zinc-400 leading-snug">
              {#if cropMode === 'blur'}
                <span class="text-emerald-400 font-medium">Full Content Visible:</span> Shows 100% of the video without cutting sides. Top and bottom are filled with a blurred video backdrop (ideal for Instagram Reels).
              {:else if cropMode === 'pad'}
                <span class="text-zinc-300 font-medium">Full Content Visible:</span> Preserves full 16:9 frame with top and bottom black bars (nothing cropped).
              {:else}
                <span class="text-amber-400/90 font-medium">Crops Edges:</span> Zooms in and crops left/right edges to fill vertical frame (text on sides may be cut).
              {/if}
            </p>
          </div>

          <!-- Step 3: FFmpeg Action Button -->
          <button
            class="btn-primary w-full text-sm py-2.5 flex items-center justify-center gap-2 shadow-lg"
            onclick={handleProcess}
            disabled={polling || !video.has_uploaded_video}
          >
            <span>⚡</span> {polling ? 'Processing with FFmpeg…' : video.has_processed_reel ? 'Re-Process as 9:16 Reel' : 'Process as Reel (1080×1920)'}
          </button>
          {#if !video.has_uploaded_video}
            <p class="text-[11px] text-amber-500/80 text-center mt-1.5">Acquire or upload video media first to enable processing.</p>
          {/if}

          <!-- Job Progress Status -->
          {#if jobStatus}
            <div class="mt-4 p-3 rounded-lg bg-surface-elevated/70 border border-surface-border space-y-2">
              <div class="flex justify-between text-xs">
                <span class="text-zinc-400">Processing Progress</span>
                <span class="text-white font-mono font-medium">{jobStatus.progress}%</span>
              </div>
              <div class="h-2 bg-surface-border rounded-full overflow-hidden">
                <div class="h-full bg-gradient-to-r from-primary to-indigo-400 transition-all duration-500 rounded-full"
                     style="width: {jobStatus.progress}%"></div>
              </div>
              <p class="text-xs text-center font-medium {jobStatus.status === 'FAILED' ? 'text-red-400' : jobStatus.status === 'FINISHED' ? 'text-emerald-400' : 'text-blue-400'}">
                {#if jobStatus.status === 'FINISHED'}
                  ✓ 100% Complete • Processed with {formatCropModeLabel(processedCropMode || cropMode)} (9:16)
                {:else if jobStatus.status === 'FAILED'}
                  FAILED – {jobStatus.error_message || 'Processing failed'}
                {:else}
                  Processing ({jobStatus.status}) – Converting to 9:16 with {formatCropModeLabel(cropMode)}…
                {/if}
              </p>
            </div>
          {/if}
        </div>

        <!-- Add to Publishing Queue -->
        {#if video.status === 'PROCESSED' || video.has_processed_reel || (jobStatus?.status === 'FINISHED')}
          <div class="card border-emerald-800/50 bg-emerald-950/20 p-5 space-y-3">
            <div class="flex items-center gap-2 text-emerald-400 text-xs font-semibold">
              <span>✓</span>
              <span>Reel Ready for Instagram</span>
            </div>
            <p class="text-[11px] text-zinc-400">
              The 1080×1920 video has been generated and validated against Instagram Reel specifications.
            </p>
            <a href="/queue?video_id={video.id}" class="btn-primary w-full text-center text-sm py-2.5 block">
              + Add to Publishing Queue
            </a>
          </div>
        {/if}
      </div>
    </div>
  {/if}
</div>
