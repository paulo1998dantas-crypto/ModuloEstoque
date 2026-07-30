"""Transactional ERP receipt service. It only uses the new erp_* tables."""
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Uuid, bindparam, text

from models import Movement, SKU
from services.estoque_service import get_sku_by_code, register_movement, to_decimal


def _id():
    return str(uuid4())


def _row(row):
    return dict(row._mapping) if row else None


def _resolve_sku_reference(db, sku_id, sku_code):
    """Resolve a missing internal id from an active stock SKU code."""
    normalized_code = str(sku_code or "").strip()
    if sku_id:
        return sku_id, normalized_code or None
    sku = get_sku_by_code(db, normalized_code, active_only=True)
    if sku:
        return sku.id, sku.sku
    return None, normalized_code or None


def create_purchase_order(db, data, actor):
    key = str(data.get("idempotency_key") or "").strip()
    if not key:
        raise ValueError("idempotency_key e obrigatoria.")
    found = _row(db.execute(text("select id from erp_purchase_orders where idempotency_key=:key"), {"key": key}).first())
    if found:
        return {"id": str(found["id"]), "replayed": True}
    lines = data.get("lines") or []
    if not lines:
        raise ValueError("A O.C. precisa ter pelo menos uma linha.")
    order_id = _id()
    total = Decimal("0")
    for line in lines:
        qty = to_decimal(line.get("quantidade_pedida"))
        if qty <= 0:
            raise ValueError("Quantidade pedida deve ser maior que zero.")
        total += qty * to_decimal(line.get("valor_unitario_pedido"))
    db.execute(text("""insert into erp_purchase_orders (id,numero_oc,categoria,fornecedor_id,fornecedor_nome,data_emissao,criado_por,status,destino,frete,data_necessidade,observacoes,valor_total_pedido,idempotency_key) values (:id,:numero,:categoria,:fornecedor_id,:fornecedor_nome,:emissao,:actor,'EMITIDA',:destino,:frete,:necessidade,:obs,:total,:key)"""), {"id":order_id,"numero":str(data.get("numero_oc") or "").strip() or order_id[:8],"categoria":str(data.get("categoria") or "GERAL").upper(),"fornecedor_id":data.get("fornecedor_id"),"fornecedor_nome":str(data.get("fornecedor_nome") or ""),"emissao":data.get("data_emissao") or datetime.utcnow(),"actor":actor,"destino":str(data.get("destino") or ""),"frete":to_decimal(data.get("frete")),"necessidade":data.get("data_necessidade") or None,"obs":str(data.get("observacoes") or ""),"total":total,"key":key})
    for number, line in enumerate(lines, 1):
        sku_id, sku_code = _resolve_sku_reference(
            db, line.get("sku_id"), line.get("sku_codigo")
        )
        db.execute(text("""insert into erp_purchase_order_lines (id,purchase_order_id,numero_linha,sku_id,sku_codigo,descricao_original,unidade,quantidade_pedida,valor_unitario_pedido,destino,data_necessidade) values (:id,:order,:number,:sku_id,:sku_codigo,:descricao,:unidade,:qty,:value,:destino,:necessidade)"""), {"id":_id(),"order":order_id,"number":number,"sku_id":sku_id,"sku_codigo":sku_code,"descricao":str(line.get("descricao_original") or line.get("descricao") or "ITEM SEM DESCRICAO"),"unidade":str(line.get("unidade") or "UN"),"qty":to_decimal(line.get("quantidade_pedida")),"value":to_decimal(line.get("valor_unitario_pedido")),"destino":str(line.get("destino") or data.get("destino") or ""),"necessidade":line.get("data_necessidade") or data.get("data_necessidade") or None})
    db.execute(text("insert into erp_audit_events(entity_type,entity_id,action,actor,after_data) values ('PURCHASE_ORDER',:id,'EMITIDA',:actor,jsonb_build_object('numero_oc',cast(:numero as text)))"), {"id":order_id,"actor":actor,"numero":str(data.get("numero_oc") or "")})
    db.commit(); return {"id":order_id,"replayed":False}


def sync_legacy_purchase_order(db, data, actor):
    """Upsert an emitted legacy Suprimentos O.C. before any receipt exists.

    The idempotency key is the legacy document id, so reprinting or editing an
    emitted O.C. never creates a second receipt target.  Once a receipt exists,
    its commercial lines are immutable and the original ERP order is preserved.
    """
    key = str(data.get("idempotency_key") or "").strip()
    if not key:
        raise ValueError("idempotency_key e obrigatoria.")
    found = _row(db.execute(text("select id from erp_purchase_orders where idempotency_key=:key for update"), {"key": key}).first())
    if not found:
        return create_purchase_order(db, data, actor)

    order_id = str(found["id"])
    receipt_count = db.execute(text("select count(*) from erp_goods_receipts where purchase_order_id=:id and status='CONFIRMADO'"), {"id": order_id}).scalar_one()
    if receipt_count:
        return {"id": order_id, "replayed": True, "locked": True}

    lines = data.get("lines") or []
    if not lines:
        raise ValueError("Informe ao menos uma linha de pedido.")
    total = Decimal("0")
    for line in lines:
        quantity = to_decimal(line.get("quantidade_pedida"))
        if quantity <= 0:
            raise ValueError("Quantidade pedida deve ser maior que zero.")
        total += quantity * to_decimal(line.get("valor_unitario_pedido"))

    db.execute(text("""update erp_purchase_orders
        set numero_oc=:numero,categoria=:categoria,fornecedor_id=:fornecedor_id,
            fornecedor_nome=:fornecedor_nome,data_emissao=:emissao,destino=:destino,
            frete=:frete,data_necessidade=:necessidade,observacoes=:obs,
            valor_total_pedido=:total,status='EMITIDA',version=version+1,updated_at=now()
        where id=:id"""), {
        "id": order_id, "numero": str(data.get("numero_oc") or "").strip() or order_id[:8],
        "categoria": str(data.get("categoria") or "GERAL").upper(), "fornecedor_id": data.get("fornecedor_id"),
        "fornecedor_nome": str(data.get("fornecedor_nome") or ""), "emissao": data.get("data_emissao") or datetime.utcnow(),
        "destino": str(data.get("destino") or ""), "frete": to_decimal(data.get("frete")),
        "necessidade": data.get("data_necessidade") or None, "obs": str(data.get("observacoes") or ""), "total": total,
    })
    db.execute(text("delete from erp_purchase_order_lines where purchase_order_id=:id"), {"id": order_id})
    for number, line in enumerate(lines, 1):
        sku_id, sku_code = _resolve_sku_reference(
            db, line.get("sku_id"), line.get("sku_codigo")
        )
        db.execute(text("""insert into erp_purchase_order_lines
            (id,purchase_order_id,numero_linha,sku_id,sku_codigo,descricao_original,unidade,quantidade_pedida,valor_unitario_pedido,destino,data_necessidade)
            values (:id,:order,:number,:sku_id,:sku_codigo,:descricao,:unidade,:qty,:value,:destino,:necessidade)"""), {
            "id": _id(), "order": order_id, "number": number, "sku_id": sku_id,
            "sku_codigo": sku_code, "descricao": str(line.get("descricao_original") or line.get("descricao") or "ITEM SEM DESCRICAO"),
            "unidade": str(line.get("unidade") or "UN"), "qty": to_decimal(line.get("quantidade_pedida")),
            "value": to_decimal(line.get("valor_unitario_pedido")), "destino": str(line.get("destino") or data.get("destino") or ""),
            "necessidade": line.get("data_necessidade") or data.get("data_necessidade") or None,
        })
    db.execute(text("""insert into erp_audit_events(entity_type,entity_id,action,actor,origin,after_data)
        values ('PURCHASE_ORDER',:id,'ATUALIZADA_ORIGEM_SUPRIMENTOS',:actor,'SUPRIMENTOS',jsonb_build_object('idempotency_key',cast(:key as text)))"""),
        {"id": order_id, "actor": actor, "key": key})
    db.commit()
    return {"id": order_id, "replayed": False, "updated": True}


