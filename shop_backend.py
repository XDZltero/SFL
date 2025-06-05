# shop_backend.py - 全新商店後端系統
import json
import time
from datetime import datetime, timezone, timedelta
from firebase_admin import firestore
from flask import Blueprint, request, jsonify
import pytz

# 創建藍圖
shop_bp = Blueprint('shop', __name__)

# 台北時區
TAIPEI_TZ = pytz.timezone('Asia/Taipei')

def get_taipei_time():
    """獲取台北時間"""
    return datetime.now(TAIPEI_TZ)

def get_taipei_timestamp():
    """獲取台北時間戳"""
    return time.time()

def get_date_boundaries(dt):
    """獲取指定日期的各種邊界時間"""
    # 當日開始 (00:00:00)
    day_start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # 本週開始 (週一 00:00:00)
    days_since_monday = dt.weekday()
    week_start = (dt - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
    
    # 本月開始 (1日 00:00:00)
    month_start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    return {
        'day_start': day_start.timestamp(),
        'week_start': week_start.timestamp(),
        'month_start': month_start.timestamp()
    }

class ShopManager:
    """商店管理器"""
    
    def __init__(self):
        self.db = firestore.client()
        self.shop_items = self.load_shop_items()
    
    def load_shop_items(self):
        """載入商店物品配置"""
        try:
            with open("parameter/shop_items.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"載入商店物品失敗: {e}")
            return []
    
    def get_user_shop_data(self, user_id):
        """獲取用戶商店資料"""
        doc_ref = self.db.collection("user_shop_data").document(user_id)
        doc = doc_ref.get()
        
        if doc.exists:
            return doc.to_dict()
        else:
            # 創建新的用戶商店資料
            initial_data = {
                "user_id": user_id,
                "last_visit_time": 0,
                "purchases": {}
            }
            doc_ref.set(initial_data)
            return initial_data
    
    def update_user_shop_data(self, user_id, data):
        """更新用戶商店資料"""
        doc_ref = self.db.collection("user_shop_data").document(user_id)
        doc_ref.set(data)
    
    def check_and_perform_resets(self, user_id):
        """檢查並執行商店重置"""
        shop_data = self.get_user_shop_data(user_id)
        current_time = get_taipei_timestamp()
        last_visit_time = shop_data.get("last_visit_time", 0)
        
        # 如果是首次訪問，不需要重置
        if last_visit_time == 0:
            shop_data["last_visit_time"] = current_time
            self.update_user_shop_data(user_id, shop_data)
            return {
                "reset_performed": {
                    "daily": False,
                    "weekly": False,
                    "monthly": False
                },
                "shop_data": shop_data
            }
        
        # 獲取當前時間和上次訪問時間的邊界
        current_dt = datetime.fromtimestamp(current_time, TAIPEI_TZ)
        last_visit_dt = datetime.fromtimestamp(last_visit_time, TAIPEI_TZ)
        
        current_boundaries = get_date_boundaries(current_dt)
        last_boundaries = get_date_boundaries(last_visit_dt)
        
        reset_performed = {
            "daily": False,
            "weekly": False,
            "monthly": False
        }
        
        purchases = shop_data.get("purchases", {})
        
        # 檢查是否需要重置
        need_daily_reset = current_boundaries['day_start'] > last_boundaries['day_start']
        need_weekly_reset = current_boundaries['week_start'] > last_boundaries['week_start']
        need_monthly_reset = current_boundaries['month_start'] > last_boundaries['month_start']
        
        # 執行重置
        for item_id, purchase_data in purchases.items():
            if need_daily_reset:
                purchase_data["daily_purchased"] = 0
                purchase_data["last_daily_reset"] = current_time
                reset_performed["daily"] = True
            
            if need_weekly_reset:
                purchase_data["weekly_purchased"] = 0
                purchase_data["last_weekly_reset"] = current_time
                reset_performed["weekly"] = True
            
            if need_monthly_reset:
                purchase_data["monthly_purchased"] = 0
                purchase_data["last_monthly_reset"] = current_time
                reset_performed["monthly"] = True
        
        # 更新最後訪問時間
        shop_data["last_visit_time"] = current_time
        shop_data["purchases"] = purchases
        
        # 保存更新
        self.update_user_shop_data(user_id, shop_data)
        
        return {
            "reset_performed": reset_performed,
            "shop_data": shop_data
        }
    
    def can_purchase_item(self, user_id, item_id, user_level, user_items):
        """檢查是否可以購買物品"""
        # 找到商品
        item = None
        for shop_item in self.shop_items:
            if shop_item["id"] == item_id:
                item = shop_item
                break
        
        if not item:
            return False, "商品不存在"
        
        # 檢查等級限制
        required_level = item.get("required_level", 1)
        if user_level < required_level:
            return False, f"等級不足 (需要Lv.{required_level}，目前Lv.{user_level})"
        
        # 獲取購買記錄
        shop_data = self.get_user_shop_data(user_id)
        purchases = shop_data.get("purchases", {})
        item_purchases = purchases.get(item_id, {
            "total_purchased": 0,
            "daily_purchased": 0,
            "weekly_purchased": 0,
            "monthly_purchased": 0
        })
        
        # 檢查總購買限制
        limit_per_account = item.get("limit_per_account", -1)
        if limit_per_account > 0 and item_purchases["total_purchased"] >= limit_per_account:
            return False, "已達總購買上限"
        
        # 檢查週期購買限制
        reset_type = item.get("reset_type", "none")
        limit_per_reset = item.get("limit_per_reset", -1)
        
        if reset_type != "none" and limit_per_reset > 0:
            purchased_key = f"{reset_type}_purchased"
            if item_purchases.get(purchased_key, 0) >= limit_per_reset:
                type_names = {"daily": "今日", "weekly": "本週", "monthly": "本月"}
                return False, f"已達{type_names.get(reset_type, reset_type)}購買上限"
        
        # 檢查貨幣是否足夠
        cost = item.get("cost", {})
        if cost:
            for cost_item_id, cost_amount in cost.items():
                owned = user_items.get(cost_item_id, 0)
                if owned < cost_amount:
                    return False, f"貨幣不足：{cost_item_id}"
        
        return True, "可以購買"
    
    def process_purchase(self, user_id, item_id, user_level, user_items):
        """處理購買"""
        # 再次檢查是否可以購買
        can_purchase, reason = self.can_purchase_item(user_id, item_id, user_level, user_items)
        if not can_purchase:
            return False, reason, None, None
        
        # 找到商品
        item = None
        for shop_item in self.shop_items:
            if shop_item["id"] == item_id:
                item = shop_item
                break
        
        if not item:
            return False, "商品不存在", None, None
        
        # 扣除貨幣
        cost = item.get("cost", {})
        for cost_item_id, cost_amount in cost.items():
            user_items[cost_item_id] = user_items.get(cost_item_id, 0) - cost_amount
        
        # 添加獲得的物品
        rewards = {}
        if item.get("type") == "bundle" and item.get("items"):
            # 禮包類型
            for reward_item in item["items"]:
                reward_item_id = reward_item["item_id"]
                reward_quantity = reward_item["quantity"]
                
                current_amount = user_items.get(reward_item_id, 0)
                new_amount = min(current_amount + reward_quantity, 999)
                user_items[reward_item_id] = new_amount
                
                rewards[reward_item_id] = rewards.get(reward_item_id, 0) + (new_amount - current_amount)
        
        elif item.get("item_id"):
            # 單一物品類型
            reward_item_id = item["item_id"]
            reward_quantity = item.get("quantity", 1)
            
            current_amount = user_items.get(reward_item_id, 0)
            new_amount = min(current_amount + reward_quantity, 999)
            user_items[reward_item_id] = new_amount
            
            rewards[reward_item_id] = new_amount - current_amount
        
        # 更新購買記錄
        shop_data = self.get_user_shop_data(user_id)
        purchases = shop_data.get("purchases", {})
        
        if item_id not in purchases:
            purchases[item_id] = {
                "total_purchased": 0,
                "daily_purchased": 0,
                "weekly_purchased": 0,
                "monthly_purchased": 0,
                "last_daily_reset": 0,
                "last_weekly_reset": 0,
                "last_monthly_reset": 0
            }
        
        # 增加購買次數
        purchases[item_id]["total_purchased"] += 1
        
        reset_type = item.get("reset_type", "none")
        if reset_type in ["daily", "weekly", "monthly"]:
            purchased_key = f"{reset_type}_purchased"
            purchases[item_id][purchased_key] += 1
        
        shop_data["purchases"] = purchases
        
        return True, "購買成功", rewards, shop_data

# 創建全域管理器實例
shop_manager = ShopManager()

@shop_bp.route('/shop_status', methods=['GET'])
def get_shop_status():
    """獲取商店狀態（進店時觸發重置檢查）"""
    try:
        # 從token獲取用戶ID
        from auth_middleware import get_user_from_token
        user_info = get_user_from_token(request)
        if not user_info:
            return jsonify({"error": "未授權"}), 401
        
        user_id = user_info["user_id"]
        
        # 檢查並執行重置
        reset_result = shop_manager.check_and_perform_resets(user_id)
        
        return jsonify({
            "success": True,
            "last_visit_time": reset_result["shop_data"]["last_visit_time"],
            "purchases": reset_result["shop_data"]["purchases"],
            "reset_performed": reset_result["reset_performed"]
        })
        
    except Exception as e:
        print(f"獲取商店狀態錯誤: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@shop_bp.route('/shop_purchase_new', methods=['POST'])
def purchase_item():
    """購買商品"""
    try:
        # 從token獲取用戶ID
        from auth_middleware import get_user_from_token
        user_info = get_user_from_token(request)
        if not user_info:
            return jsonify({"error": "未授權"}), 401
        
        user_id = user_info["user_id"]
        
        # 獲取請求資料
        data = request.get_json()
        item_id = data.get("item_id")
        
        if not item_id:
            return jsonify({"success": False, "error": "缺少商品ID"}), 400
        
        # 獲取用戶資料
        from user_utils import get_user_status, get_user_items, update_user_items
        
        user_status = get_user_status(user_id)
        if not user_status:
            return jsonify({"success": False, "error": "無法獲取用戶資料"}), 400
        
        user_items_data = get_user_items(user_id)
        user_items = user_items_data.get("items", {}) if user_items_data else {}
        user_level = user_status.get("level", 1)
        
        # 處理購買
        success, message, rewards, shop_data = shop_manager.process_purchase(
            user_id, item_id, user_level, user_items
        )
        
        if not success:
            return jsonify({"success": False, "error": message}), 400
        
        # 更新用戶物品
        update_user_items(user_id, {"items": user_items})
        
        # 更新商店資料
        shop_manager.update_user_shop_data(user_id, shop_data)
        
        return jsonify({
            "success": True,
            "message": message,
            "rewards": rewards,
            "player_items": user_items,
            "shop_status": {
                "last_visit_time": shop_data["last_visit_time"],
                "purchases": shop_data["purchases"]
            }
        })
        
    except Exception as e:
        print(f"購買商品錯誤: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@shop_bp.route('/shop_debug/<user_id>', methods=['GET'])
def debug_shop_data(user_id):
    """調試用：查看用戶商店資料"""
    try:
        shop_data = shop_manager.get_user_shop_data(user_id)
        
        # 格式化時間顯示
        if shop_data.get("last_visit_time"):
            last_visit_dt = datetime.fromtimestamp(shop_data["last_visit_time"], TAIPEI_TZ)
            shop_data["last_visit_time_formatted"] = last_visit_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
        
        return jsonify({
            "success": True,
            "shop_data": shop_data,
            "current_time": get_taipei_time().strftime("%Y-%m-%d %H:%M:%S %Z"),
            "current_timestamp": get_taipei_timestamp()
        })
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@shop_bp.route('/shop_reset/<user_id>', methods=['POST'])
def force_reset_shop(user_id):
    """調試用：強制重置商店資料"""
    try:
        # 重置所有購買記錄
        initial_data = {
            "user_id": user_id,
            "last_visit_time": 0,
            "purchases": {}
        }
        
        shop_manager.update_user_shop_data(user_id, initial_data)
        
        return jsonify({
            "success": True,
            "message": "商店資料已重置"
        })
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
