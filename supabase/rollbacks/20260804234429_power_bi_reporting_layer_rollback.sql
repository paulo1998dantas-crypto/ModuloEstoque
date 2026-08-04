
-- Remove somente a camada consultiva do Power BI.
-- Nao executar enquanto houver consumidores conectados.

drop schema if exists bi cascade;
drop role if exists powerbi_reader;
