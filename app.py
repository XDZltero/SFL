import os
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore
from battle import simulate_battle

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

# 確認存活用
@app.route("/ping")
def ping():
    return "pong", 200

@app.route("/register", methods=["POST"])
def register():
    data = request.json
    user_id = data.get("user")
    nickname = data.get("nickname", user_id)

    if not user_id:
        return jsonify({"error": "缺少使用者 ID。"}), 400

    # 檢查 user_id 是否已註冊
    ref = db.collection("users").document(user_id)
    if ref.get().exists:
        return jsonify({"error": "使用者已存在。"}), 400

    # 檢查 nickname 是否被其他人使用過
    nickname_conflict = db.collection("users").where("nickname", "==", nickname).get()
    if nickname_conflict:
        return jsonify({"error": "已經有人取過這個名字囉。"}), 400

    user_data = {
        "user_id": user_id,
        "nickname": nickname,
        "level": 1,
        "exp": 0,
        "stat_points": 0,
        "skill_points": 0,
        "base_stats": {
            "hp": 100,
            "attack": 20,
            "shield": 0,
            "evade": 0.1,
            "other_bonus": 0,
            "accuracy": 1.0,
            "luck": 10,
            "atk_speed": 100
        },
        "equipment": {
            "head": None,
            "armor": None,
            "gloves": None,
            "boots": None,
            "weapon_atk": None,
        },
        "skills": {
            "fireball": 1,
            "slash": 1
        }
    }

    # 文件 ID 使用 user_id 儲存
    ref.set(user_data)
    return jsonify({"message": f"使用者 {nickname} 建立完成！"})

@app.route("/status", methods=["GET"])
def status():
    user_id = request.args.get("user")
    if not user_id:
        return jsonify({"error": "缺少使用者參數"}), 400

    doc = db.collection("users").document(user_id).get()
    if not doc.exists:
        return jsonify({"error": "找不到使用者"}), 404

    user_data = doc.to_dict()
    return jsonify(user_data)

# 獲得怪物資訊
@app.route("/monster", methods=["GET"])
def get_monster():
    monster_id = request.args.get("id")
    if not monster_id:
        return jsonify({"error": "缺少 monster id"}), 400

    mon_doc = db.collection("monsters").document(monster_id).get()
    if not mon_doc.exists:
        return jsonify({"error": "找不到怪物"}), 404

    return jsonify(mon_doc.to_dict())

@app.route("/battle", methods=["POST"])
def battle():
    try:
        data = request.json
        user_id = data.get("user")
        monster_id = data.get("monster")

        if not user_id or not monster_id:
            return jsonify({"error": "缺少參數"}), 400

        user_doc = db.collection("users").document(user_id).get()
        if not user_doc.exists:
            return jsonify({"error": "找不到使用者"}), 404
        user_data = user_doc.to_dict()
        user_data["user_id"] = user_id

        mon_doc = db.collection("monsters").document(monster_id).get()
        if not mon_doc.exists:
            return jsonify({"error": "找不到怪物"}), 404
        monster_data = mon_doc.to_dict()

        # ✅ 玩家技能查詢
        user_skill_ids = list(user_data.get("skills", {}).keys())
        user_skill_list = []
        for i in range(0, len(user_skill_ids), 10):
            batch = user_skill_ids[i:i + 10]
            docs = db.collection("skills").where("id", "in", batch).stream()
            for doc in docs:
                user_skill_list.append(doc.to_dict())
        user_skill_list.sort(key=lambda x: x.get("sort", 9999))
        user_skill_dict = {s["id"]: s for s in user_skill_list}

        # ✅ 執行戰鬥
        result = simulate_battle(user_data, monster_data, user_skill_dict)
        db.collection("users").document(user_id).set(result["user"])

        return jsonify(result)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"伺服器內部錯誤: {str(e)}"}), 500



