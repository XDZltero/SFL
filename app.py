import os
import json
import time
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_compress import Compress
import firebase_admin
from firebase_admin import credentials, firestore, auth as firebase_auth
from battle import simulate_battle
from functools import lru_cache, wraps
import re
from urllib.parse import urlencode

app = Flask(__name__)
Compress(app)
CORS(app, origins=["https://xdzltero.github.io"])

# 🚀 新增：簡單記憶體快取系統
class CacheManager:
    def __init__(self):
        self._cache = {}
        self._cache_ttl = {}
        self._stats = {'hits': 0, 'misses': 0, 'sets': 0}
    
    def set(self, key, data, ttl=300):
        """設定快取，ttl為秒數"""
        self._cache[key] = data
        self._cache_ttl[key] = time.time() + ttl
        self._stats['sets'] += 1
        
        # 清理過期快取（每100次設定清理一次）
        if self._stats['sets'] % 100 == 0:
            self._cleanup_expired()
    
    def get(self, key):
        """取得快取資料"""
        if key in self._cache and time.time() < self._cache_ttl.get(key, 0):
            self._stats['hits'] += 1
            return self._cache[key]
        
        # 清理過期的key
        if key in self._cache:
            del self._cache[key]
            del self._cache_ttl[key]
        
        self._stats['misses'] += 1
        return None
    
    def delete(self, key):
        """刪除特定快取"""
        if key in self._cache:
            del self._cache[key]
            del self._cache_ttl[key]
    
    def clear(self):
        """清除所有快取"""
        self._cache.clear()
        self._cache_ttl.clear()
        self._stats = {'hits': 0, 'misses': 0, 'sets': 0}
    
    def _cleanup_expired(self):
        """清理過期的快取項目"""
        now = time.time()
        expired_keys = [k for k, ttl in self._cache_ttl.items() if now >= ttl]
        for key in expired_keys:
            del self._cache[key]
            del self._cache_ttl[key]
    
    def get_stats(self):
        """取得快取統計"""
        total = self._stats['hits'] + self._stats['misses']
        hit_rate = (self._stats['hits'] / total * 100) if total > 0 else 0
        return {
            'hits': self._stats['hits'],
            'misses': self._stats['misses'],
            'sets': self._stats['sets'],
            'hit_rate': round(hit_rate, 2),
            'total_items': len(self._cache),
            'expired_items': sum(1 for ttl in self._cache_ttl.values() if time.time() >= ttl)
        }

# 🚀 初始化快取管理器
cache_manager = CacheManager()

# Firebase初始化
firebase_creds_str = os.environ["FIREBASE_CREDENTIALS"]
firebase_creds = json.loads(firebase_creds_str)
cred = credentials.Certificate(firebase_creds)
firebase_admin.initialize_app(cred)
db = firestore.client()

