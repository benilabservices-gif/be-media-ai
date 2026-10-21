// MOCK — à supprimer quand GET /public/questionnaire est disponible
// Répond au même format que l'API : sections avec questions, options, visible_if

export default {
    getQuestionnaire: () => ({
        version: 1,
        sections: [
            {
                key: 'identity',
                title: 'Identité de l\'entreprise',
                questions: [
                    {
                        key: 'company',
                        label: 'Nom de l\'entreprise',
                        type: 'TEXT',
                        required: true
                    },
                    {
                        key: 'sector',
                        label: 'Secteur d\'activité',
                        type: 'SINGLE',
                        required: false,
                        options: [
                            { value: 'restaurant', label: 'Restaurant / Hospitality' },
                            { value: 'retail',      label: 'Commerce / Retail' },
                            { value: 'services',    label: 'Services professionnels' },
                            { value: 'beauty',      label: 'Beauté / Coiffure' },
                            { value: 'health',      label: 'Santé / Bien-être' },
                            { value: 'education',   label: 'Éducation / Formation' },
                            { value: 'tech',        label: 'Technologie' },
                            { value: 'artisan',     label: 'Artisan / Manufacture' },
                            { value: 'other',       label: 'Autre' }
                        ]
                    },
                    {
                        key: 'city',
                        label: 'Ville',
                        type: 'TEXT',
                        required: false
                    },
                    {
                        key: 'phone',
                        label: 'Téléphone',
                        type: 'PHONE',
                        required: false
                    },
                    {
                        key: 'whatsapp',
                        label: 'WhatsApp',
                        type: 'PHONE',
                        required: false
                    },
                    {
                        key: 'email',
                        label: 'Email',
                        type: 'EMAIL',
                        required: false
                    },
                    {
                        key: 'description',
                        label: 'Décrivez votre activité',
                        type: 'TEXT',
                        required: false
                    }
                ]
            },
            {
                key: 'presence',
                title: 'Présence web',
                questions: [
                    {
                        key: 'site',
                        label: 'Possédez-vous un site web ?',
                        type: 'SINGLE',
                        required: true,
                        options: [
                            { value: 'oui',       label: 'Oui, mon site est en ligne' },
                            { value: 'en-cours',  label: 'En cours de création' },
                            { value: 'non',       label: 'Non, je n\'ai pas de site' }
                        ]
                    },
                    {
                        key: 'domain',
                        label: 'Avez-vous un nom de domaine ?',
                        type: 'SINGLE',
                        required: false,
                        options: [
                            { value: 'oui', label: 'Oui' },
                            { value: 'non', label: 'Non' }
                        ]
                    },
                    {
                        key: 'emailprof',
                        label: 'Avez-vous un email professionnel ?',
                        type: 'SINGLE',
                        required: false,
                        options: [
                            { value: 'oui',  label: 'Oui — ex: nom@entreprise.com' },
                            { value: 'non',  label: 'Non — j\'utilise Gmail/Outlook personnel' }
                        ]
                    }
                ]
            },
            {
                key: 'visibility',
                title: 'Visibilité en ligne',
                questions: [
                    {
                        key: 'gb',
                        label: 'Avez-vous une fiche Google Business ?',
                        type: 'SINGLE',
                        required: true,
                        options: [
                            { value: 'oui',      label: 'Oui, elle est créée et validée' },
                            { value: 'en-cours', label: 'J\'ai commencé mais c\'est incomplet' },
                            { value: 'non',      label: 'Non, je n\'en ai pas' }
                        ]
                    },
                    {
                        key: 'reviews',
                        label: 'Combien d\'avis Google possédez-vous ?',
                        type: 'SINGLE',
                        required: false,
                        visible_if: { fact: 'has_google_business', eq: true },
                        options: [
                            { value: '0-5',  label: '0 à 5 avis' },
                            { value: '6-20', label: '6 à 20 avis' },
                            { value: '20+',  label: 'Plus de 20 avis' },
                            { value: 'n/a',  label: 'Pas de fiche Google' }
                        ]
                    },
                    {
                        key: 'socials',
                        label: 'Quels réseaux sociaux utilisez-vous ?',
                        type: 'MULTI',
                        required: false,
                        options: [
                            { value: 'facebook', label: '📘 Facebook' },
                            { value: 'instagram', label: '📸 Instagram' },
                            { value: 'tiktok',   label: '🎵 TikTok' },
                            { value: 'linkedin', label: '💼 LinkedIn' }
                        ]
                    },
                    {
                        key: 'seo',
                        label: 'Votre site apparaît-il dans les résultats Google ?',
                        type: 'SINGLE',
                        required: false,
                        visible_if: { fact: 'has_website', eq: true },
                        options: [
                            { value: 'top3',  label: 'Oui, en première page' },
                            { value: 'page2', label: 'Oui, mais page 2 ou plus' },
                            { value: 'never', label: 'Jamais testé / pas de site' }
                        ]
                    }
                ]
            },
            {
                key: 'acquisition',
                title: 'Acquisition',
                questions: [
                    {
                        key: 'ads',
                        label: 'Investissez-vous dans la publicité en ligne ?',
                        type: 'SINGLE',
                        required: false,
                        options: [
                            { value: 'meta',   label: 'Oui, Meta Ads (Facebook/Instagram)' },
                            { value: 'google', label: 'Oui, Google Ads' },
                            { value: 'tiktok', label: 'Oui, TikTok Ads' },
                            { value: 'none',   label: 'Non, je n\'ai jamais fait de publicité' }
                        ]
                    }
                ]
            },
            {
                key: 'conversion',
                title: 'Conversion',
                questions: [
                    {
                        key: 'contact',
                        label: 'Comment vos clients vous contactent-ils principalement ?',
                        type: 'SINGLE',
                        required: false,
                        options: [
                            { value: 'whatsapp', label: 'WhatsApp' },
                            { value: 'phone',    label: 'Téléphone' },
                            { value: 'form',     label: 'Formulaire sur le site' },
                            { value: 'dm',       label: 'Message privé (réseaux)' },
                            { value: 'walkin',   label: 'Passage physique uniquement' }
                        ]
                    },
                    {
                        key: 'booking',
                        label: 'Proposez-vous un système de réservation ou paiement en ligne ?',
                        type: 'SINGLE',
                        required: false,
                        options: [
                            { value: 'both',        label: 'Les deux (réservation + paiement)' },
                            { value: 'reservation', label: 'Réservation uniquement' },
                            { value: 'payment',     label: 'Paiement en ligne uniquement' },
                            { value: 'none',        label: 'Non, rien en ligne' }
                        ]
                    }
                ]
            },
            {
                key: 'retention',
                title: 'Fidélisation & suivi',
                questions: [
                    {
                        key: 'clientbase',
                        label: 'Avez-vous une base de clients (emails, téléphones) ?',
                        type: 'SINGLE',
                        required: false,
                        options: [
                            { value: 'crm',        label: 'Oui, dans un CRM' },
                            { value: 'spreadsheet', label: 'Oui, dans un fichier Excel / Google Sheets' },
                            { value: 'notebook',   label: 'Quelques notes, pas de système structuré' },
                            { value: 'none',       label: 'Non, pas de liste clients' }
                        ]
                    },
                    {
                        key: 'comm',
                        label: 'Envoyez-vous des communications à vos clients existants ?',
                        type: 'SINGLE',
                        required: false,
                        options: [
                            { value: 'email',     label: 'Oui, par email' },
                            { value: 'whatsapp',  label: 'Oui, par WhatsApp' },
                            { value: 'social',    label: 'Oui, via les réseaux sociaux' },
                            { value: 'none',      label: 'Non, pas de communication régulière' }
                        ]
                    }
                ]
            },
            {
                key: 'content',
                title: 'Contenu',
                questions: [
                    {
                        key: 'freq',
                        label: 'À quelle fréquence publiez-vous du contenu ?',
                        type: 'SINGLE',
                        required: false,
                        options: [
                            { value: 'daily',    label: 'Quotidiennement' },
                            { value: 'weekly',   label: '1 à 2 fois par semaine' },
                            { value: 'monthly',  label: 'Quelques fois par mois' },
                            { value: 'rarely',   label: 'Rarement ou jamais' }
                        ]
                    },
                    {
                        key: 'producer',
                        label: 'Qui produit votre contenu ?',
                        type: 'SINGLE',
                        required: false,
                        options: [
                            { value: 'internal',  label: 'Moi-même / mon équipe' },
                            { value: 'freelance', label: 'Un freelance' },
                            { value: 'agency',    label: 'Une agence' },
                            { value: 'none',      label: 'Je n\'ai pas encore de contenu' }
                        ]
                    },
                    {
                        key: 'goal',
                        label: 'Quel est votre principal objectif digital ?',
                        type: 'SINGLE',
                        required: true,
                        options: [
                            { value: 'presence',  label: 'Créer ma première présence en ligne' },
                            { value: 'visibility', label: 'Être plus visible localement' },
                            { value: 'sales',     label: 'Vendre plus / générer des leads' },
                            { value: 'brand',     label: 'Renforcer ma marque' }
                        ]
                    }
                ]
            }
        ]
    }),

    completeDiagnostic: (id) => ({
        id,
        score: {
            total: 63,
            disclaimer: 'Le Digital Score est un indicateur interne de diagnostic établi par BENILAB à partir de vos déclarations. Il ne constitue pas une certification.',
            categories: {
                PRESENCE:   { score: 72, label: 'Présence' },
                VISIBILITY: { score: 55, label: 'Visibilité' },
                ACQUISITION:{ score: 40, label: 'Acquisition' },
                CONVERSION: { score: 65, label: 'Conversion' },
                RETENTION:  { score: 58, label: 'Fidélisation' }
            },
            maturity_level: 'ESTABLISHED',
            maturity_label: 'Bases digitales solides'
        },
        passport: {
            items: [
                { key: 'WEBSITE',    status: 'ACTIVE',        source: 'DECLARED', details: { url: 'www.letedelice.ci' } },
                { key: 'DOMAIN',     status: 'ACTIVE',         source: 'DECLARED', details: { fqdn: 'letedelice.ci', expires_at: '2027-08-15' } },
                { key: 'EMAIL',      status: 'ACTIVE',         source: 'DECLARED', details: { address: 'contact@letedelice.ci' } },
                { key: 'GOOGLE_BUSINESS', status: 'IN_PROGRESS', source: 'DECLARED', details: { reviews_count: 7, rating: 4.2 } },
                { key: 'FACEBOOK',   status: 'ACTIVE',         source: 'DECLARED', details: { followers: 340 } },
                { key: 'INSTAGRAM',  status: 'ACTIVE',         source: 'DECLARED', details: { followers: 180 } },
                { key: 'TIKTOK',     status: 'NOT_CONFIGURED', source: 'DECLARED' },
                { key: 'WHATSAPP',   status: 'ACTIVE',         source: 'DECLARED' },
                { key: 'SEO',        status: 'IN_PROGRESS',    source: 'DECLARED' },
                { key: 'ADS',        status: 'NOT_CONFIGURED', source: 'DECLARED' },
                { key: 'EMAILING',   status: 'NOT_CONFIGURED', source: 'DECLARED' },
                { key: 'CRM',        status: 'NOT_CONFIGURED', source: 'DECLARED' }
            ]
        },
        action_plan: {
            items: [
                { rule_key: 'no_google_business',  module: 'VISIBILITY', priority: 'HIGH',     reason: 'Votre fiche Google Business peut être optimisée pour améliorer votre visibilité locale.', current_state: 'Fiche existante mais incomplète', recommended_action: 'Optimiser la fiche Google Business (photos, horaires, catégories)', product_code: 'DIGITAL_GROWTH', cta: 'ACTIVATE', status: 'PROPOSED' },
                { rule_key: 'social_low_frequency', module: 'VISIBILITY', priority: 'MEDIUM',   reason: 'Votre fréquence de publication est insuffisante pour maintenir une visibilité constante.', current_state: '1 à 2 publications par semaine', recommended_action: 'Augmenter la fréquence et structurer un calendrier éditorial', product_code: 'DIGITAL_GROWTH', cta: 'ACTIVATE', status: 'PROPOSED' },
                { rule_key: 'no_ads',              module: 'ACQUISITION', priority: 'LOW',      reason: 'Aucune publicité en ligne n\'est configurée. Les campagnes payantes peuvent accélérer votre acquisition.', current_state: 'Pas de campagne active', recommended_action: 'Lancer une première campagne Meta Ads ciblée', product_code: 'DIGITAL_PERFORMANCE', cta: 'CONTACT', status: 'PROPOSED' },
                { rule_key: 'no_crm',              module: 'RETENTION',  priority: 'MEDIUM',   reason: 'Vous ne disposez pas encore d\'un système structuré pour suivre vos prospects.', current_state: 'Aucun CRM', recommended_action: 'Mettre en place un suivi des prospects et relances automatiques', product_code: 'DIGITAL_PERFORMANCE', cta: 'ACTIVATE', status: 'PROPOSED' }
            ]
        }
    }),

    getCatalog: (currency) => {
        const currencies = {
            XOF: { DIGITAL_START: 89900, DIGITAL_ESSENTIAL: 25000, DIGITAL_GROWTH: 50000, DIGITAL_PERFORMANCE: 100000 },
            EUR: { DIGITAL_START: 13705, DIGITAL_ESSENTIAL: 3817,  DIGITAL_GROWTH: 7634,   DIGITAL_PERFORMANCE: 15268 }
        };
        const prices = currencies[currency] || currencies.XOF;
        return {
            currency,
            products: [
                { code: 'DIGITAL_START',        name: 'Digital Start',         kind: 'ONE_TIME',    module: 'PRESENCE',  price: { amount: prices.DIGITAL_START, currency }, description: 'Pack complet : site web, domaine, hébergement, SEO de base.' },
                { code: 'DIGITAL_ESSENTIAL',    name: 'Digital Essential',     kind: 'SUBSCRIPTION',module: 'PRESENCE',  price: { amount: prices.DIGITAL_ESSENTIAL, currency, period: 'MONTH' }, description: 'Maintenance, mises à jour, 4 contenus/mois, suivi Google Business.' },
                { code: 'DIGITAL_GROWTH',       name: 'Digital Growth',        kind: 'SUBSCRIPTION',module: 'VISIBILITY',price: { amount: prices.DIGITAL_GROWTH, currency, period: 'MONTH' }, description: 'Tout Essential + gestion réseaux, 8 contenus/mois, SEO local.' },
                { code: 'DIGITAL_PERFORMANCE',  name: 'Digital Performance',   kind: 'SUBSCRIPTION',module: 'ACQUISITION',price: { amount: prices.DIGITAL_PERFORMANCE, currency, period: 'MONTH' }, description: 'Tout Growth + publicité, email marketing, automatisations, reporting avancé.' }
            ]
        };
    }
};
