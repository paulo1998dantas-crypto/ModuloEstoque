"""Read-only, recursive component projection for the pending requests export."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation


def is_assembly(item, code):
    group = str((item or {}).get("grupo") or "").split("-", 1)[0].strip()
    return (group or (code[:2] if len(code) == 8 and code.isdigit() else "")) in {"20", "30"}


def positive(value, label):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0:
            raise InvalidOperation()
        return number
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"Quantidade inválida na composição: {label}.")


def load_graph(db, roots):
    """Use the current Cadastro graph online; local BOM only in local mode."""
    from services import cadastro_supabase_service as cadastro
    from models import SKU, BomComponent
    catalog, bom = {}, []
    pending, seen = set(roots), set()
    online = cadastro.enabled()
    try:
        while pending:
            codes = sorted(pending - seen)
            if not codes:
                break
            seen.update(codes)
            for start in range(0, len(codes), 100):
                batch = codes[start:start+100]
                if online:
                    def filter_codes(column):
                        quoted = ["\""+code.replace("\\", "\\\\").replace('"', '\\"')+"\"" for code in batch]
                        return [(column, "in.("+",".join(quoted)+")")]
                    records = cadastro._all_rows(cadastro.REGISTRATIONS_TABLE,
                        "sku,descricao_primaria,descricao_secundaria,unidade,ativo,form_values,field_values",
                        "sku.asc", filters=filter_codes("sku"))
                    for record in records:
                        item = cadastro._row_to_sku_data(record)
                        if item["sku"] in catalog:
                            raise ValueError(f"SKU duplicado no Cadastro: {item['sku']}.")
                        catalog[item["sku"]] = item
                    edges = cadastro._all_rows(cadastro.BOM_COMPONENTS_TABLE,
                        "parent_sku,component_sku,component_descricao,unidade,quantidade,ordem",
                        "parent_sku.asc,ordem.asc,id.asc", filters=filter_codes("parent_sku"))
                else:
                    records = db.query(SKU).filter(SKU.sku.in_(batch)).all()
                    ids = {item.id: item.sku for item in records}
                    for item in records:
                        catalog[item.sku] = {"sku":item.sku, "descricao":item.descricao,
                            "unidade":item.unidade, "grupo":item.grupo, "active":item.active}
                    edges = [{"parent_sku":ids[edge.item_sku_id],
                        "component_sku":edge.component_sku.sku if edge.component_sku else "",
                        "quantidade":edge.quantidade, "unidade":edge.unidade,
                        "component_descricao":edge.descricao}
                        for edge in db.query(BomComponent).filter(
                            BomComponent.item_sku_id.in_(ids)).order_by(BomComponent.id)]
                bom.extend(edge for edge in edges if is_assembly(
                    catalog.get(edge["parent_sku"]), edge["parent_sku"]))
            pending = {str(edge.get("component_sku") or "").strip() for edge in bom} - seen
            if len(seen) > 10000:
                raise ValueError("Composição excede o limite seguro de consulta de 10.000 SKUs.")
        return catalog, bom
    except cadastro.CadastroSupabaseError as exc:
        raise ValueError("Não foi possível consultar a B.O.M. vigente no Cadastro; exportação interrompida para evitar dados incompletos.") from exc


def explode_requests(items, catalog, bom):
    """One row per request/leaf; never merge different requests or duplicate parents."""
    children = defaultdict(list)
    for edge in bom:
        children[str(edge["parent_sku"])].append(edge)
    memo = {}

    def leaves(code, ancestors=()):
        if code in ancestors:
            raise ValueError("B.O.M. cíclica: "+" > ".join((*ancestors,code)))
        if len(ancestors) >= 64:
            raise ValueError(f"B.O.M. excede 64 níveis para {code}.")
        if code in memo:
            return memo[code]
        item = catalog.get(code)
        if item is None:
            raise ValueError(f"SKU {code} não encontrado no Cadastro; composição não pode ser confirmada.")
        edges = children.get(code, []) if is_assembly(item, code) else []
        if not edges:
            result = {code:Decimal(1)}
        else:
            result = {}
            for edge in edges:
                child = str(edge.get("component_sku") or "").strip()
                factor = positive(edge.get("quantidade"), code+" / "+child)
                for leaf, amount in leaves(child, (*ancestors,code)).items():
                    result[leaf] = result.get(leaf, Decimal(0)) + factor*amount
        memo[code] = result
        return result

    output = []
    for original in items:
        code = str(original["sku_codigo"])
        # Plain historical SKUs without a registration still remain traceable.
        factors = leaves(code) if code in catalog or is_assembly(None,code) else {code:Decimal(1)}
        quantity = positive(original["quantity"], code)
        exploded = code not in factors
        for leaf, factor in factors.items():
            row = dict(original)
            item = catalog.get(leaf, {})
            row.update(sku_solicitado=code, descricao_solicitada=original.get("descricao"),
                unidade_solicitada=original.get("unidade"),
                quantidade_solicitada=quantity, fator_bom=factor,
                composicao_exportacao="COMPONENTE B.O.M." if exploded else
                    "CJ/PP SEM B.O.M." if is_assembly(item,code) else "ITEM DIRETO",
                cadastro_componente="INATIVO" if item.get("active") is False else
                    "ATIVO" if item else "SEM CADASTRO")
            if exploded:
                row.update(sku_codigo=leaf, descricao=item.get("descricao") or leaf,
                           unidade=item.get("unidade") or "UN", quantity=quantity*factor)
                if row.get("anticipation_pending_quantity") is not None:
                    row["anticipation_pending_quantity"] = Decimal(str(row["anticipation_pending_quantity"]))*factor
            output.append(row)
    return output


def export_components(db, items):
    if not items:
        return []
    catalog, bom = load_graph(db, {row["sku_codigo"] for row in items})
    return explode_requests(items, catalog, bom)