# Token驗證裝飾器
def require_auth(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        
        if not auth_header:
            return jsonify({'error': '缺少授權標頭'}), 401
        
        try:
            token = auth_header.split(' ')[1]
            decoded_token = firebase_auth.verify_id_token(token)
            request.user_id = decoded_token['email']
            request.uid = decoded_token['uid']
            
        except Exception as e:
            return jsonify({'error': '無效的授權令牌'}), 401
        
        return f(*args, **kwargs)
    
    return decorated_function

def user_ref(user_id):
    return db.collection("users").document(user_id)

# 🚀 快取裝飾器
def cached_response(ttl=300):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            # ✅ 使用 force=1 時，不使用快取
            if request.args.get("force") == "1":
                return f(*args, **kwargs)

            # 使用者身分作為快取鍵的一部分
            user_id = getattr(request, 'user_id', 'anonymous')

            # ✅ 把 query string 排序後變成字串納入快取鍵，避免漏判
            query_string = urlencode(sorted(request.args.items()))
            cache_key = f"{f.__name__}_{user_id}_{request.endpoint}_{query_string}"

            cached_data = cache_manager.get(cache_key)
            if cached_data is not None:
                return jsonify(cached_data)

            # 執行原本的函式並儲存快取
            result = f(*args, **kwargs)
            if isinstance(result, dict):
                cache_manager.set(cache_key, result, ttl)
                return jsonify(result)
            elif hasattr(result, 'status_code') and result.status_code == 200:
                response_data = result.get_json()
                cache_manager.set(cache_key, response_data, ttl)
                return result
            else:
                return result
        return wrapper
    return decorator

# 檢查戰鬥冷卻時間
def check_battle_cooldown(user_data):
    last_battle = user_data.get("last_battle")
    if not last_battle:
        return True, 0
    
    current_timestamp = time.time()
    cooldown_seconds = 30  # ✅ 修正：統一為30秒，與前端一致
    
    time_diff = current_timestamp - last_battle
    if time_diff >= cooldown_seconds:
        return True, 0
    else:
        remaining = cooldown_seconds - time_diff
        return False, max(0, remaining)

# 🚀 優化的靜態資料快取（1小時TTL）
@lru_cache(maxsize=128)
def get_dungeon_data():
    with open("parameter/dungeons.json", encoding="utf-8") as f:
        return json.load(f)

@lru_cache(maxsize=128)
def get_element_table():
    with open("parameter/attribute_table.json", encoding="utf-8") as f:
        return json.load(f)

@lru_cache(maxsize=128)
def get_items_data():
    with open("parameter/items.json", encoding="utf-8") as f:
        return json.load(f)

@lru_cache(maxsize=128)
def get_equips_data():
    with open("parameter/equips.json", encoding="utf-8") as f:
        return json.load(f)

@lru_cache(maxsize=128)
def get_level_exp():
    with open("parameter/level_exp.json", encoding="utf-8") as f:
        return json.load(f)

@lru_cache(maxsize=128)
def get_all_skill_data():
    skills = db.collection("skills").stream()
    result = []
    for doc in skills:
        data = doc.to_dict()
        result.append(data)
    return result

@lru_cache(maxsize=128)
def get_item_map():
    items = db.collection("items").stream()
    result = {}
    for doc in items:
        data = doc.to_dict()
        result[data["id"]] = {
            "name": data["name"],
            "special": data.get("special", 0)
        }
    return result

# 🚀 新增：快取統計端點
@app.route("/cache_stats")
def cache_stats():
    return jsonify(cache_manager.get_stats())

# 🚀 靜態資料端點（長期快取）
@app.route("/element_table")
def element_table():
    return jsonify(get_element_table())

@app.route("/exp_table")  
def exp_table():
    return jsonify(get_level_exp())

@app.route("/dungeon_table")
def dungeon_table():
    return jsonify(get_dungeon_data())

@app.route("/items_table")
def items_table():
    items = get_items_data()
    return jsonify({item["id"]: item for item in items})

@app.route("/equips_table")
def equips_table():
    return jsonify(get_equips_data())

@app.route("/ping")
def ping():
    return "pong", 200

# 暱稱驗證函數
def validate_nickname(nickname):
    if not nickname:
        return False, "暱稱不能為空"
    
    nickname = nickname.strip()
    
    if len(nickname) < 2:
        return False, "暱稱至少需要 2 個字符"
    
    if len(nickname) > 12:
        return False, "暱稱最多 12 個字符"
    
    allowed_pattern = re.compile(r'^[\u4e00-\u9fa5a-zA-Z0-9_\-\s]+$')
    if not allowed_pattern.match(nickname):
        return False, "暱稱只能包含中文、英文、數字、底線、連字號和空格"
    
    if nickname.startswith(' ') or nickname.endswith(' '):
        return False, "暱稱開頭和結尾不能有空格"
    
    if '  ' in nickname:
        return False, "暱稱不能包含連續空格"
    
    return True, ""

@app.route("/register", methods=["POST"])
def register():
    data = request.json
    user_id = data.get("user")
    nickname = data.get("nickname", user_id)
    id_token = data.get("idToken")

    if not user_id or not id_token:
        return jsonify({"error": "缺少必要參數"}), 400

    is_valid, error_message = validate_nickname(nickname)
    if not is_valid:
        return jsonify({"error": f"暱稱驗證失敗：{error_message}"}), 400

    try:
        decoded_token = firebase_auth.verify_id_token(id_token)
        
        if decoded_token['email'] != user_id:
            return jsonify({"error": "身份驗證失敗"}), 401
            
    except Exception as e:
        return jsonify({"error": "無效的身份令牌"}), 401

    ref = db.collection("users").document(user_id)
    if ref.get().exists:
        return jsonify({"error": "使用者已存在"}), 400

    trimmed_nickname = nickname.strip()
    nickname_conflict = db.collection("users").where("nickname", "==", trimmed_nickname).get()
    if nickname_conflict:
        return jsonify({"error": "已經有人取過這個名字囉"}), 400

    user_data = {
        "user_id": user_id,
        "nickname": trimmed_nickname,
        "level": 1,
        "exp": 0,
        "stat_points": 0,
        "skill_points": 0,
        "last_battle": 0,
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
            "1": None,
            "2": None,
            "3": None,
            "4": None,
            "5": None,
        },
        "skills": {
            "fireball": 1,
            "slash": 1
        }
    }

    ref.set(user_data)
    return jsonify({"message": f"使用者 {trimmed_nickname} 建立完成！"})

