import os
import json
import time
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_compress import Compress
import firebase_admin
from firebase_admin import credentials, firestore, auth as firebase_auth
from battle import simulate_battle, get_equipment_bonus, calculate_hit, calculate_damage, get_element_multiplier, level_damage_modifier
from functools import lru_cache, wraps
import re
import datetime 
import pytz

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
    cooldown_seconds = 30
    
    time_diff = current_timestamp - last_battle
    
    if time_diff >= cooldown_seconds:
        return True, 0
    else:
        remaining = cooldown_seconds - time_diff
        remaining = max(0, round(remaining, 2))
        return False, remaining

def force_clear_user_cache(user_id):
    """強制清除用戶相關的所有緩存"""
    
    # 清除記憶體緩存
    invalidate_user_cache(user_id)
    
    # 清除LRU緩存（如果有相關的用戶數據）
    cache_patterns = [
        f"user_{user_id}",
        f"status_{user_id}",
        f"battle_{user_id}",
        "get_all_skill_data",  # 技能數據可能影響戰鬥
    ]
    
    # 清除特定緩存項目
    for pattern in cache_patterns:
        try:
            for key in list(cache_manager._cache.keys()):
                if pattern in key:
                    cache_manager.delete(key)
        except Exception as e:
            print(f"⚠️ 清除緩存 {pattern} 時出錯: {e}")

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
def status():
    user_id = request.user_id
    
    # 🚀 強制從數據庫獲取最新數據，避免緩存問題
    doc = db.collection("users").document(user_id).get()
    if not doc.exists:
        return jsonify({"error": "找不到使用者"}), 404

    user_data = doc.to_dict()
    
    # 🚀 確保 last_battle 字段存在
    if "last_battle" not in user_data:
        user_data["last_battle"] = 0
        db.collection("users").document(user_id).set({"last_battle": 0}, merge=True)
    
    # 🚀 重新計算冷卻狀態
    is_ready, remaining_seconds = check_battle_cooldown(user_data)
    user_data["battle_cooldown_remaining"] = remaining_seconds
    user_data["battle_ready"] = is_ready
    
    return jsonify(user_data)

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

        # 戰鬥前清除使用者快取
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
        
        db.collection("users").document(user_id).set(result["user"])

        # 🚀 戰鬥後再次清除快取以確保資料一致性
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

        # 🚀 戰鬥前強制清除所有緩存
        force_clear_user_cache(user_id)

        # 🚀 修正：直接從數據庫獲取最新用戶數據，避免緩存問題
        user_doc = db.collection("users").document(user_id).get()
        if not user_doc.exists:
            return jsonify({"error": "找不到使用者"}), 404

        user_data = user_doc.to_dict()
        user_data["user_id"] = user_id

        # 🚀 修正：確保使用最新的時間戳檢查冷卻
        current_check_time = time.time()
        
        is_ready, remaining_seconds = check_battle_cooldown(user_data)
        if not is_ready:
            return jsonify({
                "error": f"戰鬥冷卻中，請等待 {remaining_seconds} 秒",
                "cooldown_remaining": remaining_seconds
            }), 400

        dungeons = get_dungeon_data()
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

        # 獲取技能數據
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
        
        # 🚀 修正：戰鬥完成後立即設定精確的時間戳
        battle_end_timestamp = time.time()
        result["user"]["last_battle"] = battle_end_timestamp
        
        
        # 🚀 修正：立即寫入數據庫並確認寫入成功
        try:
            db.collection("users").document(user_id).set(result["user"])
            
            # 🚀 立即驗證寫入結果
            verify_doc = db.collection("users").document(user_id).get()
            if verify_doc.exists:
                verify_data = verify_doc.to_dict()
                stored_timestamp = verify_data.get("last_battle", 0)
                if abs(stored_timestamp - battle_end_timestamp) > 1:
                    print(f"⚠️ 時間戳寫入可能有問題！預期: {battle_end_timestamp:.2f}, 實際: {stored_timestamp:.2f}")
            
        except Exception as db_error:
            print(f"❌ 數據庫寫入失敗: {db_error}")
            return jsonify({"error": "數據儲存失敗"}), 500

        # 🚀 戰鬥後強制清除所有緩存
        force_clear_user_cache(user_id)

        # 處理副本進度
        user_key = user_id.replace(".", "_")
        progress_ref = db.collection("progress").document(user_key)
        progress_doc = progress_ref.get()
        current_progress = progress_doc.to_dict() if progress_doc.exists else {}
        current_layer = current_progress.get(dungeon_id, 0)

        if result["result"] == "lose":
            progress_ref.set({dungeon_id: 0}, merge=True)
            force_clear_user_cache(user_id)
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

        # 🚀 最後再次清除緩存
        force_clear_user_cache(user_id)

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

