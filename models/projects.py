# models/user.py
from extensions import db

class Projects(db.Model):
    __tablename__ = "projects"
    project_id       = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=True)
    project_name = db.Column(db.String(255), nullable=False, unique=True)
    description = db.Column(db.String(1024), nullable=True, unique=False)
    doc_path = db.Column(db.String(260), nullable=True, unique=False)
    # 追加: プロジェクトごとのテーマ（3パターンのキーを保存）
    theme = db.Column(db.String(32), nullable=False, default='theme-sky')

    def __repr__(self):
        return f"<Project {self.project_name}>"
