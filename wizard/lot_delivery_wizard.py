# -*- coding: utf-8 -*-
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_is_zero


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

        If the move already has stock.move.line records, pre-check the
        corresponding lots with their current quantity (edit-after-validate
        flow). FIFO pre-tick only applies to uncovered remaining demand.
        """
        Quant = self.env['stock.quant']
        quants = Quant.search([
            ('product_id', '=', move.product_id.id),
            ('location_id', '=', move.location_id.id),
            ('lot_id', '!=', False),
            ('quantity', '>', 0),
        ])

        reserved_info = self._collect_reservations(move)
        existing_selection = self._collect_existing_selection(move)

        per_lot = defaultdict(lambda: {'qty': 0.0, 'lot': None})
        for q in quants:
            per_lot[q.lot_id.id]['qty'] += q.quantity
            per_lot[q.lot_id.id]['lot'] = q.lot_id

        # Include lots that have existing move.lines even if their quant
        # went to 0 (edge case: lot consumed elsewhere since selection)
        for lot_id, qty in existing_selection.items():
            if lot_id in per_lot:
                continue
            lot = self.env['stock.lot'].browse(lot_id)
            if lot.exists():
                per_lot[lot_id]['qty'] = 0.0
                per_lot[lot_id]['lot'] = lot

        rounding = move.product_id.uom_id.rounding

        rows = []
        for lot_id, data in per_lot.items():
            lot = data['lot']
            reserved = reserved_info.get(lot_id, {'qty': 0.0, 'docs': set()})
            # Reserved_info already excludes current move's own lines.
            # So qty_free = total_at_location - reserved_elsewhere.
            qty_free = data['qty'] - reserved['qty']
            already_picked = existing_selection.get(lot_id, 0.0)
            # Show lot if: has free qty OR already selected on current move
            if (float_compare(qty_free, 0.0,
                              precision_rounding=rounding) <= 0
                    and float_compare(already_picked, 0.0,
                                      precision_rounding=rounding) <= 0):
                continue
            rows.append({
                'lot': lot,
                'qty_available': data['qty'],
                'qty_reserved': reserved['qty'],
                'reserved_on': ', '.join(sorted(reserved['docs'])) or '',
                'qty_free': qty_free,
                'already_picked': already_picked,
            })

        rows.sort(key=lambda r: (
            getattr(r['lot'], 'expiration_date', False) or fields.Date.to_date('9999-12-31'),
            r['lot'].create_date or fields.Datetime.now(),
        ))

        # Compute remaining demand after subtracting already-picked qty
        total_already = sum(existing_selection.values())
        remaining = max(move.product_uom_qty - total_already, 0.0)

        commands = []
        for r in rows:
            to_take = 0.0
            to_select = False
            # Case 1: lot already picked -> pre-check with existing qty
            if float_compare(r['already_picked'], 0.0,
                             precision_rounding=rounding) > 0:
                to_take = r['already_picked']
                to_select = True
            # Case 2: FIFO fill for uncovered demand
            elif float_compare(remaining, 0.0,
                               precision_rounding=rounding) > 0:
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

    def _collect_existing_selection(self, move):
        """Return {lot_id: quantity} of already-created stock.move.line
        on the given move. Used to pre-populate the wizard when the user
        re-opens it to edit a previously-validated selection.
        """
        result = defaultdict(float)
        for ml in move.move_line_ids:
            if not ml.lot_id:
                continue
            result[ml.lot_id.id] += ml.quantity
        return result

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
        """Sync stock.move.line on the move with the wizard selection.

        Sync logic (supports edit-after-validate):
        - Lot already picked + still ticked + same qty -> no-op
        - Lot already picked + still ticked + new qty -> UPDATE quantity
        - Lot already picked + unticked / qty 0        -> DELETE line
        - Lot not picked + ticked                      -> CREATE line
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

        total = sum(to_deliver.mapped('qty_to_take'))
        if float_compare(total, self.demand_qty,
                         precision_rounding=rounding) > 0:
            raise UserError(_(
                "Quantite totale selectionnee (%(sel)s) superieure "
                "a la demande (%(dem)s)."
            ) % {'sel': total, 'dem': self.demand_qty})

        move = self.move_id
        MoveLine = self.env['stock.move.line']

        # Target state: {lot_id: qty_to_apply}
        target = {}
        for av in to_deliver:
            take = min(av.qty_to_take, av.qty_free + av.qty_reserved
                       if av.qty_free < 0 else av.qty_to_take)
            # Simpler: take = av.qty_to_take, capped by free qty when > 0
            take = av.qty_to_take
            if av.qty_free > 0:
                take = min(take, av.qty_free + self._own_picked_on_lot(move, av.lot_id.id))
            target[av.lot_id.id] = take

        # Sync pass on existing move.lines
        for ml in move.move_line_ids:
            if not ml.lot_id or float_is_zero(ml.quantity,
                                              precision_rounding=rounding):
                # Empty placeholder or lot-less line -> drop
                ml.unlink()
                continue
            lot_id = ml.lot_id.id
            if lot_id in target:
                new_qty = target.pop(lot_id)
                if float_compare(ml.quantity, new_qty,
                                 precision_rounding=rounding) != 0:
                    ml.quantity = new_qty
                # Keep the line as-is otherwise
            else:
                # Lot no longer selected -> drop the line
                ml.unlink()

        # Remaining entries in target = new lots to create
        for lot_id, qty in target.items():
            if float_compare(qty, 0.0, precision_rounding=rounding) <= 0:
                continue
            MoveLine.create({
                'move_id': move.id,
                'picking_id': move.picking_id.id,
                'product_id': move.product_id.id,
                'product_uom_id': move.product_uom.id,
                'lot_id': lot_id,
                'location_id': move.location_id.id,
                'location_dest_id': move.location_dest_id.id,
                'quantity': qty,
                'company_id': move.company_id.id,
            })

        return {'type': 'ir.actions.act_window_close'}

    def _own_picked_on_lot(self, move, lot_id):
        """Qty already picked on *current* move for a given lot. Used to
        cap qty_to_take without double-counting the user's own reservation.
        """
        total = 0.0
        for ml in move.move_line_ids:
            if ml.lot_id.id == lot_id:
                total += ml.quantity
        return total

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