def correct_purchase_order_number(db, order_id, actor, data):
    """Correct only the visible O.C. number while preserving the ERP UUID.

    Receipts, financial settlements and stock links point to ``purchase_order_id``;
    they are deliberately not modified by this operation.  Duplicate visible
    numbers are accepted, but require an explicit confirmation from the caller.
    """
    new_number = str((data or {}).get("numero_oc") or "").strip()
    reason = str((data or {}).get("motivo") or "").strip()
    if not new_number:
        raise ValueError("Informe o novo numero da O.C.")
    if len(new_number) > 100:
        raise ValueError("Numero da O.C. deve ter no maximo 100 caracteres.")
    if not reason:
        raise ValueError("Informe o motivo da correcao de numeracao.")

    order = _row(db.execute(text("""
        select id,numero_oc,categoria,fornecedor_nome,data_emissao,status,
               idempotency_key,version
          from erp_purchase_orders
         where id=:id
         for update
    """), {"id": str(order_id or "").strip()}).first())
    if not order:
        raise ValueError("O.C. integrada nao encontrada.")
    if str(order["numero_oc"] or "").strip() == new_number:
        db.rollback()
        return {
            "id": str(order["id"]), "numero_oc": new_number,
            "unchanged": True, "duplicate_matches": [],
        }

    duplicates = [_row(row) for row in db.execute(text("""
        select id,numero_oc,categoria,fornecedor_nome,data_emissao,status
          from erp_purchase_orders
         where numero_oc=:numero and id<>:id
         order by data_emissao nulls last, created_at, id
         for share
    """), {"numero": new_number, "id": str(order["id"])}).all()]
    if duplicates and not bool((data or {}).get("confirmar_duplicidade")):
        db.rollback()
        return {
            "id": str(order["id"]), "numero_oc": str(order["numero_oc"]),
            "requires_duplicate_confirmation": True,
            "duplicate_matches": [
                {
                    "id": str(item["id"]), "numero_oc": item["numero_oc"],
                    "categoria": item["categoria"],
                    "fornecedor_nome": item["fornecedor_nome"],
                    "data_emissao": item["data_emissao"].isoformat() if item.get("data_emissao") else None,
                    "status": item["status"],
                }
                for item in duplicates
            ],
        }

    db.execute(text("""
        update erp_purchase_orders
           set numero_oc=:numero, version=version+1, updated_at=now()
         where id=:id
    """), {"id": str(order["id"]), "numero": new_number})

    # When the O.C. originated in Suprimentos, keep its buyer document in sync
    # in the same transaction.  The UUID link prevents accidentally changing a
    # different historical document that happens to have the same visible number.
    legacy_document_updated = False
    legacy_key = str(order.get("idempotency_key") or "")
    legacy_prefix = "suprimentos-oc:"
    if legacy_key.startswith(legacy_prefix):
        legacy_document_id = legacy_key[len(legacy_prefix):].strip()
        if legacy_document_id:
            document_table = db.execute(
                text("select to_regclass('public.suprimentos_documentos')")
            ).scalar_one_or_none()
            if document_table:
                result = db.execute(text("""
                    update suprimentos_documentos
                       set numero=:numero,
                           dados=jsonb_set(
                               coalesce(dados,'{}'::jsonb),
                               '{numero_oc}', to_jsonb(cast(:numero as text)), true
                           ),
                           atualizado_por=:actor,
                           updated_at=now()
                     where id=cast(:document_id as uuid)
                       and lower(coalesce(tipo,''))='oc'
                       and erp_purchase_order_id=cast(:order_id as uuid)
                """), {
                    "numero": new_number, "actor": str(actor or ""),
                    "document_id": legacy_document_id, "order_id": str(order["id"]),
                })
                legacy_document_updated = bool(result.rowcount)

    db.execute(text("""
        insert into erp_audit_events(
            entity_type,entity_id,action,actor,origin,before_data,after_data,reason
        ) values(
            'PURCHASE_ORDER',:id,'NUMERO_OC_CORRIGIDO',:actor,'SUPRIMENTOS',
            jsonb_build_object('numero_oc',cast(:before as text),'version',cast(:version as integer)),
            jsonb_build_object('numero_oc',cast(:after as text),'duplicado',cast(:duplicado as boolean),
                               'documento_suprimentos_atualizado',cast(:document_updated as boolean)),
            :reason
        )
    """), {
        "id": str(order["id"]), "actor": str(actor or ""),
        "before": str(order["numero_oc"] or ""), "after": new_number,
        "version": int(order["version"] or 0), "duplicado": bool(duplicates),
        "document_updated": legacy_document_updated, "reason": reason,
    })
    db.commit()
    return {
        "id": str(order["id"]), "numero_oc": new_number,
        "duplicate_matches": [
            {"id": str(item["id"]), "fornecedor_nome": item["fornecedor_nome"]}
            for item in duplicates
        ],
        "legacy_document_updated": legacy_document_updated,
        "unchanged": False,
    }


