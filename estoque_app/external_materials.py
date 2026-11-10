"""Materials owned by JI but held at the external bank-production supplier.

They remain in the technical BOM, never in an internal purchase/receipt BOM.
Keep this classification identical in Suprimentos and Estoque.
"""
EXTERNAL_FOAM_SKUS = frozenset({
    '10220272', '10220273', '10220274', '10220275', '10220276',
})
EXTERNAL_FABRIC_SKUS = frozenset({'10220269', '10220270', '10280062'})
EXTERNAL_MATERIAL_SKUS = EXTERNAL_FOAM_SKUS | EXTERNAL_FABRIC_SKUS

# PME porta-malas elevados are supplied/controlled as complete assemblies.
SUPPLIED_PME_SKUS = frozenset({'30240037', '30240038', '30240039', '30240103'})


def is_external_material(code):
    return str(code or '').strip().upper() in EXTERNAL_MATERIAL_SKUS


def is_supplied_bank(item):
    """A supplied seat is stocked as a unit; CJ seat kits still expand."""
    if isinstance(item, dict):
        description = item.get('descricao') or item.get('descricao_primaria') or ''
    else:
        description = getattr(item, 'descricao', '')
    return str(description or '').strip().upper().startswith(('BCO ', 'BANCO '))


def is_supplied_unit(item, code=None):
    if code is None:
        code = (item or {}).get('sku') if isinstance(item, dict) else getattr(item, 'sku', '')
    return is_supplied_bank(item) or str(code or '').strip() in SUPPLIED_PME_SKUS