# 重置能力值
@app.route("/reset_stats", methods=["POST"])
@require_auth
def reset_stats():
    """
    重置玩家的所有能力值到初始狀態
    需要消耗一個能力值重置券
    """
    try:
        user_id = request.user_id
        
        # 1. 獲取使用者資料
        user_ref = db.collection("users").document(user_id)
        user_doc = user_ref.get()
        
        if not user_doc.exists:
            return jsonify({"error": "找不到使用者"}), 404
        
        user_data = user_doc.to_dict()
        current_level = user_data.get("level", 1)
        
        # 2. 檢查使用者道具
        item_ref = db.collection("user_items").document(user_id)
        item_doc = item_ref.get()
        
        if not item_doc.exists:
            return jsonify({"error": "找不到使用者道具資料"}), 404
        
        item_data = item_doc.to_dict()
        user_items = item_data.get("items", {})
        
        # 3. 檢查是否有重置券
        reset_tickets = user_items.get("reset_stats_ticket", 0)
        if reset_tickets < 1:
            return jsonify({"error": "沒有「能力值重置券」，無法進行重置"}), 400
        
        # 4. 扣除重置券
        user_items["reset_stats_ticket"] = reset_tickets - 1
        if user_items["reset_stats_ticket"] <= 0:
            del user_items["reset_stats_ticket"]
        
        # 5. 計算返還的能力值點數
        # 公式：(等級 - 1) × 5
        points_to_return = max(0, (current_level - 1) * 5)
        
        # 6. 重置能力值到初始狀態
        initial_base_stats = {
            "hp": 100,
            "attack": 20,
            "shield": 0,
            "evade": 0.1,
            "other_bonus": 0,
            "accuracy": 1.0,
            "luck": 10,
            "atk_speed": 100
        }
        
        # 7. 更新使用者資料
        user_data["base_stats"] = initial_base_stats
        user_data["stat_points"] = user_data.get("stat_points", 0) + points_to_return
        
        # 8. 儲存更新後的資料到資料庫
        # 使用批次寫入確保資料一致性
        batch = db.batch()
        
        # 更新使用者資料
        batch.set(user_ref, user_data)
        
        # 更新道具資料
        item_data["items"] = user_items
        batch.set(item_ref, item_data)
        
        # 提交批次操作
        batch.commit()
        
        # 9. 清除相關快取
        invalidate_user_cache(user_id)
        
        # 10. 回傳成功結果
        return jsonify({
            "success": True,
            "message": "能力值重置成功",
            "points_returned": points_to_return,
            "new_stat_points": user_data["stat_points"],
            "tickets_remaining": user_items.get("reset_stats_ticket", 0)
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"伺服器錯誤: {str(e)}"}), 500

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
    return cleared_count

def invalidate_user_cache(user_id):
    """清除指定使用者的快取項目"""
    try:
        # 清除記憶體快取中與該使用者相關的項目
        user_patterns = [
            f"status_{user_id}",
            f"inventory_{user_id}", 
            f"user_items_{user_id}",
            f"user_cards_{user_id}",
            f"get_progress_{user_id}",
        ]
        
        for pattern in user_patterns:
            # 搜尋並刪除匹配的快取鍵
            keys_to_delete = [key for key in cache_manager._cache.keys() if pattern in key]
            for key in keys_to_delete:
                cache_manager.delete(key)
        
        print(f"已清除使用者 {user_id} 的快取項目")
        
    except Exception as e:
        print(f"清除使用者快取時發生錯誤: {e}")

