-- =============================================================================
-- jt-ipam — Postgres 初始化（一次性）
--
-- 執行：
--   sudo -u postgres psql -v "jtipam_password=$JTIPAM_DB_PASSWORD" -f jt-ipam-init.sql
--
-- 安全（A02 / A05）：
--   * 帳號使用 SCRAM-SHA-256（pg_hba.conf 對應設定）
--   * jt_ipam role 不是 superuser；只擁有自己的 DB
--   * 啟用 pgcrypto / citext / pg_trgm / btree_gist
-- =============================================================================

-- 角色（密碼從命令列以 -v 傳入；勿寫死）
\set jtipam_password '''' :jtipam_password ''''

CREATE ROLE jt_ipam LOGIN PASSWORD :jtipam_password;
ALTER ROLE jt_ipam SET search_path = public;

CREATE DATABASE jt_ipam OWNER jt_ipam ENCODING 'UTF8' LC_COLLATE 'C.UTF-8' LC_CTYPE 'C.UTF-8' TEMPLATE template0;

\connect jt_ipam

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- jt_ipam 為 DB owner，已有對自家 schema 的完整權限
ALTER SCHEMA public OWNER TO jt_ipam;
