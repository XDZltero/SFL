// firebase-init.js (模組版)
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
import { getAuth, onAuthStateChanged, signOut } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";

const firebaseConfig = {
  apiKey: "AIzaSyBjhRGQyr35YhQtIxwUsNVpFkN7_AFOGYE",
  authDomain: "sfl-api-f0290.firebaseapp.com",
  projectId: "sfl-api-f0290"
};

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);

// 共用登入狀態監聽
onAuthStateChanged(auth, user => {
  if (!user) {
    // 若未登入，統一跳轉至登入頁（含 iframe 容器）
    if (window.top === window.self) {
      window.location.href = "/SFL/loading.html";
    } else {
      window.top.location.href = "/SFL/loading.html";
    }
  }
});

// 可選匯出供外部使用
export { app, auth, signOut };
