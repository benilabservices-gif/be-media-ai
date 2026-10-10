# BENILAB Digital360 — Architecture UX/UI

## 1. SITEMAP COMPLET

### PUBLIC / LANDING
- `/` — Landing page + intro diagnostic
- `/diagnostic` — Questionnaire complet (étapes 1-8)
- `/score` — Digital Score + décomposition par catégorie
- `/passport` — Digital Passport de l'entreprise
- `/plan` — Plan de transformation en 5 phases
- `/offres` — Présentation des offres (Start, Essential, Growth, Performance)

### ESPACE CLIENT (dashboard.benilab360.com)
- `/client/dashboard` — Tableau de bord principal
- `/client/entreprise` — Mon entreprise / Digital Passport
- `/client/site` — Mon site web (état, aperçu, modifications)
- `/client/google-business` — Google Business
- `/client/reseaux` — Réseaux sociaux + calendrier éditorial
- `/client/contenus` — Content Studio (idées, brouillons, publiés)
- `/client/prospects` — CRM / Leads (tableau + Kanban)
- `/client/email` — Email Marketing
- `/client/publicite` — Ads Center
- `/client/conversion` — Conversion Center
- `/client/analytics` — Analytics
- `/client/abonnement` — Mon abonnement + facturation
- `/client/support` — Support / tickets

### ESPACE ADMIN (admin.benilab360.com)
- `/admin/dashboard` — Vue générale + revenus
- `/admin/clients` — Liste clients + filtres
- `/admin/client/:id` — Fiche client complète
- `/admin/diagnostics` — Tous les diagnostics
- `/admin/sites` — Kanban workflow sites
- `/admin/contenus` — Gestion contenus
- `/admin/leads` — Prospects tous clients
- `/admin/campagnes` — Campagnes publicitaires
- `/admin/abonnements` — Abonnements + facturation
- `/admin/equipe` — Gestion équipe / rôles
- `/admin/analytics` — Analytics agence
- `/admin/parametres` — Paramètres

---

## 2. USER FLOWS PRINCIPAUX

### Flow Diagnostic
1. Landing → CTA "Faire mon diagnostic gratuit"
2. Diagnostic étape 1: Identité entreprise
3. Diagnostic étape 2: Présence web
4. Diagnostic étape 3: Google / réseaux
5. Diagnostic étape 4: Acquisition
6. Diagnostic étape 5: Conversion
7. Diagnostic étape 6: Fidélisation
8. Diagnostic étape 7: Contenu
9. → Calcul score → Page Score
10. → Page Passport
11. → Page Plan de transformation
12. → CTA vers offre Digital Start

### Flow Client Dashboard
1. Connexion → Dashboard
2. Voir score + actions recommandées
3. Naviguer vers modules souhaités
4. Demander modification site
5. Valider contenu
6. Voir analytics

### Flow Admin
1. Connexion → Dashboard admin
2. Voir vue d'ensemble clients/revenus
3. Filtrer clients
4. Ouvrir fiche client
5. Suivre workflow sites
6. Gérer abonnements

---

## 3. ARCHITECTURE FRONTEND

### Technologies
- Vanilla HTML/CSS/JS (pas de framework pour MVP)
- CSS custom properties pour le design system
- SPA routing côté JS pour les dashboards
- localStorage pour le prototype (transition vers API)

### Structure des fichiers
```
digital360/
  index.html          → Landing page + diagnostic
  dashboard.html      → Espace client
  admin.html          → Espace admin
  css/
    design-system.css → Design system complet
    landing.css       → Styles landing page
    dashboard.css     → Styles dashboard client
    admin.css         → Styles admin
  js/
    app.js            → Router + state management
    diagnostic.js     → Logique questionnaire
    scoring.js        → Calcul score digital
    charts.js         → Graphiques analytics
```

---

## 4. DESIGN SYSTEM

### Couleurs
```
--bg-primary:      #0a0a0f
--bg-secondary:    #111118
--bg-tertiary:     #1a1a25
--bg-card:         #15151e
--accent-primary:  #7fb5a8   (teal — confiance, croissance)
--accent-secondary:#a8d4c8   (light teal)
--accent-warm:     #c4a882   (warm gold — premium)
--accent-rose:     #b8a9c9   (soft purple)
--text-primary:    #e8e4df
--text-secondary:  #9a95a0
--text-muted:      #5c5764
--success:         #4ade80
--warning:         #fbbf24
--error:           #f87171
--border:          rgba(127,181,168,0.12)
```

### Typographies
- Display: 'Playfair Display', serif (titres hero, accents)
- Body: 'Outfit', sans-serif (corps, UI)

### Boutons
- Primary: bg accent, text dark, rounded 100px
- Outline: border accent, transparent bg
- Ghost: transparent, hover bg subtle
- Size: sm(10px pad), md(14px), lg(18px)

### Cartes
- Border radius: 16px
- Border: 1px solid var(--border)
- Background: var(--bg-card)
- Shadow: hover lift + glow

### Inputs
- Border radius: 12px
- Padding: 14px 18px
- Border: 1px solid var(--border)
- Focus: border accent

---

## 5. COMPOSANTS RÉUTILISABLES

1. Button (primary, outline, ghost, icon)
2. Card (standard, metric, service, pricing)
3. Badge (status, category, priority)
4. Input (text, email, tel, textarea, select)
5. Modal
6. ProgressBar / Stepper
7. ScoreRing (circular score)
8. Sidebar (navigation)
9. Navbar (top header)
10. Table (data)
11. Chart (analytics)
12. TabGroup
13. EmptyState
14. LoadingSkeleton
15. NotificationBadge
16. KanbanBoard
17. Timeline
18. CalendarGrid
19. StatCard
20. AlertBanner

---

## 6. PRIORISATION MVP/V2/V3

### MVP (Phase 1)
- Landing page
- Diagnostic complet (8 étapes)
- Digital Score + décomposition
- Digital Passport
- Plan de transformation
- Offres tarifaires
- Page paiement (simulation)
- Dashboard client basique
- Admin basic (clients, vues)

### V2 (Phase 2)
- Module site web (workflow création)
- Module Google Business
- Module réseaux sociaux (calendrier)
- Module contenu (Content Studio)
- Module prospects (CRM)
- Module analytics
- Module abonnement

### V3 (Phase 3)
- Module publicité (Ads Center)
- Module email marketing
- Module conversion (funnels)
- Automations
- Intégrations API réelles
- Multi-utilisateurs RBAC complet
