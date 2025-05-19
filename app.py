from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import json
import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__)
CORS(app, origins=["https://xdzltero.github.io"])

# Firebase 初始化
firebase_creds_str = os.environ["FIREBASE_CREDENTIALS"]
firebase_creds = json.loads(firebase_creds_str)
cred = credentials.Certificate(firebase_creds)
firebase_admin.initialize_app(cred)
db = firestore.client()
