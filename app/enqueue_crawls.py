"""Daily schedule (EventBridge Scheduler runs this as a one-off ECS task): one crawl job per enabled source."""
from app import db
from app.sources import JOB


def main() -> int:
    db.init_if_requested()
    queued = 0
    with db.connect() as conn:
        for sid, kind in conn.execute("select id, kind from sources where enabled order by id").fetchall():
            # skip a source whose previous crawl is still waiting or running: crawls take hours at first
            queued += conn.execute(
                "insert into jobs(kind, payload) select %s, jsonb_build_object('source_id', %s::bigint) "
                "where not exists (select 1 from jobs where kind = %s and payload->>'source_id' = %s::text "
                "and status in ('queued', 'running'))", (JOB[kind], sid, JOB[kind], sid)).rowcount
        # an extract that failed all its attempts is requeued once a day until it yields a case (#120)
        retried = conn.execute(
            "insert into jobs(kind, payload) select 'extract', j.payload from ("
            "select distinct on ((payload->>'document_id')::bigint) payload, status from jobs where kind = 'extract' "
            "order by (payload->>'document_id')::bigint, id desc) j "
            "join documents d on d.id = (j.payload->>'document_id')::bigint and d.deleted_at is null "
            "and d.checksum = j.payload->>'checksum' "  # the version it was queued for: a newer one decided again
            "join sources s on s.id = d.source_id and s.enabled "
            "where j.status = 'failed' and not exists (select 1 from cases c where c.document_id = d.id)").rowcount
    print(f"queued {queued} crawl job(s), {retried} extract retry(ies)", flush=True)
    return queued


if __name__ == "__main__":
    main()
