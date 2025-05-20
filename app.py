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
            "phys_bonus": 0,
            "magic_bonus": 0,
            "accuracy": 1.0,
            "luck": 10,
            "atk_speed": 100
        },
        "equipment": {
            "head": None,
            "armor": None,
            "gloves": None,
            "boots": None,
            "weapon_phys": None,
            "weapon_magic": None
        },
        "skills": {
            "fireball": 1,
            "slash": 1
        },
        "buffs": {
            "phys_bonus": 0.0,
            "magic_bonus": 0.0
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
    
        mon_doc = db.collection("monsters").document(monster_id).get()
        if not mon_doc.exists:
            return jsonify({"error": "找不到怪物"}), 404
    
        user_data = user_doc.to_dict()
        monster_data = mon_doc.to_dict()
    
        result = simulate_battle(user_data, monster_data)
    
        # 更新使用者戰鬥後的狀態
        db.collection("users").document(user_id).set(result["user"])
    
        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"伺服器內部錯誤: {str(e)}"}), 500

# 副本
@app.route("/battle_dungeon", methods=["POST"])
def battle_dungeon():
    try:
        data = request.json
        user_id = data.get("user")
        dungeon_id = data.get("dungeon")
        layer = data.get("layer")

        if not user_id or dungeon_id is None or layer is None:
            return jsonify({"error": "缺少參數"}), 400

        # 取得玩家資料
        user_doc = db.collection("users").document(user_id).get()
        if not user_doc.exists:
            return jsonify({"error": "找不到使用者"}), 404
        user_data = user_doc.to_dict()

        # 取得副本資料（從本地 JSON）
        with open("parameter/dungeons.json", encoding="utf-8") as f:
            dungeons = json.load(f)

        dungeon = next((d for d in dungeons if d["id"] == dungeon_id), None)
        if not dungeon:
            return jsonify({"error": "副本不存在"}), 404

        monsters = dungeon["monsters"]
        is_boss = int(layer) == len(monsters)
        if is_boss:
            monster_id = dungeon["bossId"]
        elif 0 <= int(layer) < len(monsters):
            monster_id = monsters[int(layer)]
        else:
            return jsonify({"error": "層數不合法"}), 400

        # 取得怪物資料
        mon_doc = db.collection("monsters").document(monster_id).get()
        if not mon_doc.exists:
            return jsonify({"error": "找不到怪物"}), 404
        monster_data = mon_doc.to_dict()

        # 模擬戰鬥
        result = simulate_battle(user_data, monster_data)

        # 更新使用者狀態
        db.collection("users").document(user_id).set(result["user"])

        if result["result"] == "lose":
            # 重設副本層數為 0
            user_key = user_id.replace(".", "_")
            db.reference(f"progress/{user_key}/{dungeon_id}").set(0)
            return jsonify({
                "success": False,
                "message": "你被擊敗了，進度已重設為第一層。"
            })

        # 戰鬥勝利
        return jsonify({
            "success": True,
            "message": "戰鬥勝利",
            "is_last_layer": is_last_layer,
            "result": result["log"]
        })


    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"伺服器錯誤: {str(e)}"}), 500

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

@app.route("/skills", methods=["GET"])
def get_skills():
    user_id = request.args.get("user")
    if not user_id:
        return jsonify({"error": "缺少使用者"}), 400

    user_doc = db.collection("users").document(user_id).get()
    if not user_doc.exists:
        return jsonify({"error": "找不到使用者"}), 404

    user = user_doc.to_dict()
    user_skills = user.get("skills", {})

    skill_ref = db.collection("skills").stream()
    skills = {doc.id: doc.to_dict() for doc in skill_ref}

    result = {}
    for skill_id, level in user_skills.items():
        skill = skills.get(skill_id)
        if not skill:
            continue
        effective_multiplier = skill["multiplier"] + (level - 1) * skill.get("multiplierperlvl", 0)
        result[skill_id] = {
            "name": skill["name"],
            "level": level,
            "multiplier": round(effective_multiplier, 2),
            "type": skill["type"]
        }

    return jsonify(result)

@app.route("/skillup", methods=["POST"])
def skillup():
    data = request.json
    user_id = data.get("user")
    skill_id = data.get("skill")

    if not user_id or not skill_id:
        return jsonify({"error": "缺少參數"}), 400

    user_ref = db.collection("users").document(user_id)
    user_doc = user_ref.get()
    if not user_doc.exists:
        return jsonify({"error": "找不到使用者"}), 404

    user = user_doc.to_dict()
    if user["skill_points"] < 1:
        return jsonify({"error": "技能點不足"}), 400

    skill_doc = db.collection("skills").document(skill_id).get()
    if not skill_doc.exists:
        return jsonify({"error": "技能不存在"}), 404

    skill = skill_doc.to_dict()
    current_level = user["skills"].get(skill_id, 0)

    if user["level"] < skill["learnlvl"]:
        return jsonify({"error": f"尚未達到學習等級：{skill['learnlvl']}"})

    if current_level >= skill["maxlvl"]:
        return jsonify({"error": "技能已達最大等級"}), 400

    # 升級
    user["skills"][skill_id] = current_level + 1
    user["skill_points"] -= 1
    user_ref.set(user)
    return jsonify({"message": f"{skill['name']} 升級為 Lv {current_level + 1}", "status": user})
    
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
