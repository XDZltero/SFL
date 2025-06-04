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
from datetime import datetime, timedelta
import pytz
import threading
import schedule

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

# 🚀 管理員權限裝飾器 - 移到這裡，在 require_auth 之後
def require_admin(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 先檢查基本授權
        auth_header = request.headers.get('Authorization')
        
        if not auth_header:
            return jsonify({'error': '缺少授權標頭'}), 401
        
        try:
            token = auth_header.split(' ')[1]
            decoded_token = firebase_auth.verify_id_token(token)
            user_id = decoded_token['email']
            request.user_id = user_id
            request.uid = decoded_token['uid']
            
        except Exception as e:
            return jsonify({'error': '無效的授權令牌'}), 401
        
        # 🚀 檢查管理員權限
        try:
            user_doc = db.collection("users").document(user_id).get()
            if not user_doc.exists:
                return jsonify({'error': '使用者不存在'}), 404
            
            user_data = user_doc.to_dict()
            is_admin = user_data.get('admin', False)
            
            if not is_admin:
                return jsonify({'error': '權限不足：需要管理員權限'}), 403
            
            request.is_admin = True
            print(f"🔑 管理員 {user_id} 執行管理操作")
            
        except Exception as e:
            print(f"檢查管理員權限失敗: {e}")
            return jsonify({'error': '權限檢查失敗'}), 500
        
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
            "atk_speed": 100,
            "penetrate": 0
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
        elif stat in ["attack", "luck", "atk_speed"]:
            user["base_stats"][stat] += value
        else:
            return jsonify({"error": f"請勿竄改成 {stat} 屬性。"}), 400

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

# 取得卡片失敗次數
@app.route("/card_failure_counts", methods=["GET"])
@require_auth
@cached_response(ttl=30)  # 30秒快取
def get_card_failure_counts():
    """取得使用者所有卡片的失敗次數"""
    try:
        user_id = request.user_id
        
        failure_ref = db.collection("card_failure_counts").document(user_id)
        failure_doc = failure_ref.get()
        
        if failure_doc.exists:
            return failure_doc.to_dict().get("failure_counts", {})
        else:
            return {}
            
    except Exception as e:
        return jsonify({"error": f"取得失敗次數失敗: {str(e)}"}), 500

def calculate_enhanced_success_rate(base_rate, failure_count):
    """計算強化後的成功率"""
    # 每次失敗增加5%成功率，最高100%
    enhanced_rate = base_rate + (failure_count * 0.05)
    return min(enhanced_rate, 1.0)  # 最高100%

def get_user_card_failure_counts(user_id):
    """取得使用者卡片失敗次數"""
    try:
        failure_ref = db.collection("card_failure_counts").document(user_id)
        failure_doc = failure_ref.get()
        
        if failure_doc.exists:
            return failure_doc.to_dict().get("failure_counts", {})
        else:
            return {}
    except Exception as e:
        print(f"取得失敗次數失敗: {e}")
        return {}

def update_card_failure_count(user_id, card_id, is_success):
    """更新卡片失敗次數"""
    try:
        failure_ref = db.collection("card_failure_counts").document(user_id)
        failure_doc = failure_ref.get()
        
        if failure_doc.exists:
            failure_data = failure_doc.to_dict()
        else:
            failure_data = {"user_id": user_id, "failure_counts": {}}
        
        failure_counts = failure_data.get("failure_counts", {})
        
        if is_success:
            # 成功時清空失敗次數
            if card_id in failure_counts:
                del failure_counts[card_id]
        else:
            # 失敗時增加失敗次數
            failure_counts[card_id] = failure_counts.get(card_id, 0) + 1
        
        failure_data["failure_counts"] = failure_counts
        failure_data["last_update_time"] = time.time()
        
        failure_ref.set(failure_data)
        return True
        
    except Exception as e:
        print(f"更新失敗次數失敗: {e}")
        return False

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
    base_success_rate = data.get("success_rate", 1.0)

    if not card_id or not materials:
        return jsonify({"success": False, "error": "缺少必要參數"}), 400

    # 🚀 新增：取得失敗次數並計算強化成功率
    failure_counts = get_user_card_failure_counts(user_id)
    failure_count = failure_counts.get(card_id, 0)
    enhanced_success_rate = calculate_enhanced_success_rate(base_success_rate, failure_count)
    
    print(f"🎲 卡片 {card_id} 強化：基礎成功率 {base_success_rate*100:.0f}%，失敗次數 {failure_count}，強化後成功率 {enhanced_success_rate*100:.0f}%")

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

    # 🚀 修改：使用強化後的成功率判斷成功與否
    import random
    is_success = random.random() <= enhanced_success_rate

    # 使用批次操作確保原子性
    batch = db.batch()

    # 更新道具資料
    batch.set(item_ref, {"items": user_items})

    if is_success:
        current_level = cards_owned.get(card_id, 0)
        cards_owned[card_id] = current_level + 1
        user_data["cards_owned"] = cards_owned
        batch.set(user_ref, user_data)
        
        # 🚀 新增：成功時清空失敗次數
        update_success = update_card_failure_count(user_id, card_id, True)
        
        success_message = "製作成功"
        if failure_count > 0:
            success_message += f"！（累積失敗 {failure_count} 次後成功）"
        
        try:
            batch.commit()
            print(f"✅ 卡片 {card_id} 強化成功，失敗次數已重置")
            return jsonify({
                "success": True, 
                "message": success_message,
                "failure_count_reset": failure_count > 0,
                "previous_failure_count": failure_count
            })
        except Exception as e:
            return jsonify({"success": False, "error": f"資料儲存失敗: {str(e)}"}), 500
    else:
        # 🚀 新增：失敗時增加失敗次數
        update_card_failure_count(user_id, card_id, False)
        new_failure_count = failure_count + 1
        
        # 計算下次強化的成功率
        next_success_rate = calculate_enhanced_success_rate(base_success_rate, new_failure_count)
        
        try:
            batch.commit()
            print(f"❌ 卡片 {card_id} 強化失敗，失敗次數增加至 {new_failure_count}")
            return jsonify({
                "success": False, 
                "message": "製作失敗，材料已消耗",
                "failure_count": new_failure_count,
                "next_success_rate": next_success_rate,
                "bonus_rate": (new_failure_count * 5)
            })
        except Exception as e:
            return jsonify({"success": False, "error": f"資料儲存失敗: {str(e)}"}), 500

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
            "atk_speed": 100,
            "penetrate": 0
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
    try:
        global_ref = db.collection("world_boss_global").document("current_status")
        global_doc = global_ref.get()
        
        if not global_doc.exists:
            config = get_world_boss_config()
            correct_max_hp = config["initial_stats"]["max_hp"]  # ✅ 從配置檔讀取
            
            initial_state = {
                "current_hp": correct_max_hp,  # ✅ 使用正確血量
                "max_hp": correct_max_hp,      # ✅ 使用正確血量
                "current_phase": 1,
                "total_participants": 0,
                "total_damage_dealt": 0,
                "created_time": time.time(),
                "last_reset_time": time.time(),
                "weekly_reset_time": datetime.now(pytz.timezone('Asia/Taipei')).isoformat()
            }
            
            global_ref.set(initial_state)
            print(f"✅ 世界王全域狀態已初始化，血量：{correct_max_hp}")
            return initial_state
        else:
            return global_doc.to_dict()
            
    except Exception as e:
        print(f"❌ 初始化世界王狀態失敗: {e}")
        return None

def get_world_boss_global_state():
    """取得世界王全域狀態，包含額外的錯誤檢查"""
    try:
        global_ref = db.collection("world_boss_global").document("current_status")
        global_doc = global_ref.get()
        
        if global_doc.exists:
            state = global_doc.to_dict()
            
            # ✅ 檢查必要欄位
            required_fields = ["current_hp", "max_hp"]
            missing_fields = [f for f in required_fields if f not in state]
            
            if missing_fields:
                print(f"🚨 世界王資料異常！缺少欄位：{missing_fields}")
                print(f"📊 當前狀態：{state}")
                return None
            
            # ✅ 檢查數值合理性
            current_hp = state.get("current_hp", 0)
            max_hp = state.get("max_hp", 0)
            
            if not isinstance(current_hp, (int, float)) or not isinstance(max_hp, (int, float)):
                print(f"🚨 世界王血量數值類型異常: current_hp={type(current_hp)}, max_hp={type(max_hp)}")
                return None
            
            if max_hp <= 0:
                print(f"🚨 世界王最大血量異常: {max_hp}")
                return None
            
            if current_hp < 0:
                print(f"⚠️ 世界王當前血量小於0，自動修正為0: {current_hp}")
                state["current_hp"] = 0
                # 可選：自動修正到資料庫
                global_ref.update({"current_hp": 0})
            
            return state
        else:
            print("📝 世界王狀態文檔不存在，需要初始化")
            return initialize_world_boss_global_state()
            
    except Exception as e:
        print(f"❌ 取得世界王狀態失敗: {e}")
        import traceback
        traceback.print_exc()
        return None

def is_maintenance_time():
    """檢查是否為跨日維護時間 (23:30~00:30)"""
    taipei_tz = pytz.timezone('Asia/Taipei')
    now_taipei = datetime.now(taipei_tz)
    current_hour = now_taipei.hour
    current_minute = now_taipei.minute
    
    # 23:30~23:59 或 00:00~00:30
    if (current_hour == 23 and current_minute >= 30) or \
       (current_hour == 0 and current_minute <= 30):
        return True, "世界王跨日維護中 (23:30~00:30)，請稍後再來挑戰！"
    
    return False, ""

def is_weekend_restriction():
    """檢查是否為週日限制時間"""
    taipei_tz = pytz.timezone('Asia/Taipei')
    now_taipei = datetime.now(taipei_tz)
    
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
    """計算玩家對世界王的傷害（新增階段傷害增益）"""
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
        
        # 取得當前階段並應用玩家傷害增益
        current_phase = get_current_world_boss_phase()
        phase_config = world_boss_config["phases"][str(current_phase)]
        
        # 玩家傷害增益
        player_damage_multiplier = phase_config.get("player_damage_multiplier", 1.0)
        
        # 世界王防禦調整
        boss_defense_multiplier = phase_config.get("boss_defense_multiplier", 1.0)
        effective_boss_shield = boss_stats["shield"] * boss_defense_multiplier
        
        # 命中檢查
        player_accuracy = player_stats.get("accuracy", 0.8)
        boss_evade = boss_stats.get("evade", 0.1)
        player_luck = player_stats.get("luck", 10)
        
        hit_success = calculate_hit(player_accuracy, boss_evade, player_luck)
        
        if not hit_success:
            return 0, "攻擊未命中"
        
        # 攻擊速度影響計算
        player_speed = player_stats.get("atk_speed", 100)
        boss_speed = boss_stats.get("atk_speed", 100)
        speed_ratio = player_speed / boss_speed if boss_speed > 0 else 1.0
        speed_multiplier = max(0.1, min(3.0, speed_ratio))
        
        # 幸運暴擊計算
        crit_chance = min(player_luck * 0.0015, 0.50)
        
        import random
        is_critical = random.random() < crit_chance
        crit_multiplier = 2.0 if is_critical else 1.0
        
        # 計算基礎傷害
        player_attack = player_stats.get("attack", 20)
        other_bonus = player_stats.get("other_bonus", 0)

        # 加入護盾穿透
        player_penetrate = player_stats.get("penetrate", 0)
        
        # 屬性克制（玩家技能屬性 vs 世界王屬性）
        player_elements = ["none"]  # 預設為無屬性
        boss_elements = world_boss_config.get("element", ["all"])
        element_multiplier = get_element_multiplier(player_elements, boss_elements)
        
        # 等級差距修正
        level_multiplier = level_damage_modifier(player_level, boss_level)
        
        # 計算最終傷害時加入階段增益
        base_damage = calculate_damage(player_attack, 1.0, other_bonus, effective_boss_shield, player_penetrate)
        
        # 應用所有倍率：等級差距 × 屬性克制 × 攻擊速度 × 暴擊 × 階段增益
        final_damage = int(base_damage * 
                          level_multiplier * 
                          element_multiplier * 
                          speed_multiplier * 
                          crit_multiplier * 
                          player_damage_multiplier)  # 階段傷害增益
        
        # 確保最小傷害
        final_damage = max(final_damage, 1)
        
        # 生成詳細的戰鬥訊息（包含階段增益資訊）
        hit_message = "成功命中"
        damage_details = []

        # 護盾穿透說明
        if player_penetrate > 0:
            actual_shield_reduction = max(0, effective_boss_shield - player_penetrate)
            penetrate_reduction = effective_boss_shield - actual_shield_reduction
            if penetrate_reduction > 0:
                damage_details.append(f"【護盾穿透】減少護盾 {penetrate_reduction:.1f}")
        
        # 階段增益說明
        if player_damage_multiplier > 1.0:
            stage_name = f"第{current_phase}階段"
            bonus_percent = int((player_damage_multiplier - 1.0) * 100)
            damage_details.append(f"【{stage_name}增益】傷害提升 +{bonus_percent}%")
        
        
        # 速度影響說明
        if speed_multiplier > 1.2:
            damage_details.append(f"【高速攻擊】速度優勢 ×{speed_multiplier:.1f}")
        elif speed_multiplier < 0.8:
            damage_details.append(f"【速度劣勢】攻擊緩慢 ×{speed_multiplier:.1f}")
        
        # 暴擊說明
        if is_critical:
            damage_details.append(f"【暴擊】幸運爆發 ×{crit_multiplier:.1f}")
        
        # 組合詳細訊息
        if damage_details:
            hit_message = f"成功命中！{' '.join(damage_details)}"
        
        # 添加隨機性（±5%）
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
    """
    計算世界王全域統計更新資料（不直接更新資料庫）
    返回需要更新的資料，供批次操作使用

    """
    try:
        # 1. 獲取當前狀態並驗證
        global_state = get_world_boss_global_state()
        
        is_valid, error_code = validate_world_boss_global_state(global_state, "update_before")
        if not is_valid:
            print(f"❌ 世界王狀態更新中止: {error_code}")
            print(f"📊 異常狀態資料: {global_state}")
            
            # 🛡️ 重要：不執行任何資料庫更新，只記錄錯誤
            return {
                "success": False,
                "error": "world_boss_data_invalid",
                "error_code": error_code,
                "current_state": global_state,
                "damage_attempted": damage_dealt,
                "timestamp": time.time()
            }
        
        # 2. 計算新的狀態
        current_hp = global_state.get("current_hp", 0)
        max_hp = global_state.get("max_hp", 0)
        
        new_hp = max(0, current_hp - damage_dealt)
        new_total_damage = global_state.get("total_damage_dealt", 0) + damage_dealt
        new_total_participants = global_state.get("total_participants", 0) + 1
        
        # 3. 構造更新資料（使用原有的max_hp，不使用預設值）
        updated_state = {
            "current_hp": new_hp,
            "max_hp": max_hp,  # 🔥 關鍵修復：直接使用驗證過的值，不使用預設值
            "current_phase": get_current_world_boss_phase(),
            "total_damage_dealt": new_total_damage,
            "total_participants": new_total_participants,
            "last_update_time": time.time()
        }
        
        # 4. 驗證更新後的狀態
        is_valid_after, error_code_after = validate_world_boss_global_state(updated_state, "update_after")
        if not is_valid_after:
            print(f"❌ 更新後狀態異常: {error_code_after}")
            print(f"📊 原狀態: {global_state}")
            print(f"📊 新狀態: {updated_state}")
            
            # 🛡️ 重要：不執行資料庫更新
            return {
                "success": False,
                "error": "updated_state_invalid",
                "error_code": error_code_after,
                "original_state": global_state,
                "attempted_update": updated_state,
                "timestamp": time.time()
            }

        return {
            "success": True,
            "updates": updated_state,
            "previous_state": global_state,
            "damage_dealt": damage_dealt,
            "hp_change": current_hp - new_hp
        }
        
    except Exception as e:
        print(f"❌ 世界王狀態更新發生異常: {str(e)}")
        import traceback
        traceback.print_exc()
        
        # 🛡️ 任何異常都不執行資料庫操作
        return {
            "success": False,
            "error": "exception_during_update",
            "exception": str(e),
            "timestamp": time.time()
        }

# 🌍 世界王 API 端點

@app.route("/world_boss_status", methods=["GET"])
def world_boss_status():
    """取得世界王狀態 - 完整版本，包含死亡狀態和詳細資訊"""
    try:
        # ✅ 檢查週重置
        check_weekly_reset()
        
        # ✅ 維護時間檢查
        is_maintenance, maintenance_msg = is_maintenance_time()
        
        # 載入世界王配置
        config = get_world_boss_config()
        global_state = get_world_boss_global_state()
        
        if not global_state:
            print("❌ 無法取得世界王全域狀態")
            return jsonify({
                "error": "無法取得世界王狀態",
                "error_code": "NO_GLOBAL_STATE",
                "server_time": time.time()
            }), 500
        
        # 取得基本數據
        current_hp = global_state.get("current_hp", config["initial_stats"]["max_hp"])
        max_hp = global_state.get("max_hp", config["initial_stats"]["max_hp"])
        
        # 🚀 檢查世界王是否已死亡
        boss_defeated = current_hp <= 0
        defeated_info = {}
        
        if boss_defeated:
            # 世界王已死亡，準備詳細資訊
            defeated_info = {
                "defeated": True,
                "defeated_time": global_state.get("defeated_time", 0),
                "final_blow_by": global_state.get("final_blow_by", ""),
                "final_blow_nickname": global_state.get("final_blow_nickname", "未知英雄"),
                "reset_message": "世界王將於下週一 00:31 重新復活",
                "status_message": "🎉 世界王已被全體冒險者擊敗！",
                "next_reset_info": "下週一自動重置",
                "challenge_disabled": True
            }
            
            # 🚀 記錄世界王死亡日誌
            print(f"💀 API回應：世界王已死亡 (HP: {current_hp}/{max_hp})")
            if defeated_info["final_blow_nickname"] != "未知英雄":
                print(f"👑 最後一擊由 {defeated_info['final_blow_nickname']} 完成")
        else:
            # 世界王還活著
            print(f"✅ 世界王狀態正常 (HP: {current_hp}/{max_hp})")
        
        # 計算總攻擊次數和獨特玩家數
        total_attacks = global_state.get("total_participants", 0)
        
        try:
            # 計算獨特玩家數量（有造成傷害的玩家）
            players_ref = db.collection("world_boss_players").where("total_damage", ">", 0)
            unique_players_count = len([doc for doc in players_ref.stream()])
        except Exception as player_error:
            print(f"⚠️ 計算獨特玩家數量失敗: {player_error}")
            unique_players_count = 0
        
        # 🚀 計算當前階段
        current_phase = get_current_world_boss_phase()
        
        # 計算血量百分比
        hp_percentage = (current_hp / max_hp * 100) if max_hp > 0 else 0
        
        # 🚀 準備完整的回應數據
        result = {
            # 基本世界王資訊
            "boss_id": config["boss_id"],
            "name": config["name"],
            "description": config["description"],
            "image": config["image"],
            "level": config["level"],
            "element": config["element"],
            
            # 血量和階段資訊
            "current_hp": current_hp,
            "max_hp": max_hp,
            "hp_percentage": round(hp_percentage, 2),
            "current_phase": current_phase,
            
            # 統計資訊
            "total_participants": total_attacks,
            "unique_players": unique_players_count,
            "total_damage_dealt": global_state.get("total_damage_dealt", 0),
            
            # 階段配置
            "phases": config["phases"],
            
            # 時間相關資訊
            "last_update_time": global_state.get("last_update_time", global_state.get("created_time", time.time())),
            "server_time": time.time(),
            "created_time": global_state.get("created_time", time.time()),
            
            # 維護狀態
            "is_maintenance": is_maintenance,
            "maintenance_message": maintenance_msg if is_maintenance else None,
            
            # 🚀 世界王死亡狀態（核心新增功能）
            "boss_defeated": boss_defeated,
            "defeated_info": defeated_info,
            
            # 時間限制檢查
            "is_weekend": is_weekend_restriction()[0],
            "weekend_message": is_weekend_restriction()[1] if is_weekend_restriction()[0] else None,
            
            # API 狀態
            "api_status": "normal",
            "response_generated_at": time.time()
        }
        
        # 🚀 如果世界王已死亡，添加額外的統計資訊
        if boss_defeated and defeated_info.get("defeated_time", 0) > 0:
            defeated_time = defeated_info["defeated_time"]
            current_time = time.time()
            time_since_defeat = current_time - defeated_time
            
            result["defeated_info"].update({
                "time_since_defeat_seconds": int(time_since_defeat),
                "time_since_defeat_hours": round(time_since_defeat / 3600, 1),
                "defeated_timestamp": defeated_time
            })
        
        # 🚀 添加下次重置時間計算
        try:
            taipei_tz = pytz.timezone('Asia/Taipei')
            now_taipei = datetime.now(taipei_tz)
            
            # 計算下週一 00:31 的時間
            days_until_monday = (7 - now_taipei.weekday()) % 7
            if days_until_monday == 0 and now_taipei.hour >= 1:  # 如果是週一且已過01:00
                days_until_monday = 7
            
            next_reset = now_taipei.replace(hour=0, minute=31, second=0, microsecond=0) + timedelta(days=days_until_monday)
    
            result["next_reset_time"] = next_reset.isoformat()
            result["next_reset_timestamp"] = next_reset.timestamp()
            
        except Exception as time_error:
            print(f"⚠️ 計算下次重置時間失敗: {time_error}")
        
        # 🚀 記錄成功的API調用
        if boss_defeated:
            print(f"📤 世界王狀態API (已死亡): HP=0/{max_hp}, 攻擊次數={total_attacks}, 玩家數={unique_players_count}")
        else:
            print(f"📤 世界王狀態API (存活): HP={current_hp}/{max_hp} ({hp_percentage:.1f}%), 階段={current_phase}")
        
        return jsonify(result)
        
    except Exception as e:
        # 🚀 強化錯誤處理和日誌記錄
        print(f"❌ 取得世界王狀態時發生錯誤: {str(e)}")
        import traceback
        traceback.print_exc()
        
        # 🚀 嘗試提供基本的降級回應
        try:
            config = get_world_boss_config()
            fallback_response = {
                "error": f"取得世界王狀態失敗: {str(e)}",
                "error_code": "INTERNAL_ERROR",
                "api_status": "error",
                "server_time": time.time(),
                "fallback_data": {
                    "boss_id": config.get("boss_id", "unknown"),
                    "name": config.get("name", "世界王"),
                    "max_hp": config.get("initial_stats", {}).get("max_hp", 999999999),
                    "current_hp": 0,  # 安全的預設值
                    "boss_defeated": False,  # 保守的預設值
                    "maintenance_mode": True  # 錯誤時視為維護模式
                }
            }
            return jsonify(fallback_response), 500
            
        except Exception as fallback_error:
            # 連降級回應都失敗了，返回最基本的錯誤
            print(f"❌ 連降級回應都失敗: {fallback_error}")
            return jsonify({
                "error": "伺服器內部錯誤",
                "error_code": "CRITICAL_ERROR",
                "api_status": "critical",
                "server_time": time.time(),
                "message": "世界王系統暫時無法使用，請稍後再試"
            }), 500

def validate_world_boss_global_state(state, context=""):
    """
    驗證世界王全域狀態資料的完整性和合理性
    
    Args:
        state: 世界王狀態字典
        context: 驗證的上下文（用於日誌記錄）
    
    Returns:
        tuple: (is_valid, error_code)
    """
    try:
        if not state or not isinstance(state, dict):
            return False, "STATE_NOT_DICT"
        
        # 檢查必要欄位
        required_fields = ["current_hp", "max_hp"]
        for field in required_fields:
            if field not in state:
                return False, f"MISSING_FIELD_{field.upper()}"
        
        current_hp = state.get("current_hp")
        max_hp = state.get("max_hp")
        
        # 檢查數值類型
        if not isinstance(current_hp, (int, float)):
            return False, "INVALID_CURRENT_HP_TYPE"
        
        if not isinstance(max_hp, (int, float)):
            return False, "INVALID_MAX_HP_TYPE"
        
        # 檢查數值合理性
        if max_hp <= 0:
            return False, "INVALID_MAX_HP_VALUE"
        
        if max_hp > 500000:  # 世界王血量不應該超過50萬
            return False, "MAX_HP_TOO_HIGH"
        
        if current_hp < 0:
            return False, "NEGATIVE_CURRENT_HP"
        
        if current_hp > max_hp:
            return False, "CURRENT_HP_EXCEEDS_MAX"
        
        # 檢查階段合理性
        current_phase = state.get("current_phase", 1)
        if not isinstance(current_phase, int) or current_phase < 1 or current_phase > 3:
            return False, "INVALID_PHASE"
        
        # 檢查統計數據合理性
        total_participants = state.get("total_participants", 0)
        total_damage = state.get("total_damage_dealt", 0)
        
        if not isinstance(total_participants, (int, float)) or total_participants < 0:
            return False, "INVALID_PARTICIPANTS"
        
        if not isinstance(total_damage, (int, float)) or total_damage < 0:
            return False, "INVALID_TOTAL_DAMAGE"
        
        # 所有檢查都通過
        return True, "VALID"
        
    except Exception as e:
        print(f"❌ 驗證世界王狀態時發生異常 ({context}): {e}")
        return False, f"VALIDATION_EXCEPTION_{str(e)[:20]}"

@app.route("/world_boss_challenge", methods=["POST"])
@require_auth
def world_boss_challenge():
    """挑戰世界王 - 修正版本，包含死亡檢查和安全資料驗證"""
    try:
        # 檢查週日重置
        check_weekly_reset()
        
        user_id = request.user_id
        
        # 🚀 新增：檢查世界王是否已死亡
        global_state = get_world_boss_global_state()
        if not global_state:
            return jsonify({"error": "無法取得世界王狀態"}), 500
        
        # 🛡️ 新增：驗證世界王資料完整性
        is_valid, error_code = validate_world_boss_global_state(global_state, "challenge_start")
        if not is_valid:
            print(f"❌ 世界王挑戰中止：資料異常 {error_code}")
            print(f"📊 異常資料: {global_state}")
            
            return jsonify({
                "error": "世界王資料異常，挑戰暫時無法進行",
                "error_code": error_code,
                "message": "系統檢測到世界王資料異常，請稍後再試或聯繫管理員"
            }), 500
        
        current_world_boss_hp = global_state.get("current_hp", 0)
        if current_world_boss_hp <= 0:
            return jsonify({
                "error": "世界王已被擊敗！",
                "boss_defeated": True,
                "message": "🎉 恭喜全世界的冒險者成功擊敗世界王！\n👑 世界王將於下週一 00:31 復活並重置挑戰\n🏆 感謝你參與這場史詩級的戰鬥！",
                "reset_info": "下週一 00:31 自動重置"
            }), 403
        
        # 檢查跨日維護時間
        is_maintenance, maintenance_msg = is_maintenance_time()
        if is_maintenance:
            return jsonify({"error": maintenance_msg}), 403
        
        # 檢查週日限制
        is_restricted, restriction_msg = is_weekend_restriction()
        if is_restricted:
            return jsonify({"error": restriction_msg}), 403

        # 檢查等級限制
        user_doc = db.collection("users").document(user_id).get()
        if not user_doc.exists:
            return jsonify({"error": "找不到使用者"}), 404
        
        user_data = user_doc.to_dict()
        user_level = user_data.get("level", 1)
        
        # 等級限制：需要30等以上
        REQUIRED_LEVEL = 30
        if user_level < REQUIRED_LEVEL:
            return jsonify({
                "error": f"等級不足！需要達到 {REQUIRED_LEVEL} 等才能挑戰世界王",
                "required_level": REQUIRED_LEVEL,
                "current_level": user_level,
                "level_shortage": REQUIRED_LEVEL - user_level
            }), 403
        
        # 檢查冷卻時間
        can_challenge, remaining_cooldown, cooldown_end_time = check_world_boss_cooldown(user_id)
        if not can_challenge:
            return jsonify({
                "error": f"挑戰冷卻中，請等待 {int(remaining_cooldown/60)} 分鐘",
                "cooldown_remaining": remaining_cooldown,
                "cooldown_end_time": cooldown_end_time
            }), 400
        
        # 載入世界王配置
        config = get_world_boss_config()
        
        # 計算傷害
        damage_dealt, hit_message = calculate_world_boss_damage(user_data, config)
        
        # 🚀 重要修正：在更新血量前再次檢查當前狀態
        # 這是為了防止併發請求導致的競態條件
        fresh_global_state = get_world_boss_global_state()
        if not fresh_global_state:
            return jsonify({"error": "無法取得最新世界王狀態"}), 500
        
        # 🛡️ 新增：再次驗證最新資料
        is_valid_fresh, error_code_fresh = validate_world_boss_global_state(fresh_global_state, "challenge_fresh_check")
        if not is_valid_fresh:
            print(f"❌ 世界王挑戰中止：最新資料異常 {error_code_fresh}")
            print(f"📊 異常資料: {fresh_global_state}")
            
            return jsonify({
                "error": "世界王最新資料異常，挑戰暫時無法進行",
                "error_code": error_code_fresh,
                "message": "系統檢測到世界王最新資料異常，請稍後再試"
            }), 500
            
        fresh_current_hp = fresh_global_state.get("current_hp", 0)
        if fresh_current_hp <= 0:
            return jsonify({
                "error": "世界王已在你攻擊前被其他冒險者擊敗！",
                "boss_defeated": True,
                "message": "⚔️ 雖然你沒能給予最後一擊，但你仍是擊敗世界王的英雄之一！",
                "participation_acknowledged": True
            }), 403

        # 🛡️ 安全計算新狀態（不使用預設值）
        challenge_time = time.time()
        current_hp = fresh_global_state.get("current_hp", 0)
        max_hp = fresh_global_state.get("max_hp", 0)  # 🔥 直接使用驗證過的值，不給預設值
        
        # 🛡️ 最後檢查：確保 max_hp 是合理的
        if max_hp <= 0 or max_hp > 500000:
            print(f"🚨 檢測到異常最大血量: {max_hp}")
            return jsonify({
                "error": "世界王血量資料異常",
                "message": "系統檢測到世界王血量異常，請聯繫管理員",
                "debug_max_hp": max_hp
            }), 500
        
        new_hp = max(0, current_hp - damage_dealt)
        new_total_damage = fresh_global_state.get("total_damage_dealt", 0) + damage_dealt
        new_total_participants = fresh_global_state.get("total_participants", 0) + 1
        
        # 🚀 新增：標記世界王是否在這次攻擊後死亡
        boss_defeated_this_attack = (current_hp > 0 and new_hp <= 0)
        
        # 🛡️ 構造安全的更新資料
        global_updates = {
            "current_hp": new_hp,
            "max_hp": max_hp,  # 🔥 關鍵修復：直接使用驗證過的值
            "current_phase": get_current_world_boss_phase(),
            "total_damage_dealt": new_total_damage,
            "total_participants": new_total_participants,
            "last_update_time": challenge_time
        }
        
        # 🚀 如果世界王在這次攻擊後死亡，記錄擊殺時間和擊殺者
        if boss_defeated_this_attack:
            global_updates["defeated_time"] = challenge_time
            global_updates["final_blow_by"] = user_id
            global_updates["final_blow_nickname"] = user_data.get("nickname", user_id)
            global_updates["boss_defeated"] = True
        
        # 🛡️ 最後驗證：檢查即將寫入的資料是否合理
        is_valid_update, error_code_update = validate_world_boss_global_state(global_updates, "before_database_write")
        if not is_valid_update:
            print(f"❌ 即將寫入的資料異常，中止操作: {error_code_update}")
            print(f"📊 原始資料: {fresh_global_state}")
            print(f"📊 計算後資料: {global_updates}")
            
            return jsonify({
                "error": "計算後的世界王資料異常，為了資料安全已中止操作",
                "error_code": error_code_update,
                "message": "系統檢測到計算結果異常，請聯繫管理員"
            }), 500

        # 使用批次操作確保原子性
        batch = db.batch()
        new_cooldown_end_time = int((challenge_time + 3600) * 1000)
        
        # 1. 更新世界王全域狀態
        global_ref = db.collection("world_boss_global").document("current_status")
        batch.update(global_ref, global_updates)
        
        # 2. 準備玩家世界王資料更新
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
        
        # 更新玩家資料
        player_data["total_damage"] = player_data.get("total_damage", 0) + damage_dealt
        player_data["challenge_count"] = player_data.get("challenge_count", 0) + 1
        player_data["last_challenge_time"] = challenge_time
        player_data["nickname"] = user_data.get("nickname", user_id)
        
        # 🚀 如果玩家擊殺了世界王，標記榮譽
        if boss_defeated_this_attack:
            player_data["delivered_final_blow"] = True
            player_data["final_blow_time"] = challenge_time
        
        batch.set(player_ref, player_data)
        
        # 3. 準備經驗值更新
        exp_gained, damage_percentage, reward_tier, tier_desc = calculate_world_boss_exp_reward(damage_dealt, config)
        
        # 🚀 如果玩家擊殺了世界王，給予額外獎勵
        if boss_defeated_this_attack:
            exp_gained += 2000  # 擊殺獎勵
            reward_tier = "擊殺獎勵"
            tier_desc = "給予世界王最後一擊！"
        
        new_exp = user_data.get("exp", 0) + exp_gained
        
        user_ref = db.collection("users").document(user_id)
        batch.update(user_ref, {"exp": new_exp})
        
        # 4. 準備道具掉落
        dropped_items = {}
        
        try:
            # 取得現有道具數量
            item_doc = db.collection("user_items").document(user_id).get()
            current_items = item_doc.to_dict().get("items", {}) if item_doc.exists else {}
            
            # 🎲 計算每個掉落物品
            import random
            for drop in config["rewards"]["drops"]:
                drop_rate = drop["rate"]
                
                # 🚀 如果擊殺了世界王，掉落率提升
                if boss_defeated_this_attack:
                    drop_rate = min(1.0, drop_rate * 2.0)  # 擊殺掉落率翻倍，但不超過100%
                
                if random.random() <= drop_rate:
                    item_id = drop["id"]
                    item_value = drop["value"]
                    
                    # 🚀 擊殺額外獎勵
                    if boss_defeated_this_attack:
                        item_value = int(item_value * 1.5)  # 擊殺獎勵增加50%
                    
                    dropped_items[item_id] = dropped_items.get(item_id, 0) + item_value
                    current_items[item_id] = current_items.get(item_id, 0) + item_value
            
            # 🚀 擊殺世界王的特殊獎勵
            if boss_defeated_this_attack:
                # 保證掉落創世精髓
                special_drop_id = "world_boss_token"
                special_drop_amount = 3  # 擊殺者額外獲得
                dropped_items[special_drop_id] = dropped_items.get(special_drop_id, 0) + special_drop_amount
                current_items[special_drop_id] = current_items.get(special_drop_id, 0) + special_drop_amount
            
            # 如果有道具掉落，加入批次操作
            if dropped_items:
                item_ref = db.collection("user_items").document(user_id)
                batch.set(item_ref, {"items": current_items}, merge=True)
            
        except Exception as drop_error:
            print(f"⚠️ 世界王道具掉落處理失敗: {drop_error}")
        
        # 原子性提交所有操作
        try:
            batch.commit()
            print(f"🌍 世界王挑戰批次操作成功 - 使用者: {user_id}")
            print(f"📊 血量變化: {current_hp} -> {new_hp} (最大: {max_hp})")
            
            # 🚀 如果擊殺了世界王，記錄到日誌
            if boss_defeated_this_attack:
                print(f"👑 世界王被擊敗！最後一擊由 {user_data.get('nickname', user_id)} 完成")
                
        except Exception as batch_error:
            print(f"❌ 批次操作失敗: {batch_error}")
            return jsonify({
                "success": False, 
                "error": "資料儲存失敗，請稍後再試"
            }), 500
        
        # 計算排名（在成功提交後）
        all_players = db.collection("world_boss_players").order_by("total_damage", direction=firestore.Query.DESCENDING).stream()
        rank = 1
        for i, doc in enumerate(all_players):
            if doc.id == user_id:
                rank = i + 1
                break
        
        # 🚀 準備回應訊息
        success_message = hit_message
        
        if boss_defeated_this_attack:
            success_message = f"🎉 恭喜！你給予了世界王最後一擊！\n👑 世界王已被全體冒險者擊敗\n⚔️ {hit_message}"
            reward_tier = "👑 世界王終結者"
            tier_desc = "給予最後一擊的傳奇英雄！"
        
        # 🎯 成功回應
        result = {
            "success": True,
            "damage_dealt": damage_dealt,
            "hit_message": success_message,
            "total_damage": player_data["total_damage"],
            "current_rank": rank,
            "exp_gained": exp_gained,
            "damage_percentage": round(damage_percentage, 4),
            "reward_tier": reward_tier,
            "tier_description": tier_desc,
            "boss_defeated": boss_defeated_this_attack,  # 🚀 新增：世界王是否被擊敗
            "final_blow": boss_defeated_this_attack,     # 🚀 新增：是否為最後一擊
            "rewards": {
                "items": dropped_items,
                "bonus_for_final_blow": boss_defeated_this_attack
            },
            "cooldown_end_time": new_cooldown_end_time,
            "world_boss_hp": {
                "current": new_hp,
                "max": max_hp  # 🔥 使用驗證過的 max_hp
            }
        }
        
        return jsonify(result)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"🔥 世界王挑戰完全失敗: {str(e)}")
        return jsonify({"success": False, "error": f"挑戰失敗: {str(e)}"}), 500

