// js/firebase-init.js (整合快取版本)
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
import { getAuth, onAuthStateChanged, signOut } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";
import CacheManager from './cache-manager.js';

const firebaseConfig = {
  apiKey: "AIzaSyBjhRGQyr35YhQtIxwUsNVpFkN7_AFOGYE",
  authDomain: "sfl-api-f0290.firebaseapp.com",
  projectId: "sfl-api-f0290"
};

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);

// 🚀 初始化快取管理器
const cacheManager = new CacheManager();

// Token管理器
class TokenManager {
  static async getIdToken() {
    const user = auth.currentUser;
    if (!user) {
      throw new Error('使用者未登入');
    }
    try {
      return await user.getIdToken(true);
    } catch (error) {
      console.error('取得ID Token失敗:', error);
      throw error;
    }
  }

  static async getAuthHeaders() {
    const token = await this.getIdToken();
    return {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    };
  }
}

// 🚀 優化的API請求封裝（含快取功能）
class SecureAPI {
  static async request(url, options = {}) {
    try {
      const headers = await TokenManager.getAuthHeaders();
      const response = await fetch(url, {
        ...options,
        headers: {
          ...headers,
          ...options.headers
        }
      });

      if (response.status === 401) {
        console.log('Token無效，重新導向到登入頁面');
        window.location.href = "/SFL/login.html";
        return;
      }

      return response;
    } catch (error) {
      console.error('API請求失敗:', error);
      throw error;
    }
  }

  static async get(url, useCache = true) {
    if (useCache) {
      const cached = cacheManager.get(url);
      if (cached) {
        return {
          ok: true,
          cached: true,
          json: () => Promise.resolve(cached),
          status: 200
        };
      }
    }

    const response = await this.request(url, { method: 'GET' });
    
    if (useCache && response && response.ok) {
      try {
        const data = await response.clone().json();
        // 🎯 根據URL類型設定不同的TTL
        const ttl = this.getTTLForUrl(url);
        cacheManager.set(url, data, ttl);
      } catch (error) {
        console.warn('快取儲存失敗:', error);
      }
    }
    
    return response;
  }

  static async post(url, data) {
    return this.request(url, {
      method: 'POST',
      body: JSON.stringify(data)
    });
  }

  // 🚀 新增：取得靜態資料（長期快取）
  static async getStaticData(endpoint, forceRefresh = false) {
    const url = `https://sfl-9cb8.onrender.com/${endpoint}`;
    
    if (!forceRefresh) {
      const cached = cacheManager.get(`static_${endpoint}`);
      if (cached) {
        return {
          ok: true,
          cached: true,
          json: () => Promise.resolve(cached)
        };
      }
    }

    try {
      const response = await fetch(url);
      if (response.ok) {
        const data = await response.json();
        // 🎯 靜態資料使用長期快取（1小時）
        cacheManager.set(`static_${endpoint}`, data, 60 * 60 * 1000);
        return data;
      } else {
        throw new Error(`API請求失敗: ${response.status}`);
      }
    } catch (error) {
      console.error(`載入靜態資料失敗 (${endpoint}):`, error);
      
      // 🔄 嘗試使用過期快取作為備用
      const expired = cacheManager.cache.get(`static_${endpoint}`);
      if (expired) {
        console.warn(`使用過期快取作為備用: ${endpoint}`);
        return expired.data;
      }
      
      throw error;
    }
  }

  // 🚀 新增：取得使用者狀態（短期快取）
  static async getStatus(forceRefresh = false) {
    const url = 'https://sfl-9cb8.onrender.com/status';
    const cacheKey = 'user_status';
    
    if (!forceRefresh) {
      const cached = cacheManager.get(cacheKey);
      if (cached) {
        return {
          ok: true,
          cached: true,
          json: () => Promise.resolve(cached)
        };
      }
    }

    const response = await this.get(url, false); // 不使用URL快取，使用自定義快取
    
    if (response && response.ok) {
      try {
        const data = await response.clone().json();
        // 🎯 使用者狀態短期快取（30秒）
        cacheManager.set(cacheKey, data, 30 * 1000);
      } catch (error) {
        console.warn('使用者狀態快取失敗:', error);
      }
    }
    
    return response;
  }

  // 🚀 新增：智能重試機制
  static async getWithRetry(url, maxRetries = 3, useCache = true) {
    for (let i = 0; i < maxRetries; i++) {
      try {
        return await this.get(url, useCache && i === 0); // 第一次嘗試使用快取
      } catch (error) {
        console.warn(`請求失敗 (嘗試 ${i + 1}/${maxRetries}):`, error);
        
        if (i === maxRetries - 1) {
          // 最後一次嘗試失敗，檢查是否有快取可用
          if (useCache) {
            const cached = cacheManager.get(url);
            if (cached) {
              console.warn('使用快取作為備用');
              return {
                ok: true,
                cached: true,
                fallback: true,
                json: () => Promise.resolve(cached)
              };
            }
          }
          throw error;
        }
        
        // 等待後重試
        await new Promise(resolve => setTimeout(resolve, Math.pow(2, i) * 1000));
      }
    }
  }

