-- Perfil COMERCIAL: acesso ao modulo Cadastro, com escopo funcional
-- restrito pelo backend do Cadastro a consulta de SKUs e gestao de pessoas.
-- Esta migration e aditiva e pode ser reaplicada com seguranca.

begin;

insert into public.erp_roles (code, name, description)
values (
    'COMERCIAL',
    'Comercial',
    'Consulta de cadastros e gestao do cadastro geral de pessoas.'
)
on conflict (code) do update
set name = excluded.name,
    description = excluded.description,
    updated_at = now();

insert into public.erp_permissions (code, module, description)
values (
    'cadastro.access',
    'CADASTRO',
    'Acessar o Modulo Cadastro.'
)
on conflict (code) do update
set module = excluded.module,
    description = excluded.description;

-- Perfil fixo: COMERCIAL nao herda permissoes operacionais de outros modulos.
delete from public.erp_role_permissions
where role_code = 'COMERCIAL'
  and permission_code <> 'cadastro.access';

insert into public.erp_role_permissions (role_code, permission_code)
values ('COMERCIAL', 'cadastro.access')
on conflict (role_code, permission_code) do nothing;

commit;
