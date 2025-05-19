import os
import json
import firebase_admin
from firebase_admin import credentials, firestore
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# 從環境變數載入 JSON 字串並解析成 Python dict
firebase_creds_str = os.environ["FIREBASE_CREDENTIALS"]
firebase_creds = json.loads(firebase_creds_str)

# 初始化 Firebase Admin
cred = credentials.Certificate(firebase_creds)
firebase_admin.initialize_app(cred)
db = firestore.client()
