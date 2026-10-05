# Contrat API Digital360 — guide d'intégration frontend

> Pour : **Kilo** (frontend `digital360/`). Maintenu par Claude Code (backend `platform/`).
> Source de vérité machine : [`openapi.json`](openapi.json), régénéré à chaque changement d'API
> (un test échoue s'il n'est pas à jour). Ce fichier-ci explique comment s'en servir.

## 1. Où en est chaque endpoint

Remplace le mock correspondant dès qu'un endpoint est **Disponible**. Les autres restent à mocker, au format du §13 de `ARCHITECTURE.md`.

| Endpoint | État | Notes |
|---|---|---|
| `GET /auth/csrf` | **Disponible** | À appeler au chargement de chaque page (voir §3) |
| `POST /auth/register` | **Disponible** | Ouvre la session directement |
| `POST /auth/login` | **Disponible** | |
| `POST /auth/logout` | **Disponible** | 204 |
| `GET /me` | **Disponible** | Première requête après connexion |
| `PATCH /me` | **Disponible** | Nom, téléphone (`""` efface le téléphone) |
| `POST /orgs` | **Disponible** | Crée l'entreprise ; le créateur en devient propriétaire |
| `GET /orgs/{org_id}` · `PATCH /orgs/{org_id}` | **Disponible** | PATCH partiel : seuls les champs envoyés changent |
| `GET /orgs/{org_id}/members` | **Disponible** | |
| `GET /admin/organizations` · `GET /admin/organizations/{id}` | **Disponible** | Staff uniquement ; pagination et filtres (§5) |
| `GET /public/questionnaire` | **Disponible** | Sections, questions, options, `visible_if` (§9) |
| `POST /public/diagnostics` | **Disponible** | Renvoie `id` et `token` : les garder en `localStorage` |
| `PUT /public/diagnostics/{id}/answers` | **Disponible** | Réponses partielles, en-tête `X-Diagnostic-Token` |
| `POST /public/diagnostics/{id}/complete` | **Disponible** | Corps `{"consents": {...}}` ; renvoie score, passport, plan |
| `GET /public/diagnostics/{id}/result` | **Disponible** | Même format que `complete` |
| `POST /orgs` avec `diagnostic_id` | **Disponible** | Rattache le diagnostic à l'inscription (§9) |
| `GET /orgs/{id}/passport` | **Disponible** | 16 éléments, avec `source` |
| `GET /orgs/{id}/action-plan` · `PATCH …/items/{item_id}` | **Disponible** | Le client peut écarter une recommandation |
| `GET /orgs/{id}/diagnostics` | **Disponible** | Historique des diagnostics de l'entreprise |
| `POST /orgs/{id}/diagnostics/claim` | **Disponible** | Rattache à une entreprise **existante** un diagnostic fait avant la connexion (corps `{"diagnostic_id"}`, en-tête `X-Diagnostic-Token`) |
| `GET /admin/diagnostics` | **Disponible** | Staff : prospects captés (coordonnées, score, consentements) |
| `GET /public/catalog?currency=XOF` | **Disponible** | Offres publiques, `price` (ou `null` si non vendue dans la devise), `available_currencies`. Pour l'instant **XOF uniquement** : XAF et EUR renvoient `price: null` |
| `GET /orgs/{id}/entitlements` | **Disponible** | Droits effectifs de l'entreprise (`value` : booléen, entier ou `"UNLIMITED"`), avec leur `sources` |
| `GET·POST /admin/organizations/{id}/entitlement-overrides` · `DELETE …/{override_id}` | **Disponible** | Staff : accorder ou retirer un droit (geste commercial, test) |
| `GET /admin/dashboard` | **Disponible** | Staff : indicateurs clés (§10) |
| `GET /admin/organizations/{id}/overview` | **Disponible** | Staff : fiche entreprise complète en un appel (§10) |
| `GET·POST /admin/staff` · `DELETE /admin/staff/{user_id}/roles/{role}` | **Disponible** | ADMIN uniquement : gestion de l'équipe BENILAB (§10) |
| `GET /admin/audit-logs` | **Disponible** | ADMIN uniquement : journal d'audit, paginé comme au §5 (§10) |
| `POST /auth/password/forgot` · `POST /auth/password/reset` | **Disponible** | Mot de passe oublié (§12) |
| `POST·GET /orgs/{id}/purchase-requests` | **Disponible** | « Je veux démarrer » : demande d'achat (§11) |
| `GET /admin/purchase-requests` · `PATCH /admin/purchase-requests/{id}` | **Disponible** | ADMIN et MANAGER : traitement des demandes (§11) |
| `GET /orgs/{id}/website-project` · `PUT …/brief` · `POST …/submit` · `…/revision` · `…/approve` | **Disponible** | Projet de site Digital Start, offre encadrée (§13) |
| `GET /admin/website-projects` · `POST /admin/website-projects/{id}/transition` | **Disponible** | Équipe : suivi et étapes des projets (§13) |
| `POST /orgs/{id}/checkouts` · `GET /orgs/{id}/checkouts/{checkout_id}` | **Disponible** | Paiement en ligne Cartflox : achat ou renouvellement (§14) |
| `GET·POST·PATCH /me/closer` | **Disponible** | Closer 3.0 : espace de l'apporteur d'affaires (§15) |
| `POST /me/password` | **Disponible** | Changer son mot de passe (§16) |
| `POST·GET /orgs/{id}/invitations` · `DELETE …/{invitation_id}` · `DELETE /orgs/{id}/members/{user_id}` · `POST /invitations/accept` | **Disponible** | Équipe d'un projet (§16) |
| `GET /admin/closers` · `GET /admin/commission-statements` · `POST …/{id}/pay` · `PATCH /admin/closers/{id}` · `PUT /admin/organizations/{id}/closer` | **Disponible** | Équipe : closers, relevés et versements (§15) |

