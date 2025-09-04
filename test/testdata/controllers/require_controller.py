from flask import Blueprint, render_template, redirect, url_for, flash
from sqlalchemy.exc import IntegrityError

require_bp = Blueprint("require", __name__)

@require_bp.route("/", methods=["GET", "POST"])
def index():
    return render_template("require/index.html")

