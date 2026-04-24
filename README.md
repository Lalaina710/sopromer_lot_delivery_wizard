# SOPROMER — Assistant sélection lots BL de vente

Module Odoo 18 — **v18.0.1.1.0**

## Contexte

Sur les BL de vente (pickings sortants) de produits tracés par lot, le popup natif *"Détail des opérations"* force le magasinier à cliquer "Ajouter une ligne" N fois et à chercher chaque lot dans un dropdown. Sur les volumes SOPROMER (Madagascar, distribution produits de mer, ~115 users + 40 PdV), ça entraîne perte de temps et erreurs de saisie fréquentes.

Ce module **n'altère pas** le popup natif — il ajoute un bouton complémentaire **"Assistant lots"** (icône 🪄) sur chaque ligne de `stock.move` dans l'onglet *Opérations* du BL, qui ouvre un wizard dédié.

## Fonctionnalités

- **Liste des lots disponibles** à l'emplacement source du mouvement, avec pour chaque lot :
  - Qté dispo au lieu
  - Qté déjà réservée ailleurs (par d'autres BL/BC en cours)
  - Liste des documents réservants (colonne *"Réservé sur"*)
  - Qté libre (computed)
  - Date d'expiration (si `product_expiry` installé)
- **Pré-sélection FIFO automatique** (tri par date d'expiration puis date de création du lot) jusqu'à couvrir la demande du move
- **Checkbox + qté modifiable** par lot — décocher/recocher et ajuster librement
- **Header live** : `Demande`, `Total sélectionné`, `Restant à prendre` se recalculent au fur et à mesure des cochages (décoration rouge si restant > 0, verte si couvert)
- **Validation 1-clic** : crée les `stock.move.line` correspondants avec `lot_id` + `quantity` + `location_id` + `location_dest_id`
- **Scope BL sortants uniquement** — bouton invisible sur réceptions, transferts internes, MO, etc.
- **Lots archivés / fournisseurs purs exclus** côté quants

## Installation

```bash
# Deploy via script SOPROMER (serveur 45 test)
./scripts/deploy.sh sopromer_lot_delivery_wizard 45

# Upgrade
docker exec odoo-dev /opt/odoo/odoo-bin -c /etc/odoo/odoo.conf \
    -d SOPROMER-REST220426 -u sopromer_lot_delivery_wizard --stop-after-init
```

Puis dans Odoo : *Apps* → *Mettre à jour la liste* → chercher "SOPROMER Assistant" → Installer.

## Dépendances

- `stock`
- `sale_stock`

(`product_expiry` optionnel — si non installé, la colonne Expiration reste vide, pas de crash.)

## Utilisation

1. Ouvrir un BL de vente (`WH/OUT/...`) en état *Prêt* ou *En cours*
2. Onglet *Opérations* → sur la ligne du produit tracé par lot, cliquer **🪄 Assistant lots**
3. Le wizard s'ouvre avec tous les lots dispos au lieu, triés FIFO :
   - Les plus anciens sont **pré-cochés** jusqu'à couvrir la demande
   - Qté pré-remplie (= min reste demande / qté libre du lot)
4. Ajuster si besoin : cocher/décocher, modifier `Qté à prendre`
5. Vérifier le header (Total = Demande, Restant = 0)
6. Cliquer **"Valider la livraison"** → les `stock.move.line` sont créées sur le move et le wizard ferme
7. Retour BL : les lignes apparaissent dans Opérations avec lots + qté

## Sécurité

- Lecture/écriture : `stock.group_stock_user`
- Complet : `stock.group_stock_manager`
- Aucune `ir.rule` (données TransientModel, non transactionnelles)

## Historique de versions

| Version | Date | Change | Auteur |
|---------|------|--------|--------|
| 18.0.1.0.0 | 2026-04-24 | Fork initial — conception + scaffold | odoo-architect agent |
| 18.0.1.0.1 | 2026-04-24 | Fix `expiration_date` : computed avec `getattr` (pas de dépendance stricte à `product_expiry`) | Claude (opus-4.7) |
| 18.0.1.0.2 | 2026-04-24 | Fix header "Demand 0,000" : passage des champs related en stored set dans `default_get` | Claude (opus-4.7) |
| 18.0.1.0.3 | 2026-04-24 | Ajout `lot_id column_invisible` dans trees pour éviter erreur "champ obligatoire" à la sauvegarde | Claude (opus-4.7) |
| 18.0.1.0.4 | 2026-04-24 | Simplification design : suppression tab "Lots sélectionnés" + bouton "Livrer". Une seule table + 1 bouton "Valider". Suppression `_onchange_to_select` (fuseillait qty_to_take au render) | Claude (opus-4.7) |
| 18.0.1.1.0 | 2026-04-24 | **Feature** : verrouillage type opération BL (onchange + constrains si sale-linked) + badge dispo stock sur move (🟢 OK / 🟠 Limite / 🔴 Insuffisant) | Claude (opus-4.7) |

## TODO / v1.1

### Priorité haute (demande utilisateur)

- [x] ~~**Verrouillage type d'opération BL**~~ → livré en v1.1.0. Onchange warning + constrains ValidationError au save si un picking lié à une vente a un type non-outgoing.
- [x] ~~**Colonne badge disponibilité stock**~~ → livré en v1.1.0. `stock_warning_level` computed sur `stock.move` (none/ok/warning/danger), affiché en widget `badge` dans l'onglet Opérations du BL avec decorations success/warning/danger. Seuils : 🔴 `free < demand`, 🟠 `free < demand * 1.2`, 🟢 `free >= demand * 1.2`.

### Priorité moyenne

- [ ] Tests unitaires (`tests/test_lot_wizard.py`)
- [ ] Support multi-emplacements source (locations enfants du warehouse)
- [ ] Indicateur stock négatif (interaction avec `stock_no_negative` tiers)
- [ ] Bouton "Assistant lots global" au niveau du BL (traiter tous les produits en une passe)
- [ ] Filtre/recherche sur les lots dispos quand leur nombre est élevé
- [ ] Option "Respect stricte FIFO" (désactiver override manuel)
- [ ] Extension scope : MO (bons de production), transferts internes

## Conventions SOPROMER respectées

- Structure standard (`models/`, `wizard/`, `views/`, `security/`)
- IDs XML : `sopromer_lot_delivery_wizard.<description>`
- Héritage ORM uniquement (`_inherit` sur `stock.move`, TransientModel pour les 2 wizards)
- Version sémantique `18.0.X.Y.Z`
- License `LGPL-3`
- Pas de `sudo()`, pas de bypass ACL
- Pas d'écrasement du popup natif Odoo — coexistence safe

## Validation terrain

Testé sur `SOPROMER-REST220426` (serveur 45) le 2026-04-24 :
- ✅ BL `CFMP2/OUT/00192` (CFMP23/Depot → Customers)
- ✅ Produit TVAMV0 AMBANIVAVA ROUGE, demande 50 kg
- ✅ FIFO pré-sélection : lots `RO-26M03-A02` (32.160) + `RO26M03A07` (17.840) = 50 kg
- ✅ Validation → `stock.move.line` créés avec bonnes qty
- ✅ Stock cohérent post-validation (quants recalculés correctement, pas de négatif)

## Déploiement

| Environnement | Serveur | DB cible | Statut |
|---------------|---------|----------|--------|
| TEST | `192.73.0.45` | `SOPROMER-REST220426` | ✅ installé + validé |
| PROD | `192.73.0.43` | `SOPROMER` | ⏳ en attente recette complète |

## Auteur

SOPROMER — 2026

## Licence

LGPL-3
