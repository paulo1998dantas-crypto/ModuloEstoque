-- ROLLBACK OPERACIONAL da migration 202607301310.
--
-- Esta migration corrige uma exposicao preexistente: anon/authenticated tinham
-- acesso direto a tabelas operacionais do Estoque. Por seguranca, o rollback
-- nunca desabilita RLS nem restaura esses privilegios amplos.
--
-- Em contingencia:
--   1. desative as feature flags novas;
--   2. reapresente o servico anterior no Render;
--   3. confirme que o backend usa DATABASE_URL (owner) ou service_role;
--   4. crie uma policy minima e temporaria somente se um cliente legado
--      autorizado for comprovadamente dependente da Data API.
--
-- Nenhum dado, privilegio ou policy e alterado por este arquivo.
-- Depois de aplicada, qualquer compatibilidade comprovadamente necessaria deve
-- ser entregue por uma NOVA migration de forward-fix com privilegio minimo;
-- nunca reabrir em massa PUBLIC/anon/authenticated nem reescrever o historico.
select
    'ROLLBACK_SEGURO: manter RLS e revogacoes; reverter somente a aplicacao/flags.'
        as procedimento,
    'CORRECOES DE SCHEMA SOMENTE POR NOVA MIGRATION FORWARD_FIX'
        as schema_strategy;