# 🚀 優化的使用者狀態端點（短期快取）
@app.route("/status", methods=["GET"])
@require_auth
@cached_response(ttl=30)  # 30秒快取
def status():
    user_id = request.user_id
    
    doc = db.collection("users").document(user_id).get()
    if not doc.exists:
        return jsonify({"error": "找不到使用者"}), 404

    user_data = doc.to_dict()
    
    if "last_battle" not in user_data:
        user_data["last_battle"] = 0
        db.collection("users").document(user_id).set({"last_battle": 0}, merge=True)
    
    is_ready, remaining_seconds = check_battle_cooldown(user_data)
    user_data["battle_cooldown_remaining"] = remaining_seconds
    user_data["battle_ready"] = is_ready
    
    return user_data  # 回傳資料，讓快取裝飾器處理

@app.route("/monster", methods=["GET"])
def get_monster():
    monster_id = request.args.get("id")
    if not monster_id:
        return jsonify({"error": "缺少 monster id"}), 400

    # 🚀 新增怪物資料快取
    cache_key = f"monster_{monster_id}"
    cached_monster = cache_manager.get(cache_key)
    if cached_monster:
        return jsonify(cached_monster)

    mon_doc = db.collection("monsters").document(monster_id).get()
    if not mon_doc.exists:
        return jsonify({"error": "找不到怪物"}), 404

    monster_data = mon_doc.to_dict()
    cache_manager.set(cache_key, monster_data, 600)  # 10分鐘快取
    return jsonify(monster_data)

@app.route("/battle", methods=["POST"])
@require_auth
def battle():
    try:
        data = request.json
        user_id = request.user_id
        monster_id = data.get("monster")

        if not monster_id:
            return jsonify({"error": "缺少怪物ID"}), 400

        # 🚀 戰鬥前清除使用者快取
        print(f"🔄 戰鬥前清除使用者 {user_id} 的快取...")
        invalidate_user_cache(user_id)

        # ... 原有戰鬥邏輯 ...
        user_doc = db.collection("users").document(user_id).get()
        if not user_doc.exists:
            return jsonify({"error": "找不到使用者"}), 404
        user_data = user_doc.to_dict()
        user_data["user_id"] = user_id

        is_ready, remaining_seconds = check_battle_cooldown(user_data)
        if not is_ready:
            return jsonify({
                "error": f"戰鬥冷卻中，請等待 {remaining_seconds} 秒",
                "cooldown_remaining": remaining_seconds
            }), 400

        mon_doc = db.collection("monsters").document(monster_id).get()
        if not mon_doc.exists:
            return jsonify({"error": "找不到怪物"}), 404
        monster_data = mon_doc.to_dict()

        user_skill_ids = list(user_data.get("skills", {}).keys())
        user_skill_list = []
        for i in range(0, len(user_skill_ids), 10):
            batch = user_skill_ids[i:i + 10]
            docs = db.collection("skills").where("id", "in", batch).stream()
            for doc in docs:
                user_skill_list.append(doc.to_dict())
        user_skill_list.sort(key=lambda x: x.get("sort", 9999))
        user_skill_dict = {s["id"]: s for s in user_skill_list}

        result = simulate_battle(user_data, monster_data, user_skill_dict)
        
        current_timestamp = time.time()
        result["user"]["last_battle"] = current_timestamp
        
        print(f"🕒 設定戰鬥時間戳: {current_timestamp}")
        db.collection("users").document(user_id).set(result["user"])

        # 🚀 戰鬥後再次清除快取以確保資料一致性
        print(f"✅ 戰鬥勝利，強制清除所有快取...")
        invalidate_user_cache(user_id)

        return jsonify(result)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"伺服器內部錯誤: {str(e)}"}), 500

