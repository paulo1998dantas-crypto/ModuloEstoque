-- Mantém os comentários legíveis mesmo em clientes que transportam SQL como ASCII.
-- Não altera dados nem estrutura.

comment on column public.erp_work_orders.data_comercial_calculada is
    U&'Prazo comercial calculado a partir da maior data entre chegada e aprova\00E7\00E3o, conforme parametriza\00E7\00E3o da linha.';
comment on column public.erp_work_orders.modelo_configuracao is
    U&'Configura\00E7\00E3o comercial legada (por exemplo PACK); n\00E3o substitui marca/modelo/vers\00E3o do ve\00EDculo.';
comment on column public.erp_work_orders.pedido_compras_legacy is
    U&'Refer\00EAncia textual hist\00F3rica. Novos v\00EDnculos de compras usam erp_purchase_order_lines.work_order_id.';
comment on column public.erp_work_orders.numero_sequencia_legacy is
    U&'N\00FAmero de sequ\00EAncia explicitamente informado na origem; nunca inferido pela posi\00E7\00E3o da linha.';
