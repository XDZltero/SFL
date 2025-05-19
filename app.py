import os
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__)
CORS(app, origins=["https://xdzltero.github.io"])  # 限制只允許你的 GitHub 網頁呼叫

# 從環境變數載入 Firebase 金鑰
firebase_creds_str = os.environ["FIREBASE_CREDENTIALS"]
firebase_creds = json.loads(firebase_creds_str)
cred = credentials.Certificate(firebase_creds)
firebase_admin.initialize_app(cred)
db = firestore.client()

def user_ref(user_id):
    return db.collection("users").document(user_id)

@app.route("/register", methods=["POST"])
def register():
    data = request.json
    user_id = data.get("user")
    if user_ref(user_id).get().exists:
        return jsonify({"error": "使用者已存在"}), 400
    user_data = { "hp": 100, "attack": 10, "gold": 0, "exp": 0 }
    user_ref(user_id).set(user_data)
    return jsonify({"message": f"使用者 {user_id} 建立完成！"})

@app.route("/status", methods=["GET"])
def status():
    user_id = request.args.get("user")
    doc = user_ref(user_id).get()
    if not doc.exists:
        return jsonify({"error": "找不到使用者"}), 404
    return jsonify(doc.to_dict())

@app.route("/attack", methods=["POST"])
def attack():
    data = request.json
    user_id = data.get("user")
    doc = user_ref(user_id).get()
    if not doc.exists:
        return jsonify({"error": "使用者不存在"}), 404
    user = doc.to_dict()
    monster = { "name": "Slime", "hp": 30, "attack": 5 }
    monster_hp = monster["hp"] - user["attack"]
    if monster_hp <= 0:
        user["gold"] += 10
        user["exp"] += 5
        user_ref(user_id).set(user)
        return jsonify({ "message": f"你打敗了 {monster['name']}，獲得 10 金幣與 5 經驗值。", "status": user })
    else:
        user["hp"] -= monster["attack"]
        user_ref(user_id).set(user)
        return jsonify({ "message": f"你對 {monster['name']} 造成 {user['attack']} 傷害，但受到 {monster['attack']} 傷害。", "monster_hp": monster_hp, "your_hp": user["hp"] })

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
