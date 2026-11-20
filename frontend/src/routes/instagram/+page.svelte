<script lang="ts">
  import { onMount } from 'svelte';
  import { getInstagramStatus, refreshInstagramStatus, connectInstagram, disconnectInstagram } from '#lib/api/client';

  let status = $state<any>(null);
  let loading = $state(true);
  let refreshing = $state(false);
  let connecting = $state(false);
  let disconnecting = $state(false);
  let error = $state('');
  let successMessage = $state('');
  let showDisconnectModal = $state(false);

  onMount(async () => {
    // Check for OAuth return
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('connected') === 'true') {
      successMessage = 'Instagram account connected successfully via Meta Unified Login!';
      window.history.replaceState({}, '', '/instagram');
    }
    if (urlParams.get('error')) {
      error = `OAuth error: ${urlParams.get('error')}`;
      window.history.replaceState({}, '', '/instagram');
    }
    await loadStatus();
  });

  async function loadStatus(forceRefresh = false) {
    if (forceRefresh) refreshing = true;
    else loading = true;
    error = '';
    try {
      if (forceRefresh) {
        status = await refreshInstagramStatus();
        successMessage = 'Connection status and token health refreshed with Meta Graph API.';
      } else {
        status = await getInstagramStatus(false);
      }
    } catch (e: any) {
      error = e.message || 'Failed to retrieve Instagram connection status.';
    } finally {
      loading = false;
      refreshing = false;
    }
  }

  async function handleRefresh() {
    await loadStatus(true);
  }

  async function handleConnect() {
    connecting = true;
    error = '';
    try {
      const res = await connectInstagram();
      window.location.href = res.auth_url;
    } catch (e: any) {
      error = e.message;
      connecting = false;
    }
  }

  async function handleConfirmDisconnect() {
    disconnecting = true;
    error = '';
    try {
      await disconnectInstagram();
      showDisconnectModal = false;
      successMessage = 'Instagram account disconnected. Publishing history has been preserved.';
      await loadStatus(false);
    } catch (e: any) {
      error = e.message;
    } finally {
      disconnecting = false;
    }
  }

  function formatDateTime(val?: string | null): string {
    if (!val) return 'N/A';
    try {
      const d = new Date(val);
      if (isNaN(d.getTime())) return 'N/A';
      return d.toLocaleString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return 'N/A';
    }
  }

  function formatDate(val?: string | null): string {
    if (!val) return 'N/A';
    try {
      const d = new Date(val);
      if (isNaN(d.getTime())) return 'N/A';
      return d.toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
      });
    } catch {
      return 'N/A';
    }
  }

  let isConnected = $derived(status?.status === 'CONNECTED' && !status?.is_demo_placeholder);
  let isDemoPlaceholder = $derived(Boolean(status?.is_demo_placeholder || (status?.status === 'NOT_CONNECTED' && status?.demo_mode)));
  let tokenExpired = $derived(status?.status === 'TOKEN_EXPIRED');

  // Permissions helpers
  let grantedScopes = $derived<string[]>(status?.granted_scopes || []);
  let requiredScopes = $derived<string[]>(status?.required_scopes || ['instagram_basic', 'instagram_content_publish']);
  let missingScopes = $derived<string[]>(status?.missing_scopes || []);
  let additionalScopes = $derived<string[]>(status?.additional_scopes || []);
</script>

