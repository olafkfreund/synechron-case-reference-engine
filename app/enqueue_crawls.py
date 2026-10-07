"""Daily schedule (EventBridge Scheduler runs this as a one-off ECS task): one crawl job per enabled source."""
from app import db
from app.sources import JOB


def main() -> int:
    db.init()
    queued = 0
    with db.connect() as conn:
        for sid, kind in conn.execute("select id, kind from sources where enabled order by id").fetchall():
            # skip a source whose previous crawl is still waiting or running: crawls take hours at first
            queued += conn.execute(
                "insert into jobs(kind, payload) select %s, jsonb_build_object('source_id', %s::bigint) "
                "where not exists (select 1 from jobs where kind = %s and payload->>'source_id' = %s::text "
                "and status in ('queued', 'running'))", (JOB[kind], sid, JOB[kind], sid)).rowcount
    print(f"queued {queued} crawl job(s)", flush=True)
    return queued


if __name__ == "__main__":
    main()
