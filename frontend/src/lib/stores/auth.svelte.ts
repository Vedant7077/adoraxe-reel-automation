/**
 * Administrator Authentication Store – Adoraxe Automation Portal.
 * Manages single-owner administrator session using sessionStorage only.
 * SSR-safe: never accesses browser storage during server rendering.
 */
import { verifyAdminAuth } from '#lib/api/client';

function checkIsLocal(): boolean {
  if (typeof window === 'undefined') return false;
  const host = window.location.hostname;
  return host === 'localhost' || host === '127.0.0.1' || import.meta.env.DEV;
}

class AuthStore {
  // On localhost / development, default to authenticated immediately with zero spinner delay.
  // In production / remote deployment, start in checking mode to protect public access.
  isAuthenticated = $state(typeof window !== 'undefined' ? checkIsLocal() : false);
  isChecking = $state(typeof window !== 'undefined' ? !checkIsLocal() : true);
  loginError = $state('');

  constructor() {
    if (typeof window !== 'undefined') {
      window.addEventListener('adoraxe:unauthorized', () => {
        this.handleUnauthorized();
      });
    }
  }

  async init() {
    if (typeof window === 'undefined') {
      this.isChecking = false;
      return;
    }

    const isLocal = checkIsLocal();
    const savedKey = sessionStorage.getItem('adoraxe_admin_key');

    // On localhost, if no key is saved and dev mode is active, authenticate immediately without blocking UI
    if (isLocal && !savedKey) {
      this.isAuthenticated = true;
      this.isChecking = false;
      // Non-blocking background verification: if backend has AUTH_ENABLED=true even locally, handle it
      verifyAdminAuth('').then((works) => {
        if (!works) {
          this.isAuthenticated = false;
        }
      }).catch(() => {});
      return;
    }

    // In production or when a saved key exists, perform verification
    try {
      if (!savedKey) {
        const works = await verifyAdminAuth('');
        this.isAuthenticated = works;
      } else {
        const valid = await verifyAdminAuth(savedKey);
        if (valid) {
          this.isAuthenticated = true;
          this.loginError = '';
        } else {
          sessionStorage.removeItem('adoraxe_admin_key');
          this.isAuthenticated = false;
          this.loginError = 'Previous session expired or invalid key. Please log in again.';
        }
      }
    } catch {
      this.isAuthenticated = false;
    } finally {
      this.isChecking = false;
    }
  }

  async login(key: string): Promise<boolean> {
    const trimmed = (key || '').trim();
    if (!trimmed) {
      this.loginError = 'Please enter your administrator access key.';
      return false;
    }

    this.loginError = '';
    try {
      const valid = await verifyAdminAuth(trimmed);
      if (valid) {
        if (typeof window !== 'undefined') {
          sessionStorage.setItem('adoraxe_admin_key', trimmed);
        }
        this.isAuthenticated = true;
        this.loginError = '';
        return true;
      } else {
        this.loginError = 'Invalid administrator access key. Please check your Render configuration.';
        return false;
      }
    } catch (e: any) {
      this.loginError = e.message || 'Authentication failed. Please check network connection.';
      return false;
    }
  }

  logout() {
    if (typeof window !== 'undefined') {
      sessionStorage.removeItem('adoraxe_admin_key');
    }
    this.isAuthenticated = false;
    this.loginError = '';
  }

  handleUnauthorized() {
    this.isAuthenticated = false;
    this.loginError = 'Session authorization failed. Please re-enter your administrator key.';
  }
}

export const authStore = new AuthStore();
