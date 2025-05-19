# app.py
from flask import Flask, request, jsonify
from deta import Deta

app = Flask(__name__)

# 用你自己的 DETA_KEY 替換此處
DETA_KEY = "YOUR_DETA_KEY"
deta = Deta(DETA_KEY)
users_db = deta.Base("users")

def get_monster():
    return {
        "name": "Slime",
        "hp": 30,
        "attack": 5
    }

@app.route("/register", methods=["POST"])
def register():
    data = request.json
    user_id = data.get("user")
    if users_db.get(user_id):
        return jsonify({"error": "使用者已存在"}), 400

    new_user = {
        "key": user_id,
        "hp": 100,
        "attack": 10,
        "gold": 0,
        "exp": 0
    }
    users_db.put(new_user)
    return jsonify({"message": f"使用者 {user_id} 建立完成！"})

@app.route("/status", methods=["GET"])
def status():
    user_id = request.args.get("user")
    user = users_db.get(user_id)
    if not user:
        return jsonify({"error": "找不到使用者"}), 404
    return jsonify(user)

@app.route("/attack", methods=["POST"])
def attack():
    data = request.json
    user_id = data.get("user")
    user = users_db.get(user_id)
    if not user:
        return jsonify({"error": "使用者不存在"}), 404

    monster = get_monster()
    monster_hp = monster["hp"] - user["attack"]

    if monster_hp <= 0:
        user["gold"] += 10
        user["exp"] += 5
        users_db.put(user)
        return jsonify({
            "message": f"你打敗了 {monster['name']}！獲得 10 金幣與 5 經驗值。",
            "status": user
        })
    else:
        user["hp"] -= monster["attack"]
        users_db.put(user)
        return jsonify({
            "message": f"你對 {monster['name']} 造成 {user['attack']} 傷害，但受到 {monster['attack']} 傷害。",
            "monster_hp": monster_hp,
            "your_hp": user["hp"]
        })

if __name__ == "__main__":
    app.run()
