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
        if self.group_id and self.group_id.sale_id:
            return True
        return False

    def _sopromer_lot_selection_errors(self):
        """Controles lots d'un BL sortant avant validation (18.0.1.8.0).

        Pour chaque mouvement suivi par lot / serie non termine :

        - ligne SANS lot avec une quantite -> refus (tracabilite) ;
        - somme des lots > demande -> refus (sur-livraison silencieuse) ;
        - somme des lots < demande -> ACCEPTE : Odoo propose ensuite son
          ecran natif "Creer un reliquat / Pas de reliquat" (EF-04/06).

        :return: liste de messages (vide si rien a signaler)
        """
        errors = []
        for picking in self:
            if picking.picking_type_id.code != 'outgoing':
                continue
            for move in picking.move_ids:
                if move.state in ('done', 'cancel'):
                    continue
                if move.product_id.tracking not in ('lot', 'serial'):
                    continue
                rounding = move.product_uom.rounding
                lines = move.move_line_ids.filtered(
                    lambda ml: float_compare(
                        ml.quantity, 0.0,
                        precision_rounding=ml.product_uom_id.rounding) > 0)
                without_lot = lines.filtered(lambda ml: not ml.lot_id and not ml.lot_name)
                total_lots = sum(
                    ml.product_uom_id._compute_quantity(ml.quantity, move.product_uom, round=False)
                    for ml in lines - without_lot
                )
                params = {
                    'picking': picking.name,
                    'prod': move.product_id.display_name,
                    'demand': move.product_uom_qty,
                    'sel': total_lots,
                    'uom': move.product_uom.name,
                }
                if without_lot:
                    params['nolot'] = sum(
                        ml.product_uom_id._compute_quantity(ml.quantity, move.product_uom, round=False)
                        for ml in without_lot
                    )
                    errors.append(_(
                        "%(picking)s - %(prod)s : %(nolot)s %(uom)s sans lot.\n"
                        "Chaque quantité livrée doit porter un lot. Relancez "
                        "l'Assistant lots (baguette magique) : il remplace toutes "
                        "les lignes du produit par les lots cochés. Ou supprimez la "
                        "ligne sans lot dans Détail des opérations (icône ≡).",
                        **params,
                    ))
                elif float_compare(total_lots, move.product_uom_qty,
                                   precision_rounding=rounding) > 0:
                    errors.append(_(
                        "%(picking)s - %(prod)s : lots sélectionnés %(sel)s %(uom)s "
                        "pour une demande de %(demand)s %(uom)s.\n"
                        "On ne livre pas plus que la demande : réduisez les "
                        "quantités des lots (Assistant lots ou Détail des "
                        "opérations) à %(demand)s %(uom)s au plus. Livrer moins "
                        "que la demande est possible : Odoo proposera alors "
                        "« Créer un reliquat » ou « Pas de reliquat ».",
                        **params,
                    ))
        return errors

    def button_validate(self):
        """Refuser la validation d'un BL sortant dont les lots sont incoherents.

        18.0.1.8.0 (TICKET 09102026-03, EF-04/05) : une couverture partielle
        (somme des lots < demande) n'est plus bloquee ; seuls une ligne sans
        lot et un depassement de la demande le sont, avec un message qui
        donne une sortie (plus de boucle « supprimez / relancez »).
        """
        errors = self._sopromer_lot_selection_errors()
        if errors:
            raise UserError('\n\n'.join(errors))
        return super().button_validate()
