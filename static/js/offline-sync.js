/**
 * OfflineSyncManager - Handles offline sale queuing and syncing for POS.
 * 
 * Uses IndexedDB to store pending sales when offline.
 * Automatically syncs when connection is restored.
 */
class OfflineSyncManager {
    constructor(options = {}) {
        this.dbName = 'pos_offline_db';
        this.dbVersion = 1;
        this.storeName = 'pending_sales';
        this.db = null;
        this.isSyncing = false;
        this.csrfToken = options.csrfToken || '';
        this.syncUrl = options.syncUrl || '/sales/api/sync-offline/';
        this.batchSyncUrl = options.batchSyncUrl || '/sales/api/sync-offline-batch/';
        this.batchSize = options.batchSize || 25;
        this.checkoutUrl = options.checkoutUrl || '/sales/api/checkout/';
        
        // Callbacks
        this.onStatusChange = options.onStatusChange || (() => {});
        this.onSyncProgress = options.onSyncProgress || (() => {});
        this.onSyncComplete = options.onSyncComplete || (() => {});
        this.onSyncError = options.onSyncError || (() => {});
        this.onPendingCountChange = options.onPendingCountChange || (() => {});
        
        // Initialize
        this._initDB();
        this._initNetworkListeners();
    }

    // ==================== Database ====================

    _initDB() {
        return new Promise((resolve, reject) => {
            const request = indexedDB.open(this.dbName, this.dbVersion);

            request.onupgradeneeded = (event) => {
                const db = event.target.result;
                if (!db.objectStoreNames.contains(this.storeName)) {
                    const store = db.createObjectStore(this.storeName, { keyPath: 'client_sale_id' });
                    store.createIndex('created_at', 'created_at', { unique: false });
                    store.createIndex('status', 'status', { unique: false });
                }
            };

            request.onsuccess = (event) => {
                this.db = event.target.result;
                this._updatePendingCount();
                resolve(this.db);
            };

            request.onerror = (event) => {
                console.error('IndexedDB error:', event.target.error);
                reject(event.target.error);
            };
        });
    }

    _getDB() {
        if (this.db) return Promise.resolve(this.db);
        return this._initDB();
    }

    // ==================== Network Detection ====================

    _initNetworkListeners() {
        window.addEventListener('online', () => {
            console.log('[OfflineSync] Back online');
            this.onStatusChange(true);
            // Auto-sync after a short delay to let the connection stabilize
            setTimeout(() => this.syncPendingSales(), 1500);
        });

        window.addEventListener('offline', () => {
            console.log('[OfflineSync] Gone offline');
            this.onStatusChange(false);
        });

        // Initial status
        this.onStatusChange(navigator.onLine);
    }

    isOnline() {
        return navigator.onLine;
    }

    // ==================== Sale Queue ====================

