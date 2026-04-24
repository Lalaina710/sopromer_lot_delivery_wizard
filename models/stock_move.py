# -*- coding: utf-8 -*-
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare


class StockMove(models.Model):
    _inherit = 'stock.move'

    stock_warning_level = fields.Selection(
        [
            ('none', 'N/A'),
            ('ok', 'OK'),
            ('warning', 'Limite'),
            ('danger', 'Insuffisant'),
        ],
        compute='_compute_stock_warning_level',
        string='Dispo',
        store=False,
        help="Indicateur visuel de la disponibilite reelle du produit "
             "a l'emplacement source du mouvement.",
    )
    stock_warning_label = fields.Char(
        compute='_compute_stock_warning_level',
        string='Dispo libre',
        store=False,
    )

    @api.depends('product_id', 'location_id', 'product_uom_qty', 'state',
                 'picking_type_id.code')
    def _compute_stock_warning_level(self):
        Quant = self.env['stock.quant']
        for move in self:
            move.stock_warning_level = 'none'
            move.stock_warning_label = ''
            if (not move.product_id or not move.location_id
                    or move.state in ('done', 'cancel')
                    or move.picking_type_id.code != 'outgoing'):
                continue
            quants = Quant.search([
                ('product_id', '=', move.product_id.id),
                ('location_id', 'child_of', move.location_id.id),
            ])
            total_qty = sum(quants.mapped('quantity'))
            total_reserved = sum(quants.mapped('reserved_quantity'))
            free = total_qty - total_reserved
            demand = move.product_uom_qty or 0.0
            move.stock_warning_label = (
                "%.3f / %.3f" % (free, demand) if demand else "%.3f" % free
            )
            if demand <= 0:
                move.stock_warning_level = 'none'
            elif free < demand:
                move.stock_warning_level = 'danger'
            elif free < demand * 1.2:
                move.stock_warning_level = 'warning'
            else:
                move.stock_warning_level = 'ok'

    @api.onchange('product_uom_qty')
    def _onchange_product_uom_qty_warn_lots(self):
        """Warn when user edits demand on a move with existing lot lines."""
        if not self.move_line_ids or not self.product_uom:
            return
        if self.product_id.tracking not in ('lot', 'serial'):
            return
        lines_with_lot = self.move_line_ids.filtered('lot_id')
        if not lines_with_lot:
            return
        total_lines = sum(lines_with_lot.mapped('quantity'))
        rounding = self.product_uom.rounding
        if float_compare(total_lines, self.product_uom_qty,
                         precision_rounding=rounding) == 0:
            return
        return {
            'warning': {
                'title': _("Lots a reselectionner"),
                'message': _(
                    "Vous avez modifie la quantite demandee mais des lots "
                    "sont deja selectionnes (total actuel : %(sel)s).\n\n"
                    "La validation du BL sera bloquee tant que ces lots "
                    "ne sont pas resaisies.\n\n"
                    "Action requise :\n"
                    "1. Menu sandwich ≡ sur la ligne -> Detail des operations\n"
                    "2. Supprimez toutes les lignes de lots\n"
                    "3. Relancez l'Assistant lots (baguette magique)"
                ) % {'sel': total_lines},
            }
        }

    def _sopromer_is_in_scope_for_check(self):
        """Return True if this move must be validated by the overflow
        check (outgoing + tracked + active state + has lines)."""
        self.ensure_one()
        if self.state in ('done', 'cancel', 'draft'):
            return False
        if self.picking_type_id.code != 'outgoing':
            return False
        if self.product_id.tracking not in ('lot', 'serial'):
            return False
        if not self.move_line_ids:
            return False
        return True

    def _sopromer_raise_exceeds_demand(self, total):
        """Raise a ValidationError explaining the overflow."""
        self.ensure_one()
        raise ValidationError(_(
            "Produit '%(prod)s' : la somme des quantites des "
            "lots selectionnes (%(sel)s %(uom)s) depasse la "
            "quantite demandee (%(dem)s %(uom)s).\n\n"
            "Ajustez les quantites ou supprimez des lots via le "
            "popup 'Detail des operations' (menu ≡)."
        ) % {
            'prod': self.product_id.display_name,
            'sel': total,
            'dem': self.product_uom_qty,
            'uom': self.product_uom.name,
        })

    def _sopromer_check_exceeds_demand(self):
        """Raise if sum(move_line.quantity) > product_uom_qty on an
        outgoing tracked move (in assigned/partially_available state).

        Strict check used from the create() hook: any new line can only
        increase the total, so the overflow is always a real violation.

        NOT on unlink so the user can delete lines freely to restore
        coherence.
        """
        for move in self:
            if not move._sopromer_is_in_scope_for_check():
                continue
            total = sum(move.move_line_ids.mapped('quantity'))
            rounding = move.product_uom.rounding
            if float_compare(total, move.product_uom_qty,
                             precision_rounding=rounding) > 0:
                move._sopromer_raise_exceeds_demand(total)

    def _sopromer_check_exceeds_demand_on_write(self, old_totals):
        """Raise only when a write() introduces a NEW overflow.

        Rules (agent: odoo-backend, fix v18.0.1.6.2):
        - Allow if new_total <= demand (coherent state).
        - Allow if new_total > demand but new_total <= old_total
          (same or reduced overflow — covers Odoo adjusting lines when
          the demand is lowered, lets the user fix the state).
        - Block only if new_total > demand AND new_total > old_total
          (caller actively worsens an already-incoherent state).

        :param old_totals: dict {move_id: old_total_qty} captured BEFORE
            super().write() was called.
        """
        for move in self:
            if not move._sopromer_is_in_scope_for_check():
                continue
            new_total = sum(move.move_line_ids.mapped('quantity'))
            rounding = move.product_uom.rounding
            if float_compare(new_total, move.product_uom_qty,
                             precision_rounding=rounding) <= 0:
                # new state is coherent, nothing to do
                continue
            old_total = old_totals.get(move.id, 0.0)
            if float_compare(new_total, old_total,
                             precision_rounding=rounding) <= 0:
                # total did not grow: same overflow or reduction, allow
                # the user (or Odoo internals) to keep restoring state
                continue
            move._sopromer_raise_exceeds_demand(new_total)

    def action_open_lot_wizard(self):
        """Open the SOPROMER lot delivery wizard for the current move."""
        self.ensure_one()
        if self.picking_type_id.code != 'outgoing':
            raise UserError(_(
                "L'assistant de selection des lots n'est disponible "
                "que sur les BL de vente (sorties)."
            ))
        if self.product_id.tracking not in ('lot', 'serial'):
            raise UserError(_(
                "Le produit %s n'est pas suivi par lot/numero de serie."
            ) % self.product_id.display_name)
        if self.state in ('done', 'cancel'):
            raise UserError(_(
                "Le mouvement est deja %s - selection figee."
            ) % self.state)

        return {
            'name': _('Assistant selection lots - %s') % self.product_id.display_name,
            'type': 'ir.actions.act_window',
            'res_model': 'sopromer.lot.delivery.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_move_id': self.id,
                'default_picking_id': self.picking_id.id,
            },
        }