def pending_purchase_orders(db):
    return [_row(r) for r in db.execute(text("""select o.id,o.numero_oc,o.categoria,o.fornecedor_nome,o.status,o.data_necessidade,
        o.destino,o.valor_total_pedido,l.id as line_id,l.numero_linha,l.sku_id,l.sku_codigo,l.descricao_original,
        l.unidade,l.quantidade_pedida,l.quantidade_recebida,l.valor_unitario_pedido,
        (l.quantidade_pedida-l.quantidade_recebida) as quantidade_pendente
        from erp_purchase_orders o
        join erp_purchase_order_lines l on l.purchase_order_id=o.id
        where o.status in ('EMITIDA','PARCIALMENTE_RECEBIDA')
          and l.quantidade_pedida > l.quantidade_recebida
        order by o.data_necessidade nulls last,o.numero_oc,l.numero_linha"""))]


def pending_purchase_order_lines_by_sku(db, sku_id=None, sku_code=""):
    normalized_code = str(sku_code or "").strip().upper()
    if not sku_id and not normalized_code:
        return []
    rows = db.execute(
        text(
            """
            select o.id as purchase_order_id,o.numero_oc,o.fornecedor_nome,
                   o.data_emissao,o.data_necessidade,o.destino,o.status,
                   l.id as purchase_order_line_id,l.numero_linha,l.sku_id,
                   l.sku_codigo,l.descricao_original,l.unidade,
                   l.quantidade_pedida,l.quantidade_recebida,
                   (l.quantidade_pedida-l.quantidade_recebida)
                       as quantidade_pendente,
                   l.valor_unitario_pedido
              from erp_purchase_orders o
              join erp_purchase_order_lines l on l.purchase_order_id=o.id
             where o.status in ('EMITIDA','PARCIALMENTE_RECEBIDA')
               and l.quantidade_pedida > l.quantidade_recebida
               and (
                    (:sku_id is not null and l.sku_id=:sku_id)
                    or (
                        l.sku_id is null
                        and :sku_code <> ''
                        and upper(trim(coalesce(l.sku_codigo,'')))=:sku_code
                    )
               )
             order by o.data_necessidade nulls last,o.numero_oc,l.numero_linha
            """
        ),
        {"sku_id": sku_id, "sku_code": normalized_code},
    ).mappings()
    return [dict(row) for row in rows]


def active_work_orders(db, query="", limit=20):
    query = str(query or "").strip()
    safe_limit = max(1, min(int(limit or 20), 50))
    pattern = f"%{query}%"
    rows = db.execute(
        text(
            """
            select w.id as work_order_id,w.numero_os,w.status,w.cliente_nome,
                   e.item_number,v.chassi,v.marca,v.modelo,v.versao
              from erp_work_orders w
              join erp_vehicle_entries e on e.id=w.vehicle_entry_id
              join erp_vehicles v on v.id=e.vehicle_id
             where w.status in ('ATIVA','EM_PRODUÇÃO','EM_PRODUCAO')
               and coalesce(w.technical_status,'ABERTA')='ABERTA'
               and (
                    :query=''
                    or upper(coalesce(w.numero_os,'')) like upper(:pattern)
                    or cast(e.item_number as text) like :pattern
                    or upper(coalesce(v.chassi,'')) like upper(:pattern)
                    or upper(substr(coalesce(v.chassi,''),-8)) like upper(:pattern)
                    or upper(coalesce(w.cliente_nome,'')) like upper(:pattern)
               )
             order by e.item_number desc
             limit :limit
            """
        ),
        {"query": query, "pattern": pattern, "limit": safe_limit},
    ).mappings()
    options = []
    for raw in rows:
        row = dict(raw)
        vehicle = " ".join(
            str(row.get(field) or "").strip()
            for field in ("marca", "modelo", "versao")
            if str(row.get(field) or "").strip()
        )
        chassis = str(row.get("chassi") or "")
        row["chassi_exibicao"] = chassis[-8:] if chassis else ""
        row["veiculo"] = vehicle
        row["label"] = (
            f"O.S. {row.get('numero_os') or row.get('item_number')} · "
            f"{row['chassi_exibicao'] or 'sem chassi'} · "
            f"{row.get('cliente_nome') or vehicle or 'sem cliente'}"
        )
        options.append(row)
    return options


def work_order_materials(db, work_order_id):
    work_order = _row(
        db.execute(
            text(
                """
                select w.id,w.numero_os,e.item_number,v.chassi
                  from erp_work_orders w
                  join erp_vehicle_entries e on e.id=w.vehicle_entry_id
                  join erp_vehicles v on v.id=e.vehicle_id
                 where w.id=:id
                """
            ).bindparams(bindparam("id", type_=Uuid(as_uuid=False))),
            {"id": work_order_id},
        ).first()
    )
    if not work_order:
        raise ValueError("O.S. nao encontrada.")
    rows = [
        _row(row)
        for row in db.execute(
            text(
                """
                select m.sku_id,s.sku as sku_codigo,s.descricao,s.unidade,
                       coalesce(sum(case when m.tipo in ('EMPENHO','SAIDA')
                                         then m.quantidade else 0 end),0)
                           as quantidade_empenhada,
                       coalesce(sum(case when m.tipo='BAIXA'
                                         then m.quantidade else 0 end),0)
                           as quantidade_baixada,
                       coalesce(sum(case when m.tipo in ('EMPENHO','SAIDA')
                                         then m.quantidade else 0 end),0)
                       - coalesce(sum(case when m.tipo='BAIXA'
                                           then m.quantidade else 0 end),0)
                           as saldo_empenhado_bruto
                  from movements m
                  join skus s on s.id=m.sku_id
                 where m.work_order_id=:id
                    and coalesce(m.movement_status,'ATIVA')='ATIVA'
                    and m.tipo in ('EMPENHO','SAIDA','BAIXA')
                  group by m.sku_id,s.sku,s.descricao,s.unidade
                 order by s.sku
                """
            ).bindparams(bindparam("id", type_=Uuid(as_uuid=False))),
            {"id": work_order_id},
        ).all()
    ]
    for row in rows:
        row["saldo_empenhado"] = max(
            Decimal(str(row.pop("saldo_empenhado_bruto"))),
            Decimal("0"),
        )
    totals = {
        "quantidade_empenhada": sum(
            (Decimal(str(row["quantidade_empenhada"])) for row in rows),
            Decimal("0"),
        ),
        "quantidade_baixada": sum(
            (Decimal(str(row["quantidade_baixada"])) for row in rows),
            Decimal("0"),
        ),
        "saldo_empenhado": sum(
            (Decimal(str(row["saldo_empenhado"])) for row in rows),
            Decimal("0"),
        ),
    }
    return {"work_order": work_order, "totals": totals, "lines": rows}


