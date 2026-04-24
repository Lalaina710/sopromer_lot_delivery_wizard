# -*- coding: utf-8 -*-
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from odoo import _, api, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    @api.onchange('picking_type_id')
    def _onchange_picking_type_warn_outgoing(self):
        """Warn user when changing picking_type on a sale-linked picking
        to something other than an outgoing type."""
        if not self.picking_type_id:
            return
        if not self._is_sale_context():
            return
        if self.picking_type_id.code != 'outgoing':
            return {
                'warning': {
                    'title': _("Type d'operation non conforme"),
                    'message': _(
                        "Ce transfert est lie a une commande de vente. "
                        "Seuls les types 'Bon de livraison' (sortant) sont "
                        "autorises.\n\n"
                        "Type selectionne : %(type)s (code: %(code)s)\n\n"
                        "Veuillez choisir un type 'Bon de livraison'."
                    ) % {
                        'type': self.picking_type_id.display_name,
                        'code': self.picking_type_id.code,
                    },
                }
            }

    @api.constrains('picking_type_id')
    def _check_sale_picking_type_outgoing(self):
        """Block save if a sale-linked picking has a non-outgoing type.

        Last line of defense if user ignored the onchange warning.
        """
        for picking in self:
            if not picking._is_sale_context():
                continue
            if picking.picking_type_id.code != 'outgoing':
                raise ValidationError(_(
                    "Transfert lie a une vente : seuls les types "
                    "'Bon de livraison' (sortant) sont autorises.\n"
                    "Type actuel : %(type)s (code: %(code)s)."
                ) % {
                    'type': picking.picking_type_id.display_name,
                    'code': picking.picking_type_id.code,
                })

    def _is_sale_context(self):
        """A picking is sale-related if it has sale_id set or its
        group/origin traces back to a sale order."""
        self.ensure_one()
        if self.sale_id:
            return True
        if self.group_id and self.group_id.sale_ids:
            return True
        return False

    def button_validate(self):
        """Block validation if any outgoing tracked move has an
        inconsistency between the demanded qty and the total lot-selected
        qty (sum of move_line.quantity).

        Typical case blocked: user edits picking demand AFTER lots were
        selected via the wizard, then tries to validate -> would deliver
        a wrong qty (over or under) silently. We force a reset.
        """
        for picking in self:
            if picking.picking_type_id.code != 'outgoing':
                continue
            for move in picking.move_ids:
                if move.state in ('done', 'cancel'):
                    continue
                if move.product_id.tracking not in ('lot', 'serial'):
                    continue
                lines_with_lot = move.move_line_ids.filtered('lot_id')
                if not lines_with_lot:
                    continue
                total_lines = sum(lines_with_lot.mapped('quantity'))
                rounding = move.product_uom.rounding
                if float_compare(total_lines, move.product_uom_qty,
                                 precision_rounding=rounding) != 0:
                    raise UserError(_(
                        "Incoherence sur le produit '%(prod)s' :\n"
                        "  - Quantite demandee : %(demand)s %(uom)s\n"
                        "  - Quantite des lots selectionnes : %(sel)s %(uom)s\n\n"
                        "La demande a ete modifiee apres la selection "
                        "des lots. Avant de valider le BL, nettoyez et "
                        "resaisissez les lots :\n"
                        "  1. Cliquez sur l'icone ≡ (menu sandwich) a "
                        "droite de la ligne produit\n"
                        "  2. Supprimez toutes les lignes de lots\n"
                        "  3. Fermez le popup\n"
                        "  4. Relancez l'Assistant lots (baguette magique)\n"
                        "  5. Revenez valider le BL"
                    ) % {
                        'prod': move.product_id.display_name,
                        'demand': move.product_uom_qty,
                        'sel': total_lines,
                        'uom': move.product_uom.name,
                    })
        return super().button_validate()