# 🌍 世界王系統相關函數

@lru_cache(maxsize=1)
def get_world_boss_config():
    """載入世界王配置"""
    with open("parameter/world_boss.json", encoding="utf-8") as f:
        return json.load(f)

def initialize_world_boss_global_state():
    """初始化世界王全域狀態（僅在首次運行時）"""
    try:
        global_ref = db.collection("world_boss_global").document("current_status")
        global_doc = global_ref.get()
        
        if not global_doc.exists:
            config = get_world_boss_config()
            initial_state = {
                "current_hp": config["initial_stats"]["max_hp"],
                "max_hp": config["initial_stats"]["max_hp"],
                "current_phase": 1,
                "total_participants": 0,
                "total_damage_dealt": 0,
                "created_time": time.time(),
                "last_reset_time": time.time(),
                "weekly_reset_time": datetime.datetime.now(pytz.timezone('Asia/Taipei')).isoformat()
            }
            
            global_ref.set(initial_state)
            print("✅ 世界王全域狀態已初始化")
            return initial_state
        else:
            return global_doc.to_dict()
            
    except Exception as e:
        print(f"❌ 初始化世界王狀態失敗: {e}")
        return None

def get_world_boss_global_state():
    """取得世界王全域狀態"""
    try:
        global_ref = db.collection("world_boss_global").document("current_status")
        global_doc = global_ref.get()
        
        if global_doc.exists:
            state = global_doc.to_dict()
            # 驗證關鍵欄位
            required_fields = ["current_hp", "max_hp"]
            for field in required_fields:
                if field not in state:
                    print(f"⚠️ 缺少關鍵欄位 {field}，嘗試修復")
                    if field == "current_hp":
                        state[field] = 999999999
                    elif field == "max_hp":
                        state[field] = 999999999
                    # 更新到資料庫
                    global_ref.update({field: state[field]})
            
            return state
        else:
            print("⚠️ 全域狀態文檔不存在，自動初始化")
            return initialize_world_boss_global_state()
            
    except Exception as e:
        print(f"❌ 取得世界王全域狀態失敗: {e}")
        return None

def is_weekend_restriction():
    """檢查是否為週日限制時間 (UTC+8)"""
    taipei_tz = pytz.timezone('Asia/Taipei')
    now_taipei = datetime.datetime.now(taipei_tz)
    
    # 週日 (0=週一, 6=週日)
    if now_taipei.weekday() == 6:  # 週日
        return True, "世界王於週日休整，請於週一再來挑戰！"
    
    return False, ""

def check_world_boss_cooldown(user_id):
    """檢查世界王挑戰冷卻時間"""
    try:
        player_ref = db.collection("world_boss_players").document(user_id)
        player_doc = player_ref.get()
        
        if not player_doc.exists:
            return True, 0, None
        
        player_data = player_doc.to_dict()
        last_challenge_time = player_data.get("last_challenge_time", 0)
        
        if not last_challenge_time:
            return True, 0, None
        
        current_time = time.time()
        cooldown_duration = 60 * 60  # 1小時 = 3600秒
        time_diff = current_time - last_challenge_time
        
        if time_diff >= cooldown_duration:
            return True, 0, None
        else:
            remaining = cooldown_duration - time_diff
            cooldown_end_time = int((last_challenge_time + cooldown_duration) * 1000)  # 轉換為毫秒
            return False, remaining, cooldown_end_time
            
    except Exception as e:
        print(f"檢查世界王冷卻失敗: {e}")
        return True, 0, None