def purchase_orders_dashboard(db, limit=1000):
    """Operational read model used by Suprimentos; no balance is changed here."""
    orders = [_row(row) for row in db.execute(text("""
        select o.id,o.numero_oc,o.categoria,o.fornecedor_nome,o.data_emissao,o.data_necessidade,
               o.status,o.valor_total_pedido,o.destino,o.criado_por,o.idempotency_key,
               o.technical_status,o.technical_closed_at,o.technical_closed_by,
               o.technical_close_reason,o.financial_status,o.financial_closed_at,
               o.financial_closed_by,o.financial_close_reason,
               coalesce(lines.quantidade_pedida,0) quantidade_pedida,
               coalesce(lines.quantidade_recebida,0) quantidade_recebida,
               coalesce(lines.quantidade_pendente,0) quantidade_pendente,
               coalesce(fin.quantidade_financeira,0) quantidade_financeira,
               greatest(coalesce(lines.quantidade_recebida,0)-coalesce(fin.quantidade_financeira,0),0)
                   quantidade_disponivel_financeiro,
               coalesce(fin.valor_financeiro,0) valor_financeiro,
               case when o.financial_status='CONCLUIDA' then 0
                    else greatest(o.valor_total_pedido-coalesce(fin.valor_financeiro,0),0)
               end saldo_financeiro,
               coalesce(fin.lancamentos,0) lancamentos_financeiros
          from erp_purchase_orders o
          left join lateral (
              select sum(l.quantidade_pedida) quantidade_pedida,
                     sum(l.quantidade_recebida) quantidade_recebida,
                     sum(greatest(l.quantidade_pedida-l.quantidade_recebida,0))
                         quantidade_pendente
                from erp_purchase_order_lines l
               where l.purchase_order_id=o.id
          ) lines on true
          left join lateral (
              select sum(e.quantidade_lancada) quantidade_financeira,
                     sum(e.valor_lancado) valor_financeiro,
                     count(*) lancamentos
                from erp_purchase_order_financial_entries e
               where e.purchase_order_id=o.id
          ) fin on true
         where coalesce(o.criado_por,'') <> 'validacao-local'
         order by o.created_at desc
         limit :limit
    """), {"limit": int(limit)}).all()]
    receipts = [_row(row) for row in db.execute(text("""
        select r.id,r.data_recebimento,r.numero_nf,r.fornecedor_nome,r.origem,r.status,
               o.numero_oc,coalesce(sum(l.quantidade_aprovada),0) quantidade_aprovada,
               coalesce(sum(l.quantidade_condicional),0) quantidade_condicional,
               coalesce(sum(l.quantidade_rejeitada),0) quantidade_rejeitada
          from erp_goods_receipts r
          left join erp_purchase_orders o on o.id=r.purchase_order_id
          left join erp_goods_receipt_lines l on l.goods_receipt_id=r.id
         where coalesce(r.operador,'') <> 'validacao-local'
         group by r.id,o.numero_oc
         order by r.data_recebimento desc, r.created_at desc
         limit :receipt_limit
    """), {"receipt_limit": min(20, int(limit))}).all()]
    statuses = {
        "total": len(orders), "emitidas": 0, "parciais": 0,
        "recebidas": 0, "pendentes": 0, "tecnicamente_concluidas": 0,
        "financeiramente_parciais": 0, "financeiramente_concluidas": 0,
    }
    for order in orders:
        status = order["status"]
        statuses["emitidas"] += int(status == "EMITIDA")
        statuses["parciais"] += int(status == "PARCIALMENTE_RECEBIDA")
        statuses["recebidas"] += int(status == "RECEBIDA")
        statuses["pendentes"] += int(
            status in {"EMITIDA", "PARCIALMENTE_RECEBIDA"}
            and to_decimal(order["quantidade_pendente"]) > 0
        )
        statuses["tecnicamente_concluidas"] += int(order["technical_status"] == "CONCLUIDA")
        statuses["financeiramente_parciais"] += int(order["financial_status"] == "PARCIALMENTE_CONCLUIDA")
        statuses["financeiramente_concluidas"] += int(order["financial_status"] == "CONCLUIDA")
    return {"metrics": statuses, "orders": orders, "recent_receipts": receipts}


def purchase_order_financial_detail(db, order_id):
    order = _row(db.execute(text("""
        select o.*,
               coalesce(fin.quantidade_financeira,0) quantidade_financeira,
               coalesce(fin.valor_financeiro,0) valor_financeiro,
               case when o.financial_status='CONCLUIDA' then 0
                    else greatest(o.valor_total_pedido-coalesce(fin.valor_financeiro,0),0)
               end saldo_financeiro
          from erp_purchase_orders o
          left join lateral (
              select sum(quantidade_lancada) quantidade_financeira,
                     sum(valor_lancado) valor_financeiro
                from erp_purchase_order_financial_entries
               where purchase_order_id=o.id
          ) fin on true
         where o.id=:id
    """), {"id": order_id}).first())
    if not order:
        raise ValueError("O.C. integrada não encontrada.")
    lines = [_row(row) for row in db.execute(text("""
        select l.id,l.numero_linha,l.sku_codigo,l.descricao_original,l.unidade,
               l.quantidade_pedida,l.quantidade_recebida,l.valor_unitario_pedido,
               coalesce(fin.quantidade_baixada,0) quantidade_financeira,
               greatest(l.quantidade_recebida-coalesce(fin.quantidade_baixada,0),0)
                   quantidade_disponivel_financeiro
          from erp_purchase_order_lines l
          left join lateral (
              select sum(fl.quantidade_baixada) quantidade_baixada
                from erp_purchase_order_financial_entry_lines fl
                join erp_purchase_order_financial_entries f on f.id=fl.financial_entry_id
               where fl.purchase_order_line_id=l.id
          ) fin on true
         where l.purchase_order_id=:id
         order by l.numero_linha
    """), {"id": order_id}).all()]
    receipts = [_row(row) for row in db.execute(text("""
        select r.id,r.data_recebimento,r.numero_nf,r.fornecedor_nome,r.operador,r.status,
               coalesce(sum(l.quantidade_aprovada+l.quantidade_condicional),0) quantidade_aceita,
               coalesce(sum(l.quantidade_rejeitada),0) quantidade_rejeitada,
               coalesce(sum(l.quantidade_fisica*l.valor_unitario_real),0) valor_real
          from erp_goods_receipts r
          left join erp_goods_receipt_lines l on l.goods_receipt_id=r.id
         where r.purchase_order_id=:id
         group by r.id
         order by r.data_recebimento,r.created_at
    """), {"id": order_id}).all()]
    entries = [_row(row) for row in db.execute(text("""
        select id,entry_number,entry_type,entry_date,numero_nf,
               quantidade_lancada,valor_lancado,closes_balance,actor,reason,created_at
          from erp_purchase_order_financial_entries
         where purchase_order_id=:id
         order by entry_number
    """), {"id": order_id}).all()]
    return {"order": order, "lines": lines, "receipts": receipts, "financial_entries": entries}