@app.route("/battle_dungeon", methods=["POST"])
@require_auth
def battle_dungeon():
    try:
        data = request.json
        user_id = request.user_id
        dungeon_id = data.get("dungeon")
        layer = data.get("layer")

        if not dungeon_id or layer is None:
            return jsonify({"error": "缺少參數"}), 400

        # 🚀 戰鬥前清除使用者快取
        print(f"🔄 戰鬥前清除使用者 {user_id} 的快取...")
        invalidate_user_cache(user_id)

        # ... 原有戰鬥邏輯保持不變 ...
        user_doc = db.collection("users").document(user_id).get()
        if not user_doc.exists:
            return jsonify({"error": "找不到使用者"}), 404

        user_data = user_doc.to_dict()
        user_data["user_id"] = user_id

        is_ready, remaining_seconds = check_battle_cooldown(user_data)
        if not is_ready:
            return jsonify({
                "error": f"戰鬥冷卻中，請等待 {remaining_seconds} 秒",
                "cooldown_remaining": remaining_seconds
            }), 400

        dungeons = get_dungeon_data()  # 使用快取版本

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

        user_skill_ids = list(user_data.get("skills", {}).keys())
        user_skill_list = []
        for i in range(0, len(user_skill_ids), 10):
            batch = user_skill_ids[i:i + 10]
            docs = db.collection("skills").where("id", "in", batch).stream()
            for doc in docs:
                user_skill_list.append(doc.to_dict())
        user_skill_list.sort(key=lambda x: x.get("sort", 9999))
        user_skill_dict = {s["id"]: s for s in user_skill_list}
        
        result = simulate_battle(user_data, monster_data, user_skill_dict)
        
        current_timestamp = time.time()
        result["user"]["last_battle"] = current_timestamp
        
        print(f"🕒 設定戰鬥時間戳: {current_timestamp}")
        db.collection("users").document(user_id).set(result["user"])

        user_key = user_id.replace(".", "_")
        progress_ref = db.collection("progress").document(user_key)
        progress_doc = progress_ref.get()
        current_progress = progress_doc.to_dict() if progress_doc.exists else {}
        current_layer = current_progress.get(dungeon_id, 0)

        if result["result"] == "lose":
            progress_ref.set({dungeon_id: 0}, merge=True)
            # 🚀 失敗後清除相關快取
            print(f"❌ 戰鬥失敗，清除快取...")
            invalidate_user_cache(user_id, ['progress'])
            return jsonify({
                "success": False,
                "message": "你被擊敗了，進度已重設為第一層。",
                "battle_log": result["battle_log"]
            })

        if result["result"] == "win":
            if is_boss:
                clear_log = user_data.get("ClearLog", {})
                clear_count = clear_log.get(dungeon_id, 0)
                clear_log[dungeon_id] = clear_count + 1
                db.collection("users").document(user_id).set({"ClearLog": clear_log}, merge=True)
        
                progress_ref.set({dungeon_id: 0}, merge=True)
            elif int(layer) >= current_layer:
                progress_ref.set({dungeon_id: int(layer) + 1}, merge=True)

        # 🚀 勝利後強制清除所有相關快取
        print(f"✅ 戰鬥勝利，強制清除所有快取...")
        invalidate_user_cache(user_id)

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

@app.route("/cache_health")
def cache_health():
    """檢查快取系統健康狀態"""
    try:
        stats = cache_manager.get_stats()
        
        # 計算健康分數
        health_score = 100
        issues = []
        
        # 檢查命中率
        if stats['hit_rate'] < 50:
            health_score -= 30
            issues.append("快取命中率過低")
        
        # 檢查記憶體使用
        memory_mb = stats.get('memory', 0) / (1024 * 1024)
        if memory_mb > 40:  # 40MB 警告線
            health_score -= 20
            issues.append("記憶體使用量過高")
        
        # 檢查過期項目比例
        if stats.get('expired', 0) > stats.get('total_items', 0) * 0.3:
            health_score -= 15
            issues.append("過期快取項目過多")
        
        status = "healthy" if health_score >= 80 else "warning" if health_score >= 60 else "critical"
        
        return jsonify({
            "status": status,
            "health_score": max(0, health_score),
            "issues": issues,
            "recommendations": [
                "定期清理過期快取" if "過期" in str(issues) else None,
                "增加快取TTL時間" if "命中率" in str(issues) else None,
                "減少快取項目數量" if "記憶體" in str(issues) else None
            ],
            "stats": stats,
            "timestamp": time.time()
        })
        
    except Exception as e:
        return jsonify({
            "status": "error", 
            "error": str(e),
            "timestamp": time.time()
        }), 500


