from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request

from app import db
from app.main import User, require
from app.models_admin import APPROVAL_COLS
from app.review import page

router = APIRouter()
PAGE_SIZE = 50


@router.get("/admin/audit")
def audit_page(request: Request, p: int = Query(1, ge=1, le=100000), who: str = Query("", alias="user"),
               user: User = Depends(require("admin"))):
    """Who generated what: recent generations, newest first, optionally for one user (exact sub)."""
    with db.connect() as conn:
        rows = conn.execute(
            "select id, created_at, user_id, format, case_ids, anonymised, industry_context_ack, research_id "
            "from generations where (%s = '' or user_id = %s) order by id desc limit %s offset %s",
            (who, who, PAGE_SIZE + 1, (p - 1) * PAGE_SIZE)).fetchall()
        approvals = conn.execute(f"select {APPROVAL_COLS} from model_approvals order by id desc limit 100").fetchall()
        class_changes = conn.execute(
            "select c.changed_at, coalesce(s.name, '(deleted source)'), c.old_class, c.new_class, c.changed_by "
            "from source_class_changes c left join sources s on s.id = c.source_id order by c.id desc limit 100").fetchall()
    link = lambda n: "/admin/audit?" + urlencode({"p": n, **({"user": who} if who else {})})  # noqa: E731
    return page(request, "audit.html", user, rows=rows[:PAGE_SIZE], approvals=approvals, class_changes=class_changes, who=who, p=p,
                prev=link(p - 1) if p > 1 else None, next=link(p + 1) if len(rows) > PAGE_SIZE else None)