def register_purchase_order_financial_entry(db, order_id, actor, data):
    """Register a partial/full financial settlement without touching stock."""
    order = _row(db.execute(text("""
        select * from erp_purchase_orders where id=:id for update
    """), {"id": str(order_id or "").strip()}).first())
    if not order:
        raise ValueError("O.C. integrada não encontrada para baixa financeira.")
    if order["status"] == "CANCELADA":
        raise ValueError("O.C. cancelada não pode receber baixa financeira.")
    if order["financial_status"] == "CONCLUIDA":
        raise ValueError("O.C. já possui conclusão financeira completa.")

    key = str(data.get("idempotency_key") or "").strip()
    if not key:
        raise ValueError("Chave de idempotência é obrigatória.")
    existing = _row(db.execute(text("""
        select id,entry_type from erp_purchase_order_financial_entries
         where idempotency_key=:key
    """), {"key": key}).first())
    if existing:
        return {
            "id": str(existing["id"]),
            "financial_status": order["financial_status"],
            "replayed": True,
        }

    entry_type = str(data.get("tipo_lancamento") or data.get("entry_type") or "PARCIAL").upper()
    if entry_type not in {"PARCIAL", "COMPLETA"}:
        raise ValueError("Tipo de baixa financeira inválido.")
    numero_nf = str(data.get("numero_nf") or "").strip()
    if not numero_nf:
        raise ValueError("Informe o número da NF/invoice/proforma.")
    reason = str(data.get("motivo") or data.get("observacoes") or "").strip()
    value = to_decimal(data.get("valor_lancado"))
    if value < 0:
        raise ValueError("Valor financeiro não pode ser negativo.")

    input_lines = data.get("lines") or []
    if not input_lines:
        raise ValueError("Informe as quantidades da baixa financeira.")
    line_rows = {}
    for raw_row in db.execute(text("""
            select l.*,
                   coalesce(fin.quantidade_baixada,0) quantidade_financeira
              from erp_purchase_order_lines l
              left join lateral (
                  select sum(fl.quantidade_baixada) quantidade_baixada
                    from erp_purchase_order_financial_entry_lines fl
                    join erp_purchase_order_financial_entries f on f.id=fl.financial_entry_id
                   where fl.purchase_order_line_id=l.id
              ) fin on true
             where l.purchase_order_id=:id
             for update of l
        """), {"id": order_id}).all():
        line = _row(raw_row)
        line_rows[str(line["id"])] = line
    allocations = []
    total_quantity = Decimal("0")
    reference_total = Decimal("0")
    for item in input_lines:
        line_id = str(item.get("purchase_order_line_id") or "")
        line = line_rows.get(line_id)
        if not line:
            raise ValueError("Linha financeira não pertence à O.C.")
        quantity = to_decimal(item.get("quantidade_baixada"))
        if quantity <= 0:
            continue
        available = (
            Decimal(str(line["quantidade_recebida"]))
            - Decimal(str(line["quantidade_financeira"]))
        )
        if quantity > available:
            raise ValueError(
                f"Linha {line['numero_linha']}: baixa acima do recebimento físico disponível "
                f"({available})."
            )
        reference = quantity * Decimal(str(line["valor_unitario_pedido"]))
        allocations.append((line_id, quantity, reference))
        total_quantity += quantity
        reference_total += reference
    if not allocations:
        raise ValueError("Nenhuma quantidade disponível foi informada para baixa.")

    previous = _row(db.execute(text("""
        select coalesce(sum(valor_lancado),0) valor,
               coalesce(sum(quantidade_lancada),0) quantidade
          from erp_purchase_order_financial_entries
         where purchase_order_id=:id
    """), {"id": order_id}).first())
    remaining_value = max(
        Decimal(str(order["valor_total_pedido"])) - Decimal(str(previous["valor"])),
        Decimal("0"),
    )
    if entry_type == "PARCIAL" and value > remaining_value:
        raise ValueError(f"Valor acima do saldo financeiro pendente ({remaining_value}).")

    remaining_quantities = {
        line_id: (
            Decimal(str(line["quantidade_recebida"]))
            - Decimal(str(line["quantidade_financeira"]))
            - next((qty for current_id, qty, _ in allocations if current_id == line_id), Decimal("0"))
        )
        for line_id, line in line_rows.items()
    }
    if entry_type == "COMPLETA":
        if order["status"] != "RECEBIDA":
            raise ValueError(
                "Conclusão financeira completa exige recebimento físico total no Estoque."
            )
        if any(quantity > Decimal("0") for quantity in remaining_quantities.values()):
            raise ValueError("A baixa completa deve contemplar todo o saldo físico disponível.")
        final_actual = Decimal(str(previous["valor"])) + value
        ordered_total = Decimal(str(order["valor_total_pedido"]))
        if abs(final_actual - ordered_total) > Decimal("0.01") and not reason:
            raise ValueError("Justifique a diferença entre o valor real e o valor do pedido.")

    entry_id = _id()
    entry_number = int(db.execute(text("""
        select coalesce(max(entry_number),0)+1
          from erp_purchase_order_financial_entries
         where purchase_order_id=:id
    """), {"id": order_id}).scalar_one())
    closes = entry_type == "COMPLETA"
    db.execute(text("""
        insert into erp_purchase_order_financial_entries(
            id,purchase_order_id,entry_number,entry_type,entry_date,numero_nf,
            quantidade_lancada,valor_lancado,closes_balance,actor,reason,idempotency_key
        ) values(
            :id,:order,:number,:type,:date,:nf,:quantity,:value,:closes,:actor,:reason,:key
        )
    """), {
        "id": entry_id, "order": order_id, "number": entry_number,
        "type": entry_type, "date": data.get("data_lancamento") or datetime.utcnow(),
        "nf": numero_nf, "quantity": total_quantity, "value": value,
        "closes": closes, "actor": actor, "reason": reason, "key": key,
    })
    for line_id, quantity, reference in allocations:
        allocated_value = (
            (value * reference / reference_total).quantize(Decimal("0.01"))
            if reference_total > 0 else Decimal("0")
        )
        db.execute(text("""
            insert into erp_purchase_order_financial_entry_lines(
                id,financial_entry_id,purchase_order_line_id,
                quantidade_baixada,valor_baixado
            ) values(:id,:entry,:line,:quantity,:value)
        """), {
            "id": _id(), "entry": entry_id, "line": line_id,
            "quantity": quantity, "value": allocated_value,
        })

    financial_status = "CONCLUIDA" if closes else "PARCIALMENTE_CONCLUIDA"
    db.execute(text("""
        update erp_purchase_orders
           set financial_status=:status,
               financial_closed_at=case when :closes then now() else null end,
               financial_closed_by=case when :closes then :actor else null end,
               financial_close_reason=case when :closes then :reason else '' end,
               updated_at=now(),version=version+1
         where id=:id
    """), {
        "status": financial_status, "closes": closes, "actor": actor,
        "reason": reason, "id": order_id,
    })
    db.execute(text("""
        insert into erp_audit_events(
            entity_type,entity_id,action,actor,origin,after_data,reason
        ) values(
            'PURCHASE_ORDER',:id,:action,:actor,'SUPRIMENTOS',
            jsonb_build_object(
                'financial_entry_id',cast(:entry as text),
                'entry_type',cast(:type as text),
                'quantidade',cast(:quantity as numeric),
                'valor',cast(:value as numeric),
                'numero_nf',cast(:nf as text)
            ),
            :reason
        )
    """), {
        "id": order_id,
        "action": "CONCLUSAO_FINANCEIRA" if closes else "BAIXA_FINANCEIRA_PARCIAL",
        "actor": actor, "entry": entry_id, "type": entry_type,
        "quantity": total_quantity, "value": value, "nf": numero_nf,
        "reason": reason,
    })
    db.commit()
    return {
        "id": entry_id,
        "purchase_order_id": order_id,
        "financial_status": financial_status,
        "quantidade_lancada": total_quantity,
        "valor_lancado": value,
        "replayed": False,
    }


