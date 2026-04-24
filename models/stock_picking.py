# -*- coding: utf-8 -*-
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from odoo import _, api, models
from odoo.exceptions import ValidationError


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
