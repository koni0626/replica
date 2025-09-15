# config.py
import os
from datetime import timedelta

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change_me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "sqlite:///replica.db" # instanceディレクトリの下に作られます
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # ===== セッション（サーバサイド・DB）設定 =====
    SESSION_TYPE = "sqlalchemy"
    SESSION_USE_SIGNER = True
    SESSION_PERMANENT = True
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # 本番運用時はHTTPS前提で有効化してください
    # SESSION_COOKIE_SECURE = True

    # CSRF トークンの有効期限（24時間）
    WTF_CSRF_TIME_LIMIT = 60 * 60 * 24  # 24 hours

    # ============== LLMモデル選択関連（UIから指定可能） ==============
    # 既定モデル
    DEFAULT_LLM_MODEL = os.environ.get("DEFAULT_LLM_MODEL", "gpt-5")
    # 許可モデル（ホワイトリスト）
    ALLOWED_LLM_MODELS = os.environ.get("ALLOWED_LLM_MODELS", "gpt-5,gpt-4o").split(',')
