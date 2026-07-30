-- Rollback operacional seguro da migration 202607301300.
--
-- 1. Desligar ERP_SHARED_RBAC_ENABLED, ERP_MOVEMENT_CONTEXT_ENABLED e
--    ERP_PO_SUGGESTION_ENABLED nos servicos.
-- 2. No MES, voltar MES_AUTH_MODE para "legacy".
-- 3. Reimplantar as versoes anteriores dos aplicativos.
--
-- As tabelas, colunas, vinculos e auditorias desta migration sao aditivos e
-- deliberadamente preservados. Nao executar DROP: isso apagaria historico
-- criado durante o periodo de ativacao e dificultaria uma reativacao segura.
--
-- Este arquivo e um runbook SQL intencionalmente sem mutacoes. Depois que a
-- migration constar no historico do Supabase, qualquer defeito de schema deve
-- ser corrigido por uma NOVA migration aditiva (forward-fix), nunca editando o
-- historico aplicado nem executando DROP/rollback destrutivo em producao.
select
    'ROLLBACK_POR_FEATURE_FLAG'::text as strategy,
    'SCHEMA_PRESERVADO; CORRECOES SOMENTE POR FORWARD_FIX'::text
        as schema_strategy,
    now() as checked_at;
