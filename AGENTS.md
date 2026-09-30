# Règles pour les agents qui travaillent sur ce dépôt

Deux agents travaillent **en parallèle** sur BENILAB Digital360. Ces règles évitent qu'ils se bloquent ou se marchent dessus.

## Répartition

| Dossier | Responsable | Rôle |
|---|---|---|
| `digital360/` | **Kilo** | UX/UI, pages, composants, CSS, JS d'affichage |
| `platform/` | **Claude Code** | Backend : API, logique métier, base de données, tests |
| `vercel.json`, `.vercelignore`, `.github/` | Claude Code | Déploiement et CI |
| Site Be Media AI (`index.html`, `css/`, `js/`, `academy360/`, `booster360/`) | Selon la demande de l'utilisateur | — |

- Ne modifie pas un fichier du dossier de l'autre agent. S'il faut y changer quelque chose, écris la demande dans ta réponse à l'utilisateur.
- Ne committe que tes propres fichiers (`git add <chemins>`, jamais `git add -A` ni `git add .`).
- **Après chaque commit, pousse aussitôt** : `git push` (commande qui se termine). Vercel republie alors le site automatiquement en une à deux minutes : c'est ainsi que l'utilisateur voit tes corrections en ligne. Si le push est refusé parce que la branche distante a avancé, fais `git pull --rebase` puis `git push` ; en cas de conflit, arrête-toi et signale-le à l'utilisateur au lieu de forcer.
- Ne jamais utiliser `git push --force`.

## Contrat entre frontend et backend

- **Contrat vivant** : [`platform/contracts/README.md`](platform/contracts/README.md) indique quels endpoints sont disponibles, comment s'authentifier et quelles erreurs gérer ; [`platform/contracts/openapi.json`](platform/contracts/openapi.json) en est la version machine. Kilo le relit au début de chaque tâche.
- La conception d'ensemble est dans [`platform/ARCHITECTURE.md`](platform/ARCHITECTURE.md), en particulier le §13 (API) et le §15 (contrat avec Kilo).
- La logique métier vit **uniquement** côté serveur : le frontend ne calcule ni score, ni prix, ni droits, et ne contient pas les questions du diagnostic en dur.
- Tant qu'un endpoint n'existe pas, le frontend utilise des mocks placés **uniquement** dans `digital360/js/api/mocks/`. Chaque fichier commence par `// MOCK — à supprimer quand <endpoint> est disponible`.
- Les prix sont **HT** et chaque prix affiché porte la mention « HT ». Devises : XOF, XAF, EUR.
- Toute donnée issue d'un utilisateur ou de l'API est insérée avec `textContent` (jamais `innerHTML` brut).

## Commandes interdites dans l'outil shell

**Ne lance jamais de processus qui ne se termine pas** : `python -m http.server`, `npm run dev`, `uvicorn --reload`, `tail -f`, `watch`…, même avec `&` à la fin. L'outil shell attend la fin de la sortie du processus : l'agent reste bloqué indéfiniment et l'autre agent doit continuer sans lui.

Pour tester une page, demande à l'utilisateur de l'ouvrir, ou utilise une commande qui se termine (`python -m py_compile`, un lint, `curl` sur un serveur déjà lancé par l'utilisateur).