def calculate_world_boss_damage(user_data, world_boss_config):
    """計算玩家對世界王的傷害（加入攻擊速度和幸運影響）"""
    try:
        # 取得玩家實際戰鬥屬性（包含裝備加成）
        raw_stats = user_data.get("base_stats", {})
        equipment = user_data.get("equipment", {})
        equip_bonus = get_equipment_bonus(equipment)
        
        # 計算玩家總屬性
        player_stats = {}
        for stat in set(list(raw_stats.keys()) + list(equip_bonus.keys())):
            player_stats[stat] = raw_stats.get(stat, 0) + equip_bonus.get(stat, 0)
        
        # 世界王屬性
        boss_stats = world_boss_config["stats"]
        player_level = user_data.get("level", 1)
        boss_level = world_boss_config["level"]
        
        # 計算當前階段
        current_phase = get_current_world_boss_phase()
        phase_config = world_boss_config["phases"][str(current_phase)]
        
        # 應用階段修正
        effective_boss_shield = boss_stats["shield"] * phase_config["defense_multiplier"]
        
        # 命中檢查
        player_accuracy = player_stats.get("accuracy", 0.8)
        boss_evade = boss_stats.get("evade", 0.1)
        player_luck = player_stats.get("luck", 10)
        
        hit_success = calculate_hit(player_accuracy, boss_evade, player_luck)
        
        if not hit_success:
            return 0, "攻擊未命中"
        
        # 🚀 新增：攻擊速度影響計算
        player_speed = player_stats.get("atk_speed", 100)
        boss_speed = boss_stats.get("atk_speed", 100)
        
        # 速度比率計算：玩家速度 / 世界王速度
        speed_ratio = player_speed / boss_speed if boss_speed > 0 else 1.0
        
        # 限制速度倍率範圍（0.1x ~ 3.0x），避免過於極端
        speed_multiplier = max(0.1, min(3.0, speed_ratio))
        
        # 🚀 新增：幸運暴擊計算
        # 每點幸運增加0.15%的暴擊率，上限50%
        crit_chance = min(player_luck * 0.0015, 0.50)
        
        import random
        is_critical = random.random() < crit_chance
        crit_multiplier = 2.0 if is_critical else 1.0
        
        # 計算基礎傷害
        player_attack = player_stats.get("attack", 20)
        other_bonus = player_stats.get("other_bonus", 0)
        
        # 屬性克制（玩家技能屬性 vs 世界王屬性）
        player_elements = ["none"]  # 預設為無屬性，可以根據裝備或技能修改
        boss_elements = world_boss_config.get("element", ["all"])
        element_multiplier = get_element_multiplier(player_elements, boss_elements)
        
        # 等級差距修正
        level_multiplier = level_damage_modifier(player_level, boss_level)
        
        # 計算最終傷害
        base_damage = calculate_damage(player_attack, 1.0, other_bonus, effective_boss_shield)
        
        # 🚀 應用所有倍率：等級差距 × 屬性克制 × 攻擊速度 × 暴擊
        final_damage = int(base_damage * level_multiplier * element_multiplier * speed_multiplier * crit_multiplier)
        
        # 確保最小傷害
        final_damage = max(final_damage, 1)
        
        # 🚀 生成詳細的戰鬥訊息
        hit_message = "成功命中"
        damage_details = []
        
        # 速度影響說明
        if speed_multiplier > 1.2:
            damage_details.append(f"【高速攻擊】速度優勢 ×{speed_multiplier:.1f}")
        elif speed_multiplier < 0.8:
            damage_details.append(f"【速度劣勢】攻擊緩慢 ×{speed_multiplier:.1f}")
        
        # 暴擊說明
        if is_critical:
            damage_details.append(f"【暴擊】幸運爆發 ×{crit_multiplier:.1f}")
        
        # 階段影響說明
        if phase_config["defense_multiplier"] > 1.0:
            damage_details.append(f"【階段強化】防禦提升 ÷{phase_config['defense_multiplier']:.1f}")
        
        # 組合詳細訊息
        if damage_details:
            hit_message = f"成功命中！{' '.join(damage_details)}"
        
        # 添加一些隨機性（±5%），減少之前的±10%以讓計算更穩定
        random_factor = random.uniform(0.95, 1.05)
        final_damage = int(final_damage * random_factor)
        
        return final_damage, hit_message
        
    except Exception as e:
        print(f"計算世界王傷害失敗: {e}")
        import traceback
        traceback.print_exc()
        return 1, "計算錯誤，造成最小傷害"

