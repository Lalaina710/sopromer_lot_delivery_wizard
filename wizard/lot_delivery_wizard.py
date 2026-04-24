# -*- coding: utf-8 -*-
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare


class SopromerLotDeliveryWizard(models.TransientModel):
    _name = 'sopromer.lot.delivery.wizard'
    _description = 'SOPROMER - Assistant selection lots pour BL de vente'

    picking_id = fields.Many2one(
        'stock.picking',
        string='Bon de livraison',
        readonly=True,
        required=True,
    )
    move_id = fields.Many2one(
        'stock.move',
        string='Mouvement',
        readonly=True,
        required=True,
        ondelete='cascade',
    )
    product_id = fields.Many2one(
        'product.product',
        string='Produit',
        readonly=True,
    )
    uom_id = fields.Many2one(
        'uom.uom',
        string='Unite',
        readonly=True,
    )
    location_id = fields.Many2one(
        'stock.location',
        string='Emplacement source',
        readonly=True,
    )
    demand_qty = fields.Float(
        string='Demande',
        digits='Product Unit of Measure',
        readonly=True,
    )
    total_selected_qty = fields.Float(
        string='Total selectionne',
        compute='_compute_totals',
        digits='Product Unit of Measure',
    )
    remaining_qty = fields.Float(
        string='Restant a prendre',
        compute='_compute_totals',
        digits='Product Unit of Measure',
    )
    available_line_ids = fields.One2many(
        'sopromer.lot.delivery.wizard.available',
        'wizard_id',
        string='Lots disponibles',
    )

    @api.depends('available_line_ids.to_select',
                 'available_line_ids.qty_to_take',
                 'demand_qty')
    def _compute_totals(self):
        for wiz in self:
            total = sum(
                wiz.available_line_ids.filtered('to_select').mapped('qty_to_take')
            )
            wiz.total_selected_qty = total
            wiz.remaining_qty = wiz.demand_qty - total

    # -----------------------------------------------------------------
    # Default get : load quants, compute reservations, FIFO pre-selection
    # -----------------------------------------------------------------
    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        move_id = self.env.context.get('default_move_id') or vals.get('move_id')
        if not move_id:
            return vals

        move = self.env['stock.move'].browse(move_id)
        if not move.exists():
            return vals

        vals['picking_id'] = move.picking_id.id
        vals['product_id'] = move.product_id.id
        vals['uom_id'] = move.product_uom.id
        vals['location_id'] = move.location_id.id
        vals['demand_qty'] = move.product_uom_qty
        vals['available_line_ids'] = self._build_available_lines(move)
        return vals

    def _build_available_lines(self, move):
        """Build (0,0,{}) commands for lots at move.location_id.

        FIFO pre-tick up to demand. Wizard is only ever opened on a
        clean-slate move (no existing move.lines — guarded in the caller),
        so we don't need to reconcile with prior selections.
        """
        Quant = self.env['stock.quant']
        quants = Quant.search([
            ('product_id', '=', move.product_id.id),
            ('location_id', '=', move.location_id.id),
            ('lot_id', '!=', False),
            ('quantity', '>', 0),
        ])

        reserved_info = self._collect_reservations(move)

        per_lot = defaultdict(lambda: {'qty': 0.0, 'lot': None})
        for q in quants:
            per_lot[q.lot_id.id]['qty'] += q.quantity
            per_lot[q.lot_id.id]['lot'] = q.lot_id

        rounding = move.product_id.uom_id.rounding

        rows = []
        for lot_id, data in per_lot.items():
            lot = data['lot']
            reserved = reserved_info.get(lot_id, {'qty': 0.0, 'docs': set()})
            qty_free = data['qty'] - reserved['qty']
            if float_compare(qty_free, 0.0, precision_rounding=rounding) <= 0:
                continue
            rows.append({
                'lot': lot,
                'qty_available': data['qty'],
                'qty_reserved': reserved['qty'],
                'reserved_on': ', '.join(sorted(reserved['docs'])) or '',
                'qty_free': qty_free,
            })

        rows.sort(key=lambda r: (
            getattr(r['lot'], 'expiration_date', False) or fields.Date.to_date('9999-12-31'),
            r['lot'].create_date or fields.Datetime.now(),
        ))

        commands = []
        remaining = move.product_uom_qty
        for r in rows:
            to_take = 0.0
            to_select = False
            if float_compare(remaining, 0.0, precision_rounding=rounding) > 0:
                to_take = min(remaining, r['qty_free'])
                to_select = True
                remaining -= to_take
            commands.append((0, 0, {
                'lot_id': r['lot'].id,
                'qty_available': r['qty_available'],
                'qty_reserved': r['qty_reserved'],
                'reserved_on': r['reserved_on'],
                'qty_to_take': to_take,
                'to_select': to_select,
            }))
        return commands

    def _collect_reservations(self, move):
        """Return {lot_id: {'qty': float, 'docs': set(str)}} of reservations
        held by *other* moves on the same product/location.
        """
        MoveLine = self.env['stock.move.line']
        domain = [
            ('product_id', '=', move.product_id.id),
            ('location_id', '=', move.location_id.id),
            ('lot_id', '!=', False),
            ('state', 'not in', ('done', 'cancel')),
            ('move_id', '!=', move.id),
        ]
        lines = MoveLine.search(domain)
        result = defaultdict(lambda: {'qty': 0.0, 'docs': set()})
        for ml in lines:
            reserved_qty = getattr(ml, 'reserved_uom_qty', False)
            if not reserved_qty:
                reserved_qty = getattr(ml, 'quantity_product_uom', 0.0) or 0.0
            if reserved_qty <= 0:
                continue
            result[ml.lot_id.id]['qty'] += reserved_qty
            doc_name = ml.picking_id.name or ml.move_id.reference or _('Autre')
            result[ml.lot_id.id]['docs'].add(doc_name)
        return result

    # -----------------------------------------------------------------
    # Actions
    # -----------------------------------------------------------------
    def action_validate(self):
        """Replace the move's move_lines with the wizard selection.

        We DELETE all existing move.lines (including Odoo's auto-reserved
        ones and any placeholders) before CREATE-ing the user's picks.
        This makes the wizard the source of truth each time.

        Hard caps applied before any write:
        - Per-lot : qty_to_take <= qty_free
        - Per-move : sum(qty_to_take) <= demand_qty
        """
        self.ensure_one()
        rounding = self.product_id.uom_id.rounding

        to_deliver = self.available_line_ids.filtered(
            lambda av: av.to_select
                and av.lot_id
                and float_compare(av.qty_to_take, 0.0,
                                  precision_rounding=rounding) > 0
        )
        if not to_deliver:
            raise UserError(_("Aucun lot coche avec quantite > 0."))

        for av in to_deliver:
            if float_compare(av.qty_to_take, av.qty_free,
                             precision_rounding=rounding) > 0:
                raise UserError(_(
                    "Lot %(lot)s : quantite demandee (%(qty)s) superieure "
                    "a la quantite libre au stock (%(free)s)."
                ) % {
                    'lot': av.lot_id.name,
                    'qty': av.qty_to_take,
                    'free': av.qty_free,
                })

        total = sum(to_deliver.mapped('qty_to_take'))
        if float_compare(total, self.demand_qty,
                         precision_rounding=rounding) > 0:
            raise UserError(_(
                "Quantite totale selectionnee (%(sel)s) superieure "
                "a la demande (%(dem)s)."
            ) % {'sel': total, 'dem': self.demand_qty})

        move = self.move_id
        MoveLine = self.env['stock.move.line']

        # Wipe clean before re-creating: covers Odoo auto-reservation,
        # previous wizard validations, empty placeholders, all at once.
        move.move_line_ids.unlink()

        for av in to_deliver:
            MoveLine.create({
                'move_id': move.id,
                'picking_id': move.picking_id.id,
                'product_id': move.product_id.id,
                'product_uom_id': move.product_uom.id,
                'lot_id': av.lot_id.id,
                'location_id': move.location_id.id,
                'location_dest_id': move.location_dest_id.id,
                'quantity': av.qty_to_take,
                'company_id': move.company_id.id,
            })

        return {'type': 'ir.actions.act_window_close'}

    def action_cancel(self):
        return {'type': 'ir.actions.act_window_close'}