    /**
     * Generate a UUID v4 for client-side sale identification.
     */
    _generateUUID() {
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
            const r = Math.random() * 16 | 0;
            const v = c === 'x' ? r : (r & 0x3 | 0x8);
            return v.toString(16);
        });
    }

    /**
     * Queue a sale for later sync. Used when offline or when the server is unreachable.
     * @param {Object} saleData - The sale data (same format as checkout API)
     * @returns {Promise<Object>} - The queued sale record
     */
    async queueSale(saleData) {
        const db = await this._getDB();
        
        const record = {
            client_sale_id: this._generateUUID(),
            sale_data: saleData,
            created_at: new Date().toISOString(),
            status: 'pending', // pending, syncing, synced, error
            error: null,
            attempts: 0,
            last_attempt: null,
        };

        return new Promise((resolve, reject) => {
            const tx = db.transaction(this.storeName, 'readwrite');
            const store = tx.objectStore(this.storeName);
            const request = store.add(record);

            request.onsuccess = () => {
                this._updatePendingCount();
                resolve(record);
            };

            request.onerror = (event) => {
                console.error('[OfflineSync] Failed to queue sale:', event.target.error);
                reject(event.target.error);
            };
        });
    }

    /**
     * Get all pending (un-synced) sales.
     */
    async getPendingSales() {
        const db = await this._getDB();
        
        return new Promise((resolve, reject) => {
            const tx = db.transaction(this.storeName, 'readonly');
            const store = tx.objectStore(this.storeName);
            const index = store.index('status');
            const request = index.getAll('pending');

            request.onsuccess = () => resolve(request.result || []);
            request.onerror = (event) => reject(event.target.error);
        });
    }

    /**
     * Get all sales (any status) from IndexedDB.
     */
    async getAllSales() {
        const db = await this._getDB();
        
        return new Promise((resolve, reject) => {
            const tx = db.transaction(this.storeName, 'readonly');
            const store = tx.objectStore(this.storeName);
            const request = store.getAll();

            request.onsuccess = () => resolve(request.result || []);
            request.onerror = (event) => reject(event.target.error);
        });
    }

    /**
     * Get count of pending sales.
     */
    async getPendingCount() {
        const sales = await this.getPendingSales();
        return sales.length;
    }

    /**
     * Update the status of a sale in IndexedDB.
     */
    async _updateSaleStatus(clientSaleId, status, error = null) {
        const db = await this._getDB();
        
        return new Promise((resolve, reject) => {
            const tx = db.transaction(this.storeName, 'readwrite');
            const store = tx.objectStore(this.storeName);
            const getReq = store.get(clientSaleId);

            getReq.onsuccess = () => {
                const record = getReq.result;
                if (record) {
                    record.status = status;
                    record.error = error;
                    record.attempts += 1;
                    record.last_attempt = new Date().toISOString();
                    store.put(record);
                }
                resolve(record);
            };

            getReq.onerror = (event) => reject(event.target.error);
        });
    }

    /**
     * Remove a synced sale from IndexedDB.
     */
    async _removeSale(clientSaleId) {
        const db = await this._getDB();
        
        return new Promise((resolve, reject) => {
            const tx = db.transaction(this.storeName, 'readwrite');
            const store = tx.objectStore(this.storeName);
            const request = store.delete(clientSaleId);

            request.onsuccess = () => {
                this._updatePendingCount();
                resolve();
            };
            request.onerror = (event) => reject(event.target.error);
        });
    }

    /**
     * Clear all synced sales from IndexedDB.
     */
    async clearSyncedSales() {
        const db = await this._getDB();
        const allSales = await this.getAllSales();
        
        const synced = allSales.filter(s => s.status === 'synced');
        for (const sale of synced) {
            await this._removeSale(sale.client_sale_id);
        }
        this._updatePendingCount();
    }

    // ==================== Sync Engine ====================

    /**
     * Sync all pending sales to the server using the high-performance Batch API,
     * falling back to sequential single-sale sync if the batch endpoint is unreachable.
     */
    async syncPendingSales() {
        if (this.isSyncing) {
            console.log('[OfflineSync] Already syncing, skipping...');
            return;
        }

        if (!this.isOnline()) {
            console.log('[OfflineSync] Still offline, cannot sync.');
            return;
        }

        const pendingSales = await this.getPendingSales();
        if (pendingSales.length === 0) {
            console.log('[OfflineSync] No pending sales to sync.');
            return;
        }

        this.isSyncing = true;
        console.log(`[OfflineSync] Batch syncing ${pendingSales.length} pending sales...`);

        let synced = 0;
        let conflicts = 0;
        let failed = 0;

        // Process in chunks of batchSize
        for (let i = 0; i < pendingSales.length; i += this.batchSize) {
            if (!this.isOnline()) {
                console.warn('[OfflineSync] Network dropped during batch sync.');
                break;
            }

            const chunk = pendingSales.slice(i, i + this.batchSize);

            // Mark this chunk as 'syncing'
            for (const s of chunk) {
                await this._updateSaleStatus(s.client_sale_id, 'syncing');
            }

            try {
                const batchPayload = {
                    sales: chunk.map(s => ({
                        ...s.sale_data,
                        client_sale_id: s.client_sale_id,
                        offline_created_at: s.created_at
                    }))
                };

                const response = await fetch(this.batchSyncUrl, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': this.csrfToken,
                    },
                    body: JSON.stringify(batchPayload)
                });

                if (!response.ok) {
                    // Fallback to sequential single sync for this chunk if batch fails with HTTP error
                    console.warn(`[OfflineSync] Batch sync HTTP ${response.status}, falling back to sequential sync.`);
                    const seqRes = await this._syncChunkSequentially(chunk);
                    synced += seqRes.synced;
                    conflicts += seqRes.conflicts;
                    failed += seqRes.failed;
                    continue;
                }

                const batchResult = await response.json();
                if (batchResult.success && Array.isArray(batchResult.results)) {
                    for (const res of batchResult.results) {
                        const clientSaleId = res.client_sale_id;
                        if (res.success) {
                            await this._removeSale(clientSaleId);
                            if (res.has_conflicts) {
                                conflicts++;
                            } else {
                                synced++;
                            }
                        } else {
                            await this._updateSaleStatus(clientSaleId, 'error', res.error || 'Sync validation failed');
                            failed++;
                        }
                    }
                } else {
                    const seqRes = await this._syncChunkSequentially(chunk);
                    synced += seqRes.synced;
                    conflicts += seqRes.conflicts;
                    failed += seqRes.failed;
                }

            } catch (err) {
                console.error('[OfflineSync] Network error during batch sync:', err);
                for (const s of chunk) {
                    await this._updateSaleStatus(s.client_sale_id, 'pending', err.message);
                }
                failed += chunk.length;
                break; // Stop further batches if network dropped
            }

            this.onSyncProgress(Math.min(i + chunk.length, pendingSales.length), pendingSales.length);
        }

        this.isSyncing = false;
        await this._updatePendingCount();

        console.log(`[OfflineSync] Sync complete: ${synced} clean, ${conflicts} conflicts, ${failed} failed`);
        this.onSyncComplete(synced, failed, conflicts);
    }

    /**
     * Sequential single-sale sync fallback for a slice of sales.
     */
    async _syncChunkSequentially(chunk) {
        let synced = 0;
        let conflicts = 0;
        let failed = 0;

        for (const sale of chunk) {
            try {
                const result = await this._syncSingleSale(sale);
                if (result.success) {
                    await this._removeSale(sale.client_sale_id);
                    if (result.has_conflicts) {
                        conflicts++;
                    } else {
                        synced++;
                    }
                } else {
                    await this._updateSaleStatus(sale.client_sale_id, 'error', result.error);
                    failed++;
                }
            } catch (error) {
                await this._updateSaleStatus(sale.client_sale_id, 'pending', error.message);
                failed++;
            }
        }
        return { synced, conflicts, failed };
    }

    /**
     * Sync a single offline sale to the server.
     */
    async _syncSingleSale(sale) {
        const payload = {
            ...sale.sale_data,
            client_sale_id: sale.client_sale_id,
            offline_created_at: sale.created_at,
        };

        const response = await fetch(this.syncUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': this.csrfToken,
            },
            body: JSON.stringify(payload),
        });

        if (!response.ok) {
            if (response.status >= 500) {
                // Server error - throw to trigger retry logic
                throw new Error(`Server error: ${response.status}`);
            }
            // Client error (400, etc.) - permanent failure
            const data = await response.json().catch(() => ({ error: 'Unknown error' }));
            return { success: false, error: data.error || 'Request failed' };
        }

        const data = await response.json();
        return data;
    }

    // ==================== Attempt Sale (Online First) ====================

    /**
     * Attempt to complete a sale. Tries online first, falls back to offline queue.
     * @param {Object} saleData - The sale checkout data
     * @returns {Promise<Object>} - Result with mode ('online' or 'offline')
     */
    async attemptSale(saleData) {
        if (this.isOnline()) {
            try {
                const response = await fetch(this.checkoutUrl, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': this.csrfToken,
                    },
                    body: JSON.stringify(saleData),
                });

                if (response.ok) {
                    const data = await response.json();
                    if (data.success) {
                        return { mode: 'online', ...data };
                    }
                    // Server returned an error (e.g., validation) - don't queue offline
                    return { mode: 'online', success: false, error: data.error };
                }

                // Server unreachable or 500 error - queue offline
                if (response.status >= 500) {
                    console.warn('[OfflineSync] Server error, queuing offline');
                    const record = await this.queueSale(saleData);
                    return {
                        mode: 'offline',
                        success: true,
                        client_sale_id: record.client_sale_id,
                        message: 'Sale saved offline. Will sync when connection is restored.',
                    };
                }

                // Other client errors
                const errorData = await response.json().catch(() => ({}));
                return { mode: 'online', success: false, error: errorData.error || 'Request failed' };

            } catch (error) {
                // Network error - queue offline
                console.warn('[OfflineSync] Network error, queuing offline:', error.message);
                const record = await this.queueSale(saleData);
                return {
                    mode: 'offline',
                    success: true,
                    client_sale_id: record.client_sale_id,
                    message: 'Sale saved offline. Will sync when connection is restored.',
                };
            }
        }

        // Already offline - queue directly
        const record = await this.queueSale(saleData);
        return {
            mode: 'offline',
            success: true,
            client_sale_id: record.client_sale_id,
            message: 'Sale saved offline. Will sync when connection is restored.',
        };
    }

    // ==================== Helpers ====================

    async _updatePendingCount() {
        try {
            const count = await this.getPendingCount();
            this.onPendingCountChange(count);
        } catch (e) {
            console.error('[OfflineSync] Error updating pending count:', e);
        }
    }
}