@app.route("/get_progress", methods=["GET"])
@require_auth
@cached_response(ttl=60)  # 1分鐘快取
def get_progress():
    user_id = request.user_id
    user_key = user_id.replace(".", "_")

    doc_ref = db.collection("progress").document(user_key)
    doc = doc_ref.get()

    if not doc.exists:
        return {"progress": {}}

    progress_data = doc.to_dict() or {}

    # 確保格式正確，避免回傳字串或 None
    if not isinstance(progress_data, dict):
        progress_data = {}

    return {"progress": progress_data}

@app.route("/inventory", methods=["GET"])
@require_auth
@cached_response(ttl=60)
def inventory():
    user_id = request.user_id
    
    item_doc = db.collection("user_items").document(user_id).get()
    if item_doc.exists:
        return item_doc.to_dict()
    
    user_doc = db.collection("users").document(user_id).get()
    if user_doc.exists:
        user_data = user_doc.to_dict()
        items = user_data.get("items", {})
        return {"items": items}
    
    return {"items": {}}

@app.route("/clear_cache", methods=["GET", "POST"])
def clear_cache():
    try:
        # 清除LRU快取
        get_dungeon_data.cache_clear()
        get_element_table.cache_clear()
        get_level_exp.cache_clear()
        get_all_skill_data.cache_clear()
        get_item_map.cache_clear()
        get_items_data.cache_clear()
        get_equips_data.cache_clear()
        
        # 清除記憶體快取
        cache_manager.clear()
        
        return jsonify({"message": "所有緩存已清除"}), 200
    except Exception as e:
        return jsonify({"error": f"清除失敗: {str(e)}"}), 500

@app.route("/levelup", methods=["POST"])
@require_auth
def levelup():
    data = request.json
    user_id = request.user_id
    allocation = data.get("allocate")

    if not allocation:
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

@app.route("/skills_full", methods=["GET"])
def get_skills_full():
    return jsonify(get_all_skill_data())

@app.route("/skills_all", methods=["GET"])
@require_auth
def get_all_skills():
    user_id = request.user_id

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
@require_auth
def save_skill_distribution():
    data = request.json
    user_id = request.user_id
    new_levels = data.get("skills")

    if not isinstance(new_levels, dict):
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

@app.route("/user_items", methods=["GET"])
@require_auth
@cached_response(ttl=60)  # 🚀 新增：1分鐘快取
def user_items():
    user_id = request.user_id
    
    doc = db.collection("user_items").document(user_id).get()
    if not doc.exists:
        return jsonify({"error": "找不到使用者"}), 404
    
    user_data = doc.to_dict()
    items = user_data.get("items", {})
    return items  # 直接返回資料，讓快取裝飾器處理 jsonify

@app.route("/user_cards", methods=["GET"])
@require_auth
@cached_response(ttl=60)  # 🚀 新增：1分鐘快取
def user_cardss():
    user_id = request.user_id
    
    doc = db.collection("users").document(user_id).get()
    if not doc.exists:
        return {"error": "找不到使用者"}, 404
    
    user_data = doc.to_dict()
    cards_owned = user_data.get("cards_owned", {})
    return cards_owned  # 直接返回資料

@app.route("/cache_stats_detailed")
def cache_stats_detailed():
    """提供詳細的快取統計資訊"""
    try:
        # 取得自定義快取統計
        cache_stats = cache_manager.get_stats()
        
        # 取得 LRU 快取統計
        lru_stats = {}
        lru_functions = [
            ('dungeon_data', get_dungeon_data),
            ('element_table', get_element_table),
            ('level_exp', get_level_exp),
            ('items_data', get_items_data),
            ('equips_data', get_equips_data),
            ('all_skill_data', get_all_skill_data),
            ('item_map', get_item_map)
        ]
        
        for name, func in lru_functions:
            if hasattr(func, 'cache_info'):
                info = func.cache_info()
                lru_stats[name] = {
                    'hits': info.hits,
                    'misses': info.misses,
                    'maxsize': info.maxsize,
                    'currsize': info.currsize,
                    'hit_rate': round(info.hits / (info.hits + info.misses) * 100, 2) if (info.hits + info.misses) > 0 else 0
                }
        
        # 計算總體統計
        total_hits = cache_stats['hits'] + sum(stat['hits'] for stat in lru_stats.values())
        total_misses = cache_stats['misses'] + sum(stat['misses'] for stat in lru_stats.values())
        overall_hit_rate = round(total_hits / (total_hits + total_misses) * 100, 2) if (total_hits + total_misses) > 0 else 0
        
        return jsonify({
            "memory_cache": cache_stats,
            "lru_cache": lru_stats,
            "overall": {
                "total_hits": total_hits,
                "total_misses": total_misses,
                "overall_hit_rate": overall_hit_rate,
                "total_cached_items": cache_stats['total_items'] + sum(stat['currsize'] for stat in lru_stats.values())
            },
            "timestamp": time.time()
        })
        
    except Exception as e:
        return jsonify({"error": f"取得快取統計失敗: {str(e)}"}), 500

