/**
 * Persistent publishing state manager.
 * Survives page navigation between /queue and /history.
 * Recovers active jobs and captions from PostgreSQL on load and browser refresh.
 */
import { getActivePublishJob, getPublishJob, publishReel, reconcilePublishJob } from '../api/client';

export interface PublishingJobState {
  id: string;
  video_id: string;
  instagram_account_id?: string;
  caption?: string;
  status: string;
  current_stage?: string;
  progress: number;
  instagram_container_id?: string;
  instagram_media_id?: string;
  permalink?: string;
  error_message?: string;
  cloudinary_url?: string;
  created_at: string;
  updated_at: string;
  published_at?: string;
}

class PublishingStore {
  activeJob = $state<PublishingJobState | null>(null);
  caption = $state<string>('');
  selectedVideoId = $state<string | null>(null);
  isPublishing = $state<boolean>(false);
  isPolling = $state<boolean>(false);
  isReconciling = $state<boolean>(false);
  errorMessage = $state<string>('');
  successMessage = $state<string>('');

  private pollTimer: any = null;
  private initialized = false;

  async init(accountId?: string) {
    if (this.initialized && this.activeJob) return;
    this.initialized = true;
    try {
      const res = await getActivePublishJob(accountId);
      if (res.has_active_job && res.job) {
        this.activeJob = res.job;
        if (res.job.caption && !this.caption) {
          this.caption = res.job.caption;
        }
        if (res.job.video_id) {
          this.selectedVideoId = res.job.video_id;
        }

        const terminalStatuses = ['PUBLISHED', 'FAILED', 'SIMULATED', 'RECONCILIATION_REQUIRED'];
        if (!terminalStatuses.includes(res.job.status)) {
          this.startPolling(res.job.id);
        }
      }
    } catch (e: any) {
      console.warn('Could not restore active publishing job:', e);
    }
  }

  async publish(videoId: string, captionText: string, rightsConfirmed: boolean, accountId?: string) {
    this.isPublishing = true;
    this.errorMessage = '';
    this.successMessage = '';
    this.caption = captionText;
    this.selectedVideoId = videoId;

    try {
      const res = await publishReel(videoId, captionText, rightsConfirmed, accountId);
      this.activeJob = {
        id: res.job_id,
        video_id: videoId,
        instagram_account_id: accountId,
        caption: captionText,
        status: res.status || 'QUEUED',
        current_stage: res.current_stage || 'QUEUED',
        progress: 5,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      this.startPolling(res.job_id);
      return res;
    } catch (e: any) {
      this.errorMessage = e.message || 'Failed to submit publishing job';
      throw e;
    } finally {
      this.isPublishing = false;
    }
  }

  startPolling(jobId: string) {
    if (this.pollTimer) {
      clearTimeout(this.pollTimer);
      this.pollTimer = null;
    }
    this.isPolling = true;

    const poll = async () => {
      try {
        const job = await getPublishJob(jobId);
        this.activeJob = job;
        if (job.caption) {
          this.caption = job.caption;
        }

        const terminalStatuses = ['PUBLISHED', 'FAILED', 'SIMULATED', 'RECONCILIATION_REQUIRED'];
        if (terminalStatuses.includes(job.status)) {
          this.isPolling = false;
          if (job.status === 'PUBLISHED') {
            this.successMessage = 'Reel published successfully to Instagram!';
          } else if (job.status === 'FAILED') {
            this.errorMessage = job.error_message || 'Publishing failed';
          }
          return;
        }

        this.pollTimer = setTimeout(poll, 2000);
      } catch (e: any) {
        console.warn('Error polling publish job:', e);
        // Retry polling unless terminal
        this.pollTimer = setTimeout(poll, 3000);
      }
    };

    poll();
  }

  async reconcile() {
    if (!this.activeJob) return;
    this.isReconciling = true;
    this.errorMessage = '';
    try {
      const res = await reconcilePublishJob(this.activeJob.id);
      if (res.success) {
        this.successMessage = res.message;
        const updated = await getPublishJob(this.activeJob.id);
        this.activeJob = updated;
      } else {
        this.errorMessage = res.message;
        const updated = await getPublishJob(this.activeJob.id);
        this.activeJob = updated;
      }
    } catch (e: any) {
      this.errorMessage = `Reconciliation error: ${e.message || e}`;
    } finally {
      this.isReconciling = false;
    }
  }

  reset() {
    if (this.pollTimer) {
      clearTimeout(this.pollTimer);
      this.pollTimer = null;
    }
    this.activeJob = null;
    this.caption = '';
    this.selectedVideoId = null;
    this.errorMessage = '';
    this.successMessage = '';
    this.isPolling = false;
  }
}

export const publishingStore = new PublishingStore();
