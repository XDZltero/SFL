// firebase-init.js (安全版本)
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
import { getAuth, onAuthStateChanged, signOut } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";

const firebaseConfig = {
  apiKey: "AIzaSyBjhRGQyr35YhQtIxwUsNVpFkN7_AFOGYE",
  authDomain: "sfl-api-f0290.firebaseapp.com",
  projectId: "sfl-api-f0290"
};

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);

// Token管理器
class TokenManager {
  static async getIdToken() {
    const user = auth.currentUser;
    if (!user) {
      throw new Error('使用者未登入');
    }
    try {
      return await user.getIdToken(true); // true 強制刷新token
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

// 安全的API請求封裝
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
        // Token過期或無效，嘗試重新登入
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

  static async get(url) {
    return this.request(url, { method: 'GET' });
  }

  static async post(url, data) {
    return this.request(url, {
      method: 'POST',
      body: JSON.stringify(data)
    });
  }
}

// 共用登入狀態監聽
onAuthStateChanged(auth, user => {
  if (!user) {
    // 若未登入，統一跳轉至登入頁
    if (window.top === window.self) {
      window.location.href = "/SFL/login.html";
    } else {
      window.top.location.href = "/SFL/login.html";
    }
  }
});

// 匯出供外部使用
export { app, auth, signOut, TokenManager, SecureAPI };