def get_current_world_boss_phase(world_boss_config=None):
    """根據世界王血量計算當前階段"""
    try:
        global_state = get_world_boss_global_state()
        if not global_state:
            print("⚠️ 無法獲取全域狀態，返回階段1")
            return 1
            
        current_hp = global_state.get("current_hp", 0)
        max_hp = global_state.get("max_hp", 1)
        
        if max_hp <= 0:
            print("⚠️ 最大HP異常，返回階段1")
            return 1
            
        hp_percentage = (current_hp / max_hp) * 100
        
        # 根據血量百分比決定階段
        if hp_percentage > 60:
            return 1
        elif hp_percentage > 30:
            return 2
        else:
            return 3
            
    except Exception as e:
        print(f"❌ 取得世界王階段失敗: {e}")
        return 1

def update_world_boss_global_stats(damage_dealt):
    """更新世界王全域統計"""
    try:
        global_ref = db.collection("world_boss_global").document("current_status")
        global_state = get_world_boss_global_state()
        
        if not global_state:
            print("⚠️ 全域狀態不存在，嘗試初始化")
            config = get_world_boss_config()
            global_state = initialize_world_boss_global_state()
        
        if not global_state:
            print("❌ 無法獲取或初始化全域狀態")
            return None
        
        # 更新數據，增加更多錯誤檢查
        current_hp = global_state.get("current_hp", 0)
        if not isinstance(current_hp, (int, float)):
            print(f"⚠️ 當前HP值異常: {current_hp}, 重置為0")
            current_hp = 0
            
        new_hp = max(0, current_hp - damage_dealt)
        new_total_damage = global_state.get("total_damage_dealt", 0) + damage_dealt
        
        # 安全地獲取新階段
        try:
            new_phase = get_current_world_boss_phase()
        except Exception as phase_error:
            print(f"⚠️ 獲取階段失敗: {phase_error}, 使用預設值1")
            new_phase = 1
        
        updated_state = {
            "current_hp": new_hp,
            "max_hp": global_state.get("max_hp", 999999999),
            "current_phase": new_phase,
            "total_damage_dealt": new_total_damage,
            "last_update_time": time.time()
        }
        
        print(f"🔄 更新世界王狀態: HP {current_hp} -> {new_hp}, 傷害 +{damage_dealt}")
        
        # 合併更新，保留其他欄位
        global_ref.update(updated_state)
        
        # 返回更新後的完整狀態
        global_state.update(updated_state)
        return global_state
        
    except Exception as e:
        print(f"❌ 更新世界王全域統計失敗: {e}")
        import traceback
        traceback.print_exc()
        return None

# 🌍 世界王 API 端點

@app.route("/world_boss_status", methods=["GET"])
def world_boss_status():
    """取得世界王當前狀態"""
    try:
        config = get_world_boss_config()
        
        # 取得或初始化全域狀態
        global_state = get_world_boss_global_state()
        if not global_state:
            return jsonify({"error": "無法取得世界王狀態"}), 500
        
        # 計算參與者總數（有造成傷害的玩家）
        try:
            players_ref = db.collection("world_boss_players").where("total_damage", ">", 0)
            participants_count = len([doc for doc in players_ref.stream()])
        except Exception:
            participants_count = global_state.get("total_participants", 0)
        
        # 更新參與者數量到 Firebase（可選，減少重複計算）
        if participants_count != global_state.get("total_participants", 0):
            try:
                db.collection("world_boss_global").document("current_status").update({
                    "total_participants": participants_count
                })
                global_state["total_participants"] = participants_count
            except Exception as e:
                print(f"更新參與者數量失敗: {e}")
        
        result = {
            "boss_id": config["boss_id"],
            "name": config["name"],
            "description": config["description"],
            "image": config["image"],
            "level": config["level"],
            "element": config["element"],
            "current_hp": global_state.get("current_hp", config["initial_stats"]["max_hp"]),
            "max_hp": global_state.get("max_hp", config["initial_stats"]["max_hp"]),
            "current_phase": global_state.get("current_phase", 1),
            "total_participants": participants_count,
            "total_damage_dealt": global_state.get("total_damage_dealt", 0),
            "phases": config["phases"],
            "last_update_time": global_state.get("last_update_time", global_state.get("created_time", time.time()))
        }
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({"error": f"取得世界王狀態失敗: {str(e)}"}), 500

