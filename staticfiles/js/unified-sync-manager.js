/**
 * UnifiedSyncManager
 * Handles bidirectional synchronization between IndexedDB and Backend API.
 */
class UnifiedSyncManager {
    constructor() {
        this.deviceId = this.getOrCreateDeviceId();
        this.deviceType = this.detectDeviceType();
        this.isOnline = navigator.onLine;
        this.isSyncing = false;
        this.syncInterval = null;

        // Configuration
        this.API_BASE = '/api/';
        this.SYNC_INTERVAL_MS = 2 * 60 * 1000; // 2 minutes

        this.init();
    }

    init() {
        // Event Listeners
        window.addEventListener('online', () => {
            this.isOnline = true;
            this.updateStatus('online');
            this.triggerSync();
        });

        window.addEventListener('offline', () => {
            this.isOnline = false;
            this.updateStatus('offline');
        });

        // Start periodic sync
        this.startSyncLoop();

        // Initial check
        this.checkUnsyncedData();
    }

    getOrCreateDeviceId() {
        let id = localStorage.getItem('device_id');
        if (!id) {
            id = 'dev_' + Math.random().toString(36).substr(2, 9) + Date.now().toString(36);
            localStorage.setItem('device_id', id);
        }
        return id;
    }

    detectDeviceType() {
        const width = window.innerWidth;
        if (width < 768) return 'mobile';
        if (width < 1024) return 'tablet';
        return 'desktop';
    }

    updateStatus(status, message = '') {
        const indicator = document.getElementById('sync-status-indicator');
        if (indicator) {
            indicator.className = `status-${status}`;
            indicator.title = message || status;
            indicator.innerText = status === 'syncing' ? '↻' : (status === 'online' || status === 'synced' ? '✓' : '⊗');
        }

        // Dispatch event for UI
        window.dispatchEvent(new CustomEvent('sync-status-change', {
            detail: { status, message }
        }));
    }

    startSyncLoop() {
        if (this.syncInterval) clearInterval(this.syncInterval);
        this.syncInterval = setInterval(() => {
            if (this.isOnline) this.triggerSync();
        }, this.SYNC_INTERVAL_MS);
    }

    async triggerSync() {
        if (this.isSyncing || !this.isOnline) return;

        this.isSyncing = true;
        this.updateStatus('syncing');

        try {
            // 1. Push Local Transactions
            await this.pushTransactions();

            // 2. Pull Updates (Products, Inventory)
            await this.pullUpdates();

            this.updateStatus('synced');
        } catch (error) {
            console.error('Sync failed:', error);
            this.updateStatus('error', error.message);
        } finally {
            this.isSyncing = false;
        }
    }

    async pushTransactions() {
        const pending = await DBManager.getPendingTransactions();
        if (pending.length === 0) return;

        // Process in batches if needed, for now one by one or small batch
        for (const tx of pending) {
            try {
                const response = await fetch(`${this.API_BASE}transactions/`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Device-ID': this.deviceId,
                        'X-CSRFToken': this.getCsrfToken()
                    },
                    body: JSON.stringify({
                        ...tx,
                        device_id: this.deviceId,
                        device_type: this.deviceType
                    })
                });

                if (!response.ok) throw new Error(`HTTP ${response.status}`);

                const result = await response.json();
                if (result.status === 'created' || result.status === 'exists') {
                    await DBManager.markTransactionSynced(tx.client_id, result.server_id);
                }
            } catch (err) {
                console.error(`Failed to sync tx ${tx.client_id}`, err);
                // Update retry count logic here
            }
        }
    }

    async pullUpdates() {
        const lastSync = localStorage.getItem('last_sync_timestamp');
        let url = `${this.API_BASE}sync/changes/`;
        if (lastSync) {
            url += `?last_sync=${encodeURIComponent(lastSync)}`;
        }

        const response = await fetch(url, {
            headers: {
                'X-Device-ID': this.deviceId
            }
        });

        if (!response.ok) throw new Error('Failed to pull updates');

        const data = await response.json();

        // Apply updates
        if (data.products && data.products.length > 0) {
            await DBManager.updateProducts(data.products);
        }

        if (data.inventory && data.inventory.length > 0) {
            await DBManager.updateInventory(data.inventory);
        }

        // Update timestamp
        if (data.server_timestamp) {
            localStorage.setItem('last_sync_timestamp', data.server_timestamp);
        }
    }

    async checkUnsyncedData() {
        const pending = await DBManager.getPendingTransactions();
        if (pending.length > 0) {
            this.updateStatus('pending', `${pending.length} unsynced items`);
            if (this.isOnline) {
                this.triggerSync(); // Auto-trigger if online
            }
        }
    }

    getCsrfToken() {
        const name = 'csrftoken';
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }
}

// Instantiate globally
window.syncManager = new UnifiedSyncManager();