@app.route("/items", methods=["GET"])
def get_items():
    return jsonify(get_item_map())

@lru_cache(maxsize=128)
def get_card_data():
    with open("parameter/cards_data.json", encoding="utf-8") as f:
        return json.load(f)

@app.route("/cards_data")
def cards_data():
    return jsonify(get_card_data())

@app.route("/craft_card", methods=["POST"])
@require_auth
def craft_card():
    data = request.json
    user_id = request.user_id
    card_id = data.get("card_id")
    materials = data.get("materials")
    success_rate = data.get("success_rate", 1.0)

    if not card_id or not materials:
        return jsonify({"success": False, "error": "缺少必要參數"}), 400

    # 取得卡片資訊
    user_ref = db.collection("users").document(user_id)
    user_doc = user_ref.get()
    if not user_doc.exists:
        return jsonify({"success": False, "error": "找不到使用者"}), 404
    user_data = user_doc.to_dict()
    cards_owned = user_data.get("cards_owned", {})

    # 改為從 user_items collection 取得道具資料
    item_ref = db.collection("user_items").document(user_id)
    item_doc = item_ref.get()
    if not item_doc.exists:
        return jsonify({"success": False, "error": "找不到使用者道具資料"}), 404

    # 正確解析 items
    raw_items = item_doc.to_dict()
    user_items = raw_items.get("items", {})
    user_items = {str(k): v for k, v in user_items.items()}

    # 檢查材料是否足夠
    for material_id, required_qty in materials.items():
        owned_qty = user_items.get(str(material_id), 0)
        if owned_qty < required_qty:
            return jsonify({
                "success": False,
                "error": f"材料 {material_id} 不足（持有 {owned_qty}，需要 {required_qty}）"
            }), 400

    # 扣除材料
    for material_id, required_qty in materials.items():
        mat_id = str(material_id)
        user_items[mat_id] = user_items.get(mat_id, 0) - required_qty
        if user_items[mat_id] <= 0:
            del user_items[mat_id]

    # 判斷成功與否
    import random
    is_success = random.random() <= success_rate

    # 更新道具資料
    item_ref.set({"items": user_items})

    if is_success:
        current_level = cards_owned.get(card_id, 0)
        cards_owned[card_id] = current_level + 1
        user_data["cards_owned"] = cards_owned
        user_ref.set(user_data)

        return jsonify({"success": True, "message": "製作成功"})
    else:
        return jsonify({"success": False, "message": "製作失敗，材料已消耗"})

@app.route("/save_equipment", methods=["POST"])
@require_auth
def save_equipment():
    data = request.json
    user_id = request.user_id
    equipment = data.get("equipment")
    
    user_ref = db.collection("users").document(user_id)
    user_doc = user_ref.get()
    
    if not user_doc.exists:
        return jsonify({"success": False, "error": "使用者不存在"}), 404
    
    user_data = user_doc.to_dict()
    user_data["equipment"] = equipment
    
    try:
        user_ref.set(user_data)
        return jsonify({"success": True, "message": "裝備更新成功"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

def invalidate_user_cache(user_id, cache_patterns=None):
    """清除使用者相關的所有快取"""
    if cache_patterns is None:
        cache_patterns = ['status', 'inventory', 'user_items', 'user_cards', 'progress']
    
    cleared_count = 0
    # 🎯 修正：改進快取清除邏輯，正確匹配快取鍵
    for key in list(cache_manager._cache.keys()):
        should_clear = False
        
        # 檢查是否包含使用者ID
        if user_id in key:
            # 檢查是否匹配任何快取模式
            for pattern in cache_patterns:
                if pattern in key:
                    should_clear = True
                    break
            
            # 🚀 新增：額外檢查完整的API端點名稱
            api_endpoints = ['status_', 'get_progress_', 'inventory_', 'user_items_', 'user_cards_']
            for endpoint in api_endpoints:
                if endpoint in key:
                    should_clear = True
                    break
        
        if should_clear:
            cache_manager.delete(key)
            cleared_count += 1
            print(f"🧹 清除快取: {key}")
    
    print(f"✅ 已清除使用者 {user_id} 的 {cleared_count} 個快取項目")
    return cleared_count

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