@app.route("/battle_dungeon", methods=["POST"])
def battle_dungeon():
    try:
        data = request.json
        user_id = data.get("user")
        dungeon_id = data.get("dungeon")
        layer = data.get("layer")

        if not user_id or not dungeon_id or layer is None:
            return jsonify({"error": "缺少參數"}), 400

        user_doc = db.collection("users").document(user_id).get()
        if not user_doc.exists:
            return jsonify({"error": "找不到使用者"}), 404

        user_data = user_doc.to_dict()
        user_data["user_id"] = user_id

        with open("parameter/dungeons.json", encoding="utf-8") as f:
            dungeons = json.load(f)

        dungeon = next((d for d in dungeons if d["id"] == dungeon_id), None)
        if not dungeon:
            return jsonify({"error": "副本不存在"}), 404

        monsters = dungeon.get("monsters", [])
        is_boss = int(layer) == len(monsters)

        if is_boss:
            monster_id = dungeon["bossId"]
        elif 0 <= int(layer) < len(monsters):
            monster_id = monsters[int(layer)]
        else:
            return jsonify({"error": "層數不合法"}), 400

        mon_doc = db.collection("monsters").document(monster_id).get()
        if not mon_doc.exists:
            return jsonify({"error": "找不到怪物"}), 404

        monster_data = mon_doc.to_dict()

        # ✅ 玩家技能查詢
        user_skill_ids = list(user_data.get("skills", {}).keys())
        user_skill_list = []
        for i in range(0, len(user_skill_ids), 10):
            batch = user_skill_ids[i:i + 10]
            docs = db.collection("skills").where("id", "in", batch).stream()
            for doc in docs:
                user_skill_list.append(doc.to_dict())
        user_skill_list.sort(key=lambda x: x.get("sort", 9999))
        user_skill_dict = {s["id"]: s for s in user_skill_list}
        
        # ✅ 傳入 simulate_battle
        result = simulate_battle(user_data, monster_data, user_skill_dict)
        db.collection("users").document(user_id).set(result["user"])

        user_key = user_id.replace(".", "_")
        progress_ref = db.collection("progress").document(user_key)
        progress_doc = progress_ref.get()
        current_progress = progress_doc.to_dict() if progress_doc.exists else {}
        current_layer = current_progress.get(dungeon_id, 0)

        if result["result"] == "lose":
            progress_ref.set({dungeon_id: 0}, merge=True)
            return jsonify({
                "success": False,
                "message": "你被擊敗了，進度已重設為第一層。",
                "battle_log": result["battle_log"]
            })

        if result["result"] == "win":
            if is_boss:
                progress_ref.set({dungeon_id: 0}, merge=True)
            elif int(layer) >= current_layer:
                progress_ref.set({dungeon_id: int(layer) + 1}, merge=True)

        return jsonify({
            "success": True,
            "message": "戰鬥勝利",
            "is_last_layer": is_boss,
            "battle_log": result["battle_log"],
            "rewards": result.get("rewards"),
            "user": result.get("user")
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"伺服器錯誤: {str(e)}"}), 500


# 獲得副本層數
@app.route("/get_progress", methods=["GET"])
def get_progress():
    user_id = request.args.get("user")
    if not user_id:
        return jsonify({"error": "缺少 user 參數"}), 400

    # Firestore 不允許有 . 符號，需轉換為 _
    user_key = user_id.replace(".", "_")

    doc_ref = db.collection("progress").document(user_key)
    doc = doc_ref.get()

    if not doc.exists:
        return jsonify({"progress": {}})

    return jsonify({"progress": doc.to_dict()})

@app.route("/inventory", methods=["GET"])
def inventory():
    user_id = request.args.get("user")
    if not user_id:
        return jsonify({"error": "缺少使用者參數"}), 400

    item_doc = db.collection("user_items").document(user_id).get()
    if not item_doc.exists:
        return jsonify({"items": {}})

    return jsonify(item_doc.to_dict())