## 2. Configuration

- **Montants** : toujours en **unité mineure** et **HT**. XOF et XAF : en francs (`89900` = 89 900 FCFA). EUR : en **centimes** (`13705` = 137,05 €), donc diviser par 100 à l'affichage.
- Le plan d'action du diagnostic porte désormais un `price` dans la devise du pays déclaré (`null` si non vendu dans cette devise, ou si la recommandation n'a pas de produit).

- **Base de l'API** : ne pas coder `'/api/v1'` en dur (le frontend et l'API seront sur deux sous-domaines, ex. `app.benilab360.com` et `api.benilab360.com`). Utiliser une variable, par exemple `window.D360_API_BASE || 'http://localhost:8000/api/v1'`.
- **Toujours** `credentials: 'include'` (déjà fait dans `client.js`) : la session est un cookie.
- **En local** : lancer l'API (`platform/README.md`) et ajouter l'origine de ton serveur de pages à `CORS_ALLOWED_ORIGINS` dans `platform/.env` (ex. `http://localhost:5500`). Les cookies de `localhost` sont partagés entre ports : tout fonctionne en local.

## 3. Authentification pas à pas

1. **Au chargement** : `GET /auth/csrf` renvoie `{"csrf_token": "..."}` et pose le cookie `csrf_token`.
2. **Toute requête POST / PUT / PATCH / DELETE** envoie l'en-tête `X-CSRF-Token` avec cette valeur. Garde la valeur renvoyée dans le corps en mémoire : c'est plus fiable que de relire `document.cookie` quand le frontend et l'API ne sont pas sur le même hôte.
3. **Inscription ou connexion** : la réponse contient `user` et un **nouveau** `csrf_token`, à utiliser pour la suite (rotation). Le cookie de session `d360_session` est `HttpOnly` : le JavaScript ne le voit pas, c'est normal.
4. **Puis `GET /me`** pour construire l'interface :

```json
{
  "user": { "id": "…", "email": "awa@exemple.ci", "full_name": "Awa Koné", "phone": null, "created_at": "…" },
  "staff_roles": [],
  "staff_permissions": [],
  "memberships": [
    {
      "organization_id": "…",
      "organization_name": "Maquis Le Délice",
      "role": "CLIENT_OWNER",
      "permissions": ["checklist:submit", "diagnostic:read", "invoice:read", "order:create", "…"]
    }
  ]
}
```

- `memberships` vide : l'utilisateur n'a pas encore d'entreprise, proposer `POST /orgs`.
- `staff_roles` non vide : c'est un membre de l'équipe BENILAB, lui donner accès à l'admin.
- Pour afficher ou masquer un bouton, tester la **permission** (`permissions.includes('order:create')`), jamais le rôle. Le serveur revérifie de toute façon.

5. **Session expirée** : toute route peut renvoyer `401` avec `code` = `UNAUTHENTICATED` ou `SESSION_EXPIRED`. Rediriger vers la connexion.

## 4. Erreurs

Toutes les erreurs sont en `application/problem+json`. Baser la logique sur `code` (stable), afficher `detail` (déjà en français) :

```json
{ "status": 400, "code": "WEAK_PASSWORD", "detail": "Le mot de passe ne respecte pas les règles de sécurité.",
  "errors": [{ "field": "password", "reason": "too_short" }], "request_id": "…" }
```

| `code` | Quand | Quoi faire côté UI |
|---|---|---|
| `VALIDATION_ERROR` | Champ invalide (400) | Afficher `errors[].message` sous `errors[].field` (ex. `body.phone`) |
| `WEAK_PASSWORD` | Inscription (400) | `reason` ∈ `too_short`, `too_long`, `too_common`, `same_as_email`, `single_character` |
| `ALREADY_EXISTS` | Email déjà inscrit (409) | Proposer la connexion |
| `INVALID_CREDENTIALS` | Connexion refusée (401) | Message générique, ne pas préciser email ou mot de passe |
| `UNAUTHENTICATED`, `SESSION_EXPIRED` | Pas ou plus de session (401) | Rediriger vers la connexion |
| `CSRF_FAILED` | En-tête CSRF absent (403) | Rappeler `GET /auth/csrf`, puis réessayer une fois |
| `FORBIDDEN` | Rôle insuffisant (403) | Masquer l'action |
| `NOT_FOUND` | Ressource absente, ou organisation dont on n'est pas membre (404) | |
| `RATE_LIMITED` | Trop de tentatives (429) | Attendre `Retry-After` secondes (en-tête) |

Formats attendus : téléphone en **E.164** (`+2250700000000`), pays en **ISO 3166 alpha-2** (`CI`), couleurs `#RRGGBB`, horaires `{"mon": "08:00-12:00,14:00-18:00", "sun": "closed"}` (jours `mon`…`sun`).

## 5. Listes admin : pagination et filtres

`GET /admin/organizations?limit=25&status=LEAD&q=delice` renvoie :

```json
{ "data": [ … ], "page": { "next_cursor": "AZJ…", "has_more": true, "limit": 25 } }
```

Page suivante : même requête avec `&cursor=<next_cursor>`. Tri : du plus récent au plus ancien.

## 6. Retours sur `digital360/js/api/client.js`

1. **Bug** dans `parseResponse` : le `throw err` est dans un `try` dont le `catch` le rattrape et le remplace par une erreur générique. Le `code` est donc toujours perdu. Correction :

   ```js
   if (!res.ok) {
       let problem = null;
       if (bodyText && isProblemResponse(headers)) {
           try { problem = JSON.parse(bodyText); } catch (_) { /* corps illisible */ }
       }
       const err = new Error(problem?.detail || `HTTP ${res.status}`);
       Object.assign(err, {
           status: res.status, code: problem?.code, detail: problem?.detail,
           errors: problem?.errors, requestId: problem?.request_id,
       });
       throw err;
   }
   ```

