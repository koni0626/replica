# models/user.py
from extensions import db
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash


class Users(db.Model):
    __tablename__ = 'users'

    user_id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    def set_password(self, password):
        """パスワードをハッシュ化して保存します。"""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """入力されたパスワードがハッシュと一致するかを確認します。"""
        return check_password_hash(self.password_hash, password)

    def get_id(self):
        """ユーザーの一意のIDを返します。"""
        return str(self.user_id)

    @property
    def is_authenticated(self):
        """ユーザーが認証されているかどうかを示します。"""
        return True

    @property
    def is_active(self):
        """ユーザーがアクティブであるかどうかを示します。"""
        return True

    @property
    def is_anonymous(self):
        """ユーザーが匿名ユーザーであるかどうかを示します。"""
        return False

    def __repr__(self):
        return f'<User {self.username}>'