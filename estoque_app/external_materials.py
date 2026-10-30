"""Materials owned by JI but held at the external bank-production supplier.

They remain in the technical BOM, never in an internal purchase/receipt BOM.
Keep this classification identical in Suprimentos and Estoque.
"""
EXTERNAL_FOAM_SKUS = frozenset({
    '10220272', '10220273', '10220274', '10220275', '10220276',
})
EXTERNAL_FABRIC_SKUS = frozenset({'10220269', '10220270', '10280062'})
EXTERNAL_MATERIAL_SKUS = EXTERNAL_FOAM_SKUS | EXTERNAL_FABRIC_SKUS


def is_external_material(code):
    return str(code or '').strip().upper() in EXTERNAL_MATERIAL_SKUS