def confirm_receipt(db, data, actor, user_id):
    key=str(data.get("idempotency_key") or "").strip()
    if not key: raise ValueError("idempotency_key e obrigatoria.")
    po_id=data.get("purchase_order_id")
    existing=_row(db.execute(text("select id,purchase_order_id from erp_goods_receipts where idempotency_key=:key"),{"key":key}).first())
    if existing:
        if str(existing.get("purchase_order_id") or "") != str(po_id or ""):
            raise ValueError(
                "Chave de idempotencia ja utilizada por outro recebimento."
            )
        return {"id":str(existing['id']),"replayed":True}
    lines=data.get("lines") or []
    if not lines: raise ValueError("Informe ao menos uma linha de recebimento.")
    receipt_id=_id()
    if po_id:
        order = _row(db.execute(text("""
            select id,status from erp_purchase_orders where id=:id for update
        """), {"id": po_id}).first())
        if not order:
            raise ValueError("O.C. nao encontrada.")
        # Recheck after locking the order: two retries with the same command
        # key may both pass the optimistic lookup above, but only one can hold
        # this row lock at a time.
        existing = _row(
            db.execute(
                text(
                    "select id,purchase_order_id from erp_goods_receipts "
                    "where idempotency_key=:key"
                ),
                {"key": key},
            ).first()
        )
        if existing:
            if str(existing.get("purchase_order_id") or "") != str(po_id or ""):
                raise ValueError(
                    "Chave de idempotencia ja utilizada por outro recebimento."
                )
            return {"id": str(existing["id"]), "replayed": True}
        if order["status"] not in {"EMITIDA", "PARCIALMENTE_RECEBIDA"}:
            raise ValueError("O.C. nao esta ativa para recebimento.")
    db.execute(text("""insert into erp_goods_receipts(id,purchase_order_id,origem,data_recebimento,fornecedor_nome,numero_nf,operador,observacoes,motivo_excecao,idempotency_key) values(:id,:po,:origem,:date,:supplier,:nf,:actor,:obs,:reason,:key)"""),{"id":receipt_id,"po":po_id,"origem":"PURCHASE_ORDER" if po_id else "MANUAL","date":data.get("data_recebimento") or datetime.utcnow(),"supplier":str(data.get("fornecedor_nome") or ""),"nf":str(data.get("numero_nf") or ""),"actor":actor,"obs":str(data.get("observacoes") or ""),"reason":str(data.get("motivo_excecao") or ""),"key":key})
    for line_index, input_line in enumerate(lines, start=1):
        po_line_id=input_line.get("purchase_order_line_id")
        po_line=None
        if po_line_id:
            po_line=_row(db.execute(text("select * from erp_purchase_order_lines where id=:id for update"),{"id":po_line_id}).first())
            if not po_line: raise ValueError("Linha da O.C. nao encontrada.")
            if not po_id or str(po_line["purchase_order_id"]) != str(po_id):
                raise ValueError("Linha informada nao pertence a O.C. selecionada.")
            if Decimal(str(po_line["quantidade_pedida"])) <= Decimal(
                str(po_line["quantidade_recebida"])
            ):
                raise ValueError("Linha da O.C. nao possui saldo pendente.")
        physical=to_decimal(input_line.get("quantidade_fisica")); approved=to_decimal(input_line.get("quantidade_aprovada")); conditional=to_decimal(input_line.get("quantidade_condicional")); rejected=to_decimal(input_line.get("quantidade_rejeitada"))
        result=str(input_line.get("resultado_inspecao") or "A").upper()
        if result not in {'A','AC','D'} or min(physical,approved,conditional,rejected)<0 or approved+conditional+rejected>physical: raise ValueError("Quantidades ou resultado de inspecao invalidos.")
        if po_line and physical > Decimal(str(po_line['quantidade_pedida']))-Decimal(str(po_line['quantidade_recebida'])) and not data.get('allow_overreceipt'): raise ValueError("Recebimento acima do saldo pendente.")
        sku_id, sku_code = _resolve_sku_reference(
            db,
            input_line.get('sku_id') or (po_line and po_line['sku_id']),
            input_line.get('sku_codigo') or (po_line and po_line['sku_codigo']),
        )
        if po_line and po_line.get("sku_id") and sku_id:
            if int(po_line["sku_id"]) != int(sku_id):
                raise ValueError("SKU recebido nao corresponde a linha da O.C.")
        if (
            po_line
            and not po_line.get("sku_id")
            and po_line.get("sku_codigo")
            and sku_code
            and str(po_line["sku_codigo"]).strip().upper()
            != str(sku_code).strip().upper()
        ):
            raise ValueError("SKU recebido nao corresponde ao codigo da linha da O.C.")
        if approved and not sku_id: raise ValueError("SKU e obrigatorio para quantidade aprovada em estoque.")
        line_id=_id(); pending=(Decimal(str(po_line['quantidade_pedida']))-Decimal(str(po_line['quantidade_recebida']))) if po_line else Decimal('0')
        db.execute(text("""insert into erp_goods_receipt_lines(id,goods_receipt_id,purchase_order_line_id,sku_id,sku_codigo,quantidade_esperada,quantidade_recebida_anterior,saldo_pendente,quantidade_fisica,quantidade_aprovada,quantidade_condicional,quantidade_rejeitada,valor_unitario_pedido,valor_unitario_real,certificado_exigido,certificado_apresentado,validade_certificado,resultado_inspecao,justificativa_divergencia) values(:id,:receipt,:po_line,:sku_id,:sku_code,:expected,:previous,:pending,:physical,:approved,:conditional,:rejected,:ordered_value,:actual_value,:cert_required,:cert_presented,:cert_expiry,:result,:reason)"""),{"id":line_id,"receipt":receipt_id,"po_line":po_line_id,"sku_id":sku_id,"sku_code":sku_code,"expected":po_line['quantidade_pedida'] if po_line else 0,"previous":po_line['quantidade_recebida'] if po_line else 0,"pending":pending,"physical":physical,"approved":approved,"conditional":conditional,"rejected":rejected,"ordered_value":po_line['valor_unitario_pedido'] if po_line else 0,"actual_value":to_decimal(input_line.get('valor_unitario_real')),"cert_required":bool(input_line.get('certificado_exigido')),"cert_presented":bool(input_line.get('certificado_apresentado')),"cert_expiry":input_line.get('validade_certificado') or None,"result":result,"reason":str(input_line.get('justificativa_divergencia') or '')})
        movement=None
        if approved:
            sku=db.get(SKU,int(sku_id))
            movement_key = f"{key}:line:{po_line_id or sku_id}:{line_index}"
            movement=register_movement(db,sku,'ENTRADA',approved,user_id,documento=str(data.get('numero_nf') or ''),observacao=f'Recebimento ERP {receipt_id}',commit=False,source_type='GOODS_RECEIPT',idempotency_key=movement_key)
            movement.source_id=receipt_id; movement.source_line_id=line_id
        link_key = f"{key}:line:{po_line_id or sku_id or line_index}:{line_index}"
        db.execute(text("insert into erp_stock_receipt_links(goods_receipt_line_id,movement_id,quantidade_disponivel,quantidade_quarentena,idempotency_key) values(:line,:movement,:available,:quarantine,:key)"),{"line":line_id,"movement":movement.id if movement else None,"available":approved,"quarantine":conditional,"key":link_key})
        if po_line:
            # Rejeitado/devolvido foi inspecionado fisicamente, mas não atende
            # a quantidade do pedido. AC atende o pedido e permanece bloqueado
            # em quarentena; somente A aumenta o saldo disponível.
            accepted_for_order = approved + conditional
            new_received=Decimal(str(po_line['quantidade_recebida']))+accepted_for_order; line_status='RECEBIDA' if new_received>=Decimal(str(po_line['quantidade_pedida'])) else ('PARCIALMENTE_RECEBIDA' if new_received>0 else 'PENDENTE')
            db.execute(text("update erp_purchase_order_lines set quantidade_recebida=:received,status=:status where id=:id"),{"received":new_received,"status":line_status,"id":po_line_id})
    if po_id:
        state=_row(db.execute(text("""select
            count(*) filter(where status='RECEBIDA') as done,
            count(*) filter(where quantidade_recebida > 0) as with_receipt,
            count(*) as total
            from erp_purchase_order_lines where purchase_order_id=:id"""),{"id":po_id}).first())
        status=(
            'RECEBIDA' if state['done']==state['total']
            else ('PARCIALMENTE_RECEBIDA' if state['with_receipt'] else 'EMITIDA')
        )
        db.execute(text("update erp_purchase_orders set status=:status,version=version+1,updated_at=now() where id=:id"),{"status":status,"id":po_id})
    db.execute(text("insert into erp_audit_events(entity_type,entity_id,action,actor,origin,after_data) values ('GOODS_RECEIPT',:id,'CONFIRMADO',:actor,'ESTOQUE',jsonb_build_object('idempotency_key',cast(:key as text)))"),{"id":receipt_id,"actor":actor,"key":key})
    db.commit(); return {"id":receipt_id,"replayed":False}


