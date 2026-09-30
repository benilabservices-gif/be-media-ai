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
| `GET /public/questionnaire`, `/public/diagnostics/*` | À mocker | Prochaine étape backend (M3) |
| `GET /public/catalog` | À mocker | M4 |
| `/orgs/{id}/passport`, `/action-plan`, `/entitlements`, `/website-projects` | À mocker | M3 à M6 |

## 2. Configuration

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
5. Pour M3 : les endpoints `/public/diagnostics/{id}/*` exigeront l'en-tête `X-Diagnostic-Token` (jeton renvoyé par `POST /public/diagnostics`). Tu peux déjà prévoir le paramètre.

## 7. Types

Pour de l'autocomplétion en JavaScript (JSDoc) sans maintenance manuelle :

```bash
npx openapi-typescript platform/contracts/openapi.json -o digital360/js/api/types.d.ts
```

## 8. Demander un changement

Besoin d'un champ, d'un filtre ou d'un endpoint absent ? Écris-le dans ta réponse à l'utilisateur, sous un titre « Demandes pour le backend ». Chaque changement d'API met à jour ce fichier et `openapi.json`.