class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        # Strict check: a new line can only increase the total.
        lines.mapped('move_id')._sopromer_check_exceeds_demand()
        return lines

    def write(self, vals):
        # agent: odoo-backend - fix v18.0.1.6.2
        # Only relevant field changes may alter the line total or move
        # membership. For anything else, skip the capture/check entirely
        # to keep writes cheap.
        relevant = 'quantity' in vals or 'lot_id' in vals or 'move_id' in vals
        if not relevant:
            return super().write(vals)

        # Capture the "before" total per move BEFORE super().write() so
        # we can distinguish a user/Odoo reducing an existing overflow
        # (allowed) from a write that actively worsens it (blocked).
        # Include both the current moves of self and any move the lines
        # may be moved away from (if move_id changes in vals).
        moves_before = self.mapped('move_id')
        old_totals = {
            move.id: sum(move.move_line_ids.mapped('quantity'))
            for move in moves_before
        }

        result = super().write(vals)

        # After write, check the union of old and new moves: a line may
        # have been reassigned (move_id in vals), shifting qty between
        # two moves. Missing moves default to old_total=0.0 inside the
        # helper, i.e. strict check - correct for a freshly-impacted
        # move that had no lines (or no captured value) before.
        moves_to_check = moves_before | self.mapped('move_id')
        moves_to_check._sopromer_check_exceeds_demand_on_write(old_totals)
        return result
