from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request

from app import db
from app.main import User, require
from app.models_admin import APPROVAL_COLS
from app.review import page

router = APIRouter()
PAGE_SIZE = 50


def shown(v):
    return ", ".join(map(str, v)) if isinstance(v, list) else v


def config_summary(action, old, new):
    if action == "create":
        return "created"
    if action != "config":
        return ""
    changes = []
    if old.get("name") != new.get("name"):
        changes.append(f"name: {old.get('name', '')} → {new.get('name', '')}")
    before, after = old.get("config") or {}, new.get("config") or {}
    for key in sorted(before.keys() | after.keys()):
        if before.get(key) != after.get(key):
            changes.append(f"{key}: {shown(before.get(key, '—'))} → {shown(after.get(key, '—'))}")
    return "; ".join(changes)


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
        acl_changes = conn.execute(
            "select c.changed_at, coalesce(s.name, '(deleted source)'), c.old_groups, c.new_groups, c.changed_by "
            "from source_acl_changes c left join sources s on s.id = c.source_id order by c.id desc limit 100").fetchall()
        config_rows = conn.execute(
            "select c.changed_at, coalesce(s.name, '(deleted source)'), c.action, c.old, c.new, c.changed_by "
            "from source_config_changes c left join sources s on s.id = c.source_id order by c.id desc limit 100").fetchall()
        overrides = conn.execute("select approved_at, case_id, score, reason, approved_by from case_approval_overrides "
                                 "order by id desc limit 100").fetchall()
        tool_rows = conn.execute("select created_at, user_key, client_id, tool, outcome, ms from tool_calls "
                                 "order by id desc limit 200").fetchall()
        tool_counts = conn.execute("select tool, count(*) from tool_calls where created_at > now() - interval '30 days' "
                                   "group by tool order by tool").fetchall()
        arch_changes = conn.execute("select changed_at, case_id, action, changed_by from architecture_changes order by id desc limit 200").fetchall()
        logo_changes = conn.execute("select l.changed_at, coalesce(c.name, '(deleted client)'), l.action, l.changed_by "
                                    "from client_logo_changes l left join clients c on c.id = l.client_id order by l.id desc limit 100").fetchall()
    link = lambda n: "/admin/audit?" + urlencode({"p": n, **({"user": who} if who else {})})  # noqa: E731
    config_changes = [(at, name, action, config_summary(action, old, new), by)
                      for at, name, action, old, new, by in config_rows]
    return page(request, "audit.html", user, rows=rows[:PAGE_SIZE], approvals=approvals, class_changes=class_changes, acl_changes=acl_changes, config_changes=config_changes, overrides=overrides, tool_rows=tool_rows, tool_counts=tool_counts, arch_changes=arch_changes, logo_changes=logo_changes, who=who, p=p,
                prev=link(p - 1) if p > 1 else None, next=link(p + 1) if len(rows) > PAGE_SIZE else None)
