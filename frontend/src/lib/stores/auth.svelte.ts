/**
 * Administrator Authentication Store – Adoraxe Automation Portal.
 * Manages single-owner administrator session using sessionStorage only.
 * SSR-safe: never accesses browser storage during server rendering.
 */
import { verifyAdminAuth } from '#lib/api/client';

class AuthStore {
  isAuthenticated = $state(false);
  isChecking = $state(true);
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

    try {
      const savedKey = sessionStorage.getItem('adoraxe_admin_key');
      if (!savedKey) {
        // Test if the backend requires authentication (dev mode might be open)
        const works = await verifyAdminAuth('');
        if (works) {
          // Backend has auth disabled (local development mode)
          this.isAuthenticated = true;
        } else {
          this.isAuthenticated = false;
        }
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
