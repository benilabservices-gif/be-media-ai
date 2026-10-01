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
| `/orgs/{id}/website-projects` | À mocker | M6 |

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
  "users": { "total": 40, "created_last_7_days": 9 }
}
```

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
- **201** : demande créée. **200** : une demande est déjà en cours pour cette offre, et c'est elle qui est renvoyée. Traite les deux cas de la même façon : affiche « Demande envoyée, un conseiller vous contacte sous 24 h ».
- La réponse contient `id`, `product_name`, `price` (HT, unité mineure, devise du pays de l'entreprise), `status` et `created_at`.
- Erreurs : 422 `PRODUCT_NOT_AVAILABLE` (offre inconnue ou non vendue dans la devise du client), 422 `VALIDATION_ERROR` (recommandation d'une autre offre), 404 (recommandation inconnue).

**`GET /orgs/{id}/purchase-requests`** renvoie `{ "data": [...] }`, la plus récente en premier. Sers-t'en au chargement : pour une offre qui a une demande `NEW` ou `CONTACTED`, remplace le bouton par « Demande envoyée le … ». Statuts : `NEW` (envoyée), `CONTACTED` (un conseiller vous a contacté), `WON` (offre en cours d'activation), `LOST` (demande close).

**Admin, onglet « Demandes »** (visible avec la permission `order:create`) :
- `GET /admin/purchase-requests?status=NEW` : pagination du §5. En plus des champs ci-dessus : `organization_name`, `requested_by_name`, `requested_by_email`, `requested_by_phone` et `staff_note`.
- `PATCH /admin/purchase-requests/{id}` avec `{ "status": "CONTACTED", "staff_note": "…" }` (les deux champs sont facultatifs ; `""` efface la note).
- Transitions possibles : `NEW` → `CONTACTED`, `WON` ou `LOST`, et `CONTACTED` → `WON` ou `LOST`. `WON` et `LOST` sont définitifs : toute autre transition renvoie 409 `INVALID_TRANSITION`. Propose uniquement les boutons permis.
- Après `WON`, l'équipe active l'offre avec les droits manuels (`POST /admin/organizations/{id}/entitlement-overrides`).

## 12. Mot de passe oublié

- `POST /auth/password/forgot` avec `{ "email" }` renvoie toujours **202**, que le compte existe ou non. 429 au-delà de 3 demandes par heure pour une même adresse.
- L'e-mail contient un lien vers `reset-password.html#token=…`. Le jeton est **après le `#`** : lis-le avec `new URLSearchParams(location.hash.slice(1)).get('token')`, puis efface-le avec `history.replaceState`.
- `POST /auth/password/reset` avec `{ "token", "password" }` renvoie **204**. Toutes les sessions sont fermées : renvoie vers la connexion. Erreurs : 400 `INVALID_RESET_TOKEN` (lien expiré, déjà utilisé ou faux) et 400 `WEAK_PASSWORD` (raisons dans `errors[]`, comme à l'inscription). Après un `WEAK_PASSWORD`, le lien reste valable.

## 8. Demander un changement

Besoin d'un champ, d'un filtre ou d'un endpoint absent ? Écris-le dans ta réponse à l'utilisateur, sous un titre « Demandes pour le backend ». Chaque changement d'API met à jour ce fichier et `openapi.json`.
