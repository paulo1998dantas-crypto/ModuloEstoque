-- ROLLBACK OPERACIONAL da migration 202607301330.
--
-- As FKs, indices e revogacoes desta migration protegem dados reais. O
-- rollback seguro nao remove constraints, nao reabre funcoes para
-- anon/authenticated e nao altera registros.
--
-- Em contingencia:
--   1. desative ERP_PO_SUGGESTION_ENABLED e ERP_MOVEMENT_CONTEXT_ENABLED;
--   2. reapresente a versao anterior dos servicos;
--   3. preserve as FKs e os indices, pois o codigo anterior grava os mesmos
--      identificadores validos ou NULL;
--   4. entregue qualquer compatibilidade necessaria por nova migration
--      forward-fix, depois de reconciliar os dados.
select
    'ROLLBACK_SEGURO: reverter aplicacao/flags e preservar integridade.'
        as procedimento,
    'CORRECOES DE SCHEMA SOMENTE POR NOVA MIGRATION FORWARD_FIX'
        as schema_strategy;
