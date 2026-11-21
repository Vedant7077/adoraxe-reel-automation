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

  // yt-dlp Retrieval State
  let ytdlpStatus = $state<any>(null);
  let ytdlpRightsConfirmed = $state(false);
  let autoProcessOnYtDlp = $state(false);
  let retrievingYtDlp = $state(false);
  let showYtDlpSection = $state(true);
  let showReacquireOptions = $state(false);
  let retrievalStatus = $state<'IDLE' | 'DOWNLOAD_STARTED' | 'DOWNLOADED' | 'REUSED' | 'FAILED'>('IDLE');

  // Job & Upload State
  let jobId = $state<string | null>(null);
  let jobStatus = $state<any>(null);
  let polling = $state(false);
  let processing = $state(false);
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
    if (!video?.has_uploaded_video || (!video?.has_processed_reel && !processedAsset)) return null;
    // Prefer direct verified Cloudinary HTTPS URL for HTML5 video playback without 401; fallback to local stream
    const cld = processedAsset?.cloudinary_url || (video?.processed_video_url?.startsWith('http') ? video.processed_video_url : null);
    if (cld && (cld.startsWith('https://') || cld.startsWith('http://'))) {
      return cld;
    }
    return `${getVideoStreamUrl(videoId, 'processed')}?t=${processedVideoVersion}`;
  });

  let canAddToQueue = $derived(
    Boolean(
      video?.has_uploaded_video &&
      (video?.has_processed_reel || jobStatus?.status === 'FINISHED') &&
      !processing &&
      !polling &&
      jobStatus?.status !== 'FAILED'
    )
  );

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

  $effect(() => {
    if (videoId) {
      loadVideo();
      loadYtDlpStatus();
    }
  });

  async function loadVideo() {
    try {
      const fetchedVideo = await getVideo(videoId);
      if (fetchedVideo) {
        // Defensive: If fetchedVideo says has_uploaded_video is false, BUT we already have an active local/retrieved asset, preserve it
        if (!fetchedVideo.has_uploaded_video && video?.has_uploaded_video) {
          fetchedVideo.has_uploaded_video = true;
          if ((!fetchedVideo.assets || fetchedVideo.assets.length === 0) && video.assets?.length > 0) {
            fetchedVideo.assets = video.assets;
          }
          if (!fetchedVideo.active_asset && video.active_asset) {
            fetchedVideo.active_asset = video.active_asset;
          }
        }
        video = fetchedVideo;
        originalVideoVersion = Date.now();
      }
      // Restore active in-flight processing job across page navigation and refresh
      if (video?.active_job_id && (video?.is_processing || video?.status === 'PROCESSING') && !polling) {
        jobId = video.active_job_id;
        polling = true;
        processing = true;
        pollJob();
      } else if (!video?.active_job_id && video?.status !== 'PROCESSING') {
        polling = false;
        processing = false;
      }
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

      // 1. Immediately reset retrieving flag so button never stays stuck
      retrievingYtDlp = false;

      // 2. Set live retrieval status badge and success message
      retrievalStatus = res.status === 'REUSED' ? 'REUSED' : 'DOWNLOADED';
      retrievalSuccessMsg = `✓ [${retrievalStatus}] ${res.message} (${res.width || '—'}×${res.height || '—'}, ${formatDuration(res.duration_seconds)})`;

      // 3. Immediately and reactively update video and asset state so preview renders instantly
      const retrievedAsset = {
        id: res.asset_id,
        video_id: videoId,
        original_filename: res.original_filename,
        duration_seconds: res.duration_seconds,
        width: res.width,
        height: res.height,
        codec: res.codec,
        file_size_bytes: res.file_size_bytes,
        is_processed: false,
        is_reel_compatible: false,
        cloudinary_url: null,
      };

      if (video) {
        const remainingAssets = (video.assets || []).filter((a: any) => a.id !== res.asset_id && !a.is_processed);
        const processedAssets = (video.assets || []).filter((a: any) => a.is_processed);
        video = {
          ...video,
          has_uploaded_video: true,
          status: video.status === 'FAILED' ? 'IMPORTED' : video.status,
          duration_seconds: res.duration_seconds || video.duration_seconds,
          original_video_url: res.original_stream_url || `/api/v1/videos/${videoId}/stream/original`,
          assets: [retrievedAsset, ...remainingAssets, ...processedAssets],
          active_asset: retrievedAsset,
        };
      }
      originalVideoVersion = Date.now();

      // 4. Kick off background Reel conversion job polling if autoProcess was selected
      if (res.job_id) {
        jobId = res.job_id;
        polling = true;
        processing = true;
        pollJob();
      }

      // 5. Asynchronously reload complete video record from backend without blocking UI preview
      try {
        await loadVideo();
      } catch (loadErr) {
        console.warn('Could not reload full video state:', loadErr);
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
      if (video) {
        video = {
          ...video,
          has_uploaded_video: true,
        };
      }
      originalVideoVersion = Date.now();
      await loadVideo();
      input.value = '';
    } catch (e: any) {
      uploadErrorMsg = e.message || 'Upload failed';
    } finally {
      uploading = false;
    }
  }

  async function handleProcess() {
    if (processing || polling) return;
    processing = true;
    polling = true;
    uploadErrorMsg = '';
    uploadSuccessMsg = '';
    retrievalErrorMsg = '';
    retrievalSuccessMsg = '';
    // Show immediate progress bar & status so the user gets instant visual feedback
    jobStatus = {
      id: '',
      status: 'PROCESSING',
      current_stage: `Initializing 9:16 conversion (${formatCropModeLabel(cropMode)})…`,
      progress: 5,
    };
    try {
      const res = await processVideo(videoId, cropMode);
      jobId = res.job_id;
      jobStatus = {
        ...jobStatus,
        id: res.job_id,
      };
      pollJob();
    } catch (e: any) {
      error = e.message;
      processing = false;
      polling = false;
      jobStatus = null;
    }
  }

  function handleCancelReset() {
    polling = false;
    processing = false;
    jobStatus = null;
    loadVideo();
  }

  async function pollJob() {
    if (!jobId || !polling) return;
    try {
      jobStatus = await getJobStatus(jobId);
      if (jobStatus.status === 'FINISHED' || jobStatus.status === 'FAILED') {
        polling = false;
        processing = false;
        if (jobStatus.status === 'FINISHED') {
          processedVideoVersion = Date.now();
          await loadVideo();
          uploadSuccessMsg = `✓ 9:16 Reel processed successfully with ${formatCropModeLabel(processedCropMode || cropMode)}! Playable in preview now.`;
        } else if (jobStatus.status === 'FAILED') {
          await loadVideo();
          uploadErrorMsg = `Processing failed: ${jobStatus.error_message || 'Unknown error'}`;
        }
      } else {
        setTimeout(pollJob, 1000);
      }
    } catch (e) {
      setTimeout(pollJob, 1500);
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
                Fallback: Upload the video file directly (MP4/MOV) or retrieve media via yt-dlp.
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
                        preload="auto"
                        poster={video.thumbnail_url || ''}
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
                    <p class="text-[11px] text-zinc-500 mt-1 max-w-[240px]">Acquire media via yt-dlp retrieval or direct file upload in the pipeline panel.</p>
                    <div class="flex flex-wrap gap-2 mt-3 justify-center">
                      <button class="btn-secondary text-xs py-1 px-3 flex items-center gap-1" onclick={() => uploadInput?.click()}>
                        <span>📁</span> Upload File
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
                  {#if polling || processing}
                    <span class="badge text-[10px] bg-blue-900/40 text-blue-300 border border-blue-700 animate-pulse font-mono">
                      Encoding ({jobStatus?.progress ?? 5}%)
                    </span>
                  {:else if video.has_uploaded_video && video.has_processed_reel}
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
                        preload="auto"
                        poster={video.thumbnail_url || ''}
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
                {:else if polling || processing}
                  <div class="rounded-xl border border-primary/40 bg-surface-elevated/80 p-5 flex flex-col items-center justify-center text-center aspect-[9/16] max-h-[340px] mx-auto space-y-2.5 shadow-inner">
                    <div class="w-9 h-9 rounded-full border-2 border-primary border-t-transparent animate-spin"></div>
                    <div class="space-y-1">
                      <h3 class="text-xs font-semibold text-white">Converting to 9:16 Reel…</h3>
                      <p class="text-[11px] text-zinc-400 font-mono line-clamp-2 max-w-[200px]">
                        {jobStatus?.current_stage || `Applying ${formatCropModeLabel(cropMode)} with FFmpeg…`}
                      </p>
                    </div>
                    <div class="w-full max-w-[180px] space-y-1 pt-1">
                      <div class="flex justify-between text-[10px] text-zinc-400 font-mono">
                        <span>Progress</span>
                        <span class="text-primary font-bold">{jobStatus?.progress ?? 5}%</span>
                      </div>
                      <div class="h-1.5 bg-zinc-800 rounded-full overflow-hidden border border-zinc-700/40">
                        <div
                          class="h-full bg-gradient-to-r from-primary to-indigo-400 transition-all duration-300 rounded-full"
                          style="width: {Math.max(5, Math.min(100, jobStatus?.progress ?? 5))}%"
                        ></div>
                      </div>
                    </div>
                  </div>
                {:else}
                  <div class="rounded-xl border-2 border-dashed border-surface-border bg-surface-elevated/40 p-6 flex flex-col items-center justify-center text-center aspect-[9/16] max-h-[340px] mx-auto">
                    <span class="text-2xl mb-2">⚡</span>
                    <h3 class="text-xs font-semibold text-white">Reel Not Generated Yet</h3>
                    <p class="text-[11px] text-zinc-500 mt-1 max-w-[180px]">Select crop mode and click "Process as Reel" to convert.</p>
                    {#if video.has_uploaded_video}
                      <button
                        class="btn-primary text-xs mt-3 flex items-center gap-1.5"
                        onclick={handleProcess}
                        disabled={polling || processing || !video.has_uploaded_video}
                      >
                        ⚡ {processing ? 'Starting...' : polling ? 'Processing...' : 'Process Now'}
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
                      preload="auto"
                      poster={video.thumbnail_url || ''}
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
                    <button class="btn-secondary text-xs flex items-center gap-2" onclick={() => uploadInput?.click()}>
                      <span>📁</span> Upload Video File
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
                {#if polling || processing}
                  <span class="badge text-[10px] bg-blue-900/40 text-blue-300 border border-blue-700 animate-pulse font-mono">
                    Encoding ({jobStatus?.progress ?? 5}%)
                  </span>
                {:else if video.has_uploaded_video && video.has_processed_reel}
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
                      preload="auto"
                      poster={video.thumbnail_url || ''}
                      class="w-full h-full object-contain"
                      src={processedVideoUrl}
                    >
                      <track kind="captions" />
                    </video>
                  {/key}
                </div>
              {:else if polling || processing}
                <div class="rounded-2xl border-2 border-primary/40 bg-surface-elevated/70 p-8 flex flex-col items-center justify-center text-center aspect-[9/16] max-h-[460px] mx-auto space-y-3 shadow-xl">
                  <div class="w-12 h-12 rounded-full border-3 border-primary border-t-transparent animate-spin"></div>
                  <h3 class="text-sm font-semibold text-white">Converting to 9:16 Reel…</h3>
                  <p class="text-xs text-zinc-400 font-mono max-w-xs">
                    {jobStatus?.current_stage || `Encoding 1080×1920 Reel with ${formatCropModeLabel(cropMode)}…`}
                  </p>
                  <div class="w-full max-w-xs space-y-1.5 pt-2">
                    <div class="flex justify-between text-xs text-zinc-400 font-mono">
                      <span>Encoding Progress</span>
                      <span class="text-primary font-bold">{jobStatus?.progress ?? 5}%</span>
                    </div>
                    <div class="h-2 bg-zinc-800 rounded-full overflow-hidden p-0.5 border border-zinc-700/40">
                      <div
                        class="h-full bg-gradient-to-r from-primary via-fuchsia-500 to-indigo-400 transition-all duration-300 rounded-full"
                        style="width: {Math.max(5, Math.min(100, jobStatus?.progress ?? 5))}%"
                      ></div>
                    </div>
                  </div>
                </div>
              {:else}
                <div class="rounded-2xl border-2 border-dashed border-surface-border bg-surface-elevated/40 p-10 flex flex-col items-center justify-center text-center aspect-[9/16] max-h-[460px] mx-auto">
                  <span class="text-4xl mb-3">⚡</span>
                  <h3 class="text-sm font-semibold text-white">Reel Not Generated</h3>
                  <p class="text-xs text-zinc-500 mt-1 max-w-xs">Convert this video into 9:16 format with FFmpeg to enable Reel playback.</p>
                  {#if video.has_uploaded_video}
                    <button
                      class="btn-primary text-xs mt-4"
                      onclick={handleProcess}
                      disabled={polling || processing || !video.has_uploaded_video}
                    >
                      ⚡ {processing ? 'Starting...' : polling ? 'Processing...' : 'Process as Reel'}
                    </button>
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
            {#if processing || polling}
              <span class="badge badge-processing animate-pulse font-mono text-xs">Encoding 9:16 Reel ({jobStatus?.progress ?? 5}%)</span>
            {:else if video.has_uploaded_video && (video.has_processed_reel || jobStatus?.status === 'FINISHED')}
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
                <span class="text-[10px] text-emerald-400 bg-emerald-950/60 px-2 py-0.5 rounded border border-emerald-800">✓ Media Ready</span>
              {:else}
                <span class="text-[10px] text-amber-400 bg-amber-950/60 px-2 py-0.5 rounded border border-amber-800">Media Required</span>
              {/if}
            </div>

            {#if video.has_uploaded_video}
              <!-- Confirmed Media Summary (Hide bulky acquisition form) -->
              <div class="p-3 rounded-lg bg-emerald-950/30 border border-emerald-800/50 space-y-2 text-xs">
                <div class="flex items-center justify-between">
                  <span class="text-emerald-400 font-medium flex items-center gap-1.5">
                    <span>✓</span> Source Media Active & Playable
                  </span>
                  <span class="text-[10px] bg-emerald-900/60 text-emerald-300 px-1.5 py-0.5 rounded font-mono">
                    {activeAsset?.width && activeAsset?.height ? `${activeAsset.width}×${activeAsset.height}` : 'Playable'}
                  </span>
                </div>
                {#if activeAsset?.original_filename}
                  <p class="text-[11px] text-zinc-300 truncate font-mono">
                    <span class="text-zinc-500">File:</span> {activeAsset.original_filename}
                  </p>
                {/if}
                <div class="flex items-center gap-3 text-[11px] text-zinc-400">
                  {#if video.duration_seconds || activeAsset?.duration_seconds}
                    <span>⏱ {formatDuration(video.duration_seconds || activeAsset?.duration_seconds)}</span>
                  {/if}
                  {#if activeAsset?.file_size_bytes}
                    <span>💾 {formatBytes(activeAsset.file_size_bytes)}</span>
                  {/if}
                  {#if activeAsset?.codec}
                    <span>📼 {activeAsset.codec}</span>
                  {/if}
                </div>
              </div>

              <!-- Collapsible Options: Replace or Re-retrieve -->
              <div class="pt-1 border-t border-surface-border/60">
                <button
                  type="button"
                  class="text-[11px] text-zinc-400 hover:text-white flex items-center justify-between w-full py-1 transition-colors"
                  onclick={() => showReacquireOptions = !showReacquireOptions}
                >
                  <span>Need to replace or re-download media?</span>
                  <span class="text-primary font-medium">{showReacquireOptions ? 'Hide Replace Options ▴' : 'Replace Media ▾'}</span>
                </button>

                {#if showReacquireOptions}
                  <div class="mt-2.5 space-y-3 pt-2 border-t border-surface-border/40 text-xs">
                    <!-- Option 1: Re-retrieve from YouTube -->
                    <div class="p-2.5 rounded bg-surface border border-surface-border space-y-2">
                      <span class="text-[11px] font-semibold text-zinc-300 uppercase tracking-wider block">Re-retrieve via yt-dlp</span>
                      <label class="flex items-start gap-2 cursor-pointer text-[11px] text-zinc-300 leading-snug">
                        <input
                          type="checkbox"
                          bind:checked={ytdlpRightsConfirmed}
                          class="rounded border-zinc-700 text-primary mt-0.5"
                        />
                        <span>I confirm authorization to re-download this video.</span>
                      </label>
                      <button
                        class="btn-secondary text-xs w-full py-1.5 flex items-center justify-center gap-1.5"
                        onclick={handleYtDlpRetrieve}
                        disabled={retrievingYtDlp || !ytdlpRightsConfirmed || !ytdlpStatus?.enabled}
                      >
                        <span>▶</span>
                        <span>{retrievingYtDlp ? 'Re-retrieving Stream…' : 'Re-retrieve YouTube Stream'}</span>
                      </button>
                    </div>

                    <!-- Option 2: Replace with Local File -->
                    <div class="p-2.5 rounded bg-surface border border-surface-border space-y-2">
                      <span class="text-[11px] font-semibold text-zinc-300 uppercase tracking-wider block">Replace with Local File</span>
                      <input type="file" accept="video/mp4,video/quicktime,video/webm" class="hidden" bind:this={uploadInput} onchange={handleUpload}/>
                      <button
                        class="btn-secondary text-xs w-full flex items-center justify-center gap-1.5 py-1.5"
                        onclick={() => uploadInput?.click()}
                        disabled={uploading}
                      >
                        <span>📁</span>
                        <span>{uploading ? 'Uploading Video File...' : 'Upload Replacement File (MP4/MOV)'}</span>
                      </button>
                    </div>
                  </div>
                {/if}
              </div>
            {:else}
              <p class="text-[11px] text-zinc-400 leading-relaxed">
                Acquire media to enable playback and 9:16 Reel conversion. Retrieve directly from YouTube using yt-dlp or upload a video file manually.
              </p>

              <!-- Option 1: YouTube Media (yt-dlp) -->
              <div class="pt-1 border-t border-surface-border/60">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-[11px] font-semibold text-zinc-300 uppercase tracking-wider flex items-center gap-1.5">
                    <span>Option 1: YouTube Media (yt-dlp)</span>
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

              <!-- Option 2: Manual File Upload -->
              <div class="pt-2 border-t border-surface-border/60">
                <span class="text-[11px] font-semibold text-zinc-300 uppercase tracking-wider block mb-2">Option 2: Manual File Upload</span>
                <input type="file" accept="video/mp4,video/quicktime,video/webm" class="hidden" bind:this={uploadInput} onchange={handleUpload}/>
                <button
                  class="btn-secondary text-xs w-full flex items-center justify-center gap-2 py-1.5"
                  onclick={() => uploadInput?.click()}
                  disabled={uploading}
                >
                  <span>📁</span> {uploading ? 'Uploading Video File...' : 'Upload Video File (MP4/MOV)'}
                </button>
              </div>
            {/if}
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
            class="btn-primary w-full text-sm py-2.5 flex items-center justify-center gap-2 shadow-lg transition-all"
            onclick={handleProcess}
            disabled={polling || processing || !video.has_uploaded_video}
          >
            {#if processing || polling}
              <span class="inline-block animate-spin text-sm">⚙️</span>
              <span>Converting ({jobStatus?.progress ?? 5}%)…</span>
            {:else if video.has_processed_reel}
              <span>⚡</span> Re-Process as 9:16 Reel
            {:else}
              <span>⚡</span> Process as Reel (1080×1920)
            {/if}
          </button>
          {#if !video.has_uploaded_video}
            <p class="text-[11px] text-amber-500/80 text-center mt-1.5">Acquire or upload video media first to enable processing.</p>
          {/if}

          <!-- Live Conversion Progress Bar & Status -->
          {#if jobStatus || processing || polling}
            <div class="mt-4 p-3.5 rounded-lg bg-surface-elevated/90 border border-surface-border shadow-md space-y-2.5 transition-all">
              <!-- Top Row: Stage Indicator & Percentage -->
              <div class="flex items-center justify-between text-xs">
                <div class="flex items-center gap-2 font-medium">
                  {#if jobStatus?.status === 'FINISHED'}
                    <span class="text-emerald-400 flex items-center gap-1.5 font-semibold">
                      <span class="w-2 h-2 rounded-full bg-emerald-400"></span>
                      <span>Conversion Complete</span>
                    </span>
                  {:else if jobStatus?.status === 'FAILED'}
                    <span class="text-red-400 flex items-center gap-1.5 font-semibold">
                      <span class="w-2 h-2 rounded-full bg-red-400"></span>
                      <span>Conversion Failed</span>
                    </span>
                  {:else}
                    <span class="text-primary flex items-center gap-1.5 font-semibold">
                      <span class="relative flex h-2 w-2">
                        <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75"></span>
                        <span class="relative inline-flex rounded-full h-2 w-2 bg-primary"></span>
                      </span>
                      <span>Converting Aspect Ratio</span>
                    </span>
                  {/if}
                </div>
                <div class="flex items-center gap-1.5">
                  <span class="text-xs font-mono font-bold {jobStatus?.status === 'FINISHED' ? 'text-emerald-400' : jobStatus?.status === 'FAILED' ? 'text-red-400' : 'text-primary'}">
                    {jobStatus?.progress ?? (processing ? 5 : 0)}%
                  </span>
                </div>
              </div>

              <!-- Animated Progress Bar Track -->
              <div class="h-2.5 bg-zinc-800/90 rounded-full overflow-hidden p-0.5 border border-zinc-700/60">
                <div
                  class="h-full rounded-full transition-all duration-300 ease-out {jobStatus?.status === 'FINISHED' ? 'bg-gradient-to-r from-emerald-500 to-teal-400' : jobStatus?.status === 'FAILED' ? 'bg-red-500' : 'bg-gradient-to-r from-primary via-fuchsia-500 to-indigo-400'}"
                  style="width: {Math.max(5, Math.min(100, jobStatus?.progress ?? (processing ? 5 : 0)))}%"
                ></div>
              </div>

              <!-- Stage & Progress Details -->
              <div class="text-[11px] leading-snug">
                {#if jobStatus?.status === 'FINISHED'}
                  <p class="text-emerald-300/90 font-medium">
                    ✓ Successfully generated 1080×1920 Reel with {formatCropModeLabel(processedCropMode || cropMode)} format. Playable in preview now.
                  </p>
                {:else if jobStatus?.status === 'FAILED'}
                  <div class="space-y-2">
                    <p class="text-red-300 font-medium">
                      {jobStatus?.error_message || 'Processing failed. Check source video specs.'}
                    </p>
                    <div class="flex items-center gap-2 pt-1">
                      <button
                        type="button"
                        class="btn-primary text-xs py-1.5 px-3 flex items-center gap-1.5 font-medium"
                        onclick={handleProcess}
                      >
                        <span>🔄</span> Retry Conversion
                      </button>
                      <button
                        type="button"
                        class="btn-secondary text-xs py-1.5 px-3"
                        onclick={() => { jobStatus = null; }}
                      >
                        Dismiss
                      </button>
                    </div>
                  </div>
                {:else}
                  <div class="space-y-1">
                    <p class="text-zinc-200 font-mono text-[11px] truncate">
                      {jobStatus?.current_stage || `FFmpeg encoding with ${formatCropModeLabel(cropMode)}…`}
                    </p>
                    <div class="flex items-center justify-between text-[10px] text-zinc-500 pt-0.5">
                      <span>Target: 1080×1920 (9:16) H.264 / AAC</span>
                      <button
                        type="button"
                        class="text-zinc-400 hover:text-red-400 underline transition-colors cursor-pointer"
                        onclick={handleCancelReset}
                      >
                        Cancel / Reset
                      </button>
                    </div>
                  </div>
                {/if}
              </div>
            </div>
          {/if}
        </div>

        <!-- Add to Publishing Queue -->
        {#if canAddToQueue}
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
