# models/docs.py
from datetime import datetime
from extensions import db

class Knowledge(db.Model):
    __tablename__ = 'knowledge'

    knowledge_id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.project_id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id'), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(255), nullable=True)
    content = db.Column(db.Text, nullable=False)
    active = db.Column(db.Boolean, default=True)
    order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)