@app.route("/world_boss_challenge", methods=["POST"])
@require_auth
def world_boss_challenge():
    """挑戰世界王"""
    try:
        user_id = request.user_id
        
        # 檢查週日限制
        is_restricted, restriction_msg = is_weekend_restriction()
        if is_restricted:
            return jsonify({"error": restriction_msg}), 403
        
        # 檢查冷卻時間
        can_challenge, remaining_cooldown, cooldown_end_time = check_world_boss_cooldown(user_id)
        if not can_challenge:
            return jsonify({
                "error": f"挑戰冷卻中，請等待 {int(remaining_cooldown/60)} 分鐘",
                "cooldown_remaining": remaining_cooldown,
                "cooldown_end_time": cooldown_end_time
            }), 400
        
        # 取得使用者資料
        user_doc = db.collection("users").document(user_id).get()
        if not user_doc.exists:
            return jsonify({"error": "找不到使用者"}), 404
        
        user_data = user_doc.to_dict()
        user_data["user_id"] = user_id
        
        # 載入世界王配置
        config = get_world_boss_config()
        
        # 計算傷害
        damage_dealt, hit_message = calculate_world_boss_damage(user_data, config)
        
        # 記錄挑戰時間
        challenge_time = time.time()
        new_cooldown_end_time = int((challenge_time + 3600) * 1000)  # 1小時後，轉為毫秒
        
        # 更新玩家世界王資料
        player_ref = db.collection("world_boss_players").document(user_id)
        player_doc = player_ref.get()
        
        if player_doc.exists:
            player_data = player_doc.to_dict()
        else:
            player_data = {
                "user_id": user_id,
                "nickname": user_data.get("nickname", user_id),
                "total_damage": 0,
                "challenge_count": 0,
                "first_challenge_time": challenge_time,
                "last_challenge_time": 0
            }
        
        # 更新數據
        player_data["total_damage"] = player_data.get("total_damage", 0) + damage_dealt
        player_data["challenge_count"] = player_data.get("challenge_count", 0) + 1
        player_data["last_challenge_time"] = challenge_time
        player_data["nickname"] = user_data.get("nickname", user_id)  # 更新暱稱
        
        player_ref.set(player_data)
        
        # 更新世界王全域統計
        global_stats = update_world_boss_global_stats(damage_dealt)
        
        # 計算玩家排名
        all_players = db.collection("world_boss_players").order_by("total_damage", direction=firestore.Query.DESCENDING).stream()
        rank = 1
        for i, doc in enumerate(all_players):
            if doc.id == user_id:
                rank = i + 1
                break
        
        # 🚀 新的經驗值計算系統
        exp_gained, damage_percentage, reward_tier, tier_desc = calculate_world_boss_exp_reward(damage_dealt, config)
        user_data["exp"] += exp_gained
        
        # 更新使用者經驗值
        db.collection("users").document(user_id).update({"exp": user_data["exp"]})
        
        # 道具獎勵（保持原有邏輯）
        drop_result = {"items": {}}
        if damage_dealt >= 10:  # 只有造成足夠傷害才有道具獎勵
            from battle import apply_drops
            drop_result = apply_drops(db, user_id, config["rewards"]["drops"], user_data.get("luck", 10))
        
        # 🔧 修復：正確獲取最大HP值
        max_hp = config.get("initial_stats", {}).get("max_hp", 9999999)
        if global_stats:
            max_hp = global_stats.get("max_hp", max_hp)
        
        result = {
            "success": True,
            "damage_dealt": damage_dealt,
            "hit_message": hit_message,
            "total_damage": player_data["total_damage"],
            "current_rank": rank,
            "exp_gained": exp_gained,
            "damage_percentage": round(damage_percentage, 4),  # 保留4位小數
            "reward_tier": reward_tier,
            "tier_description": tier_desc,
            "rewards": drop_result,
            "cooldown_end_time": new_cooldown_end_time,
            "world_boss_hp": {
                "current": global_stats.get("current_hp", 0) if global_stats else 0,
                "max": max_hp
            }
        }
        
        return jsonify(result)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"🔥 世界王挑戰錯誤詳情: {str(e)}")
        return jsonify({"error": f"挑戰世界王失敗: {str(e)}"}), 500