  // 🎯 根據URL設定適當的TTL
  static getTTLForUrl(url) {
    if (url.includes('status')) return 30 * 1000;        // 30秒
    if (url.includes('inventory')) return 60 * 1000;     // 1分鐘
    if (url.includes('progress')) return 60 * 1000;      // 1分鐘
    if (url.includes('_table')) return 60 * 60 * 1000;   // 1小時
    return 5 * 60 * 1000;                                // 預設5分鐘
  }

  // 🧹 快取管理方法
  static clearCache() {
    cacheManager.clear();
  }

  static clearUserCache() {
    cacheManager.clearUserCache();
  }

  static clearStaticCache() {
    cacheManager.clearStaticCache();
  }
}

// 🚀 智能預載入系統
class PreloadManager {
  static async preloadEssentialData() {
    console.log('🎯 開始預載入核心資料...');
    
    const essentialData = [
      'exp_table',
      'element_table'
    ];
    
    const promises = essentialData.map(endpoint => 
      SecureAPI.getStaticData(endpoint).catch(error => {
        console.warn(`預載入失敗: ${endpoint}`, error);
        return null;
      })
    );
    
    await Promise.allSettled(promises);
    console.log('✅ 核心資料預載入完成');
  }

  static async preloadUserData() {
    try {
      console.log('🎯 預載入使用者資料...');
      await SecureAPI.getStatus(false);
      console.log('✅ 使用者資料預載入完成');
    } catch (error) {
      console.warn('使用者資料預載入失敗:', error);
    }
  }

  static async preloadGameData() {
    console.log('🎯 預載入遊戲資料...');
    
    const gameData = [
      'dungeon_table',
      'items_table',
      'equips_table'
    ];
    
    const promises = gameData.map(endpoint => 
      SecureAPI.getStaticData(endpoint).catch(error => {
        console.warn(`預載入失敗: ${endpoint}`, error);
        return null;
      })
    );
    
    await Promise.allSettled(promises);
    console.log('✅ 遊戲資料預載入完成');
  }
}

// 🚀 錯誤處理與降級機制
class ErrorHandler {
  static handleNetworkError(error, endpoint) {
    console.error(`網路錯誤 (${endpoint}):`, error);
    
    // 嘗試使用快取
    const cached = cacheManager.get(endpoint);
    if (cached) {
      console.warn(`使用快取資料作為降級方案: ${endpoint}`);
      return cached;
    }
    
    throw new Error(`無法載入資料且無快取可用: ${endpoint}`);
  }

  static handleAuthError(error) {
    console.error('認證錯誤:', error);
    
    // 清除可能過期的快取
    cacheManager.clearUserCache();
    
    // 重新導向到登入頁面
    if (window.top === window.self) {
      window.location.href = "/SFL/login.html";
    } else {
      window.top.location.href = "/SFL/login.html";
    }
  }
}

// 🚀 效能監控
class PerformanceMonitor {
  static startTiming(label) {
    performance.mark(`${label}-start`);
  }

  static endTiming(label) {
    performance.mark(`${label}-end`);
    performance.measure(label, `${label}-start`, `${label}-end`);
    
    const measure = performance.getEntriesByName(label)[0];
    console.log(`⏱️ ${label}: ${Math.round(measure.duration)}ms`);
    
    return measure.duration;
  }

  static measureCachePerformance() {
    const stats = cacheManager.getStats();
    console.log('📊 快取效能統計:', stats);
    return stats;
  }
}

// 🚀 共用登入狀態監聽（優化版）
let loginStateInitialized = false;

onAuthStateChanged(auth, async (user) => {
  if (!user) {
    if (window.top === window.self) {
      window.location.href = "/SFL/login.html";
    } else {
      window.top.location.href = "/SFL/login.html";
    }
    return;
  }

  // 🎯 只在首次登入時執行預載入
  if (!loginStateInitialized) {
    loginStateInitialized = true;
    
    try {
      // 🚀 並行預載入
      await Promise.allSettled([
        PreloadManager.preloadEssentialData(),
        PreloadManager.preloadUserData()
      ]);
      
      // 🎯 背景預載入遊戲資料（不阻塞主流程）
      PreloadManager.preloadGameData().catch(error => {
        console.warn('背景預載入失敗:', error);
      });
      
    } catch (error) {
      console.warn('預載入過程發生錯誤:', error);
    }
  }
});

// 🚀 全域快取統計函數
window.getCacheStats = () => cacheManager.getStats();

// 🚀 開發者工具（僅在開發模式下啟用）
if (window.location.hostname === 'localhost' || window.location.search.includes('debug=true')) {
  window.devTools = {
    cache: cacheManager,
    api: SecureAPI,
    preload: PreloadManager,
    performance: PerformanceMonitor,
    
    // 快速指令
    clearCache: () => SecureAPI.clearCache(),
    clearUserCache: () => SecureAPI.clearUserCache(),
    showStats: () => console.table(cacheManager.getStats()),
    preloadAll: async () => {
      await PreloadManager.preloadEssentialData();
      await PreloadManager.preloadGameData();
      await PreloadManager.preloadUserData();
    }
  };
  
  console.log('🔧 開發者工具已啟用，使用 window.devTools 存取');
}

// 🚀 匯出
export { 
  app, 
  auth, 
  signOut, 
  TokenManager, 
  SecureAPI,
  PreloadManager,
  ErrorHandler,
  PerformanceMonitor,
  getCacheStats: () => cacheManager.getStats()
};