2. `API_BASE` : rendre configurable (§2).
3. Ajouter `register`, `login`, `logout`, `getCsrf`, `updateMe`, `createOrg`, `updateOrg`, `getMembers`, `adminListOrgs` (§1).
4. Garder le jeton CSRF renvoyé par `/auth/csrf` et par la connexion en mémoire, plutôt que de relire le cookie (§3).
5. Diagnostic : voir §9 (en-tête `X-Diagnostic-Token`, consentements, suppression de `saveContact`).

## 9. Diagnostic : parcours et écarts avec tes mocks

Les formats de réponse reprennent **tes mocks** (`getQuestionnaire`, `completeDiagnostic`) : les écrans existants n'ont presque rien à changer. Écarts à corriger :

| Sujet | Ton mock | API réelle |
|---|---|---|
| `visible_if` | Portait sur des faits serveur (`has_google_business`) que le navigateur ne connaît pas | Porte sur les **réponses** : `{"fact": "site", "eq": "oui"}`, `{"fact": "gb", "in": ["oui", "en-cours"]}` |
| Question « pays » | Absente | Ajoutée (`country`, obligatoire, valeurs `CI`, `SN`, `CM`…) : elle fixe la devise et le pays de l'entreprise |
| Avis Google | Option `n/a` | Supprimée : la question n'apparaît que si `gb` vaut `oui` ou `en-cours` |
| Libellés | Avec émojis | Sans émojis (ajoute tes icônes à l'affichage, en te basant sur `value`) |
| Contact | `saveContact` vers `PUT /contact` | **Supprimé** : les coordonnées sont des questions de la section `identity` |
| Consentement | Absent | `complete` exige `{"consents": {"privacy": true, "marketing_email": false, "marketing_whatsapp": false}}` ; sans `privacy: true`, réponse 422 `CONSENT_REQUIRED` |
| Plan d'action | — | Chaque élément a en plus `id`, `phase` (`PRESENT`, `VISIBLE`, `ATTRACT`, `CONVERT`, `RETAIN`, pour grouper en 5 phases) et `price` (`null` jusqu'au catalogue, M4) |

**Évaluer `visible_if` dans le navigateur**, avec la même grammaire que le serveur, sur l'objet des réponses courantes :

```js
function evaluate(cond, answers) {
    if (cond.all) return cond.all.every(c => evaluate(c, answers));
    if (cond.any) return cond.any.some(c => evaluate(c, answers));
    if (cond.not) return !evaluate(cond.not, answers);
    const value = answers[cond.fact];
    if ('exists' in cond) return (value != null) === cond.exists;
    if (value == null) return false;
    if ('eq' in cond) return value === cond.eq;
    if ('ne' in cond) return value !== cond.ne;
    if ('in' in cond) return cond.in.includes(value);
    if ('contains' in cond) {
        return Array.isArray(value) ? value.includes(cond.contains) : String(value).includes(cond.contains);
    }
    if ('gt' in cond) return value > cond.gt;
    if ('gte' in cond) return value >= cond.gte;
    if ('lt' in cond) return value < cond.lt;
    if ('lte' in cond) return value <= cond.lte;
    return false;
}
```

**Parcours** :

