# Conexão do Power BI ao Supabase

## 1. Obter o endpoint

No Dashboard Supabase, abra **Connect** e copie os dados do **Session Pooler**. Use porta `5432`. Não copie senha para arquivos ou GitHub.

Parâmetros sugeridos no Power BI:

- `pServidor`: `aws-0-us-west-2.pooler.supabase.com:5432`.
- `pBanco`: `postgres`.
- Usuário PostgreSQL: `login_privado.rodtxswtqbsbtukmvobn`.

Escolher o conector **PostgreSQL database**, modo **Importar**. Na primeira autenticação, usar o login consultivo criado pelo template SQL e guardar a senha apenas no Power BI/Gateway.

## 2. Função Power Query

Criar uma consulta em branco chamada `fnBiView`:

```powerquery
(NomeView as text) as table =>
let
    Fonte = PostgreSQL.Database(
        pServidor,
        pBanco,
        [CreateNavigationProperties = false, CommandTimeout = #duration(0, 0, 10, 0)]
    ),
    View = Fonte{[Schema = "bi", Item = NomeView]}[Data]
in
    View
```

Depois criar uma consulta para cada objeto:

```powerquery
dim_sku = fnBiView("dim_sku")
dim_ordem_servico = fnBiView("dim_ordem_servico")
fato_estoque_atual = fnBiView("fato_estoque_atual")
fato_empenhos_abertos = fnBiView("fato_empenhos_abertos")
fato_movimentacoes_estoque = fnBiView("fato_movimentacoes_estoque")
fato_inventarios = fnBiView("fato_inventarios")
fato_necessidades_os = fnBiView("fato_necessidades_os")
fato_etapas_producao = fnBiView("fato_etapas_producao")
fato_compras_transito = fnBiView("fato_compras_transito")
fato_recebimentos_inspecao = fnBiView("fato_recebimentos_inspecao")
fato_forecast = fnBiView("fato_forecast")
fato_forecast_necessidades = fnBiView("fato_forecast_necessidades")
fato_mrp = fnBiView("fato_mrp")
```

No Editor Avançado de cada consulta, o conteúdo real deve ser apenas, por exemplo:

```powerquery
let
    Fonte = fnBiView("fato_mrp")
in
    Fonte
```

## 3. Atualização

- Desktop: atualizar manualmente para validar o modelo.
- Power BI Service: instalar/configurar o gateway se o endpoint exigir acesso por rede não suportado diretamente.
- Frequência inicial: 60 minutos em horário produtivo.
- Definir privacidade da fonte como **Organizacional**.
- Nunca usar `service_role`, usuário do aplicativo ou senha principal do banco.

## 4. Teste de segurança

Com o login consultivo, `SELECT * FROM bi.fato_mrp LIMIT 1` deve funcionar. Uma tentativa de `INSERT`, `UPDATE`, `DELETE` ou acesso direto às tabelas operacionais deve falhar por falta de privilégio. A proteção real é a concessão exclusiva de `SELECT`; o modo somente leitura da sessão é uma segunda barreira.
