// js/cache-manager.js - 前端快取管理系統
class CacheManager {
  constructor() {
    this.cache = new Map();
    this.cacheTTL = new Map();
    this.stats = {
      hits: 0,
      misses: 0,
      sets: 0,
      memory: 0
    };
    
    // 🧹 定期清理過期快取（每5分鐘）
    setInterval(() => this.cleanup(), 5 * 60 * 1000);
    
    // 📊 監控記憶體使用量
    setInterval(() => this.updateMemoryStats(), 30 * 1000);
  }

  /**
   * 設定快取
   * @param {string} key - 快取鍵
   * @param {any} data - 要快取的資料
   * @param {number} ttl - 存活時間（毫秒）
   */
  set(key, data, ttl = 5 * 60 * 1000) {
    try {
      // 🔐 序列化資料以計算大小
      const serialized = JSON.stringify(data);
      
      this.cache.set(key, {
        data: data,
        size: new Blob([serialized]).size,
        timestamp: Date.now()
      });
      
      this.cacheTTL.set(key, Date.now() + ttl);
      this.stats.sets++;
      
      console.log(`💾 快取設定: ${key} (${this.getHumanSize(new Blob([serialized]).size)}, TTL: ${ttl/1000}s)`);
      
      // 🚨 檢查記憶體限制（50MB）
      this.checkMemoryLimit();
      
    } catch (error) {
      console.error('快取設定失敗:', error);
    }
  }

  /**
   * 取得快取資料
   * @param {string} key - 快取鍵
   * @returns {any|null} 快取的資料或null
   */
  get(key) {
    const ttl = this.cacheTTL.get(key);
    
    if (ttl && Date.now() < ttl && this.cache.has(key)) {
      this.stats.hits++;
      const cached = this.cache.get(key);
      console.log(`🎯 快取命中: ${key} (${this.getHumanSize(cached.size)})`);
      return cached.data;
    }
    
    // 清理過期的快取
    if (this.cache.has(key)) {
      this.delete(key);
    }
    
    this.stats.misses++;
    console.log(`❌ 快取未命中: ${key}`);
    return null;
  }

  /**
   * 刪除特定快取
   * @param {string} key - 快取鍵
   */
  delete(key) {
    if (this.cache.has(key)) {
      this.cache.delete(key);
      this.cacheTTL.delete(key);
      console.log(`🗑️ 快取刪除: ${key}`);
    }
  }

  /**
   * 清除所有快取
   */
  clear() {
    this.cache.clear();
    this.cacheTTL.clear();
    this.stats = { hits: 0, misses: 0, sets: 0, memory: 0 };
    console.log('🧹 所有快取已清除');
  }

  /**
   * 清除使用者相關快取
   * @param {string} userId - 使用者ID
   */
  clearUserCache(userId = '') {
    const userPattern = userId ? `_${userId}_` : '_user_';
    const keysToDelete = Array.from(this.cache.keys()).filter(key => 
      key.includes(userPattern) || key.includes('status') || key.includes('inventory')
    );
    
    keysToDelete.forEach(key => this.delete(key));
    console.log(`🧹 已清除 ${keysToDelete.length} 個使用者快取項目`);
  }

  /**
   * 清除靜態資料快取
   */
  clearStaticCache() {
    const staticKeys = ['element_table', 'exp_table', 'dungeon_table', 'items_table', 'equips_table'];
    staticKeys.forEach(key => this.delete(key));
    console.log('🧹 靜態資料快取已清除');
  }

  /**
   * 清理過期快取
   */
  cleanup() {
    const now = Date.now();
    let cleanedCount = 0;
    
    for (const [key, ttl] of this.cacheTTL.entries()) {
      if (now >= ttl) {
        this.delete(key);
        cleanedCount++;
      }
    }
    
    if (cleanedCount > 0) {
      console.log(`🧹 已清理 ${cleanedCount} 個過期快取項目`);
    }
  }

  /**
   * 檢查記憶體限制
   */
  checkMemoryLimit() {
    const totalSize = this.getTotalSize();
    const limitMB = 50;
    const limitBytes = limitMB * 1024 * 1024;
    
    if (totalSize > limitBytes) {
      console.warn(`⚠️ 快取記憶體超過限制 (${this.getHumanSize(totalSize)}/${limitMB}MB)`);
      this.evictOldestItems(Math.ceil(totalSize * 0.2)); // 移除20%最舊的項目
    }
  }

