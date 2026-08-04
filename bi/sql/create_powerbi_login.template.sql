-- Executar de forma privada no SQL Editor do Supabase.
-- Substituir LOGIN_PRIVADO e SENHA_FORTE_PRIVADA antes de executar.
-- Nao salvar a versao preenchida no GitHub.

create role LOGIN_PRIVADO
    login
    password 'SENHA_FORTE_PRIVADA'
    nosuperuser
    nocreatedb
    nocreaterole
    inherit
    noreplication
    nobypassrls;

grant powerbi_reader to LOGIN_PRIVADO;

alter role LOGIN_PRIVADO set search_path = bi, pg_catalog;
alter role LOGIN_PRIVADO set default_transaction_read_only = on;
alter role LOGIN_PRIVADO set statement_timeout = '10min';
alter role LOGIN_PRIVADO set idle_in_transaction_session_timeout = '2min';

-- Teste esperado depois de conectar com LOGIN_PRIVADO:
-- select * from bi.fato_mrp limit 1;        -- deve funcionar
-- update public.stock_balances set saldo_atual = 0; -- deve falhar