<div>
  <div class="page-header flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
    <div>
      <h1 class="page-title">Instagram Account</h1>
      <p class="page-subtitle">Meta Unified Login connection health, System-User token status, and publishing permissions.</p>
    </div>
    {#if isConnected}
      <div class="flex items-center gap-2">
        <button
          class="btn-secondary text-xs"
          onclick={handleRefresh}
          disabled={refreshing}
          title="Query Meta Graph API for fresh token validity and profile data"
        >
          {refreshing ? '⏳ Verifying…' : '🔄 Refresh Status'}
        </button>
        <button
          class="btn-secondary text-xs"
          onclick={handleConnect}
          disabled={connecting}
        >
          🔗 Reconnect
        </button>
        <button
          class="btn-danger text-xs"
          onclick={() => showDisconnectModal = true}
          disabled={disconnecting}
        >
          🔌 Disconnect
        </button>
      </div>
    {/if}
  </div>

  {#if error}
    <div class="mb-5 p-4 rounded-xl bg-red-950/40 border border-red-800/60 flex items-start gap-3">
      <span class="text-red-400 text-lg">⚠</span>
      <div class="flex-1">
        <p class="text-xs font-semibold text-red-300">Connection or Verification Error</p>
        <p class="text-xs text-red-400/90 mt-0.5">{error}</p>
      </div>
      <button class="text-red-400 hover:text-white text-xs" onclick={() => error = ''}>✕</button>
    </div>
  {/if}

  {#if successMessage}
    <div class="mb-5 p-4 rounded-xl bg-emerald-950/40 border border-emerald-800/60 flex items-start gap-3">
      <span class="text-emerald-400 text-lg">✓</span>
      <div class="flex-1">
        <p class="text-xs font-semibold text-emerald-300">Success</p>
        <p class="text-xs text-emerald-400/90 mt-0.5">{successMessage}</p>
      </div>
      <button class="text-emerald-400 hover:text-white text-xs" onclick={() => successMessage = ''}>✕</button>
    </div>
  {/if}

  {#if loading}
    <div class="grid grid-cols-1 md:grid-cols-2 gap-5 animate-pulse">
      <div class="card h-52 bg-surface-elevated"></div>
      <div class="card h-52 bg-surface-elevated"></div>
      <div class="card h-52 bg-surface-elevated"></div>
      <div class="card h-52 bg-surface-elevated"></div>
    </div>

  {:else if isConnected}
    <!-- ==================== CONNECTED DASHBOARD ==================== -->
    <div class="space-y-6">

      <!-- TOP ROW: Account Info & Token Health -->
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-5">

        <!-- 1. ACCOUNT INFORMATION CARD -->
        <div class="card relative overflow-hidden">
          <div class="flex items-start justify-between mb-4">
            <h2 class="text-sm font-semibold text-white tracking-wide uppercase font-headline">Account Information</h2>
            <span class="badge badge-published">
              <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
              Connected
            </span>
          </div>

          <div class="flex items-start gap-4">
            {#if status.profile_picture_url}
              <img
                src={status.profile_picture_url}
                alt={status.username}
                class="w-16 h-16 rounded-full object-cover border-2 border-primary/40 shadow-md"
              />
            {:else}
              <div class="w-16 h-16 rounded-full bg-ig-gradient flex items-center justify-center text-white font-bold text-2xl shadow-md">
                {status.username?.[0]?.toUpperCase() || 'I'}
              </div>
            {/if}

            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="text-lg font-bold text-white truncate font-headline">@{status.username}</span>
                <span class="badge badge-imported text-[11px]">{status.account_type || 'CREATOR'}</span>
              </div>
              <p class="text-sm text-zinc-300 font-medium mt-0.5">{status.full_name || 'Instagram Creator'}</p>

              <div class="mt-3 grid grid-cols-2 gap-2 text-xs">
                <div class="p-2 rounded-lg bg-surface-elevated border border-surface-border">
                  <span class="text-zinc-500 block text-[10px] uppercase tracking-wider">Instagram User ID</span>
                  <span class="text-zinc-200 font-mono text-[11px] select-all truncate block">{status.instagram_user_id || 'N/A'}</span>
                </div>
                <div class="p-2 rounded-lg bg-surface-elevated border border-surface-border">
                  <span class="text-zinc-500 block text-[10px] uppercase tracking-wider">Connected Since</span>
                  <span class="text-zinc-200 text-[11px] block">{formatDate(status.connected_at)}</span>
                </div>
              </div>
            </div>
          </div>

          <div class="mt-4 pt-3 border-t border-surface-border flex items-center justify-between text-xs text-zinc-400">
            <span>Meta Application:</span>
            <span class="font-medium text-white">{status.meta_app_name || 'Adoraxe Reel Automation'}</span>
          </div>
        </div>

        <!-- 2. TOKEN HEALTH & SECURITY CARD -->
        <div class="card">
          <div class="flex items-start justify-between mb-4">
            <h2 class="text-sm font-semibold text-white tracking-wide uppercase font-headline">Token Health & Security</h2>
            {#if status.token_validity === 'VALID'}
              <span class="badge badge-published">✓ Valid Token</span>
            {:else if status.token_validity === 'INVALID'}
              <span class="badge badge-failed">✕ Invalid Token</span>
            {:else}
              <span class="badge badge-pending">? Verification Unknown</span>
            {/if}
          </div>

          <div class="grid grid-cols-2 gap-3 text-xs mb-4">
            <div class="p-3 rounded-lg bg-surface-elevated border border-surface-border">
              <span class="text-zinc-500 block text-[10px] uppercase tracking-wider mb-1">Token Type</span>
              <div class="flex items-center gap-1.5">
                <span class="badge badge-processed text-[11px] font-mono">{status.token_type || 'SYSTEM_USER'}</span>
              </div>
            </div>

            <div class="p-3 rounded-lg bg-surface-elevated border border-surface-border">
              <span class="text-zinc-500 block text-[10px] uppercase tracking-wider mb-1">Expiration</span>
              {#if status.expires_at_raw === 0 || status.token_status_message === 'No expiry reported by Meta'}
                <span class="text-emerald-400 font-medium text-xs">No expiry reported by Meta</span>
                <span class="text-[10px] text-zinc-500 block">(System-User Token)</span>
              {:else if status.token_expires_at}
                <span class="text-amber-400 font-medium text-xs">{formatDate(status.token_expires_at)}</span>
              {:else}
                <span class="text-zinc-300 text-xs">{status.token_status_message || 'No expiry reported'}</span>
              {/if}
            </div>

            <div class="p-3 rounded-lg bg-surface-elevated border border-surface-border">
              <span class="text-zinc-500 block text-[10px] uppercase tracking-wider mb-1">Data Access Expiry</span>
              <span class="text-zinc-300 text-xs">
                {status.data_access_expires_at === 0 ? 'No expiry reported by Meta' : 'Active'}
              </span>
            </div>

            <div class="p-3 rounded-lg bg-surface-elevated border border-surface-border">
              <span class="text-zinc-500 block text-[10px] uppercase tracking-wider mb-1">Last Meta Verification</span>
              <span class="text-zinc-300 text-[11px]">
                {formatDateTime(status.last_verified_at)}
              </span>
            </div>
          </div>

          <!-- Security note -->
          <div class="p-2.5 rounded-lg bg-surface-elevated border border-surface-border text-[11px] text-zinc-400 flex items-start gap-2">
            <span class="text-emerald-400 mt-0.5">🔒</span>
            <p class="leading-tight">
              Tokens are encrypted at rest with <strong class="text-zinc-300">Fernet AES-128-CBC</strong>. Raw credentials and App Secrets are never sent to the browser.
            </p>
          </div>
        </div>
      </div>

      <!-- BOTTOM ROW: Permissions & Meta Technical Details -->
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-5">

        <!-- 3. GRANTED PERMISSIONS CARD (2 cols on large) -->
        <div class="card lg:col-span-2">
          <div class="flex items-center justify-between mb-3">
            <h2 class="text-sm font-semibold text-white tracking-wide uppercase font-headline">Meta Token Permissions</h2>
            <span class="text-xs text-zinc-500">Verified via Meta debug_token</span>
          </div>

          <!-- Required Permissions -->
          <div class="mb-4">
            <span class="text-xs font-semibold text-zinc-400 uppercase tracking-wider block mb-2">Required for Reel Publishing</span>
            <div class="space-y-2">
              <!-- instagram_basic -->
              <div class="p-3 rounded-lg bg-surface-elevated border border-surface-border flex items-center justify-between">
                <div class="flex items-center gap-3">
                  {#if grantedScopes.includes('instagram_basic')}
                    <span class="w-6 h-6 rounded-full bg-emerald-950 border border-emerald-800 text-emerald-400 flex items-center justify-center text-xs font-bold">✓</span>
                  {:else}
                    <span class="w-6 h-6 rounded-full bg-red-950 border border-red-800 text-red-400 flex items-center justify-center text-xs font-bold">✕</span>
                  {/if}
                  <div>
                    <code class="text-xs text-white font-semibold">instagram_basic</code>
                    <p class="text-[11px] text-zinc-400">Read basic profile, username, and account media</p>
                  </div>
                </div>
                <span class="badge {grantedScopes.includes('instagram_basic') ? 'badge-published' : 'badge-failed'} text-[11px]">
                  {grantedScopes.includes('instagram_basic') ? 'Granted' : 'Missing'}
                </span>
              </div>

              <!-- instagram_content_publish -->
              <div class="p-3 rounded-lg bg-surface-elevated border border-surface-border flex items-center justify-between">
                <div class="flex items-center gap-3">
                  {#if grantedScopes.includes('instagram_content_publish')}
                    <span class="w-6 h-6 rounded-full bg-emerald-950 border border-emerald-800 text-emerald-400 flex items-center justify-center text-xs font-bold">✓</span>
                  {:else}
                    <span class="w-6 h-6 rounded-full bg-red-950 border border-red-800 text-red-400 flex items-center justify-center text-xs font-bold">✕</span>
                  {/if}
                  <div>
                    <code class="text-xs text-white font-semibold">instagram_content_publish</code>
                    <p class="text-[11px] text-zinc-400">Upload media containers and publish Reels to Instagram</p>
                  </div>
                </div>
                <span class="badge {grantedScopes.includes('instagram_content_publish') ? 'badge-published' : 'badge-failed'} text-[11px]">
                  {grantedScopes.includes('instagram_content_publish') ? 'Granted' : 'Missing'}
                </span>
              </div>
            </div>
          </div>

          <!-- Additional Granted Permissions -->
          {#if additionalScopes.length > 0}
            <div>
              <span class="text-xs font-semibold text-zinc-400 uppercase tracking-wider block mb-2">Additional Granted Scopes</span>
              <div class="flex flex-wrap gap-2">
                {#each additionalScopes as scope}
                  <span class="badge badge-simulated text-[11px] font-mono text-zinc-300">
                    <span class="text-emerald-400">✓</span> {scope}
                  </span>
                {/each}
              </div>
            </div>
          {/if}

          <!-- Missing Scope Alert -->
          {#if missingScopes.length > 0}
            <div class="mt-4 p-3 rounded-lg bg-red-950/40 border border-red-800/60 text-xs text-red-300 flex items-start gap-2">
              <span>⚠</span>
              <p>Missing required publishing scopes: {missingScopes.join(', ')}. Please reconnect to grant full publishing permissions.</p>
            </div>
          {/if}
        </div>

        <!-- 4. META CONNECTION METADATA CARD -->
        <div class="card flex flex-col justify-between">
          <div>
            <h2 class="text-sm font-semibold text-white tracking-wide uppercase font-headline mb-3">Connection Details</h2>
            <div class="space-y-3 text-xs">
              <div class="pb-2 border-b border-surface-border">
                <span class="text-zinc-500 block text-[10px] uppercase">Meta App ID</span>
                <span class="text-white font-mono select-all text-xs">{status.meta_app_id || '1681791737015717'}</span>
              </div>
              <div class="pb-2 border-b border-surface-border">
                <span class="text-zinc-500 block text-[10px] uppercase">Configuration ID</span>
                <span class="text-white font-mono select-all text-xs">{status.meta_config_id || 'Not configured'}</span>
              </div>
              <div class="pb-2 border-b border-surface-border">
                <span class="text-zinc-500 block text-[10px] uppercase">Meta Graph API Version</span>
                <span class="text-white font-mono text-xs">{status.meta_api_version || 'v26.0'}</span>
              </div>
              <div class="pb-2 border-b border-surface-border">
                <span class="text-zinc-500 block text-[10px] uppercase">Login Variation</span>
                <span class="text-white text-xs">Unified Login (Facebook Login for Business)</span>
              </div>
              <div>
                <span class="text-zinc-500 block text-[10px] uppercase">Token Storage Mode</span>
                <span class="text-emerald-400 font-mono text-xs">Fernet Encrypted (PostgreSQL)</span>
              </div>
            </div>
          </div>

          <div class="mt-4 pt-3 border-t border-surface-border">
            <span class="text-[11px] text-zinc-500 block mb-2">Publishing Status:</span>
            <div class="flex items-center gap-2">
              <span class="badge {status.demo_mode ? 'badge-pending' : 'badge-published'} text-[11px]">
                {status.demo_mode ? 'DEMO MODE (Simulated)' : 'LIVE PUBLISHING ACTIVE'}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>

  {:else}
    <!-- ==================== NOT CONNECTED OR DEMO PLACEHOLDER ==================== -->
    <div class="space-y-6">

      {#if isDemoPlaceholder}
        <!-- Explicit Demo Placeholder Warning Banner -->
        <div class="p-4 rounded-xl bg-amber-950/40 border border-amber-800/60 flex items-start gap-3">
          <span class="text-amber-400 text-xl mt-0.5">⚠️</span>
          <div>
            <h3 class="text-sm font-bold text-amber-300 uppercase tracking-wider font-headline">DEMO ACCOUNT / NOT CONNECTED</h3>
            <p class="text-xs text-amber-200/80 mt-1 leading-relaxed">
              No live Meta account is connected. Adoraxe is running in demo mode with a synthetic placeholder (<code class="text-amber-200">@adoraxe_demo</code>).
              Reel publishing is currently simulated. Connect your Meta Instagram Creator or Business account to enable live publishing.
            </p>
          </div>
        </div>
      {/if}

      <!-- Connect Card -->
      <div class="card max-w-xl mx-auto text-center py-10 px-6">
        <div class="w-16 h-16 rounded-2xl bg-ig-gradient flex items-center justify-center text-white text-3xl mx-auto mb-5 shadow-lg shadow-primary/20">
          📸
        </div>
        <h2 class="text-xl font-bold text-white font-headline mb-2">Connect Instagram Creator Account</h2>
        <p class="text-sm text-zinc-400 mb-6 leading-relaxed max-w-md mx-auto">
          Authenticate using Meta Unified Login (Facebook Login for Business) to grant System-User access for publishing Reels via the official Meta Graph API.
        </p>

        {#if !status?.instagram_configured}
          <div class="mb-5 p-3 rounded-lg bg-amber-900/30 border border-amber-800/50 text-left">
            <p class="text-xs text-amber-400 font-medium mb-1">⚙ Meta API Not Configured</p>
            <p class="text-xs text-zinc-400">Set <code class="text-amber-300">META_APP_ID</code>, <code class="text-amber-300">META_APP_SECRET</code>, and <code class="text-amber-300">META_CONFIG_ID</code> in your .env file.</p>
          </div>
        {/if}

        <button
          class="btn-primary w-full justify-center mb-6 py-3 text-sm font-bold shadow-md shadow-primary/20"
          onclick={handleConnect}
          disabled={connecting || !status?.instagram_configured}
        >
          {connecting ? '⏳ Opening Meta Login…' : '📸 Connect with Meta Unified Login'}
        </button>

        <!-- Setup checklist -->
        <div class="text-left space-y-2.5 p-4 rounded-xl bg-surface-elevated border border-surface-border text-xs text-zinc-400 mb-5">
          <p class="font-semibold text-white mb-1">Meta Unified Login Requirements:</p>
          <div class="flex items-start gap-2">
            <span class="text-emerald-400 font-bold">1.</span>
            <span>Instagram profile must be switched to <strong class="text-zinc-200">Professional (Creator or Business)</strong></span>
          </div>
          <div class="flex items-start gap-2">
            <span class="text-emerald-400 font-bold">2.</span>
            <span>Account must be assigned to your <strong class="text-zinc-200">Meta Business Portfolio</strong></span>
          </div>
          <div class="flex items-start gap-2">
            <span class="text-emerald-400 font-bold">3.</span>
            <span>Authorize with <strong class="text-zinc-200">instagram_basic</strong> and <strong class="text-zinc-200">instagram_content_publish</strong></span>
          </div>
        </div>

        <p class="text-[11px] text-zinc-500">
          🔒 Secure OAuth 2.0 flow. Credentials and access tokens are encrypted with Fernet and never stored in plain text.
        </p>
      </div>
    </div>
  {/if}

  <!-- ==================== DISCONNECT CONFIRMATION MODAL ==================== -->
  {#if showDisconnectModal}
    <div class="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
      <div class="card max-w-md w-full border border-red-800/60 shadow-2xl bg-surface-card p-6">
        <div class="flex items-center gap-3 mb-3">
          <div class="w-10 h-10 rounded-full bg-red-950 border border-red-800 flex items-center justify-center text-red-400 text-lg">
            ⚠
          </div>
          <div>
            <h3 class="text-base font-bold text-white font-headline">Disconnect Instagram Account?</h3>
            <p class="text-xs text-zinc-400">@{status?.username}</p>
          </div>
        </div>

        <div class="p-3.5 rounded-lg bg-surface-elevated border border-surface-border text-xs text-zinc-300 space-y-2 mb-5">
          <p>Are you sure you want to disconnect this Instagram account?</p>
          <div class="space-y-1 text-zinc-400">
            <p class="text-emerald-400">✓ All past publishing history and videos remain intact.</p>
            <p class="text-amber-400">⚠️ Automated Reel publishing will be paused until an account is reconnected.</p>
          </div>
        </div>

        <div class="flex justify-end gap-3">
          <button
            class="btn-secondary text-xs"
            onclick={() => showDisconnectModal = false}
            disabled={disconnecting}
          >
            Cancel
          </button>
          <button
            class="btn-danger text-xs"
            onclick={handleConfirmDisconnect}
            disabled={disconnecting}
          >
            {disconnecting ? '⏳ Disconnecting…' : 'Yes, Disconnect Account'}
          </button>
        </div>
      </div>
    </div>
  {/if}
</div>
