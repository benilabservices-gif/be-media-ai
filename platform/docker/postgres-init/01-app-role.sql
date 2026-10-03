-- Exécuté une seule fois, à la création du volume PostgreSQL.
-- L'application se connecte avec un rôle NON superuser et sans BYPASSRLS :
-- sinon PostgreSQL ignorerait les politiques d'isolation des tenants (RLS).
CREATE ROLE digital360 LOGIN PASSWORD 'digital360' NOSUPERUSER NOBYPASSRLS NOCREATEROLE;
CREATE DATABASE digital360 OWNER digital360;
