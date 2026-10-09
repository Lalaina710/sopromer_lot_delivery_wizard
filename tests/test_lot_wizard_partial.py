"""Assistant lots : couverture partielle (TICKET 09102026-03, EF-04/05/06).

Fixtures en quantites brutes (demande 100 ; lots 40 + 30 ; lots 80 + 60 ;
lot 60 + ligne sans lot 40). Les BL de controle sont des sorties simples
(sans BC) : le comportement teste est celui de ce module seul, quel que
soit le module de synchro BC <-> BL installe a cote. Un scenario BC (S4)
verifie l'enchainement avec sale_stock.
"""
from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import Form, TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestLotWizardPartial(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.admin = cls.env.ref('base.user_admin')
        cls.kg = cls.env.ref('uom.product_uom_kgm')
        cls.warehouse = cls.env['stock.warehouse'].create({
            'name': 'WH test assistant lots',
            'code': 'TALW',
            'company_id': cls.company.id,
        })
        cls.stock_loc = cls.warehouse.lot_stock_id
        cls.out_type = cls.warehouse.out_type_id
        cls.customer_loc = cls.out_type.default_location_dest_id
        cls.product = cls.env['product.product'].create({
            'name': 'Poisson suivi par lot (test assistant)',
            'type': 'consu',
            'is_storable': True,
            'tracking': 'lot',
            'uom_id': cls.kg.id,
            'uom_po_id': cls.kg.id,
            'invoice_policy': 'delivery',
        })
        cls.lot_a = cls.env['stock.lot'].create({
            'name': 'SO-TEST-ALW-A',
            'product_id': cls.product.id,
            'company_id': cls.company.id,
        })
        cls.lot_b = cls.env['stock.lot'].create({
            'name': 'SO-TEST-ALW-B',
            'product_id': cls.product.id,
            'company_id': cls.company.id,
        })
        cls.partner = cls.env['res.partner'].create({'name': 'Client assistant lots'})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _stock(self, qty, lot):
        self.env['stock.quant']._update_available_quantity(
            self.product, self.stock_loc, qty, lot_id=lot)

    def _out_picking(self, qty):
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.out_type.id,
            'partner_id': self.partner.id,
            'location_id': self.stock_loc.id,
            'location_dest_id': self.customer_loc.id,
            'move_ids': [Command.create({
                'name': self.product.display_name,
                'product_id': self.product.id,
                'product_uom_qty': qty,
                'product_uom': self.kg.id,
                'location_id': self.stock_loc.id,
                'location_dest_id': self.customer_loc.id,
            })],
        })
        picking.action_confirm()
        return picking, picking.move_ids

    def _run_assistant(self, move, quantities=None):
        """Ouvre l'assistant comme le bouton, ajuste, valide.

        :param quantities: {lot: qty} pour modifier la pre-selection FIFO.
        """
        action = move.with_user(self.admin).action_open_lot_wizard()
        model = self.env[action['res_model']].with_user(self.admin).with_context(action['context'])
        with Form(model) as form:
            if quantities:
                for index in range(len(form.available_line_ids)):
                    with form.available_line_ids.edit(index) as line:
                        lot = self.env['stock.lot'].browse(line.lot_id.id)
                        if lot in quantities:
                            line.to_select = bool(quantities[lot])
                            line.qty_to_take = quantities[lot]
        wizard = form.record
        wizard.action_validate()
        return wizard

    def _validate(self, picking):
        return picking.with_user(self.admin).with_company(self.company).button_validate()

    def _backorder_wizard(self, res):
        self.assertIsInstance(res, dict)
        self.assertEqual(res.get('res_model'), 'stock.backorder.confirmation')
        model = self.env['stock.backorder.confirmation'].with_user(self.admin).with_context(
            res['context'])
        return Form(model).save()

    # ------------------------------------------------------------------
    # EF-04 / EF-06 : couverture partielle acceptee
    # ------------------------------------------------------------------

    def test_ef04_partial_lots_open_native_backorder_screen(self):
        self._stock(40.0, self.lot_a)
        self._stock(30.0, self.lot_b)
        picking, move = self._out_picking(100.0)

        wizard = self._run_assistant(move)

        self.assertAlmostEqual(wizard.total_selected_qty, 70.0, places=3)
        self.assertAlmostEqual(wizard.remaining_qty, 30.0, places=3)
        self.assertEqual(move.move_line_ids.lot_id, self.lot_a | self.lot_b)
        self.assertFalse(move.move_line_ids.filtered(lambda ml: not ml.lot_id))
        self.assertAlmostEqual(move.quantity, 70.0, places=3)

        # Plus de message d'incoherence : l'ecran natif reliquat s'ouvre.
        res = self._validate(picking)
        self.assertNotEqual(picking.state, 'done')
        self._backorder_wizard(res).process()
        self.assertEqual(picking.state, 'done')
        self.assertAlmostEqual(picking.backorder_ids.move_ids.product_uom_qty, 30.0, places=3)

    def test_ef04_partial_lots_no_backorder(self):
        self._stock(60.0, self.lot_a)
        picking, move = self._out_picking(100.0)
        self._run_assistant(move)

        self._backorder_wizard(self._validate(picking)).process_cancel_backorder()

        self.assertEqual(picking.state, 'done')
        self.assertFalse(picking.backorder_ids)
        self.assertAlmostEqual(move.quantity, 60.0, places=3)

    def test_ef10_full_lots_validate_directly(self):
        self._stock(100.0, self.lot_a)
        picking, move = self._out_picking(100.0)
        self._run_assistant(move)

        res = self._validate(picking)

        self.assertNotIsInstance(res, dict)
        self.assertEqual(picking.state, 'done')

    # ------------------------------------------------------------------
    # EF-05 : cas toujours refuses
    # ------------------------------------------------------------------

    def test_ef05_lots_above_demand_refused(self):
        self._stock(80.0, self.lot_a)
        self._stock(60.0, self.lot_b)
        picking, move = self._out_picking(100.0)
        line_b = move.move_line_ids.filtered(lambda ml: ml.lot_id == self.lot_b)
        self.assertAlmostEqual(line_b.quantity, 20.0, places=3)
        line_b.with_user(self.admin).write({'quantity': 40.0})
        self.assertAlmostEqual(move.quantity, 120.0, places=3)

        with self.assertRaises(UserError) as error:
            self._validate(picking)
        message = str(error.exception)
        self.assertIn(picking.name, message)
        self.assertIn('120', message)
        self.assertIn('100', message)
        self.assertNotIn('Supprimez toutes les lignes', message)
        self.assertNotEqual(picking.state, 'done')

        # Sortie : l'assistant ramene a la demande, la validation passe.
        self._run_assistant(move)
        self.assertAlmostEqual(move.quantity, 100.0, places=3)
        self.assertNotIsInstance(self._validate(picking), dict)
        self.assertEqual(picking.state, 'done')

    def test_ef05_line_without_lot_refused_then_assistant_fixes(self):
        """Ligne sans lot (pre-remplissage d'avant 18.0.1.2.0 de la synchro
        BC/BL, ou saisie manuelle) : refus clair, l'assistant donne la sortie
        et ne cree aucune ligne sans lot."""
        self._stock(60.0, self.lot_a)
        picking, move = self._out_picking(100.0)
        self.env['stock.move.line'].create({
            'move_id': move.id,
            'picking_id': picking.id,
            'product_id': self.product.id,
            'product_uom_id': self.kg.id,
            'quantity': 40.0,
            'location_id': self.stock_loc.id,
            'location_dest_id': self.customer_loc.id,
        })
        self.assertAlmostEqual(move.quantity, 100.0, places=3)

        with self.assertRaises(UserError) as error:
            self._validate(picking)
        self.assertIn('sans lot', str(error.exception))
        self.assertIn('Assistant lots', str(error.exception))

        self._run_assistant(move)
        self.assertFalse(move.move_line_ids.filtered(lambda ml: not ml.lot_id))
        self.assertAlmostEqual(move.quantity, 60.0, places=3)

        self._backorder_wizard(self._validate(picking)).process()
        self.assertEqual(picking.state, 'done')
        self.assertAlmostEqual(picking.backorder_ids.move_ids.product_uom_qty, 40.0, places=3)

    def test_assistant_caps_still_enforced(self):
        self._stock(60.0, self.lot_a)
        picking, move = self._out_picking(100.0)
        with self.assertRaises(UserError):
            self._run_assistant(move, {self.lot_a: 70.0})

    # ------------------------------------------------------------------
    # Avertissement a la modification de la demande
    # ------------------------------------------------------------------

    def test_demand_onchange_warns_only_when_lots_exceed(self):
        self._stock(100.0, self.lot_a)
        picking, move = self._out_picking(100.0)

        lower = move.new(origin=move)
        lower.product_uom_qty = 80.0
        warning = lower._onchange_product_uom_qty_warn_lots()
        self.assertTrue(warning and warning.get('warning'))

        higher = move.new(origin=move)
        higher.product_uom_qty = 120.0
        self.assertFalse(higher._onchange_product_uom_qty_warn_lots())

    # ------------------------------------------------------------------
    # S4 : BC 100, deux lots 40 + 30 via l'assistant
    # ------------------------------------------------------------------

    def test_s4_sale_order_two_lots_with_backorder(self):
        self._stock(40.0, self.lot_a)
        self._stock(30.0, self.lot_b)
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'warehouse_id': self.warehouse.id,
            'order_line': [Command.create({
                'product_id': self.product.id,
                'product_uom_qty': 100.0,
                'product_uom': self.kg.id,
                'price_unit': 18400.0,
            })],
        })
        order.action_confirm()
        picking = order.picking_ids
        move = picking.move_ids

        self._run_assistant(move)
        self.assertAlmostEqual(move.quantity, 70.0, places=3)
        self._backorder_wizard(self._validate(picking)).process()

        sol = order.order_line
        self.assertAlmostEqual(sol.qty_delivered, 70.0, places=3)
        self.assertAlmostEqual(sol.product_uom_qty, 100.0, places=3)
        backorder = picking.backorder_ids
        self.assertAlmostEqual(backorder.move_ids.product_uom_qty, 30.0, places=3)
        self.assertEqual(backorder.move_ids.sale_line_id, sol)
