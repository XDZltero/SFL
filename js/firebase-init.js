// 必須先載入 firebase-app-compat.js 和 firebase-auth-compat.js
// 使用 firebase compat 版本的初始化

const firebaseConfig = {
  apiKey: "AIzaSyBjhRGQyr35YhQtIxwUsNVpFkN7_AFOGYE",
  authDomain: "sfl-api-f0290.firebaseapp.com",
  projectId: "sfl-api-f0290"
};

// 避免重複初始化
if (!firebase.apps.length) {
  firebase.initializeApp(firebaseConfig);
}

// 驗證登入狀態，未登入就導回登入頁
firebase.auth().onAuthStateChanged(user => {
  if (!user) {
    // 整個 container 跳出
    window.parent.location.href = "/SFL/login.html";
  }
});
