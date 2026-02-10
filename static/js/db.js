/**
 * localized IndexedDB using Dexie.js
 * Stores offline data for transactions, products, inventory, and sync queue.
 */

// Initialize Database
const db = new Dexie('POS_DB');

db.version(1).stores({
    transactions: '++id, client_id, tenant_id, shop_id, synced, created_at, device_id, device_type, [tenant_id+shop_id]',
    products: 'id, tenant_id, shop_id, sku, name, updated_at, version, [tenant_id+shop_id]',
    inventory: 'id, product_id, tenant_id, shop_id, quantity, updated_at, version',
    sync_queue: '++id, type, timestamp, device_id, synced, retry_count',
    cache: 'key, expires_at',
    sync_log: '++id, entity_type, entity_id, action, timestamp, status'
});

// Helper class for DB operations
class DBManager {
    static async init() {
        if (!db.isOpen()) {
            await db.open();
        }
        return db;
    }

    // Transactions
    static async saveTransaction(data) {
        return await db.transactions.add({
            ...data,
            synced: 0,
            created_at: new Date().toISOString(),
            retry_count: 0
        });
    }

    static async getPendingTransactions() {
        return await db.transactions.where('synced').equals(0).toArray();
    }

    static async markTransactionSynced(client_id, server_id) {
        return await db.transactions
            .where('client_id').equals(client_id)
            .modify({ synced: 1, server_id: server_id, synced_at: new Date().toISOString() });
    }

    // Products & Inventory (Read-heavy)
    static async updateProducts(products) {
        return await db.products.bulkPut(products);
    }

    static async updateInventory(inventoryItems) {
        return await db.inventory.bulkPut(inventoryItems);
    }

    static async getProductBySku(sku) {
        return await db.products.where('sku').equals(sku).first();
    }

    static async getInventory(productId) {
        return await db.inventory.where('product_id').equals(productId).first();
    }

    // Sync Queue
    static async addToSyncQueue(type, data) {
        return await db.sync_queue.add({
            type,
            data,
            timestamp: new Date().toISOString(),
            synced: 0,
            retry_count: 0
        });
    }
}
