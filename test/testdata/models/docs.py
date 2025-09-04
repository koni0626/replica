# models/docs.py
from datetime import datetime
from extensions import db

class Docs(db.Model):
    __tablename__ = "docs"

    doc_id = db.Column(db.Integer, primary_key=True)
    # プロジェクトと紐づけない場合は nullable=True にしてください
    project_id = db.Column(db.Integer, db.ForeignKey("projects.project_id"), nullable=False)
    user_id    = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=True)

    prompt     = db.Column(db.Text, nullable=False)
    content    = db.Column(db.Text, nullable=False)  # GPT生成結果（コミット時に保存）

    committed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
