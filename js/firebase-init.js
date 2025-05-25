// js/firebase-init.js

import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
import { getAuth, onAuthStateChanged, signOut } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";


// ✅ 全站通用 Firebase 初始化設定
const firebaseConfig = {
  apiKey: "AIzaSyBjhRGQyr35YhQtIxwUsNVpFkN7_AFOGYE",
  authDomain: "sfl-api-f0290.firebaseapp.com",
  projectId: "sfl-api-f0290"
};

// ⚠️ 避免重複初始化（多次進入 iframe）
if (!firebase.apps.length) {
  firebase.initializeApp(firebaseConfig);
} else {
  firebase.app(); // 若已初始化則使用現有實例
} 


// 初始化 Firebase
const app = initializeApp(firebaseConfig);
const auth = getAuth(app);

// ✅ 驗證登入狀態（未登入會跳轉）
export function verifyLogin() {
  return new Promise(resolve => {
    onAuthStateChanged(auth, user => {
      if (!user) {
        window.top.location.href = "loading.html"; // ✅ 統一跳轉
      }
      resolve(user);
    });
  });
}

// ✅ 登出函式
export function logout() {
  return signOut(auth);
}

// ✅ 取得目前使用者
export function getCurrentUser() {
  return auth.currentUser;
}