@app.route("/world_boss_player_data", methods=["GET"])
@require_auth
def world_boss_player_data():
    """取得玩家世界王數據"""
    try:
        user_id = request.user_id
        
        # 取得玩家世界王資料
        player_ref = db.collection("world_boss_players").document(user_id)
        player_doc = player_ref.get()
        
        if player_doc.exists:
            player_data = player_doc.to_dict()
        else:
            player_data = {
                "total_damage": 0,
                "challenge_count": 0,
                "last_challenge_time": 0
            }
        
        # 計算排名
        all_players = db.collection("world_boss_players").order_by("total_damage", direction=firestore.Query.DESCENDING).stream()
        rank = 0
        for i, doc in enumerate(all_players):
            if doc.id == user_id:
                rank = i + 1
                break
        
        # 檢查冷卻狀態
        can_challenge, remaining_cooldown, cooldown_end_time = check_world_boss_cooldown(user_id)
        
        result = {
            "total_damage": player_data.get("total_damage", 0),
            "challenge_count": player_data.get("challenge_count", 0),
            "rank": rank,
            "can_challenge": can_challenge,
            "cooldown_remaining": remaining_cooldown if not can_challenge else 0,
            "cooldown_end_time": cooldown_end_time,
            "last_challenge_time": player_data.get("last_challenge_time", 0)
        }
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({"error": f"取得玩家數據失敗: {str(e)}"}), 500

@app.route("/world_boss_reset", methods=["POST"])
def world_boss_reset():
    """重置世界王（管理員功能或週重置）"""
    try:
        # 這個端點可以用於每週重置世界王
        # 在實際部署時，建議加上管理員權限檢查
        
        config = get_world_boss_config()
        
        # 重置全域狀態
        global_ref = db.collection("world_boss_global").document("current_status")
        reset_data = {
            "current_hp": config["initial_stats"]["max_hp"],
            "max_hp": config["initial_stats"]["max_hp"],
            "current_phase": 1,
            "total_participants": 0,
            "total_damage_dealt": 0,
            "last_reset_time": time.time(),
            "weekly_reset_time": datetime.datetime.now(pytz.timezone('Asia/Taipei')).isoformat(),
            "created_time": time.time()
        }
        global_ref.set(reset_data)
        
        # 可選：清除玩家數據（如果需要每週重置排行榜）
        # 注意：這會刪除所有玩家的世界王數據，請謹慎使用
        clear_leaderboard = request.json.get("clear_leaderboard", False) if request.json else False
        if clear_leaderboard:
            try:
                players_ref = db.collection("world_boss_players")
                batch = db.batch()
                docs_deleted = 0
                for doc in players_ref.stream():
                    batch.delete(doc.reference)
                    docs_deleted += 1
                    # Firebase 批次操作限制500個操作
                    if docs_deleted >= 500:
                        batch.commit()
                        batch = db.batch()
                        docs_deleted = 0
                
                if docs_deleted > 0:
                    batch.commit()
                    
                print(f"已清除所有玩家世界王數據")
            except Exception as e:
                print(f"清除玩家數據時發生錯誤: {e}")
        
        return jsonify({
            "message": "世界王已重置", 
            "reset_time": reset_data["weekly_reset_time"],
            "new_hp": reset_data["current_hp"],
            "leaderboard_cleared": clear_leaderboard
        })
        
    except Exception as e:
        return jsonify({"error": f"重置世界王失敗: {str(e)}"}), 500

