<script lang="ts">
  import { authStore } from '#lib/stores/auth.svelte';

  let keyInput = $state('');
  let submitting = $state(false);
  let showPassword = $state(false);

  async function handleLogin() {
    if (!keyInput.trim() || submitting) return;
    submitting = true;
    try {
      await authStore.login(keyInput);
    } finally {
      submitting = false;
    }
  }
</script>

<div class="min-h-screen w-full flex items-center justify-center p-4 bg-surface relative overflow-hidden">
  <!-- Ambient background glow -->
  <div class="absolute -top-32 -left-32 w-96 h-96 bg-primary/10 rounded-full blur-3xl pointer-events-none"></div>
  <div class="absolute -bottom-32 -right-32 w-96 h-96 bg-secondary/10 rounded-full blur-3xl pointer-events-none"></div>

  <div class="w-full max-w-md card border-surface-border shadow-2xl relative z-10 p-6 sm:p-8 bg-surface-card/90 backdrop-blur-xl">
    <!-- Header with Adoraxe branding -->
    <div class="text-center mb-6">
      <div class="w-14 h-14 rounded-2xl bg-ig-gradient flex items-center justify-center text-white font-extrabold text-2xl mx-auto mb-4 shadow-lg ring-4 ring-primary/20">
        A
      </div>
      <h1 class="text-xl font-bold text-white font-headline tracking-tight">Adoraxe Administrator</h1>
      <p class="text-xs text-zinc-400 mt-1.5 max-w-xs mx-auto">
        Single-owner automation portal protected by administrator key authentication.
      </p>
    </div>

    {#if authStore.loginError}
      <div class="mb-5 p-3.5 rounded-xl bg-red-950/60 border border-red-800/60 text-xs text-red-300 flex items-start gap-2.5 animate-shake">
        <span class="text-red-400 font-bold text-sm">⚠</span>
        <div class="flex-1 min-w-0">
          <p class="font-medium text-red-200">Authentication Failed</p>
          <p class="text-[11px] text-red-300/90 mt-0.5">{authStore.loginError}</p>
        </div>
      </div>
    {/if}

    <form onsubmit={(e) => { e.preventDefault(); handleLogin(); }} class="space-y-4">
      <div>
        <label for="admin-key-input" class="block text-xs font-semibold uppercase tracking-wider text-zinc-300 mb-1.5 font-headline">
          Administrator Key
        </label>
        <div class="relative">
          <input
            id="admin-key-input"
            type={showPassword ? 'text' : 'password'}
            bind:value={keyInput}
            placeholder="Paste your ADMIN_API_KEY…"
            disabled={submitting}
            autocomplete="current-password"
            class="input w-full pr-10 text-sm font-mono tracking-wider py-2.5 bg-surface-elevated border-surface-border text-white placeholder-zinc-500 rounded-xl focus:border-primary focus:ring-1 focus:ring-primary"
          />
          <button
            type="button"
            class="absolute inset-y-0 right-0 pr-3 flex items-center text-zinc-400 hover:text-zinc-200 text-xs"
            onclick={() => showPassword = !showPassword}
            tabindex="-1"
            title={showPassword ? 'Hide key' : 'Show key'}
          >
            {showPassword ? '👁' : '👁‍🗨'}
          </button>
        </div>
        <p class="text-[11px] text-zinc-500 mt-1.5">
          Configured as <code class="text-amber-400 font-mono">ADMIN_API_KEY</code> in Render / environment settings.
        </p>
      </div>

      <button
        type="submit"
        disabled={submitting || !keyInput.trim()}
        class="btn-primary w-full py-2.5 rounded-xl text-sm font-bold shadow-lg shadow-primary/20 flex items-center justify-center gap-2"
      >
        {#if submitting}
          <span class="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
          <span>Verifying Key…</span>
        {:else}
          <span>🔐 Unlock Portal</span>
        {/if}
      </button>
    </form>

    <!-- Security footnotes -->
    <div class="mt-6 pt-5 border-t border-surface-border text-center space-y-2">
      <div class="flex items-center justify-center gap-4 text-[11px] text-zinc-400">
        <span class="flex items-center gap-1">
          <span class="text-emerald-400">✓</span> Session Memory Only
        </span>
        <span class="flex items-center gap-1">
          <span class="text-emerald-400">✓</span> Timing Safe
        </span>
        <span class="flex items-center gap-1">
          <span class="text-emerald-400">✓</span> Fail-Closed
        </span>
      </div>
      <p class="text-[10px] text-zinc-500">
        Key is never persisted to disk, localStorage, or third-party tracking.
      </p>
    </div>
  </div>
</div>