  /**
   * 移除最舊的快取項目
   * @param {number} targetBytes - 要釋放的位元組數
   */
  evictOldestItems(targetBytes) {
    const items = Array.from(this.cache.entries())
      .map(([key, value]) => ({ key, ...value }))
      .sort((a, b) => a.timestamp - b.timestamp);
    
    let freedBytes = 0;
    let evictedCount = 0;
    
    for (const item of items) {
      if (freedBytes >= targetBytes) break;
      
      freedBytes += item.size;
      this.delete(item.key);
      evictedCount++;
    }
    
    console.log(`🗑️ 已移除 ${evictedCount} 個舊快取項目，釋放 ${this.getHumanSize(freedBytes)}`);
  }

  /**
   * 更新記憶體統計
   */
  updateMemoryStats() {
    this.stats.memory = this.getTotalSize();
  }

  /**
   * 計算總快取大小
   * @returns {number} 總大小（位元組）
   */
  getTotalSize() {
    let total = 0;
    for (const value of this.cache.values()) {
      total += value.size || 0;
    }
    return total;
  }

  /**
   * 轉換為人類可讀的大小格式
   * @param {number} bytes - 位元組數
   * @returns {string} 格式化的大小字串
   */
  getHumanSize(bytes) {
    const sizes = ['B', 'KB', 'MB', 'GB'];
    if (bytes === 0) return '0 B';
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return Math.round(bytes / Math.pow(1024, i) * 100) / 100 + ' ' + sizes[i];
  }

  /**
   * 取得快取統計資訊
   * @returns {object} 統計資料
   */
  getStats() {
    const total = this.stats.hits + this.stats.misses;
    const hitRate = total > 0 ? (this.stats.hits / total * 100) : 0;
    const expiredItems = Array.from(this.cacheTTL.values())
      .filter(ttl => Date.now() >= ttl).length;
    
    return {
      total: this.cache.size,
      hits: this.stats.hits,
      misses: this.stats.misses,
      sets: this.stats.sets,
      hit_rate: Math.round(hitRate * 100) / 100,
      memory: this.stats.memory,
      expired: expiredItems,
      keys: Array.from(this.cache.keys())
    };
  }

  /**
   * 預載入常用資料
   * @param {object} secureAPI - SecureAPI實例
   */
  async preloadCommonData(secureAPI) {
    const commonEndpoints = [
      'element_table',
      'exp_table', 
      'dungeon_table',
      'items_table',
      'equips_table'
    ];
    
    console.log('🎯 開始預載入常用資料...');
    
    const promises = commonEndpoints.map(async (endpoint) => {
      try {
        await secureAPI.getStaticData(endpoint);
        console.log(`✅ 預載入完成: ${endpoint}`);
      } catch (error) {
        console.warn(`❌ 預載入失敗: ${endpoint}`, error);
      }
    });
    
    await Promise.allSettled(promises);
    console.log('🎉 預載入完成');
  }

  /**
   * 檢查快取健康狀態
   * @returns {object} 健康狀態報告
   */
  getHealthStatus() {
    const stats = this.getStats();
    const memoryUsagePercent = (stats.memory / (50 * 1024 * 1024)) * 100; // 50MB限制
    
    return {
      status: memoryUsagePercent > 90 ? 'critical' : 
              memoryUsagePercent > 70 ? 'warning' : 'healthy',
      memory_usage_percent: Math.round(memoryUsagePercent * 100) / 100,
      cache_hit_rate: stats.hit_rate,
      total_items: stats.total,
      expired_items: stats.expired,
      recommendations: this.getRecommendations(stats, memoryUsagePercent)
    };
  }

  /**
   * 取得優化建議
   * @param {object} stats - 統計資料
   * @param {number} memoryUsagePercent - 記憶體使用百分比
   * @returns {array} 建議列表
   */
  getRecommendations(stats, memoryUsagePercent) {
    const recommendations = [];
    
    if (stats.hit_rate < 50) {
      recommendations.push('快取命中率偏低，考慮增加TTL或檢查資料存取模式');
    }
    
    if (memoryUsagePercent > 80) {
      recommendations.push('記憶體使用量過高，建議清理過期快取或減少快取項目');
    }
    
    if (stats.expired > stats.total * 0.3) {
      recommendations.push('過期快取項目過多，建議手動清理或調整TTL設定');
    }
    
    if (stats.total === 0) {
      recommendations.push('無快取項目，建議啟用預載入功能');
    }
    
    return recommendations;
  }
}

// 🚀 匯出快取管理器
export default CacheManager;