def cancel_purchase_order(db, order_id, actor, reason):
    order=_row(db.execute(text('select * from erp_purchase_orders where id=:id for update'),{'id':order_id}).first())
    if not order: raise ValueError('O.C. nao encontrada.')
    received=db.execute(text('select count(*) from erp_goods_receipts where purchase_order_id=:id and status=\'CONFIRMADO\''),{'id':order_id}).scalar_one()
    if received: raise ValueError('O.C. com recebimento confirmado deve ser estornada antes do cancelamento.')
    db.execute(text("update erp_purchase_orders set status='CANCELADA',updated_at=now(),version=version+1 where id=:id"),{'id':order_id})
    db.execute(text("update erp_purchase_order_lines set status='CANCELADA' where purchase_order_id=:id"),{'id':order_id})
    db.execute(text("insert into erp_audit_events(entity_type,entity_id,action,actor,reason) values('PURCHASE_ORDER',:id,'CANCELADA',:actor,:reason)"),{'id':order_id,'actor':actor,'reason':reason or ''})
    db.commit()


def cancel_purchase_order_by_idempotency_key(db, key, actor, reason):
    order = _row(db.execute(text("select id from erp_purchase_orders where idempotency_key=:key"), {"key": str(key or "").strip()}).first())
    if not order:
        raise ValueError("O.C. integrada nao encontrada para cancelamento.")
    cancel_purchase_order(db, str(order["id"]), actor, reason)
    return {"id": str(order["id"])}


def close_purchase_order_technical(db, order_id, actor, reason):
    """Record PCP/Compras completion without touching receipts or stock."""
    order = _row(db.execute(text(
        "select id,status,technical_status from erp_purchase_orders where id=:id for update"
    ), {"id": str(order_id or "").strip()}).first())
    if not order:
        raise ValueError("O.C. integrada nao encontrada para conclusao.")
    if order["status"] == "CANCELADA":
        raise ValueError("O.C. cancelada nao pode receber conclusao tecnica.")
    order_id = str(order["id"])
    if order["technical_status"] == "CONCLUIDA":
        return {"id": order_id, "status": order["status"], "technical_status": "CONCLUIDA", "replayed": True}
    db.execute(text("""
        update erp_purchase_orders
           set technical_status='CONCLUIDA',technical_closed_at=now(),
               technical_closed_by=:actor,technical_close_reason=:reason,
               updated_at=now(),version=version+1
         where id=:id
    """), {"id": order_id, "actor": actor, "reason": reason or ""})
    db.execute(text("""insert into erp_audit_events(entity_type,entity_id,action,actor,reason,origin)
        values ('PURCHASE_ORDER',:id,'CONCLUSAO_TECNICA',:actor,:reason,'SUPRIMENTOS')"""),
        {"id": order_id, "actor": actor, "reason": reason or ""})
    db.commit()
    return {
        "id": order_id, "status": order["status"],
        "technical_status": "CONCLUIDA", "replayed": False,
    }


