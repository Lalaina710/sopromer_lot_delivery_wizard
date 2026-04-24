# -*- coding: utf-8 -*-
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from odoo import _, api, fields, models
from odoo.exceptions import UserError


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
            # Only meaningful for outgoing moves not yet done
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

    def action_open_lot_wizard(self):
        """Open the SOPROMER lot delivery wizard for the current move.

        Guards:
        - Scope: outgoing only, tracked product, not done/cancel
        - Clean-slate: blocks if move already has lot-filled move_lines,
          to force the user through a deliberate reset via the native
          'Detail des operations' popup (avoids stock over-allocation
          from multi-pass edits).
        """
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

        existing = self.move_line_ids.filtered(
            lambda ml: ml.lot_id and ml.quantity > 0
        )
        if existing:
            lot_names = ', '.join(existing.mapped('lot_id.name'))
            raise UserError(_(
                "Des lots sont deja selectionnes sur ce mouvement :\n\n"
                "   %(lots)s\n\n"
                "Pour modifier la selection, procedez ainsi :\n"
                "  1. Cliquez sur l'icone ≡ (menu sandwich) a droite de "
                "la ligne produit dans l'onglet Operations\n"
                "     (ou double-cliquez sur la ligne pour ouvrir le popup "
                "'Detail des operations')\n"
                "  2. Supprimez toutes les lignes de lots (icone corbeille)\n"
                "  3. Fermez le popup\n"
                "  4. Relancez l'Assistant lots ici\n\n"
                "Cette regle evite les sur-allocations de stock quand la "
                "demande ou les lots changent apres une premiere validation."
            ) % {'lots': lot_names})

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
