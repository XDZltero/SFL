# auth_middleware.py - 認證中間件
from firebase_admin import auth
from flask import request, jsonify
from functools import wraps
import traceback

def get_user_from_token(request):
    """從請求中獲取用戶資訊"""
    try:
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            print("❌ 缺少 Authorization header")
            return None
        
        if not auth_header.startswith('Bearer '):
            print("❌ Authorization header 格式錯誤")
            return None
        
        token = auth_header.split(' ')[1]
        if not token:
            print("❌ Token 為空")
            return None
        
        # 驗證 Firebase ID Token
        decoded_token = auth.verify_id_token(token)
        
        user_info = {
            "user_id": decoded_token.get('email', decoded_token.get('uid')),
            "uid": decoded_token.get('uid'),
            "email": decoded_token.get('email'),
            "name": decoded_token.get('name'),
            "firebase_user": decoded_token
        }
        
        print(f"✅ 用戶認證成功: {user_info['user_id']}")
        return user_info
        
    except auth.ExpiredIdTokenError:
        print("❌ Token 已過期")
        return None
    except auth.RevokedIdTokenError:
        print("❌ Token 已被撤銷")
        return None
    except auth.InvalidIdTokenError:
        print("❌ Token 無效")
        return None
    except Exception as e:
        print(f"❌ Token 驗證失敗: {e}")
        print(traceback.format_exc())
        return None

def require_auth(f):
    """裝飾器：要求認證"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_info = get_user_from_token(request)
        if not user_info:
            return jsonify({"error": "未授權", "code": "UNAUTHORIZED"}), 401
        
        # 將用戶信息添加到請求中
        request.user_info = user_info
        return f(*args, **kwargs)
    
    return decorated_function

def get_current_user():
    """獲取當前登入用戶"""
    return getattr(request, 'user_info', None)

def get_user_id():
    """獲取當前用戶ID"""
    user_info = get_current_user()
    return user_info['user_id'] if user_info else None