def close_purchase_order_financial(db, order_id, actor, reason):
    """Record financial completion only after Estoque received every line."""
    order = _row(db.execute(text("""
        select id,status,financial_status
          from erp_purchase_orders
         where id=:id
         for update
    """), {"id": str(order_id or "").strip()}).first())
    if not order:
        raise ValueError("O.C. integrada nao encontrada para conclusao financeira.")
    if order["status"] != "RECEBIDA":
        raise ValueError(
            "Conclusao financeira bloqueada: o Almoxarifado precisa concluir "
            "o recebimento total no Modulo Estoque."
        )
    order_id = str(order["id"])
    if order["financial_status"] == "CONCLUIDA":
        return {"id": order_id, "status": order["status"], "financial_status": "CONCLUIDA", "replayed": True}
    db.execute(text("""
        update erp_purchase_orders
           set financial_status='CONCLUIDA',financial_closed_at=now(),
               financial_closed_by=:actor,financial_close_reason=:reason,
               updated_at=now(),version=version+1
         where id=:id
    """), {"id": order_id, "actor": actor, "reason": reason or ""})
    db.execute(text("""insert into erp_audit_events(entity_type,entity_id,action,actor,reason,origin)
        values ('PURCHASE_ORDER',:id,'CONCLUSAO_FINANCEIRA',:actor,:reason,'SUPRIMENTOS')"""),
        {"id": order_id, "actor": actor, "reason": reason or ""})
    db.commit()
    return {
        "id": order_id, "status": order["status"],
        "financial_status": "CONCLUIDA", "replayed": False,
    }


def close_purchase_order_by_idempotency_key(db, key, actor, reason):
    order = _row(db.execute(text(
        "select id from erp_purchase_orders where idempotency_key=:key"
    ), {"key": str(key or "").strip()}).first())
    if not order:
        raise ValueError("O.C. integrada nao encontrada para conclusao.")
    return close_purchase_order_technical(db, str(order["id"]), actor, reason)


def reverse_receipt(db, receipt_id, actor, user_id, reason):
    receipt=_row(db.execute(text('select * from erp_goods_receipts where id=:id for update'),{'id':receipt_id}).first())
    if not receipt: raise ValueError('Recebimento nao encontrado.')
    if receipt['status']!='CONFIRMADO': raise ValueError('Somente recebimento confirmado pode ser estornado.')
    if receipt["purchase_order_id"]:
        financial_entries = db.execute(text("""
            select count(*) from erp_purchase_order_financial_entries
             where purchase_order_id=:id
        """), {"id": receipt["purchase_order_id"]}).scalar_one()
        if financial_entries:
            raise ValueError(
                "Recebimento possui baixa financeira vinculada. "
                "Estorne/corrija a baixa financeira antes de estornar o recebimento físico."
            )
    links=db.execute(text("select l.*,x.movement_id,x.quantidade_disponivel from erp_goods_receipt_lines l join erp_stock_receipt_links x on x.goods_receipt_line_id=l.id where l.goods_receipt_id=:id"),{'id':receipt_id}).all()
    for raw in links:
        line=dict(raw._mapping)
        if line['movement_id'] and Decimal(str(line['quantidade_disponivel'])):
            sku=db.get(SKU,int(line['sku_id'])); movement=register_movement(db,sku,'AJUSTE',-Decimal(str(line['quantidade_disponivel'])),user_id,documento=receipt['numero_nf'],observacao=f'Estorno ERP {receipt_id}: {reason}',commit=False,related_movement_id=line['movement_id'])
            movement.source_type='GOODS_RECEIPT_REVERSAL'; movement.source_id=receipt_id; movement.source_line_id=line['id']; movement.idempotency_key=f'reversal:{receipt_id}:{line["id"]}'
        if line['purchase_order_line_id']:
            db.execute(text("""
                update erp_purchase_order_lines
                   set quantidade_recebida=greatest(0,quantidade_recebida-:qty),
                       status=case
                           when greatest(0,quantidade_recebida-:qty) >= quantidade_pedida then 'RECEBIDA'
                           when greatest(0,quantidade_recebida-:qty) > 0 then 'PARCIALMENTE_RECEBIDA'
                           else 'PENDENTE'
                       end
                 where id=:id
            """),{
                'qty': (
                    Decimal(str(line['quantidade_aprovada']))
                    + Decimal(str(line['quantidade_condicional']))
                ),
                'id': line['purchase_order_line_id'],
            })
    db.execute(text("update erp_goods_receipts set status='ESTORNADO',reversed_at=now(),motivo_excecao=:reason where id=:id"),{'id':receipt_id,'reason':reason or ''})
    if receipt['purchase_order_id']:
        order_before = _row(db.execute(text("""
            select financial_status
              from erp_purchase_orders
             where id=:id
             for update
        """), {'id': receipt['purchase_order_id']}).first())
        state = _row(db.execute(text("""
            select count(*) filter(where status='RECEBIDA') as recebidas,
                   count(*) filter(where quantidade_recebida > 0) as com_recebimento,
                   count(*) as total
              from erp_purchase_order_lines
             where purchase_order_id=:id
        """), {'id': receipt['purchase_order_id']}).first())
        if state['recebidas'] == state['total']:
            order_status = 'RECEBIDA'
        elif state['com_recebimento']:
            order_status = 'PARCIALMENTE_RECEBIDA'
        else:
            order_status = 'EMITIDA'
        db.execute(text("""
            update erp_purchase_orders
               set status=:status,
                   financial_status=case when :status='RECEBIDA' then financial_status else 'PENDENTE' end,
                   financial_closed_at=case when :status='RECEBIDA' then financial_closed_at else null end,
                   financial_closed_by=case when :status='RECEBIDA' then financial_closed_by else null end,
                   financial_close_reason=case when :status='RECEBIDA' then financial_close_reason else '' end,
                   updated_at=now(),version=version+1
             where id=:id
        """), {'id': receipt['purchase_order_id'], 'status': order_status})
        if (
            order_status != 'RECEBIDA'
            and order_before
            and order_before['financial_status'] == 'CONCLUIDA'
        ):
            db.execute(text("""
                insert into erp_audit_events(
                    entity_type,entity_id,action,actor,reason,origin
                ) values (
                    'PURCHASE_ORDER',:id,'REABERTURA_FINANCEIRA_POR_ESTORNO',
                    :actor,:reason,'ESTOQUE'
                )
            """), {
                'id': receipt['purchase_order_id'],
                'actor': actor,
                'reason': reason or '',
            })
    db.execute(text("insert into erp_audit_events(entity_type,entity_id,action,actor,reason) values('GOODS_RECEIPT',:id,'ESTORNADO',:actor,:reason)"),{'id':receipt_id,'actor':actor,'reason':reason or ''})
    db.commit()
