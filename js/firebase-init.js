// js/firebase-init.js

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