1. `GET /public/questionnaire` : une étape par section, en masquant les questions dont `visible_if` est faux.
2. `POST /public/diagnostics` : stocker `{id, token}` en `localStorage` (reprise possible si la page est rechargée).
3. À chaque étape : `PUT /public/diagnostics/{id}/answers` avec `{"answers": {...}}` et l'en-tête `X-Diagnostic-Token: <token>`. Réponses partielles acceptées ; `null` ou `""` efface une réponse. La réponse donne `missing_required` (questions obligatoires encore vides) et les réponses normalisées (le téléphone `+225 07 00 00 00 00` devient `+2250700000000`).
4. Dernière étape : case de consentement à la politique de confidentialité (obligatoire) et cases facultatives (offres par email, par WhatsApp), puis `POST /public/diagnostics/{id}/complete`. Un double appel renvoie le même résultat (double clic sans risque).
5. Afficher `score`, `passport`, `action_plan`. Le texte `score.disclaimer` doit apparaître près du score (ce n'est pas une certification).
6. **Inscription** : `POST /auth/register`, puis `POST /orgs` avec `{"diagnostic_id": "<id>"}` et l'en-tête `X-Diagnostic-Token`. Nom, pays, ville, téléphone, WhatsApp, email et description de l'entreprise sont repris des réponses (tout champ envoyé dans le corps les remplace). Le Passport et le plan d'action de l'entreprise sont créés dans la foulée. Supprimer ensuite `{id, token}` du `localStorage`.

7. **Connexion avec un compte existant** (au lieu de l'inscription) : après `POST /auth/login`, si un diagnostic terminé est en `localStorage`, appeler `GET /me` :
   - l'utilisateur a déjà une entreprise (`memberships` non vide) : `POST /orgs/{organization_id}/diagnostics/claim` avec `{"diagnostic_id": "<id>"}` et l'en-tête `X-Diagnostic-Token` ; le Passport est mis à jour (les éléments vérifiés par BENILAB sont conservés) ;
   - aucune entreprise : `POST /orgs` avec `{"diagnostic_id"}` comme à l'inscription.

   Dans les deux cas, supprimer ensuite `{id, token}` du `localStorage` et rediriger vers `dashboard.html`. Réponse `409 DIAGNOSTIC_ALREADY_CLAIMED` : le diagnostic est déjà rattaché, supprimer simplement la copie locale.

Erreurs propres au diagnostic : `NOT_FOUND` (identifiant ou jeton faux, 404), `QUESTIONNAIRE_INCOMPLETE` (422, `errors[].field` = `answers.<clé>`), `CONSENT_REQUIRED` (422), `DIAGNOSTIC_ALREADY_COMPLETED` (409, réponses figées), `DIAGNOSTIC_NOT_COMPLETED` (409), `DIAGNOSTIC_ALREADY_CLAIMED` (409), `RATE_LIMITED` (429, 30 diagnostics par heure et par IP).

## 7. Types

Pour de l'autocomplétion en JavaScript (JSDoc) sans maintenance manuelle :

```bash
npx openapi-typescript platform/contracts/openapi.json -o digital360/js/api/types.d.ts
```

## 10. Espace d'administration (`admin.html`)

**Contrôle d'accès** : au chargement, `GET /me`. Un 401 renvoie vers la connexion. Si `staff_roles` est vide, le visiteur est un client : renvoie-le vers `dashboard.html`. N'affiche le lien « Espace Admin » que si `staff_roles` n'est pas vide. Le serveur refuse de toute façon (403 `FORBIDDEN`) : la redirection sert le confort, pas la sécurité.

**Ce que voit chaque rôle** : utilise `staff_permissions` de `/me` pour masquer les blocs inaccessibles. `staff:manage` donne accès à l'onglet Équipe, `audit:read` au journal d'audit. Ces deux permissions ne concernent aujourd'hui que le rôle ADMIN.

**`GET /admin/dashboard`** (tous les rôles staff) :

```json
{
  "diagnostics": {
    "in_progress": 12, "completed_not_claimed": 30, "claimed": 18,
    "completed_last_24h": 4, "completed_last_7_days": 21,
    "conversion_rate": 0.375, "average_score": 41.2
  },
  "organizations_by_status": { "LEAD": 15, "ACTIVE": 3, "SUSPENDED": 0, "CHURNED": 0 },
  "users": { "total": 40, "created_last_7_days": 9 },
  "sales": {
    "new": 2, "contacted": 1, "won": 5, "lost": 2, "won_last_30_days": 3, "win_rate": 0.714,
    "revenue": [{ "currency": "XOF", "total": 449500, "last_30_days": 269700 }]
  }
}
```

- `sales` : demandes « Je veux démarrer » par statut, `win_rate` = gagnées / (gagnées + perdues), `null` tant qu'aucune n'est close. `revenue` : chiffre d'affaires HT des ventes gagnées, **une entrée par devise** (ne jamais additionner FCFA et euros), en unité mineure.
- Une vente gagnée fait passer l'entreprise de `LEAD` à `ACTIVE` : `organizations_by_status.ACTIVE` compte donc les clients.

- `completed_not_claimed` : les **prospects à relancer** (diagnostic terminé, pas de compte).
- `conversion_rate` est compris entre 0 et 1 (afficher `37,5 %`). Il vaut `null`, comme `average_score`, tant qu'aucun diagnostic n'est terminé : afficher « — ».

**`GET /admin/organizations/{id}/overview`** (tous les rôles staff) : `{ organization, members, diagnostics, passport, entitlements }`. Chaque élément a le même format que la route client correspondante (`/orgs/{id}`, `/members`, `/diagnostics`, `/passport`, `/entitlements`). **`diagnostics` et `passport` valent `null`** quand le rôle n'a pas le droit de les lire (rôle FINANCE) : affiche alors « Accès réservé », pas une liste vide. Entreprise inconnue : 404.

**Équipe** (ADMIN) :
- `GET /admin/staff` renvoie `{ "data": [{ "user_id", "email", "full_name", "roles": ["MANAGER"], "last_login_at" }] }`.
- `POST /admin/staff` avec `{ "email", "role" }` renvoie 201 et le membre à jour. La personne doit déjà avoir un compte, sinon 404 `NOT_FOUND` : afficher « Cette personne doit d'abord créer son compte ». Attribuer deux fois le même rôle est sans effet.
- `DELETE /admin/staff/{user_id}/roles/{role}` renvoie 204. Un administrateur ne peut pas retirer **son propre** rôle ADMIN (403) : masque ce bouton sur sa ligne. Un rôle non attribué renvoie 404.
- Rôles : `ADMIN`, `MANAGER`, `CONTENT_MANAGER`, `DEVELOPER`, `FINANCE`.

**`GET /admin/audit-logs`** (ADMIN) : filtres `organization_id`, `action` (ex. `staff_role.grant`) et `entity_type`, pagination comme au §5. Chaque entrée contient `occurred_at`, `actor_type`, `actor_label`, `action`, `entity_type`, `entity_id`, `organization_id`, `old_value` et `new_value` (objets ou `null`), `ip`. Affiche `old_value` et `new_value` avec `textContent` (`JSON.stringify`), jamais avec `innerHTML`.

## 11. « Je veux démarrer » : demandes d'achat

En attendant le paiement en ligne, le client demande une offre et l'équipe le rappelle. Le paiement est encaissé hors ligne.

**Où placer le bouton** : sur chaque recommandation du plan d'action qui a un `product_code` (bouton « Je veux démarrer » à la place de « Activer »), et sur chaque offre de la page Abonnement. Il est réservé au propriétaire de l'entreprise (`memberships[].permissions` contient `order:create`) ; pour un simple membre, affiche « Demandez au responsable de votre entreprise ».

**`POST /orgs/{id}/purchase-requests`** :

```json
{ "product_code": "DIGITAL_START", "plan_item_id": "…", "channel": "WHATSAPP", "message": "Rappelez-moi le matin" }
```

- `plan_item_id` : facultatif, l'`id` de la recommandation d'origine. Elle passe alors à `ACCEPTED`.
- `channel` : `WHATSAPP`, `PHONE` ou `EMAIL`. `message` : facultatif, 1000 caractères au maximum.
- `contact_number` : facultatif, au format E.164 (`+2250700000000`). Pour `WHATSAPP` ou `PHONE`, le serveur prend ce numéro, sinon celui de la fiche entreprise (WhatsApp puis téléphone), sinon celui du compte. S'il n'en trouve aucun : 422 `CONTACT_NUMBER_REQUIRED`. **Dans la fenêtre** : quand le client choisit WhatsApp ou Téléphone, affiche un champ « Numéro », prérempli avec `whatsapp` ou `phone` de `GET /orgs/{id}`, modifiable, et obligatoire s'il est vide. Le numéro retenu est renvoyé dans `contact_number`.
- **Une offre couvre plusieurs recommandations** (Growth couvre la fiche Google, le calendrier éditorial et le suivi des prospects) : la demande fait passer **toutes** les recommandations de l'offre à `ACCEPTED`, puis à `IN_PROGRESS` quand la vente est gagnée (de nouveau `PROPOSED` si elle est perdue). Dans l'espace client, regroupe-les sous l'offre : « Couvert par votre demande Growth du … », plutôt que d'afficher le même bouton sur chaque recommandation.
- **201** : demande créée. **200** : une demande est déjà en cours pour cette offre, et c'est elle qui est renvoyée. Traite les deux cas de la même façon : affiche « Demande envoyée, un conseiller vous contacte sous 24 h ».
- La réponse contient `id`, `product_name`, `price` (HT, unité mineure, devise du pays de l'entreprise), `status` et `created_at`.
- Erreurs : 422 `PRODUCT_NOT_AVAILABLE` (offre inconnue ou non vendue dans la devise du client), 422 `VALIDATION_ERROR` (recommandation d'une autre offre), 404 (recommandation inconnue).

**`GET /orgs/{id}/purchase-requests`** renvoie `{ "data": [...] }`, la plus récente en premier. Sers-t'en au chargement : pour une offre qui a une demande `NEW` ou `CONTACTED`, remplace le bouton par « Demande envoyée le … ». Statuts : `NEW` (envoyée), `CONTACTED` (un conseiller vous a contacté), `WON` (offre en cours d'activation), `LOST` (demande close).

**Admin, onglet « Demandes »** (visible avec la permission `order:create`) :
- `GET /admin/purchase-requests?status=NEW` : pagination du §5. En plus des champs ci-dessus : `organization_name`, `requested_by_name`, `requested_by_email`, `requested_by_phone`, **`contact_number`** (le numéro à rappeler : c'est lui qu'il faut afficher en premier) et `staff_note`.
- `PATCH /admin/purchase-requests/{id}` avec `{ "status": "CONTACTED", "staff_note": "…" }` (les deux champs sont facultatifs ; `""` efface la note).
- **Passer à `WON` exige le paiement reçu** : `{ "status": "WON", "payment": { "method", "reference", "amount", "received_on" } }`. `method` vaut `ORANGE_MONEY`, `MTN_MOMO`, `MOOV_MONEY`, `WAVE`, `BANK_TRANSFER` ou `CASH`. `reference` (identifiant de la transaction) est obligatoire sauf pour `CASH`. `amount` est en unité mineure HT, et facultatif : le prix de l'offre s'applique s'il est absent. `received_on` vaut aujourd'hui par défaut. Sans `payment` : 422 `PAYMENT_REQUIRED`. Le paiement est renvoyé dans `payment` de chaque demande, et un **reçu** part par e-mail au client.
- **Vente directe** (le client a payé sans faire de demande) : `POST /admin/organizations/{id}/sales` avec `{ "product_code", "payment", "note" }`, qui renvoie 201 et la demande au statut `WON`. Même activation qu'une demande gagnée. Erreurs : 409 `OPEN_REQUEST_EXISTS` (une demande est en cours : la passer à `WON` dans l'onglet Demandes), 404 (entreprise inconnue).
- Le chiffre d'affaires de `/admin/dashboard` est calculé sur les **montants réellement encaissés**.
- **Pas de double paiement** : une offre ponctuelle (Digital Start) déjà payée ne se revend pas, et la demande du client, le passage à `WON` et la vente directe renvoient 409 `ALREADY_PURCHASED`. Un abonnement encore en cours se prolonge par **Renouveler**, pas par une nouvelle vente : 409 `SUBSCRIPTION_ACTIVE`. Côté client, une fois Start payé, remplace « Je veux démarrer » par « Suivre mon site ».
- **Abonnements payés hors ligne** : une vente Essential, Growth ou Performance couvre 31 jours (366 en annuel). `GET /admin/subscriptions?status=EXPIRING|EXPIRED|ACTIVE` renvoie, par entreprise et par offre, `covers_until` (échéance), `status` (`EXPIRING` = échéance dans moins de 5 jours), `days_left` et `last_payment`, de l'échéance la plus proche à la plus lointaine.
- **Renouveler** : `POST /admin/organizations/{id}/renewals` avec `{ "product_code", "payment" }` (même `payment` que pour `WON`), qui renvoie 201 et le nouvel abonnement. Le mois payé s'ajoute à l'échéance (paiement en avance), ou repart d'aujourd'hui si l'abonnement a expiré. Un reçu part au client. 404 `NO_SUBSCRIPTION` si le client n'a jamais eu cet abonnement (enregistrer plutôt une vente).
- **Espace client** : `GET /orgs/{id}/billing` (propriétaire de l'entreprise, `invoice:read`) renvoie `subscriptions` (même format que `/admin/subscriptions`) et `payments` (`receipt_number`, `product_name`, `amount`, `currency`, `method`, `reference`, `received_on`, `covers_until`), du plus récent au plus ancien. Il renvoie aussi `online_payment_available` (§14).
- **Rappel automatique** : chaque jour à 7 h UTC, le client (propriétaires) et l'équipe (`SALES_ALERT_EMAILS`) reçoivent un e-mail pour chaque abonnement qui expire dans les 5 jours, une seule fois par période. `/admin/dashboard` → `sales.renewals_due` compte les abonnements à renouveler (échéance proche ou dépassée).
- Transitions possibles : `NEW` → `CONTACTED`, `WON` ou `LOST`, et `CONTACTED` → `WON` ou `LOST`. `WON` et `LOST` sont définitifs : toute autre transition renvoie 409 `INVALID_TRANSITION`. Propose uniquement les boutons permis.
- **Passer une demande à `WON` active automatiquement l'offre** : les droits du produit sont accordés à l'entreprise, sans date de fin pour une offre ponctuelle (Digital Start), ou pour 31 jours (366 pour un abonnement annuel), le temps de la période payée hors ligne. Ils apparaissent dans `GET /orgs/{id}/entitlements`, avec dans `sources` le motif « Offre … : demande d'achat … gagnée » et la date `expires_at`. Pour l'admin : affiche une confirmation avant « Gagnée » (« Le paiement a-t-il bien été reçu ? L'offre sera activée »).

## 12. Mot de passe oublié

- `POST /auth/password/forgot` avec `{ "email" }` renvoie toujours **202**, que le compte existe ou non. 429 au-delà de 3 demandes par heure pour une même adresse.
- L'e-mail contient un lien vers `reset-password.html#token=…`. Le jeton est **après le `#`** : lis-le avec `new URLSearchParams(location.hash.slice(1)).get('token')`, puis efface-le avec `history.replaceState`.
- `POST /auth/password/reset` avec `{ "token", "password" }` renvoie **204**. Toutes les sessions sont fermées : renvoie vers la connexion. Erreurs : 400 `INVALID_RESET_TOKEN` (lien expiré, déjà utilisé ou faux) et 400 `WEAK_PASSWORD` (raisons dans `errors[]`, comme à l'inscription). Après un `WEAK_PASSWORD`, le lien reste valable.

## 13. Projet de site Digital Start (« Mon projet »)

**L'offre est encadrée.** La conception est offerte grâce à l'IA ; le client paie le domaine, l'hébergement et la mise en ligne. Il ne commande donc pas un site sur mesure : il remplit un brief guidé, choisit un modèle et une palette, et dispose d'une seule série de corrections de **contenu**. Le serveur refuse tout ce qui sort de ce cadre. Affiche toujours le bloc `offer` (ce qui est inclus et ce qui ne l'est pas) avant l'envoi du brief.

**Création** : automatique quand une demande d'achat **Start** passe à « Gagnée ». Le brief est prérempli avec la fiche entreprise, et le client reçoit l'e-mail « Votre offre Start est activée ».

**`GET /orgs/{id}/website-project`** (404 tant qu'aucun projet n'existe) :
- `status` : `BRIEF_PENDING` (brief à remplir), `IN_PRODUCTION` (en préparation), `CLIENT_REVIEW` (à relire), `REVISION` (corrections en cours), `APPROVED` (validé, mise en ligne en cours), `LIVE` (en ligne).
- `brief`, `missing_fields` (champs encore requis : `business_name`, `activity`, `services`, `phone`, `template`, `palette`, `has_logo`), `due_on` (date de livraison promise), `preview_url`, `live_url`, `revisions_left`.
- `available_actions` : n'affiche que ces actions. `EDIT_BRIEF` permet de modifier le brief, `IN_PRODUCTION` d'envoyer le brief, `REVISION` de demander les corrections, `APPROVED` de valider.
- `offer` : `pitch`, `pages`, `included`, `not_included`, `templates` et `palettes` (`key`, `name`, `description`), `max_services` (6), `delivery_business_days` (7), `revision_categories` (libellés).

**Côté client** (propriétaire de l'entreprise, en-tête CSRF) :
- `PUT …/website-project/brief` : enregistre le brouillon. Les champs : `business_name`, `activity` (500 caractères au maximum), `services` (6 au maximum, chacun `{name, description}`), `city`, `address`, `phone`, `whatsapp` (format `+225…`), `email`, `opening_hours`, `facebook`, `instagram`, `tiktok` (URL), `desired_domain` (`maboutique.ci`), `template`, `palette` (une des `key` de `offer`), `has_logo` (booléen). Tout autre champ, un 7e service ou un modèle inconnu renvoie 400. Après le démarrage de la production : 409 `BRIEF_LOCKED`.
- `POST …/website-project/submit` avec `{ "accept_scope": true }` : case à cocher obligatoire « J'ai lu ce qui est inclus et ce qui ne l'est pas ». Sinon 422 `SCOPE_NOT_ACCEPTED`. Si le brief est incomplet : 422 `TRANSITION_GUARD_FAILED`, avec la liste des champs dans `errors`. En cas de succès, `due_on` est fixée à 7 jours ouvrés.
- `POST …/website-project/revision` avec `{ "items": [{ "category": "TEXT"|"PHOTO"|"CONTACT"|"HOURS", "page": "Accueil"|"À propos"|"Services"|"Galerie"|"Contact", "text": "…" }] }` (10 éléments au maximum). **Une seule fois** : ensuite, 422. Aucune catégorie ne concerne le design, une page inconnue renvoie 400.
- `POST …/website-project/approve` : validation de la version proposée.

**Côté équipe** (`website_project:read` pour lire, `:transition` pour agir) :
- `GET /admin/website-projects?status=IN_PRODUCTION` : pagination du §5, `organization_name` inclus.
- `POST /admin/website-projects/{id}/transition` avec `{ "target", "preview_url", "live_url", "reason" }` :
  - `CLIENT_REVIEW` exige `preview_url` (première version, ou version corrigée après `REVISION`) ;
  - `LIVE` exige `live_url` ;
  - `CANCELLED` exige `reason` (réservé à ADMIN et MANAGER).

  Une condition non remplie renvoie 422 `TRANSITION_GUARD_FAILED`.

**E-mails automatiques** : au client à l'activation, à la réception du brief (avec la date de livraison), quand la préversion est prête et à la mise en ligne. À l'équipe (`SALES_ALERT_EMAILS`) : « nouveau site à produire », avec le **prompt de conception en PDF joint**, et « corrections demandées ».

**Prompt de conception** (onglet Projets de site) : à l'envoi du brief, le serveur rédige le prompt à donner à l'outil d'IA. Il reprend l'entreprise, les 5 pages, les services, le modèle et la palette, les coordonnées, le référencement et le cadre de l'offre à respecter, et liste les informations manquantes. L'équipe peut l'améliorer avant de lancer la conception.
- `GET /admin/website-projects/{id}/design-prompt` (`website_project:read`) renvoie `{ project_id, organization_name, prompt, saved, edited_at, edited_by_name }`. `saved: false` : le brief n'est pas encore envoyé, et le texte est un aperçu rédigé depuis le brief actuel.
- `PUT …/design-prompt` avec `{ "prompt" }` (20 à 30 000 caractères, `website_project:transition`) enregistre la version de l'équipe.
- `POST …/design-prompt/regenerate` réécrit le prompt depuis le brief actuel ; les modifications de l'équipe sont remplacées.
- `GET …/design-prompt.pdf` renvoie le PDF de la version actuelle (`Content-Disposition: attachment`). Le télécharger avec `fetch` (cookies inclus), puis créer un lien vers le `blob`.

## 14. Paiement en ligne (Cartflox)

Le client paie seul (mobile money ou carte) sur la page Cartflox ; l'offre s'active dès que Cartflox confirme. Le paiement manuel (§11) reste disponible.

**Afficher le bouton** « Payer en ligne » seulement si `GET /orgs/{id}/billing` renvoie **`online_payment_available: true`** (Cartflox configuré et entreprise facturée en XOF). Réservé, comme « Je veux démarrer », au propriétaire (`order:create`). Garde « Je veux démarrer » à côté, pour les clients qui préfèrent être rappelés.

**`POST /orgs/{id}/checkouts`** avec `{ "product_code": "DIGITAL_START" }` (achat) ou un abonnement déjà souscrit (**renouvellement** : la période s'ajoute à l'échéance) :
- **201** : `{ id, product_code, product_name, amount, currency, period, status: "PENDING", checkout_url, receipt_number: null, created_at }`. Redirige aussitôt : `window.location.href = checkout_url`. Un double clic dans les 30 minutes renvoie le même paiement et la même page.
- Erreurs : 503 `ONLINE_PAYMENT_UNAVAILABLE` (paiement en ligne non activé), 422 `ONLINE_PAYMENT_UNAVAILABLE` (devise autre que XOF), 422 `PRODUCT_NOT_AVAILABLE`, 409 `ALREADY_PURCHASED` (Digital Start déjà payé), 502 `PAYMENT_PROVIDER_ERROR` (Cartflox ne répond pas : proposer de réessayer ou « Je veux démarrer »).

**Retour de Cartflox** : le client revient sur `dashboard.html?paiement={id}` (ou `…&annule=1` s'il a annulé). Appelle alors **`GET /orgs/{id}/checkouts/{id}`** : le serveur interroge Cartflox et active l'offre si le paiement est confirmé. Selon `status` :
- `PAID` : « Paiement confirmé, votre offre est active » (+ `receipt_number`), puis recharge les données (droits, abonnement, projet de site).
- `PENDING` : « Paiement en cours de confirmation… ». Rappelle la route toutes les 5 secondes pendant une minute, puis affiche « Nous vous confirmons votre paiement par e-mail dès sa validation » (le serveur continue de vérifier pendant environ deux jours).
- `FAILED` ou `CANCELLED` : « Paiement non abouti », avec un bouton pour réessayer. `EXPIRED` : paiement abandonné.
- Retire ensuite `?paiement=` de l'adresse (`history.replaceState`).

Un paiement confirmé suit le même chemin qu'un encaissement manuel : reçu par e-mail, client actif, projet de site créé pour Start, et une demande « Je veux démarrer » en cours pour la même offre passe à `WON`. Dans `payments`, `method` vaut **`CARTFLOX`** (« Paiement en ligne ») et `reference` la référence Cartflox (`CS-…`). Côté admin, ces paiements ont `payment.channel = "ONLINE"` ; l'équipe ne peut pas saisir `CARTFLOX` à la main (400).

## 15. Closer 3.0 (apporteurs d'affaires)

Un client qui a **déjà payé une offre** peut devenir Closer 3.0. Il touche **20 % du montant HT** de chaque paiement des clients qu'il apporte, pendant les **12 premiers mois** de chaque client (365 jours après son premier paiement), quelle que soit l'offre. Le 1er de chaque mois, les commissions du mois écoulé forment un **relevé** ; l'équipe verse le montant puis marque le relevé comme versé. Tous les calculs sont faits par le serveur.

**Capter le code de parrainage** : le lien du closer est `https://digital360.bemedia-ai.online/?ref=CODE`. Sur toute page, si l'adresse contient `ref`, garde-le en `localStorage` (`d360_ref`). À la création de l'entreprise, ajoute **`"referral_code": "<code>"`** au corps de `POST /orgs` (avec ou sans `diagnostic_id`). Un code inconnu, suspendu ou celui du client lui-même est ignoré sans erreur. Retire `d360_ref` après la création.

**Espace client, rubrique « Closer 3.0 »** :
- `GET /me/closer` renvoie `{ eligible, closer, referrals, pending, statements }`.
  - `closer` vaut `null` tant que le client n'a pas rejoint le programme. Si `eligible` est faux : « Réservé aux clients qui ont déjà payé une offre ».
  - Sinon : `{ id, code, referral_link, status, payout_method, payout_account, created_at }`.
  - `referrals` : `[{ organization_name, joined_at, commissions_total }]`.
  - `pending` : commissions du mois en cours, pas encore relevées, `[{ currency, amount, count }]`.
  - `statements` : `[{ id, period, currency, total_amount, commission_count, status: "DUE"|"PAID", payout_method, payout_reference, paid_on }]`. `period` est le premier jour du mois couvert.
- `POST /me/closer` avec `{ "payout_method": "MTN_MOMO", "payout_account": "+22997000000", "accept_terms": true }` pour rejoindre le programme ; l'appel est idempotent. `payout_method` vaut `ORANGE_MONEY`, `MTN_MOMO`, `MOOV_MONEY`, `WAVE` ou `BANK_TRANSFER`. Erreur 403 `NOT_ELIGIBLE` si le client n'a jamais payé ; 400 si `accept_terms` n'est pas `true`. Affiche les conditions avant la case à cocher : 20 % pendant 12 mois, versement chaque mois, pas d'auto-parrainage.
- `PATCH /me/closer` avec `{ payout_method, payout_account }` pour changer le compte de versement.
- Montre le lien avec un bouton « Copier » et un bouton « Partager sur WhatsApp » (`https://wa.me/?text=…`).

**Admin, onglet « Closers »** (lecture : `invoice:read` ; actions : `closer:manage`, accordé à ADMIN, MANAGER et FINANCE) :
- `GET /admin/closers` renvoie `{ data, due_total }`.
  - Chaque closer : `full_name`, `email`, `code`, `status`, `payout_method`, `payout_account`, `referred_clients`, `paying_clients`, et les montants `earned` (gagné), `due` (à verser) et `paid` (versé), par devise.
  - `due_total` : le total à verser, tous closers confondus.
- `GET /admin/commission-statements?status=DUE` liste les relevés avec le nom du closer et son compte de versement.
- `POST /admin/commission-statements/{id}/pay` avec `{ "method", "reference", "paid_on" }` (`paid_on` vaut aujourd'hui par défaut) marque le relevé comme versé, et le closer reçoit un e-mail. 409 `ALREADY_PAID` si c'est déjà fait.
- `PATCH /admin/closers/{id}` avec `{ "status": "SUSPENDED" | "ACTIVE" }`. Un closer suspendu ne gagne plus de commission et son code n'est plus accepté.
- `PUT /admin/organizations/{id}/closer` avec `{ "closer_code": "AB12CD" }`, ou `null` pour détacher, rattache un client à la main (client amené par téléphone). Le rattachement vaut pour les paiements à venir. 422 `SELF_REFERRAL` si le closer est membre de cette entreprise.

## 16. Mon compte, équipe et projets

**Un compte, plusieurs projets.** Chaque entreprise est un projet distinct, avec son diagnostic, son plan d'action, ses offres et ses paiements. `GET /me` → `memberships` liste les projets de l'utilisateur.
- Le projet affiché est mémorisé dans `localStorage` (`d360_org`). Changer de projet recharge l'espace, pour qu'aucune donnée de l'ancien projet ne reste affichée.
- Un diagnostic terminé par un utilisateur connecté n'est **jamais** rattaché au hasard :
  - lancé depuis « + Nouveau projet » (`d360_diag_target = "new"`) : `POST /orgs` avec `diagnostic_id` ;
  - lancé depuis un projet (`d360_diag_target = <id>`) : `POST /orgs/{id}/diagnostics/claim` ;
  - sinon, le client choisit le projet dans une fenêtre, ou crée un nouveau projet.

**Profil** : `PATCH /me` avec `{ full_name, phone }` (§1).

**Mot de passe** : `POST /me/password` avec `{ current_password, new_password }` → 204. Les autres appareils connectés sont déconnectés ; celui-ci reste connecté. Erreurs : 400 `INVALID_CURRENT_PASSWORD`, 400 `WEAK_PASSWORD` (mêmes règles qu'à l'inscription), 429 après 5 essais en 15 minutes.

**Équipe** (réservé au responsable, `membership:manage`) :
- `POST /orgs/{id}/invitations` avec `{ email, role }`, où `role` vaut `CLIENT_MEMBER` (consulte l'espace) ou `CLIENT_OWNER` (peut acheter et tout gérer) → 201. La personne reçoit un e-mail avec un lien `…/#invitation=<jeton>`, valable 7 jours. Réinviter la même adresse renvoie un nouveau lien. Erreurs : 409 `ALREADY_MEMBER`, 422 `TOO_MANY_INVITATIONS` (20 en attente au maximum).
- `GET /orgs/{id}/invitations` liste les invitations en attente. `DELETE /orgs/{id}/invitations/{invitation_id}` en annule une (le lien ne marche plus).
- `GET /orgs/{id}/members` liste les membres. `DELETE /orgs/{id}/members/{user_id}` retire un membre, qui perd l'accès aussitôt. 409 `LAST_OWNER` : le projet garde au moins un responsable.
- **Accepter** : `POST /invitations/accept` avec `{ token }`, une fois connecté **avec l'adresse invitée** (après inscription si besoin). Renvoie `{ organization_id, organization_name, role }`. Erreurs : 400 `INVALID_INVITATION` (expirée, annulée ou déjà utilisée), 403 `INVITATION_EMAIL_MISMATCH` (autre compte connecté : se reconnecter avec la bonne adresse).

## 8. Demander un changement

Besoin d'un champ, d'un filtre ou d'un endpoint absent ? Écris-le dans ta réponse à l'utilisateur, sous un titre « Demandes pour le backend ». Chaque changement d'API met à jour ce fichier et `openapi.json`.
