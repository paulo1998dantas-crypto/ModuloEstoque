-- Restringe baixa por ID e correcao de empenhos ao perfil ADMIN.
-- Idempotente; nao altera movimentos, quantidades ou saldos.
begin;

set local lock_timeout = 5000;
set local statement_timeout = 30000;

do $$
begin
    if to_regclass('public.erp_permissions') is null
       or to_regclass('public.erp_role_permissions') is null
       or to_regclass('public.erp_roles') is null then
        raise exception
            'Schema RBAC incompleto. Aplique primeiro as migrations compartilhadas de acesso.';
    end if;
end
$$;

insert into public.erp_permissions (code, module, description)
values (
    'estoque.commitment.reconcile_admin',
    'estoque',
    'Baixar por ID ou corrigir empenhos'
)
on conflict (code) do update
set module = excluded.module,
    description = excluded.description;

-- Defesa em profundidade: uma reexecucao remove eventual concessao acidental
-- por perfil. A aplicacao tambem exige membership ADMIN no backend.
delete from public.erp_role_permissions
where permission_code = 'estoque.commitment.reconcile_admin'
  and role_code <> 'ADMIN';

insert into public.erp_role_permissions (role_code, permission_code)
values ('ADMIN', 'estoque.commitment.reconcile_admin')
on conflict do nothing;

commit;
