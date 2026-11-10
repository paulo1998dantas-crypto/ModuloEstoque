import copy
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ('estoque_app' if (ROOT / 'estoque_app').exists() else 'compras_app')))
from material_export_bom import MaterialBom


class MaterialExportBomTests(unittest.TestCase):
    def setUp(self):
        self.catalog = {
            'CJ': dict(descricao='CJ exemplo', unidade='cj', grupo='30'),
            'PP': dict(descricao='PP exemplo', unidade='pc', grupo='20'),
            'MAT': dict(descricao='Material', unidade='m', grupo='10'),
            'BANK': dict(descricao='BCO MC RECLINAVEL', unidade='pc', grupo='30'),
        }
        self.edges = [
            dict(parent_sku='CJ', component_sku='PP', quantidade=2),
            dict(parent_sku='PP', component_sku='MAT', quantidade='0.8'),
            dict(parent_sku='CJ', component_sku='MAT', quantidade='0.1'),
            dict(parent_sku='CJ', component_sku='BANK', quantidade=3),
            dict(parent_sku='BANK', component_sku='10220272', quantidade=2),
        ]

    def test_nested_phantoms_sum_shared_parts_but_stop_at_supplied_bank(self):
        before = copy.deepcopy((self.catalog, self.edges))
        result = MaterialBom(self.catalog, self.edges).factors('CJ')
        self.assertEqual({'MAT': Decimal('1.7'), 'BANK': Decimal(3)}, result)
        self.assertEqual(before, (self.catalog, self.edges))

    def test_direct_bank_is_atomic_even_with_external_only_bom(self):
        self.assertEqual({'BANK': Decimal(1)}, MaterialBom(self.catalog, self.edges).factors('BANK'))

    def test_insumo_bom_does_not_turn_supplied_item_into_phantom(self):
        self.edges.append(dict(parent_sku='MAT', component_sku='BANK', quantidade=4))
        self.assertEqual({'MAT': Decimal(1)}, MaterialBom(self.catalog, self.edges).factors('MAT'))

    def test_missing_phantom_composition_is_an_actionable_error(self):
        with self.assertRaisesRegex(ValueError, 'PP sem composição'):
            MaterialBom(self.catalog, []).factors('PP')

    def test_missing_registration_description_and_unit_are_errors(self):
        for item in (None, dict(descricao='', unidade='pc'), dict(descricao='Peça', unidade='')):
            catalog = copy.deepcopy(self.catalog)
            if item is None:
                del catalog['MAT']
            else:
                catalog['MAT'] = item
            with self.subTest(item=item), self.assertRaises(ValueError):
                MaterialBom(catalog, self.edges).factors('CJ')

    def test_zero_negative_nonfinite_and_invalid_factors_are_errors(self):
        for value in (0, -1, 'NaN', 'Infinity', None, 'inválida'):
            edges = [dict(parent_sku='CJ', component_sku='MAT', quantidade=value)]
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'Quantidade inválida'):
                MaterialBom(self.catalog, edges).factors('CJ')

    def test_cycle_blocks_export(self):
        edges = [dict(parent_sku='CJ', component_sku='PP', quantidade=1),
                 dict(parent_sku='PP', component_sku='CJ', quantidade=1)]
        with self.assertRaisesRegex(ValueError, 'cíclica'):
            MaterialBom(self.catalog, edges).factors('CJ')

    def test_external_only_phantom_does_not_silently_reappear_as_material(self):
        with self.assertRaisesRegex(ValueError, 'sem peças internas válidas'):
            MaterialBom(self.catalog, [dict(parent_sku='CJ', component_sku='10220272', quantidade=1)]).factors('CJ')

    def test_inactive_direct_item_remains_but_inactive_component_is_blocked_even_when_cached(self):
        self.catalog['MAT']['active'] = False
        projection = MaterialBom(self.catalog, self.edges)
        self.assertEqual({'MAT': Decimal(1)}, projection.factors('MAT'))
        with self.assertRaisesRegex(ValueError, 'inativo'):
            projection.factors('CJ')

    def test_all_five_salt_glass_sets_expand_to_real_pieces(self):
        expected = {
            '30120029': ['10120122', '10120123', '10120124', '10120125', '10120126', '10120127'],
            '30120030': ['10120128', '10120129', '10120130', '10120131', '10120122', '10120123', '10120126', '10120127'],
            '30120031': ['10120136', '10120137', '10120138', '10120139', '10120140', '10120141'],
            '30120032': ['10120142', '10120143', '10120144', '10120145', '10120146', '10120147'],
            '30120033': ['10120148', '10120149', '10120150', '10120151', '10120143', '10120142', '10120146', '10120147'],
        }
        catalog, edges = {}, []
        for parent, leaves in expected.items():
            catalog[parent] = dict(descricao='CJ VIDRO SALT', unidade='cj', grupo='30')
            for leaf in leaves:
                catalog[leaf] = dict(descricao='VIDRO SALT ' + leaf, unidade='pc', grupo='10')
                edges.append(dict(parent_sku=parent, component_sku=leaf, quantidade=1))
        projection = MaterialBom(catalog, edges)
        for parent, leaves in expected.items():
            self.assertEqual({leaf: Decimal(1) for leaf in leaves}, projection.factors(parent))


if __name__ == '__main__':
    unittest.main()