class SopromerLotDeliveryWizardAvailable(models.TransientModel):
    _name = 'sopromer.lot.delivery.wizard.available'
    _description = 'SOPROMER - Ligne lot disponible (wizard)'
    _order = 'lot_name asc'

    wizard_id = fields.Many2one(
        'sopromer.lot.delivery.wizard',
        required=True,
        ondelete='cascade',
    )
    lot_id = fields.Many2one('stock.lot', string='Lot', required=True)
    lot_name = fields.Char(related='lot_id.name', readonly=True)
    qty_available = fields.Float(
        string='Qte dispo',
        digits='Product Unit of Measure',
        readonly=True,
    )
    qty_reserved = fields.Float(
        string='Deja reserve',
        digits='Product Unit of Measure',
        readonly=True,
    )
    reserved_on = fields.Char(
        string='Reserve sur',
        readonly=True,
    )
    qty_free = fields.Float(
        string='Qte libre',
        compute='_compute_qty_free',
        digits='Product Unit of Measure',
        store=False,
    )
    qty_to_take = fields.Float(
        string='Qte a prendre',
        digits='Product Unit of Measure',
    )
    to_select = fields.Boolean(
        string='Selectionner',
    )
    expiration_date = fields.Date(
        string='Expiration',
        compute='_compute_expiration_date',
        store=False,
        readonly=True,
    )

    @api.depends('qty_available', 'qty_reserved')
    def _compute_qty_free(self):
        for line in self:
            line.qty_free = (line.qty_available or 0.0) - (line.qty_reserved or 0.0)

    @api.depends('lot_id')
    def _compute_expiration_date(self):
        for line in self:
            line.expiration_date = getattr(line.lot_id, 'expiration_date', False) or False