@app.route("/levelup", methods=["POST"])
def levelup():
    data = request.json
    user_id = data.get("user")
    allocation = data.get("allocate")  # dict: {"hp": 1, "attack": 2, "luck": 2}

    if not user_id or not allocation:
        return jsonify({"error": "缺少參數"}), 400

    ref = db.collection("users").document(user_id)
    snap = ref.get()
    if not snap.exists:
        return jsonify({"error": "使用者不存在"}), 404

    user = snap.to_dict()
    total_points = sum(allocation.values())

    if user["stat_points"] < total_points:
        return jsonify({"error": "點數不足"}), 400

    # 更新能力值
    for stat, value in allocation.items():
        if stat not in user["base_stats"]:
            return jsonify({"error": f"無效屬性：{stat}"}), 400
        if stat == "hp":
            user["base_stats"][stat] += value * 5
        else:
            user["base_stats"][stat] += value

    user["stat_points"] -= total_points
    ref.set(user)
    return jsonify({"message": "屬性分配完成", "status": user})

# 抓出所有技能
@app.route("/skills_full", methods=["GET"])
def get_skills_full():
    skills = db.collection("skills").stream()
    return jsonify([doc.to_dict() for doc in skills])

@app.route("/skills_all", methods=["GET"])
def get_all_skills():
    user_id = request.args.get("user")
    if not user_id:
        return jsonify({"error": "缺少 user 參數"}), 400

    user_doc = db.collection("users").document(user_id).get()
    if not user_doc.exists:
        return jsonify({"error": "找不到使用者"}), 404

    user = user_doc.to_dict()
    user_skills = user.get("skills", {})
    skill_points = user.get("skill_points", 0)

    skill_docs = db.collection("skills").stream()
    skills = []
    for doc in skill_docs:
        s = doc.to_dict()
        s["id"] = doc.id
        skills.append(s)

    return jsonify({
        "skills": skills,
        "user_skills": user_skills,
        "remaining": skill_points
    })

@app.route("/skills_save", methods=["POST"])
def save_skill_distribution():
    data = request.json
    user_id = data.get("user")
    new_levels = data.get("skills")  # {"fireball": 5, "slash": 0, ...}

    if not user_id or not isinstance(new_levels, dict):
        return jsonify({"error": "參數錯誤"}), 400

    user_ref = db.collection("users").document(user_id)
    user_doc = user_ref.get()
    if not user_doc.exists:
        return jsonify({"error": "找不到使用者"}), 404

    user = user_doc.to_dict()
    old_skills = user.get("skills", {})
    skill_points = user.get("skill_points", 0)

    skill_docs = db.collection("skills").stream()
    skill_data = {doc.id: doc.to_dict() for doc in skill_docs}

    # 驗證新技能配置是否合法
    total_used = 0
    for skill_id, new_lvl in new_levels.items():
        if skill_id not in skill_data:
            return jsonify({"error": f"技能 {skill_id} 不存在"}), 400
        skill_info = skill_data[skill_id]

        if new_lvl < 0 or new_lvl > skill_info["maxlvl"]:
            return jsonify({"error": f"{skill_id} 超出等級範圍"}), 400
        if new_lvl > 0 and user["level"] < skill_info.get("learnlvl", 1):
            return jsonify({"error": f"{skill_id} 等級未達要求"}), 400

        total_used += new_lvl

    total_available = sum(old_skills.values()) + user.get("skill_points", 0)
    if total_used > total_available:
        return jsonify({"error": "技能點數不足"}), 400

    user["skills"] = {k: v for k, v in new_levels.items() if v > 0}
    user["skill_points"] = total_available - total_used

    user_ref.set(user)
    return jsonify({"message": "技能升級完成", "status": user})

@app.route("/items", methods=["GET"])
def get_all_items():
    items = db.collection("items").stream()
    result = {}
    for doc in items:
        data = doc.to_dict()
        result[data["id"]] = {
            "name": data["name"],
            "special": data.get("special", 0)
        }
    return jsonify(result)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
