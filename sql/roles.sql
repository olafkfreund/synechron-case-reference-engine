-- The application's own database role: it can read and write rows, never change the schema.
-- Applied by app.migrate (as the master user) when DB_APP_ROLE_SECRET_ARN is set; the password is
-- set separately, from that secret. Idempotent.
do $$
begin
  if not exists (select from pg_roles where rolname = 'refs_app') then
    create role refs_app login nosuperuser nocreatedb nocreaterole nobypassrls;
  end if;
end $$;

grant usage on schema public to refs_app;
revoke create on schema public from refs_app;
grant select, insert, update, delete on all tables in schema public to refs_app;
grant usage, select on all sequences in schema public to refs_app;
-- tables the master creates later (schema changes) are covered too
alter default privileges in schema public grant select, insert, update, delete on tables to refs_app;
alter default privileges in schema public grant usage, select on sequences to refs_app;
-- audit logs are append-only for the app
revoke update, delete on source_class_changes from refs_app;
revoke update, delete on source_acl_changes from refs_app;
