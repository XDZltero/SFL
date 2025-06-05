# user_utils.py - 用戶工具函數
from firebase_admin import firestore
import time
import json

# 全域資料庫連接
db = firestore.client()

def get_user_status(user_id):
    """獲取用戶狀態"""
    try:
        doc_ref = db.collection("users").document(user_id)
        doc = doc_ref.get()
        
        if doc.exists:
            user_data = doc.to_dict()
            print(f"✅ 獲取用戶狀態成功: {user_id}")
            return user_data
        else:
            print(f"❌ 用戶不存在: {user_id}")
            return None
            
    except Exception as e:
        print(f"❌ 獲取用戶狀態失敗: {e}")
        return None

def get_user_items(user_id):
    """獲取用戶物品"""
    try:
        doc_ref = db.collection("user_items").document(user_id)
        doc = doc_ref.get()
        
        if doc.exists:
            items_data = doc.to_dict()
            print(f"✅ 獲取用戶物品成功: {user_id}")
            return items_data
        else:
            # 如果用戶物品不存在，創建初始資料
            initial_data = {
                "id": user_id,
                "items": {}
            }
            doc_ref.set(initial_data)
            print(f"✅ 創建初始用戶物品: {user_id}")
            return initial_data
            
    except Exception as e:
        print(f"❌ 獲取用戶物品失敗: {e}")
        return {"id": user_id, "items": {}}

def update_user_items(user_id, items_data):
    """更新用戶物品"""
    try:
        doc_ref = db.collection("user_items").document(user_id)
        
        # 確保資料格式正確
        if "id" not in items_data:
            items_data["id"] = user_id
        
        # 確保所有物品數量在有效範圍內
        if "items" in items_data:
            for item_id, quantity in items_data["items"].items():
                if quantity < 0:
                    items_data["items"][item_id] = 0
                elif quantity > 999:
                    items_data["items"][item_id] = 999
        
        doc_ref.set(items_data)
        print(f"✅ 更新用戶物品成功: {user_id}")
        return True
        
    except Exception as e:
        print(f"❌ 更新用戶物品失敗: {e}")
        return False

def add_user_item(user_id, item_id, quantity):
    """添加用戶物品"""
    try:
        items_data = get_user_items(user_id)
        current_items = items_data.get("items", {})
        
        current_amount = current_items.get(item_id, 0)
        new_amount = min(current_amount + quantity, 999)
        current_items[item_id] = new_amount
        
        items_data["items"] = current_items
        
        success = update_user_items(user_id, items_data)
        if success:
            print(f"✅ 添加物品成功: {user_id} 獲得 {item_id} x{quantity}")
            return new_amount - current_amount  # 返回實際添加的數量
        else:
            return 0
            
    except Exception as e:
        print(f"❌ 添加用戶物品失敗: {e}")
        return 0

def remove_user_item(user_id, item_id, quantity):
    """移除用戶物品"""
    try:
        items_data = get_user_items(user_id)
        current_items = items_data.get("items", {})
        
        current_amount = current_items.get(item_id, 0)
        if current_amount < quantity:
            print(f"❌ 物品數量不足: {user_id} {item_id} 需要:{quantity} 擁有:{current_amount}")
            return False
        
        new_amount = current_amount - quantity
        current_items[item_id] = new_amount
        
        items_data["items"] = current_items
        
        success = update_user_items(user_id, items_data)
        if success:
            print(f"✅ 移除物品成功: {user_id} 失去 {item_id} x{quantity}")
            return True
        else:
            return False
            
    except Exception as e:
        print(f"❌ 移除用戶物品失敗: {e}")
        return False

def get_user_item_count(user_id, item_id):
    """獲取用戶特定物品數量"""
    try:
        items_data = get_user_items(user_id)
        return items_data.get("items", {}).get(item_id, 0)
    except Exception as e:
        print(f"❌ 獲取物品數量失敗: {e}")
        return 0

def load_item_metadata():
    """載入物品元數據"""
    try:
        with open("parameter/items.json", "r", encoding="utf-8") as f:
            items_list = json.load(f)
        
        # 轉換為字典格式
        items_dict = {}
        for item in items_list:
            items_dict[item["id"]] = item
        
        print(f"✅ 載入物品元數據成功: {len(items_dict)} 個物品")
        return items_dict
        
    except Exception as e:
        print(f"❌ 載入物品元數據失敗: {e}")
        return {}

def get_item_name(item_id, items_meta=None):
    """獲取物品名稱"""
    if items_meta is None:
        items_meta = load_item_metadata()
    
    return items_meta.get(item_id, {}).get("name", item_id)

def validate_user_currency(user_id, cost_items):
    """驗證用戶是否有足夠的貨幣"""
    try:
        items_data = get_user_items(user_id)
        user_items = items_data.get("items", {})
        
        for item_id, required_amount in cost_items.items():
            owned_amount = user_items.get(item_id, 0)
            if owned_amount < required_amount:
                return False, f"物品不足: {item_id} (需要:{required_amount}, 擁有:{owned_amount})"
        
        return True, "貨幣充足"
        
    except Exception as e:
        print(f"❌ 驗證用戶貨幣失敗: {e}")
        return False, "驗證失敗"

def create_transaction_log(user_id, action, details):
    """創建交易日誌"""
    try:
        log_data = {
            "user_id": user_id,
            "action": action,
            "details": details,
            "timestamp": time.time(),
            "created_at": firestore.SERVER_TIMESTAMP
        }
        
        db.collection("transaction_logs").add(log_data)
        print(f"✅ 創建交易日誌: {user_id} - {action}")
        
    except Exception as e:
        print(f"❌ 創建交易日誌失敗: {e}")

# 批量操作函數
def batch_update_user_items(user_id, item_changes, action_description="批量更新"):
    """批量更新用戶物品"""
    try:
        items_data = get_user_items(user_id)
        current_items = items_data.get("items", {})
        
        # 記錄變更
        changes_log = {}
        
        for item_id, change_amount in item_changes.items():
            old_amount = current_items.get(item_id, 0)
            new_amount = max(0, min(old_amount + change_amount, 999))
            
            current_items[item_id] = new_amount
            changes_log[item_id] = {
                "old": old_amount,
                "new": new_amount,
                "change": change_amount,
                "actual_change": new_amount - old_amount
            }
        
        items_data["items"] = current_items
        success = update_user_items(user_id, items_data)
        
        if success:
            # 創建交易日誌
            create_transaction_log(user_id, action_description, {
                "changes": changes_log,
                "success": True
            })
            
            print(f"✅ 批量更新物品成功: {user_id}")
            return True, changes_log
        else:
            return False, {}
            
    except Exception as e:
        print(f"❌ 批量更新物品失敗: {e}")
        return False, {}

# 緩存機制（可選）
_item_metadata_cache = None
_cache_timestamp = 0

def get_cached_item_metadata():
    """獲取快取的物品元數據"""
    global _item_metadata_cache, _cache_timestamp
    
    current_time = time.time()
    # 快取5分鐘
    if _item_metadata_cache is None or current_time - _cache_timestamp > 300:
        _item_metadata_cache = load_item_metadata()
        _cache_timestamp = current_time
        print("🔄 物品元數據快取已更新")
    
    return _item_metadata_cache