# 世界王死亡狀態檢查端點
@app.route("/world_boss_death_status", methods=["GET"])
def world_boss_death_status():
    """專門檢查世界王是否已死亡的輕量級端點"""
    try:
        global_state = get_world_boss_global_state()
        if not global_state:
            return jsonify({
                "error": "無法取得世界王狀態",
                "boss_defeated": False,
                "status": "unknown"
            }), 500
        
        current_hp = global_state.get("current_hp", 0)
        max_hp = global_state.get("max_hp", 1)
        boss_defeated = current_hp <= 0
        
        result = {
            "boss_defeated": boss_defeated,
            "current_hp": current_hp,
            "max_hp": max_hp,
            "hp_percentage": (current_hp / max_hp * 100) if max_hp > 0 else 0,
            "status": "defeated" if boss_defeated else "alive",
            "check_time": time.time()
        }
        
        if boss_defeated:
            result.update({
                "defeated_time": global_state.get("defeated_time", 0),
                "final_blow_by": global_state.get("final_blow_nickname", "未知英雄"),
                "reset_message": "世界王將於下週一 00:31 重新復活"
            })
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({
            "error": f"檢查世界王死亡狀態失敗: {str(e)}",
            "boss_defeated": False,
            "status": "error"
        }), 500

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
@require_admin  # 🚀 改用管理員權限裝飾器
def world_boss_reset():
    """重置世界王（管理員功能）"""
    try:
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
            "weekly_reset_time": datetime.now(pytz.timezone('Asia/Taipei')).isoformat(),
            "created_time": time.time(),
            "reset_by": request.user_id  # 🚀 記錄重置者
        }
        global_ref.set(reset_data)
        
        # 可選：清除玩家數據
        clear_leaderboard = request.json.get("clear_leaderboard", False) if request.json else False
        if clear_leaderboard:
            try:
                players_ref = db.collection("world_boss_players")
                batch = db.batch()
                docs_deleted = 0
                for doc in players_ref.stream():
                    batch.delete(doc.reference)
                    docs_deleted += 1
                    if docs_deleted >= 500:
                        batch.commit()
                        batch = db.batch()
                        docs_deleted = 0
                
                if docs_deleted > 0:
                    batch.commit()
                    
                print(f"管理員 {request.user_id} 清除了所有玩家世界王數據")
            except Exception as e:
                print(f"清除玩家數據時發生錯誤: {e}")
        
        print(f"🔄 管理員 {request.user_id} 重置了世界王")
        
        return jsonify({
            "message": "世界王已重置", 
            "reset_time": reset_data["weekly_reset_time"],
            "new_hp": reset_data["current_hp"],
            "total_attacks_reset": True,
            "leaderboard_cleared": clear_leaderboard,
            "reset_by": request.user_id
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
        if damage_percentage >= 0.1:
            exp_gained = 1000
            reward_tier = "S級傷害"
            tier_desc = "造成0.1%以上傷害"
        elif damage_percentage >= 0.05:
            exp_gained = 500
            reward_tier = "A級傷害"
            tier_desc = "造成0.05%~0.1%傷害"
        elif damage_percentage >= 0.01:
            exp_gained = 300
            reward_tier = "B級傷害"
            tier_desc = "造成0.01%~0.05%傷害"
        else:
            exp_gained = 100
            reward_tier = "C級傷害"
            tier_desc = "造成0.01%以下傷害"
        
        return exp_gained, damage_percentage, reward_tier, tier_desc
        
    except Exception as e:
        print(f"計算經驗值獎勵失敗: {e}")
        # 發生錯誤時給予最低獎勵
        return 20, 0.0, "計算錯誤", "系統錯誤，給予基礎獎勵"

@app.route("/world_boss_leaderboard", methods=["GET"])
def world_boss_leaderboard():
    """取得世界王排行榜"""
    try:
        # 🎯 設定顯示人數上限，前端會進一步篩選為前10名
        limit = request.args.get("limit", 50, type=int)  # 預設50名，給前端更多選擇空間
        limit = min(limit, 100)  # 最多不超過100名避免性能問題
        
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
        
        # 🎯 計算總參與者數量（用於顯示統計）
        total_participants_ref = db.collection("world_boss_players").where("total_damage", ">", 0)
        total_count = len([doc for doc in total_participants_ref.stream()])
        
        return jsonify({
            "leaderboard": leaderboard,
            "total_players": total_count,
            "limit": limit,
            "returned_count": len(leaderboard)
        })
        
    except Exception as e:
        return jsonify({"error": f"取得排行榜失敗: {str(e)}"}), 500

@app.route("/world_boss_player_rank", methods=["GET"])
@require_auth
def world_boss_player_rank():
    """取得玩家在世界王排行榜中的排名（獨立計算，不受顯示限制影響）"""
    try:
        user_id = request.user_id
        
        # 取得玩家資料
        player_ref = db.collection("world_boss_players").document(user_id)
        player_doc = player_ref.get()
        
        if not player_doc.exists:
            return jsonify({
                "rank": 0,
                "total_damage": 0,
                "challenge_count": 0,
                "message": "尚未參與世界王挑戰"
            })
        
        player_data = player_doc.to_dict()
        player_damage = player_data.get("total_damage", 0)
        
        if player_damage <= 0:
            return jsonify({
                "rank": 0,
                "total_damage": 0,
                "challenge_count": player_data.get("challenge_count", 0),
                "message": "尚未造成傷害"
            })
        
        # 🎯 計算真實排名：統計傷害比該玩家高的玩家數量
        higher_damage_players = db.collection("world_boss_players").where("total_damage", ">", player_damage)
        rank = len([doc for doc in higher_damage_players.stream()]) + 1
        
        return jsonify({
            "rank": rank,
            "total_damage": player_damage,
            "challenge_count": player_data.get("challenge_count", 0),
            "last_challenge_time": player_data.get("last_challenge_time", 0),
            "nickname": player_data.get("nickname", user_id)
        })
        
    except Exception as e:
        return jsonify({"error": f"取得玩家排名失敗: {str(e)}"}), 500

# 🚀 新增：獲取使用者管理員狀態的 API
@app.route("/admin_status", methods=["GET"])
@require_auth
def admin_status():
    """檢查當前使用者是否為管理員"""
    try:
        user_id = request.user_id
        
        user_doc = db.collection("users").document(user_id).get()
        if not user_doc.exists:
            return jsonify({"error": "使用者不存在"}), 404
        
        user_data = user_doc.to_dict()
        is_admin = user_data.get('admin', False)
        
        return jsonify({
            "is_admin": is_admin,
            "user_id": user_id,
            "nickname": user_data.get("nickname", user_id),
            "level": user_data.get("level", 1)
        })
        
    except Exception as e:
        return jsonify({"error": f"檢查管理員狀態失敗: {str(e)}"}), 500
# 🚀 新增：管理員限定的快取清除 API
@app.route("/admin_clear_cache", methods=["POST"])
@require_admin
def admin_clear_cache():
    """管理員專用的完全快取清除"""
    try:
        # 清除LRU快取
        get_dungeon_data.cache_clear()
        get_element_table.cache_clear()
        get_level_exp.cache_clear()
        get_all_skill_data.cache_clear()
        get_item_map.cache_clear()
        get_items_data.cache_clear()
        get_equips_data.cache_clear()
        get_world_boss_config.cache_clear()
        
        # 清除記憶體快取
        cache_manager.clear()
        
        print(f"🧹 管理員 {request.user_id} 清除了所有快取")
        
        return jsonify({
            "message": "所有快取已清除",
            "cleared_by": request.user_id,
            "timestamp": time.time()
        }), 200
        
    except Exception as e:
        return jsonify({"error": f"清除失敗: {str(e)}"}), 500

# 🚀 新增：管理員限定的系統狀態查詢
@app.route("/admin_system_status", methods=["GET"])
@require_admin
def admin_system_status():
    """管理員專用的系統狀態查詢"""
    try:
        # 取得快取統計
        cache_stats = cache_manager.get_stats()
        
        # 取得世界王狀態
        world_boss_state = get_world_boss_global_state()
        
        # 取得玩家總數
        try:
            users_count = len([doc for doc in db.collection("users").stream()])
        except:
            users_count = "無法計算"
        
        # 取得世界王參與者數量
        try:
            wb_players_count = len([doc for doc in db.collection("world_boss_players").stream()])
        except:
            wb_players_count = "無法計算"
        
        return jsonify({
            "cache_stats": cache_stats,
            "world_boss": {
                "current_hp": world_boss_state.get("current_hp", 0) if world_boss_state else 0,
                "max_hp": world_boss_state.get("max_hp", 0) if world_boss_state else 0,
                "phase": world_boss_state.get("current_phase", 1) if world_boss_state else 1,
                "total_attacks": world_boss_state.get("total_participants", 0) if world_boss_state else 0
            },
            "player_statistics": {
                "total_users": users_count,
                "world_boss_participants": wb_players_count
            },
            "server_info": {
                "timestamp": time.time(),
                "timezone": "Asia/Taipei"
            },
            "queried_by": request.user_id
        })
        
    except Exception as e:
        return jsonify({"error": f"取得系統狀態失敗: {str(e)}"}), 500

# 世界王週日重置
def check_weekly_reset():
    """檢查是否需要進行週一重置"""
    try:
        taipei_tz = pytz.timezone('Asia/Taipei')
        now_taipei = datetime.now(taipei_tz)
        
        # 只在週一 01:30~02:00 之間進行重置
        if now_taipei.weekday() == 0 and 1 <= now_taipei.hour <= 2:
            global_ref = db.collection("world_boss_global").document("current_status")
            global_doc = global_ref.get()
            
            if global_doc.exists:
                state = global_doc.to_dict()
                last_reset_time = state.get("weekly_reset_time", "")
                
                # 檢查是否本週已經重置過
                if last_reset_time:
                    try:
                        last_reset = datetime.fromisoformat(last_reset_time.replace('Z', '+00:00'))
                        last_reset_taipei = last_reset.astimezone(taipei_tz)
                        
                        # 如果上次重置是上週，則執行重置
                        if last_reset_taipei.isocalendar()[1] < now_taipei.isocalendar()[1] or \
                           last_reset_taipei.year < now_taipei.year:
                            perform_weekly_reset(now_taipei)
                            return True
                    except:
                        # 如果解析失敗，執行重置
                        perform_weekly_reset(now_taipei)
                        return True
                else:
                    # 如果沒有重置記錄，執行重置
                    perform_weekly_reset(now_taipei)
                    return True
        
        return False
        
    except Exception as e:
        print(f"檢查週重置失敗: {e}")
        return False

def perform_weekly_reset(reset_time):
    """執行週重置"""
    try:
        config = get_world_boss_config()
        
        # 使用批次操作確保原子性
        batch = db.batch()
        
        # 1. 重置全域狀態
        global_ref = db.collection("world_boss_global").document("current_status")
        reset_data = {
            "current_hp": config["initial_stats"]["max_hp"],
            "max_hp": config["initial_stats"]["max_hp"],
            "current_phase": 1,
            "total_participants": 0,
            "total_damage_dealt": 0,
            "last_reset_time": time.time(),
            "weekly_reset_time": reset_time.isoformat(),
            "auto_reset": True,  # 標記為自動重置
            "unique_players": 0
        }
        batch.set(global_ref, reset_data)
        
        # 2. 清空排行榜
        players_ref = db.collection("world_boss_players")
        for doc in players_ref.stream():
            batch.delete(doc.reference)
        
        # 提交批次操作
        batch.commit()
        
        print(f"🔄 週重置成功執行於：{reset_time.isoformat()}")
        return True
        
    except Exception as e:
        print(f"❌ 週重置執行失敗: {e}")
        return False


# 商店
@lru_cache(maxsize=128)
def get_shop_items():
    """載入商店道具配置"""
    with open("parameter/shop_items.json", encoding="utf-8") as f:
        return json.load(f)

def get_current_reset_periods():
    """取得當前的重置週期字串"""
    taipei_tz = pytz.timezone('Asia/Taipei')
    now_taipei = datetime.now(taipei_tz)
    
    # 週重置：以週一為起始的週數 (ISO week)
    year = now_taipei.year
    year, week_num, _ = now_taipei.isocalendar()
    weekly_period = f"{year}-W{week_num:02d}"
    
    # 月重置：年-月
    monthly_period = f"{year}-{now_taipei.month:02d}"
    
    # 日重置：年-月-日
    daily_period = f"{year}-{now_taipei.month:02d}-{now_taipei.day:02d}"
    
    return {
        'weekly': weekly_period,
        'monthly': monthly_period, 
        'daily': daily_period
    }

def validate_shop_purchase(user_id, item_id, user_items, user_purchases, user_level=None):
    """驗證商店購買請求 - 包含自動重置檢查"""
    try:
        # 🔄 在驗證前先模擬重置檢查（不修改資料，只用於驗證）
        current_periods = get_current_reset_periods()
        temp_purchases = user_purchases.get("purchases", {}).copy()
        
        # 如果目標商品有過期的計數，臨時重置來進行驗證
        if item_id in temp_purchases:
            item_data = temp_purchases[item_id]
            
            # 檢查每日重置
            current_daily = current_periods.get('daily')
            last_daily = item_data.get('last_daily_period', '')
            if current_daily != last_daily:
                print(f"🔍 驗證時檢測到每日重置: {item_id}")
                item_data['daily_purchased'] = 0
            
            # 檢查每週重置
            current_weekly = current_periods.get('weekly') 
            last_weekly = item_data.get('last_weekly_period', '')
            if current_weekly != last_weekly:
                print(f"🔍 驗證時檢測到每週重置: {item_id}")
                item_data['weekly_purchased'] = 0
            
            # 檢查每月重置
            current_monthly = current_periods.get('monthly')
            last_monthly = item_data.get('last_monthly_period', '')
            if current_monthly != last_monthly:
                print(f"🔍 驗證時檢測到每月重置: {item_id}")
                item_data['monthly_purchased'] = 0
        
        # 使用臨時重置後的資料進行驗證
        temp_user_purchases = {"purchases": temp_purchases}
        
        # 原有的驗證邏輯，但使用 temp_user_purchases
        shop_items = get_shop_items()
        shop_item = next((item for item in shop_items if item["id"] == item_id), None)
        
        if not shop_item:
            return False, "商品不存在"
        
        if not shop_item.get("available", True):
            return False, "商品暫時不可購買"
        
        # 等級限制檢查
        required_level = shop_item.get("required_level", 1)
        if user_level and user_level < required_level:
            return False, f"等級不足！需要達到 {required_level} 等才能購買此商品（目前等級：{user_level}）"
        
        # 使用臨時資料檢查限購
        item_purchases = temp_purchases.get(item_id, {})
        
        if shop_item["limit_per_account"] > 0:
            total_purchased = item_purchases.get("total_purchased", 0)
            if total_purchased >= shop_item["limit_per_account"]:
                return False, "已達帳號總限購數量"
        
        # 檢查重置週期限購（使用已重置的臨時資料）
        reset_type = shop_item["reset_type"]
        if reset_type != "none" and shop_item["limit_per_reset"] > 0:
            purchased_key = f"{reset_type}_purchased"
            reset_purchased = item_purchases.get(purchased_key, 0)
            
            if reset_purchased >= shop_item["limit_per_reset"]:
                reset_names = {"daily": "每日", "weekly": "每週", "monthly": "每月"}
                return False, f"已達{reset_names.get(reset_type, reset_type)}限購數量"
        
        # 原有的道具檢查邏輯...
        if shop_item.get("cost") and len(shop_item["cost"]) > 0:
            for cost_item, cost_amount in shop_item["cost"].items():
                owned_amount = user_items.get(cost_item, 0)
                if owned_amount < cost_amount:
                    return False, f"道具 {cost_item} 數量不足 (需要:{cost_amount}, 擁有:{owned_amount})"
        
        # 檢查禮包道具999限制
        is_valid_limit, limit_error = validate_bundle_limits(shop_item, user_items)
        if not is_valid_limit:
            return False, limit_error
        
        return True, ""
        
    except Exception as e:
        print(f"驗證購買失敗: {e}")
        return False, f"驗證過程發生錯誤: {str(e)}"
        
def process_shop_purchase(user_id, item_id, user_items, user_purchases):
    """處理商店購買邏輯 - 支援多道具禮包和自動重置"""
    try:
        shop_items = get_shop_items()
        shop_item = next((item for item in shop_items if item["id"] == item_id), None)
        
        if not shop_item:
            raise ValueError("商品不存在")
        
        # 🔥 關鍵修復：購買前自動檢查並重置過期計數
        updated_purchases = user_purchases.copy()
        purchases = updated_purchases.get("purchases", {})
        
        # 📅 取得當前時間週期
        current_periods = get_current_reset_periods()
        
        # 🔄 檢查所有購買記錄是否需要重置
        for check_item_id, item_purchases in purchases.items():
            # 檢查每日重置
            current_daily = current_periods.get('daily')
            last_daily = item_purchases.get('last_daily_period', '')
            if current_daily != last_daily:
                print(f"🌅 自動重置每日計數: {check_item_id} ({last_daily} → {current_daily})")
                item_purchases['daily_purchased'] = 0
                item_purchases['last_daily_period'] = current_daily
            
            # 檢查每週重置
            current_weekly = current_periods.get('weekly')
            last_weekly = item_purchases.get('last_weekly_period', '')
            if current_weekly != last_weekly:
                print(f"📅 自動重置每週計數: {check_item_id} ({last_weekly} → {current_weekly})")
                item_purchases['weekly_purchased'] = 0
                item_purchases['last_weekly_period'] = current_weekly
            
            # 檢查每月重置
            current_monthly = current_periods.get('monthly')
            last_monthly = item_purchases.get('last_monthly_period', '')
            if current_monthly != last_monthly:
                print(f"🗓️ 自動重置每月計數: {check_item_id} ({last_monthly} → {current_monthly})")
                item_purchases['monthly_purchased'] = 0
                item_purchases['last_monthly_period'] = current_monthly
        
        # 更新購買記錄結構
        updated_purchases["purchases"] = purchases
        
        # 原有的購買處理邏輯保持不變...
        updated_items = user_items.copy()
        
        # 消耗道具 (只有非免費道具才需要消耗)
        if shop_item.get("cost") and len(shop_item["cost"]) > 0 and (shop_item["type"] == "trade" or shop_item["type"] == "bundle"):
            for cost_item, cost_amount in shop_item["cost"].items():
                updated_items[cost_item] = updated_items.get(cost_item, 0) - cost_amount
                if updated_items[cost_item] <= 0:
                    del updated_items[cost_item]
        
        # 處理多道具禮包
        if shop_item["type"] == "bundle" and "items" in shop_item:
            for item_data in shop_item["items"]:
                target_item = item_data["item_id"]
                item_quantity = item_data["quantity"]
                updated_items[target_item] = updated_items.get(target_item, 0) + item_quantity
        else:
            target_item = shop_item["item_id"]
            updated_items[target_item] = updated_items.get(target_item, 0) + shop_item["quantity"]
        
        # 更新購買記錄
        item_purchases = purchases.get(item_id, {
            "total_purchased": 0,
            "daily_purchased": 0,
            "weekly_purchased": 0,
            "monthly_purchased": 0,
            "last_daily_period": "",
            "last_weekly_period": "",
            "last_monthly_period": "",
            "first_purchase_time": 0,
            "last_purchase_time": 0
        })
        
        # 取得當前時間和週期
        current_time = time.time()
        reset_type = shop_item["reset_type"]
        
        # 更新購買計數
        item_purchases["total_purchased"] += 1
        item_purchases["last_purchase_time"] = current_time
        
        if item_purchases["first_purchase_time"] == 0:
            item_purchases["first_purchase_time"] = current_time
        
        # 處理重置週期計數（使用已經重置過的週期資料）
        if reset_type != "none":
            purchased_key = f"{reset_type}_purchased"
            last_period_key = f"last_{reset_type}_period"
            
            current_period = current_periods.get(reset_type)
            
            # 更新計數和週期
            item_purchases[purchased_key] = item_purchases.get(purchased_key, 0) + 1
            item_purchases[last_period_key] = current_period
        
        # 更新購買記錄
        purchases[item_id] = item_purchases
        updated_purchases["purchases"] = purchases
        updated_purchases["last_update_time"] = current_time
        
        return updated_items, updated_purchases
        
    except Exception as e:
        print(f"處理購買失敗: {e}")
        raise e

# 🏪 商店系統API端點
# 驗證禮包是否會超過999限制
def validate_bundle_limits(shop_item, user_items):
    """驗證禮包購買是否會超過道具999限制"""
    if shop_item["type"] == "bundle" and "items" in shop_item:
        for item_data in shop_item["items"]:
            target_item = item_data["item_id"]
            item_quantity = item_data["quantity"]
            current_amount = user_items.get(target_item, 0)
            
            if current_amount + item_quantity > 999:
                return False, f"道具 {target_item} 購買後會超過999個上限"
    else:
        # 單一道具驗證
        target_item = shop_item["item_id"]
        current_amount = user_items.get(target_item, 0)
        if current_amount + shop_item["quantity"] > 999:
            return False, "購買後會超過999個上限"
    
    return True, ""


@app.route("/shop_items", methods=["GET"])
def shop_items_endpoint():
    """取得商店道具列表"""
    try:
        return jsonify(get_shop_items())
    except Exception as e:
        return jsonify({"error": f"取得商店道具失敗: {str(e)}"}), 500

@app.route("/shop_user_purchases", methods=["GET"])
@require_auth
@cached_response(ttl=60)  # 1分鐘快取
def shop_user_purchases():
    """取得用戶商店購買記錄"""
    try:
        user_id = request.user_id
        
        # 取得用戶購買記錄
        purchase_ref = db.collection("shop_purchases").document(user_id)
        purchase_doc = purchase_ref.get()
        
        if purchase_doc.exists:
            return purchase_doc.to_dict()
        else:
            # 返回空的購買記錄結構
            return {
                "user_id": user_id,
                "purchases": {},
                "last_update_time": 0
            }
            
    except Exception as e:
        return jsonify({"error": f"取得購買記錄失敗: {str(e)}"}), 500

@app.route("/shop_purchase", methods=["POST"])
@require_auth
def shop_purchase():
    """處理商店購買請求 - 支援批量購買和等級限制"""
    try:
        data = request.json
        user_id = request.user_id
        item_id = data.get("item_id")
        quantity_multiplier = data.get("quantity", 1)  # 🆕 批量購買倍數
        
        if not item_id:
            return jsonify({"success": False, "error": "缺少商品ID"}), 400
        
        if quantity_multiplier < 1 or quantity_multiplier > 50:
            return jsonify({"success": False, "error": "購買數量必須在1-50之間"}), 400
        
        # 🆕 取得用戶等級
        user_doc = db.collection("users").document(user_id).get()
        if not user_doc.exists:
            return jsonify({"success": False, "error": "找不到使用者"}), 404
        
        user_data = user_doc.to_dict()
        user_level = user_data.get("level", 1)
        
        # 清除相關快取確保資料一致性
        invalidate_user_cache(user_id)
        
        # 取得用戶道具資料
        item_ref = db.collection("user_items").document(user_id)
        item_doc = item_ref.get()
        
        if not item_doc.exists:
            return jsonify({"success": False, "error": "找不到使用者道具資料"}), 404
        
        user_items = item_doc.to_dict().get("items", {})
        
        # 取得用戶購買記錄
        purchase_ref = db.collection("shop_purchases").document(user_id)
        purchase_doc = purchase_ref.get()
        
        user_purchases = purchase_doc.to_dict() if purchase_doc.exists else {
            "user_id": user_id,
            "purchases": {},
            "last_update_time": 0
        }
        
        # 🆕 批量購買驗證（包含等級檢查）
        for i in range(quantity_multiplier):
            is_valid, error_message = validate_shop_purchase(user_id, item_id, user_items, user_purchases, user_level)
            if not is_valid:
                if i == 0:
                    return jsonify({"success": False, "error": error_message}), 400
                else:
                    # 部分成功購買
                    break
        
        successful_purchases = 0
        total_items_received = {}
        
        # 🆕 執行批量購買
        for i in range(quantity_multiplier):
            try:
                # 重新驗證每次購買（包含等級檢查）
                is_valid, error_message = validate_shop_purchase(user_id, item_id, user_items, user_purchases, user_level)
                if not is_valid:
                    break
                
                # 處理單次購買
                updated_items, updated_purchases = process_shop_purchase(
                    user_id, item_id, user_items, user_purchases
                )
                
                # 更新本地變數
                user_items = updated_items
                user_purchases = updated_purchases
                successful_purchases += 1
                
                # 記錄獲得的道具
                shop_items = get_shop_items()
                shop_item = next((item for item in shop_items if item["id"] == item_id), None)
                if shop_item:
                    if shop_item["type"] == "bundle" and "items" in shop_item:
                        # 禮包：記錄多個道具
                        for item_data in shop_item["items"]:
                            target_item = item_data["item_id"]
                            item_quantity = item_data["quantity"]
                            total_items_received[target_item] = total_items_received.get(target_item, 0) + item_quantity
                    else:
                        # 單一道具
                        target_item = shop_item["item_id"]
                        item_quantity = shop_item["quantity"]
                        total_items_received[target_item] = total_items_received.get(target_item, 0) + item_quantity
                
            except Exception as single_purchase_error:
                print(f"單次購買失敗 (第{i+1}次): {single_purchase_error}")
                break
        
        if successful_purchases == 0:
            return jsonify({"success": False, "error": "無法完成任何購買"}), 400
        
        # 💾 批次更新資料庫
        batch = db.batch()
        batch.set(item_ref, {"items": user_items})
        batch.set(purchase_ref, user_purchases)
        
        try:
            batch.commit()
            print(f"🏪 批量購買成功 - 使用者: {user_id}, 商品: {item_id}, 成功次數: {successful_purchases}")
            
        except Exception as batch_error:
            print(f"❌ 批量購買批次操作失敗: {batch_error}")
            return jsonify({
                "success": False, 
                "error": "資料儲存失敗，請稍後再試"
            }), 500
        
        # 清除快取確保資料一致性
        invalidate_user_cache(user_id)
        
        # 準備回應訊息
        shop_items = get_shop_items()
        shop_item = next((item for item in shop_items if item["id"] == item_id), None)
        
        purchase_type = "領取" if shop_item and shop_item.get("type") == "free" else "購買"
        item_name = shop_item.get("name", item_id) if shop_item else item_id
        
        # 建立獲得道具摘要
        items_summary = []
        for item_id_received, qty in total_items_received.items():
            items_summary.append(f"{item_id_received} x{qty}")
        
        success_message = f"成功{purchase_type} {item_name}"
        if successful_purchases > 1:
            success_message += f" x{successful_purchases}"
        if len(items_summary) > 0:
            success_message += f"，獲得：{', '.join(items_summary)}"
        
        # 部分成功提醒
        if successful_purchases < quantity_multiplier:
            success_message += f" (僅完成 {successful_purchases}/{quantity_multiplier} 次購買)"
        
        return jsonify({
            "success": True,
            "message": success_message,
            "user_items": user_items,
            "purchases": user_purchases,
            "purchase_info": {
                "item_id": item_id,
                "item_name": item_name,
                "successful_purchases": successful_purchases,
                "requested_purchases": quantity_multiplier,
                "total_items_received": total_items_received,
                "purchase_type": purchase_type,
                "purchase_time": user_purchases["last_update_time"],
                "user_level": user_level  # 🆕 返回用戶等級信息
            }
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"🔥 批量購買完全失敗: {str(e)}")
        return jsonify({"success": False, "error": f"購買失敗: {str(e)}"}), 500


# 新增：管理員限定的使用者管理 API
@app.route("/admin_user_info", methods=["GET"])
@require_admin
def admin_user_info():
    """管理員查詢特定使用者資訊"""
    try:
        target_user_id = request.args.get("user_id")
        if not target_user_id:
            return jsonify({"error": "缺少 user_id 參數"}), 400
        
        # 取得使用者基本資料
        user_doc = db.collection("users").document(target_user_id).get()
        if not user_doc.exists:
            return jsonify({"error": "使用者不存在"}), 404
        
        user_data = user_doc.to_dict()
        
        # 取得使用者世界王資料
        wb_player_doc = db.collection("world_boss_players").document(target_user_id).get()
        wb_data = wb_player_doc.to_dict() if wb_player_doc.exists else {}
        
        # 取得使用者道具資料
        items_doc = db.collection("user_items").document(target_user_id).get()
        items_data = items_doc.to_dict() if items_doc.exists else {"items": {}}
        
        result = {
            "basic_info": {
                "user_id": target_user_id,
                "nickname": user_data.get("nickname", target_user_id),
                "level": user_data.get("level", 1),
                "exp": user_data.get("exp", 0),
                "is_admin": user_data.get("admin", False),
                "stat_points": user_data.get("stat_points", 0),
                "skill_points": user_data.get("skill_points", 0)
            },
            "world_boss": {
                "total_damage": wb_data.get("total_damage", 0),
                "challenge_count": wb_data.get("challenge_count", 0),
                "last_challenge": wb_data.get("last_challenge_time", 0)
            },
            "items_count": len(items_data.get("items", {})),
            "equipment": user_data.get("equipment", {}),
            "queried_by": request.user_id,
            "query_time": time.time()
        }
        
        print(f"🔍 管理員 {request.user_id} 查詢了使用者 {target_user_id} 的資料")
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({"error": f"查詢使用者資料失敗: {str(e)}"}), 500




# 商店重置器
class ShopResetManager:
    def __init__(self, db):
        self.db = db
        self.reset_thread = None
        self.running = False
    
    def start_scheduler(self):
        """啟動重置排程器"""
        if self.running:
            return
            
        self.running = True
        
        # 設定重置時間
        schedule.every().day.at("00:01").do(self.daily_reset)
        schedule.every().monday.at("00:01").do(self.weekly_reset)
        # 🔧 修正：每日檢查是否需要月度重置
        schedule.every().day.at("00:01").do(self.check_monthly_reset)
        
        # 啟動背景執行緒
        self.reset_thread = threading.Thread(target=self._run_scheduler, daemon=True)
        self.reset_thread.start()
        
        print("🔄 商店重置排程器已啟動")
    
    def _run_scheduler(self):
        """背景執行排程器"""
        while self.running:
            try:
                schedule.run_pending()
                time.sleep(60)  # 🔧 修正：改為 time.sleep 而不是 time_module.sleep
            except Exception as e:
                print(f"排程器錯誤: {e}")
                time.sleep(300)  # 🔧 修正：錯誤時等待5分鐘再重試
    
    def daily_reset(self):
        """每日重置"""
        try:
            taipei_tz = pytz.timezone('Asia/Taipei')
            now_taipei = datetime.now(taipei_tz)
            current_daily_period = f"{now_taipei.year}-{now_taipei.month:02d}-{now_taipei.day:02d}"
            
            print(f"🌅 執行每日重置：{current_daily_period}")
            
            # 重置所有使用者的每日購買計數
            users_ref = self.db.collection("shop_purchases")
            batch = self.db.batch()
            batch_count = 0
            
            for doc in users_ref.stream():
                user_data = doc.to_dict()
                purchases = user_data.get("purchases", {})
                
                updated = False
                for item_id, purchase_data in purchases.items():
                    if purchase_data.get("last_daily_period", "") != current_daily_period:
                        purchase_data["daily_purchased"] = 0
                        purchase_data["last_daily_period"] = current_daily_period
                        updated = True
                
                if updated:
                    user_data["purchases"] = purchases
                    user_data["last_daily_reset"] = time.time()
                    batch.update(doc.reference, user_data)
                    batch_count += 1
                    
                    # 批次提交（Firebase限制500個操作）
                    if batch_count >= 400:
                        batch.commit()
                        batch = self.db.batch()
                        batch_count = 0
            
            if batch_count > 0:
                batch.commit()
            
            print(f"✅ 每日重置完成，影響 {batch_count} 個使用者")
            
        except Exception as e:
            print(f"❌ 每日重置失敗: {e}")
        
    def weekly_reset(self):
        """每週重置"""
        try:
            taipei_tz = pytz.timezone('Asia/Taipei')
            now_taipei = datetime.now(taipei_tz)
            year, week_num, _ = now_taipei.isocalendar()
            current_weekly_period = f"{year}-W{week_num:02d}"
            
            print(f"📅 執行每週重置：{current_weekly_period}")
            
            users_ref = self.db.collection("shop_purchases")
            batch = self.db.batch()
            batch_count = 0
            
            for doc in users_ref.stream():
                user_data = doc.to_dict()
                purchases = user_data.get("purchases", {})
                
                updated = False
                for item_id, purchase_data in purchases.items():
                    if purchase_data.get("last_weekly_period", "") != current_weekly_period:
                        purchase_data["weekly_purchased"] = 0
                        purchase_data["last_weekly_period"] = current_weekly_period
                        updated = True
                
                if updated:
                    user_data["purchases"] = purchases
                    user_data["last_weekly_reset"] = time.time()
                    batch.update(doc.reference, user_data)
                    batch_count += 1
                    
                    if batch_count >= 400:
                        batch.commit()
                        batch = self.db.batch()
                        batch_count = 0
            
            if batch_count > 0:
                batch.commit()
            
            print(f"✅ 每週重置完成，影響 {batch_count} 個使用者")
            
        except Exception as e:
            print(f"❌ 每週重置失敗: {e}")
        
    def monthly_reset(self):
        """每月重置"""
        try:
            taipei_tz = pytz.timezone('Asia/Taipei')
            now_taipei = datetime.now(taipei_tz)
            current_monthly_period = f"{now_taipei.year}-{now_taipei.month:02d}"
            
            print(f"🗓️ 執行每月重置：{current_monthly_period}")
            
            users_ref = self.db.collection("shop_purchases")
            batch = self.db.batch()
            batch_count = 0
            
            for doc in users_ref.stream():
                user_data = doc.to_dict()
                purchases = user_data.get("purchases", {})
                
                updated = False
                for item_id, purchase_data in purchases.items():
                    if purchase_data.get("last_monthly_period", "") != current_monthly_period:
                        purchase_data["monthly_purchased"] = 0
                        purchase_data["last_monthly_period"] = current_monthly_period
                        updated = True
                
                if updated:
                    user_data["purchases"] = purchases
                    user_data["last_monthly_reset"] = time.time()
                    batch.update(doc.reference, user_data)
                    batch_count += 1
                    
                    if batch_count >= 400:
                        batch.commit()
                        batch = self.db.batch()
                        batch_count = 0
            
            if batch_count > 0:
                batch.commit()
            
            print(f"✅ 每月重置完成，影響 {batch_count} 個使用者")
            
        except Exception as e:
            print(f"❌ 每月重置失敗: {e}")

    def check_monthly_reset(self):
        """檢查是否需要執行月度重置"""
        try:
            taipei_tz = pytz.timezone('Asia/Taipei')
            now_taipei = datetime.now(taipei_tz)
            
            # 只在每月1號執行月度重置
            if now_taipei.day == 1:
                print(f"🗓️ 檢測到月初，執行月度重置：{now_taipei.strftime('%Y-%m-%d')}")
                self.monthly_reset()
            
        except Exception as e:
            print(f"❌ 檢查月度重置失敗: {e}")

# ✅ 修正：將初始化代碼移到類定義外部
shop_reset_manager = ShopResetManager(db)

try:
    shop_reset_manager.start_scheduler()
    print("✅ 商店重置排程器已啟動")
except Exception as e:
    print(f"❌ 排程器啟動失敗: {e}")

if __name__ == "__main__":
    shop_reset_manager.start_scheduler()
    import os
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
