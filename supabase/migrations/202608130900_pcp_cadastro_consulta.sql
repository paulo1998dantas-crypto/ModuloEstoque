-- PCP may consult Cadastro to plan materials and production. Cadastro itself
-- enforces this profile as read-only for all write endpoints.
-- Idempotent RBAC grant: no users or operational records are changed.
insert into public.erp_role_permissions (role_code, permission_code)
values ('PCP', 'cadastro.access')
on conflict (role_code, permission_code) do nothing;
