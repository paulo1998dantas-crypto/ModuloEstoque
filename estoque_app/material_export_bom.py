"""Read-only material projection. Keep identical in Estoque and Suprimentos."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from external_materials import is_external_material, is_supplied_unit


def assembly(item, code):
    if is_supplied_unit(item, code):
        return False
    group = str((item or {}).get('grupo') or '').split('-', 1)[0].strip()
    return (group or (code[:2] if len(code) == 8 and code.isdigit() else '')) in {'20', '30'}


def number(value, label, positive=False):
    try:
        result = Decimal(str(value).replace(',', '.'))
        if not result.is_finite() or result < 0 or (positive and result == 0):
            raise InvalidOperation()
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f'Quantidade inválida: {label}.') from None


class MaterialBom:
    def __init__(self, catalog, edges):
        self.catalog = catalog
        self.children = defaultdict(list)
        self.memo = {}
        for edge in edges:
            self.children[str(edge.get('parent_sku') or '').strip()].append(edge)

    def factors(self, code, ancestors=()):
        code = str(code or '').strip()
        if code in ancestors:
            raise ValueError('B.O.M. cíclica: ' + ' > '.join((*ancestors, code)))
        if len(ancestors) >= 64:
            raise ValueError(f'B.O.M. excede 64 níveis para {code}.')
        item = self.catalog.get(code)
        if item is None:
            raise ValueError(f'SKU {code} não encontrado no Cadastro; exportação interrompida.')
        if not str(item.get('descricao') or '').strip() or not str(item.get('unidade') or '').strip():
            raise ValueError(f'SKU {code} sem descrição ou unidade no Cadastro; exportação interrompida.')
        if ancestors and item.get('active') is False:
            raise ValueError(f'Componente {code} inativo no Cadastro; revise a B.O.M. antes de exportar.')
        if code in self.memo:
            return self.memo[code]
        edges = self.children.get(code) or []
        if not assembly(item, code) or not edges:
            result = {code: Decimal(1)}
        else:
            result = {}
            for edge in edges:
                child = str(edge.get('component_sku') or '').strip()
                factor = number(edge.get('quantidade'), f'{code} / {child}', positive=True)
                if is_external_material(child):
                    continue
                for leaf, amount in self.factors(child, (*ancestors, code)).items():
                    result[leaf] = result.get(leaf, Decimal(0)) + factor * amount
            if not result:
                raise ValueError(f'CJ/PP {code} sem peças internas válidas; revise a composição no Cadastro.')
        self.memo[code] = result
        return result