# 🚀 新增：世界王初始化檢查端點
@app.route("/world_boss_init_check", methods=["GET"])
def world_boss_init_check():
    try:
        global_state = get_world_boss_global_state()

        if global_state:
            return jsonify({
                "initialized": True,
                "current_hp": global_state.get("current_hp", 0),
                "max_hp": global_state.get("max_hp", 0),
                "message": "世界王狀態正常"
            })
        else:
            return jsonify({
                "initialized": False,
                "error": "世界王初始化失敗"
            }), 500
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            "initialized": False,
            "error": f"檢查世界王狀態失敗: {str(e)}"
        }), 500

def calculate_world_boss_exp_reward(damage_dealt, world_boss_config):
    """
    根據對世界王總血量的傷害百分比計算經驗值獎勵
    
    Args:
        damage_dealt: 造成的傷害
        world_boss_config: 世界王配置
    
    Returns:
        tuple: (獲得的經驗值, 傷害百分比, 獎勵等級說明)
    """
    try:
        # 🎯 取得世界王最大血量
        max_hp = world_boss_config["initial_stats"]["max_hp"]
        
        # 🧮 計算傷害百分比
        damage_percentage = (damage_dealt / max_hp) * 100
        
        # 🏆 根據傷害百分比給予經驗值
        if damage_percentage >= 1.0:
            exp_gained = 300
            reward_tier = "S級傷害"
            tier_desc = "造成1.0%以上傷害"
        elif damage_percentage >= 0.5:
            exp_gained = 200
            reward_tier = "A級傷害"
            tier_desc = "造成0.5%~1.0%傷害"
        elif damage_percentage >= 0.1:
            exp_gained = 100
            reward_tier = "B級傷害"
            tier_desc = "造成0.1%~0.5%傷害"
        else:
            exp_gained = 20
            reward_tier = "C級傷害"
            tier_desc = "造成0.1%以下傷害"
        
        return exp_gained, damage_percentage, reward_tier, tier_desc
        
    except Exception as e:
        print(f"計算經驗值獎勵失敗: {e}")
        # 發生錯誤時給予最低獎勵
        return 20, 0.0, "計算錯誤", "系統錯誤，給予基礎獎勵"

@app.route("/world_boss_leaderboard", methods=["GET"])
def world_boss_leaderboard():
    """取得世界王排行榜"""
    try:
        # 可選：設定顯示人數上限
        limit = request.args.get("limit", 10, type=int)  # 預設顯示前50名
        limit = min(limit, 10)  # 最多不超過20名
        
        # 取得排行榜數據（按累積傷害降序）
        players_ref = db.collection("world_boss_players").order_by("total_damage", direction=firestore.Query.DESCENDING)
        
        # 只取得有造成傷害的玩家
        players_ref = players_ref.where("total_damage", ">", 0)
        
        # 應用人數限制
        players_ref = players_ref.limit(limit)
        
        leaderboard = []
        for doc in players_ref.stream():
            player_data = doc.to_dict()
            leaderboard.append({
                "user_id": doc.id,
                "nickname": player_data.get("nickname", doc.id),
                "total_damage": player_data.get("total_damage", 0),
                "challenge_count": player_data.get("challenge_count", 0),
                "last_challenge_time": player_data.get("last_challenge_time", 0)
            })
        
        return jsonify({
            "leaderboard": leaderboard,
            "total_players": len(leaderboard),
            "limit": limit
        })
        
    except Exception as e:
        return jsonify({"error": f"取得排行榜失敗: {str(e)}"}), 500

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
