// init-firebase.js
const firebaseConfig = {
  apiKey: "AIzaSyBjhRGQyr35YhQtIxwUsNVpFkN7_AFOGYE",
  authDomain: "sfl-api-f0290.firebaseapp.com",
  projectId: "sfl-api-f0290"
};

// 初始化 Firebase
firebase.initializeApp(firebaseConfig);

// 登入檢查與保護
firebase.auth().onAuthStateChanged((user) => {
  if (!user) {
    // 尚未登入，導向登入頁
    window.location.href = "/SFL/login.html";
  }
});
