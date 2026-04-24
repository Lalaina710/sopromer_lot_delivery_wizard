# -*- coding: utf-8 -*-
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
{
    'name': 'SOPROMER - Assistant selection lots BL de vente',
    'version': '18.0.1.3.0',
    'category': 'Inventory/Inventory',
    'summary': 'Wizard intuitif multi-lots BL de vente (FIFO + checkbox + qty modifiable + 1-clic valider)',
    'description': """
SOPROMER - Assistant selection lots BL de vente
================================================

Ajoute un bouton "Assistant lots" sur chaque ligne de mouvement
(stock.move) d'un BL de vente sortant, ouvrant un wizard dedie :

* Liste des lots disponibles au lieu d'origine, avec qty dispo /
  qty reservee ailleurs / qty libre / date d'expiration.
* Pre-selection FIFO (par date d'expiration puis date de creation)
  jusqu'a couvrir la demande du move.
* Case a cocher + quantite modifiable par lot.
* Header live : total selectionne et restant a prendre en temps reel.
* Validation 1-clic : cree les stock.move.line sur le move et ferme.

N'altere pas le popup natif "Detail des operations" : le bouton est
ajoute a cote. Scope limite aux pickings sortants (outgoing).
""",
    'author': 'SOPROMER',
    'website': 'https://github.com/Lalaina710/sopromer_lot_delivery_wizard',
    'license': 'LGPL-3',
    'depends': [
        'stock',
        'sale_stock',
    ],
    'data': [
        'security/ir.model.access.csv',
        'wizard/lot_delivery_wizard_view.xml',
        'views/stock_picking_view.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
