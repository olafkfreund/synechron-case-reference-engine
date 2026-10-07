from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import db
from app.main import User, require
from app.review import page

router = APIRouter()
APPROVAL_COLS = "id, model, data_class, approved_by, approved_at, expires_at, expires_at > now(), note, revoked_by"


@router.get("/admin/models")
def models_page(request: Request, user: User = Depends(require("admin"))):
    with db.connect() as conn:
        rows = conn.execute(f"select {APPROVAL_COLS} from model_approvals order by id desc").fetchall()
    return page(request, "models.html", user, approvals=rows)


@router.post("/admin/models")
def approve(model: str = Form(), data_class: str = Form(), expires: str = Form(), note: str = Form(""),
            user: User = Depends(require("admin"))):
    """Let one exact model id read one data class until the end of the `expires` day (at most 12 months)."""
    try:
        day = date.fromisoformat(expires)
    except ValueError:
        raise HTTPException(400, "expiry must be a date, YYYY-MM-DD") from None
    if not model.strip() or data_class != "confidential":  # the other classes need no approval
        raise HTTPException(400, "a model id is required, and approvals only apply to confidential data")
    with db.connect() as conn:
        # the bounds are checked in SQL so "12 months" means what Postgres says
        n = conn.execute(
            "insert into model_approvals(model, data_class, approved_by, expires_at, note) "
            "select %s,%s,%s,e,%s from (select (%s::date + 1)::timestamptz as e) t "
            "where e > now() and e <= now() + interval '12 months'",
            (model.strip(), data_class, user.sub, note.strip(), day)).rowcount
    if not n:
        raise HTTPException(400, "expiry must be in the future and at most 12 months ahead")
    return RedirectResponse("/admin/models", status_code=303)


@router.post("/admin/models/{aid}/revoke")
def revoke(aid: int, user: User = Depends(require("admin"))):
    with db.connect() as conn:
        if not conn.execute("update model_approvals set expires_at=now(), revoked_by=%s where id=%s and expires_at > now()",
                            (user.sub, aid)).rowcount:
            raise HTTPException(404, "no such active approval")
    return RedirectResponse("/admin/models", status_code=303)
