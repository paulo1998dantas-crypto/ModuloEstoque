-- Rollback operacional seguro:
-- 1. desligue ERP_SHARED_RBAC_ENABLED / CADASTRO_SHARED_RBAC_ENABLED;
-- 2. restaure MES_AUTH_MODE=legacy;
-- 3. reinicie os servicos.
--
-- Esta migration apenas preenche vinculos ausentes derivados de users.role.
-- Nao os remova automaticamente: eles ja podem ter sido editados e usados
-- por sessoes reais depois do corte. Qualquer correcao deve ser individual,
-- auditada e feita pela tela central /usuarios.
select 'Nenhuma alteracao destrutiva executada.' as rollback_status;
