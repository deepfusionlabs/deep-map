#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap's own SQL declaration reader (build/sql_reader.py): what a .sql file declares, read the same way on every
engine end, in time linear in the file (design .ai/design/2026-11/sql/design.md section 4; gate plan section 13).

The field report: a .NET repository's 12 T-SQL scripts were not in the graph. The engine's optional SQL grammar finds
0 objects in SQL Server scripts (bracketed names, GO, CREATE PROCEDURE) and parses them in superlinear time, so DeepMap
reads .sql itself: every file a :Module, every table / view / type / package a :Class, every procedure / function /
trigger / Oracle package member a :Function, each with a deterministic summary and `call_by_name: False`.

Sections (the reader itself; pure, no engine, no Neo4j, no network):
  A  dialects: exact [(kind, qualified, line)] for T-SQL (brackets, GO, IF NOT EXISTS wrappers, CREATE OR ALTER, the
     fixed-point stub), SSDT one-object files, PostgreSQL, MySQL (DELIMITER, DEFINER, END IF), mysqldump `/*!` wrappers,
     SQLite, Oracle (EDITIONABLE, PACKAGE + PACKAGE BODY members, a package with no `/` lines, TYPE BODY), the
     generated 1,935-line script and its variants A-E, Northwind's [Order Details], EF Core idempotent scripts and SSMS
     existence checks (definitions run from EXEC(N'...') / sp_executesql), variable-executed batches, Oracle EXECUTE
     IMMEDIATE; from the review: mysqldump's two view shapes (8.0 SELECT 1 placeholder, 5.7 placeholder table), an SSMS
     literal that starts with its header comment, PostgreSQL DO blocks, bracketed / @stmt / SELECT @v executors,
     VIRTUAL / FOREIGN / EXTERNAL tables, MySQL told by `) ENGINE=`, a DELIMITER // line or `#` comments alone.
  B  negative shapes (KL-mistake:008): CREATE in comments, in non-EXEC strings (PRINT / RAISERROR / SELECT, E'' and
     MySQL backslash strings), in a dollar body; temp tables; ALTER TABLE; pg_dump ALTER ... OWNER TO; non-node kinds;
     identifiers that hold "create"; GRANT / REVOKE privilege lists; SSMS template names; SQL*Plus REM / REMARK / PROMPT
     lines; a GO line inside an unclosed string or comment ends it; 'C:\' in T-SQL; ON DELETE / AFTER UPDATE / FOR
     UPDATE are not writes; sys.*, inserted / deleted / NEW and CTE names are not reads; from the review: COPY ... FROM
     stdin rows (apostrophes, /*, DDL text), MySQL / T-SQL hints inside comments and strings or as words (engine =,
     a delimiter column), concatenated dynamic SQL, Oracle q-quotes, MATERIALIZED VIEW LOG, a literal inside a nested
     batch, VALUES rows / @value / THROW / stop-word strings, the privilege rule itself.
  C  encodings: UTF-8, UTF-8 BOM + CRLF, UTF-16 LE / BE with BOM, UTF-16 LE / BE without BOM, cp1252: identical
     output.
  N  names: brackets with spaces and `]]`, double quotes, backticks, db..obj, Unicode, a comment before the name;
     stop words that are quoted or qualified ([dbo].[Statement], "values"); placeholder schemas (${schema}, $(Schema),
     &&schema..).
  T  facts: columns, references (inline and via ALTER TABLE, also to the one table of that name), params, trigger
     targets, reads / writes / runs with aliases, module alters / writes, dynamic_unread; END CASE, BEGIN TRAN,
     EXTRACT(YEAR FROM / TRIM(BOTH FROM / IS DISTINCT FROM, INSERT IGNORE.
  D  summaries: exact strings, <= 300 characters, no em or en dash.
  E  node dicts: keys, labels, `call_by_name is False`, `documented is False`, one :Module per file (empty and DML-only
     files too), de-duplication, two files -> two nodes, read_file raises only OSError, a 300-character Windows path,
     _os_path on long drive and UNC paths, a parse that ends past the deadline is slow.
  F  linear time on adversarial shapes at large sizes (1x vs 8x input, min of 3 runs, time(1x) >= 120 ms, bound 24x:
     linear gives 8, quadratic 64; and at most 60 ms/KB), the per-file deadline (Slow, also checked after parse), the
     8 MB stat-first cap, the per-sync byte budget.
  K  the per-file cache: miss then hit with byte-equal nodes, a corrupt (or too deeply nested) entry re-parsed, a
     READER_REV change misses, unused entries pruned by exact name, nothing else in the directory touched.
  Z  determinism, JSON round trip of the facts, a seeded fuzz (parse never raises).
  X  the extension lock: sql_reader.EXTS is in graphify_extract.SOURCE_CODE_EXTS and is the census's "SQL".

Integration (design sections 5-6; a temp DEEPMAP_HOME; the real-engine parts skip cleanly without graphify and must
pass on 0.8.39 and 0.8.51):
  G  graphify_extract._sql_supplement on fake engine results: a SQL-free tree gets the same object back; engine .sql
     nodes dropped with their calls / imports and the keys that target them, DeepMap's added; a reader exception is
     state "error" with the result unchanged; an unopenable file is counted; the cache in <graphify_out_dir>/sqlcache
     (hits, a corrupt entry, a READER_REV change, exact-name pruning); reader off: the same object, state "off" only
     beside an engine .sql node; the size limit and the byte budget in sorted order; the sync log line counts the
     files read in full apart from those read as a file node only, and its opt-out says what it does.
  H  DeepMap's own file rule over git and non-git trees: .gitignore (nested too, a tracked file it names),
     .graphifyignore, the engine's sensitive-file rule, the census's directory rule, macOS metadata, an upper-case
     suffix, a re-included build/ file; counts and examples per rule; a missing engine helper skips only its filter.
     The zero-node rule's question (_first_engine_source) under the same filters plus sync's vendor filter. Windows:
     a .sql path past MAX_PATH is listed in a git tree (tracked and untracked) and outside git (a directory the plain
     walk cannot list), and the zero-node rule never takes a path the engine cannot read for its source.
  I  the real engine through extract_with_report: reader on vs off, non-SQL nodes / calls / imports byte-identical,
     SQL nodes exactly the reader's; a SQL-only tree and a .sql + .R + .pl tree get a graph (no ast fall-back) and
     READY with not-parsed lines; an engine that gives nothing for .py still fails (sync falls back to ast); .sql
     beside a .py the engine never saw (.graphifyignore, .gitignore, git, the sensitive-file rule) keeps its SQL
     graph, while a tracked .cs or a .py no rule names the engine gave nothing for still fails.
  J  edge isolation: build_name_index, sync._resolve_calls, the typed-edge Resolver and resolve_imports never bind a
     SQL node.
  V  the verdict: the field case READY, ALTER-only migrations READY, gitignored dumps subtracted, the reader error
     named, the not-read / left-out / dynamic-SQL lines, the ast fall-back unchanged, long paths (a .sql the reader
     read is no finding; a .sql it never listed and a .cs beside it still are).
  Q  sync's own wiring: a real sync.sync() (extraction and Neo4j writes stubbed) hands the reader's report to
     build_coverage, so the manifest's coverage has "sql", the left-out files are subtracted and no SQL density
     reason fires.
  L  the switch: SQL_DECLARATIONS_DEFAULT off and sync_status's knob set beside it, DEEPMAP_ENGINE_DROP_EXTS internal,
     the truth table, an explicit 0 recorded and replayed, every OFF word gives the pre-change mapping.
  S  Phase B2: the case shim drops .sql from the engine's table at import time before the odd-case aliasing; the shim
     runs only when the reader is on, the tree holds .sql and tree_sitter_sql is importable.
Not here: M (the impact notes, build/retrieval.py) and P (the G-SQL protocol lock, benchmark/sql_reader/PREREG.md).
SQL_READER_GATES_ONLY=G,H,... runs a subset.

Fixtures are Python strings written to a temp dir (never committed .sql files: they would enter this repository's own
graph). Usage: python tests/sql_reader_gates.py
"""
from __future__ import annotations

import contextlib
import gc
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "build"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import sql_reader as S  # noqa: E402
    _IMPORT_ERROR = None
except Exception as exc:  # the RED state before the module exists
    S = None
    _IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

_PASS = _FAIL = 0
_TMP: list = []
# The integration sections write under DEEPMAP_HOME (graphify's output dir and the reader's cache): a short temp one,
# removed with the other temp dirs at the end. The switches start unset.
_TMP.append(Path(tempfile.mkdtemp(prefix="dm_sqh_")))
os.environ["DEEPMAP_HOME"] = str(_TMP[0])
for _k in ("DEEPMAP_SQL_DECLARATIONS", "DEEPMAP_ENGINE_DROP_EXTS", "DEEPMAP_EXTRACTOR", "DEEPMAP_EXCLUDE_TESTS",
           "DEEPMAP_PRECISE_CALLS"):
    os.environ.pop(_k, None)


def check(desc: str, cond, detail="") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {detail}"))


def tmpdir() -> Path:
    d = Path(tempfile.mkdtemp(prefix="dm_sql_"))
    _TMP.append(d)
    return d


def line_of(text: str, needle: str, nth: int = 1) -> int:
    """The 1-based line of the nth occurrence of `needle`: an oracle independent of the reader."""
    pos = -1
    for _ in range(nth):
        pos = text.index(needle, pos + 1)
    return text.count("\n", 0, pos) + 1


def decls(text: str) -> list:
    return [(d["kind"], d["qualified"], d["line"]) for d in S.parse(text)["decls"]]


def by_q(facts: dict) -> dict:
    return {d["qualified"]: d for d in facts["decls"]}


# ---------------------------------------------------------------------------------------------------- fixtures
TSQL = """-- SQL Server T-SQL (caf\u00e9 edition), the way a .NET shop writes a deployment / DbUpdates script
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO

IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Customers]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Customers](
    [Id] [int] IDENTITY(1,1) NOT NULL,
    [Email] [nvarchar](256) NOT NULL,
    [CreatedAt] [datetime2](7) NOT NULL CONSTRAINT [DF_Customers_CreatedAt] DEFAULT (sysutcdatetime()),
 CONSTRAINT [PK_Customers] PRIMARY KEY CLUSTERED ([Id] ASC)
) ON [PRIMARY]
END
GO

CREATE TABLE [dbo].[Orders](
    [Id] [int] IDENTITY(1,1) NOT NULL,
    [CustomerId] [int] NOT NULL,
    [Total] [decimal](18, 2) NOT NULL,
    [Status] [nvarchar](20) NULL,
 CONSTRAINT [PK_Orders] PRIMARY KEY CLUSTERED ([Id] ASC)
)
GO

CREATE TABLE dbo.OrderLines (
    Id INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    OrderId INT NOT NULL,
    Sku NVARCHAR(50) NOT NULL,
    Qty INT NOT NULL
);
GO

ALTER TABLE [dbo].[Orders]  WITH CHECK ADD  CONSTRAINT [FK_Orders_Customers] FOREIGN KEY([CustomerId])
REFERENCES [dbo].[Customers] ([Id])
GO

ALTER TABLE [dbo].[Orders] CHECK CONSTRAINT [FK_Orders_Customers]
GO

ALTER TABLE dbo.OrderLines ADD CONSTRAINT FK_OrderLines_Orders FOREIGN KEY (OrderId) REFERENCES dbo.Orders (Id);
GO

CREATE NONCLUSTERED INDEX [IX_Orders_CustomerId] ON [dbo].[Orders] ([CustomerId] ASC)
GO

CREATE VIEW [dbo].[vw_CustomerTotals]
AS
SELECT c.Id, c.Email, SUM(o.Total) AS Total
FROM dbo.Customers c
INNER JOIN dbo.Orders o ON o.CustomerId = c.Id
GROUP BY c.Id, c.Email
GO

CREATE PROCEDURE dbo.usp_GetOrdersForCustomer
    @CustomerId INT
AS
BEGIN
    SET NOCOUNT ON;
    SELECT o.Id, o.Total FROM dbo.Orders o WHERE o.CustomerId = @CustomerId;
END
GO

CREATE OR ALTER PROCEDURE [dbo].[usp_AddOrderLine]
    @OrderId INT,
    @Sku NVARCHAR(50),
    @Qty INT = 1
AS
BEGIN
    SET NOCOUNT ON;
    IF NOT EXISTS (SELECT 1 FROM dbo.Orders WHERE Id = @OrderId)
    BEGIN
        RAISERROR('Order not found', 16, 1);
        RETURN;
    END
    INSERT INTO dbo.OrderLines (OrderId, Sku, Qty) VALUES (@OrderId, @Sku, @Qty);
    EXEC dbo.usp_GetOrdersForCustomer @CustomerId = 1;
END
GO

CREATE FUNCTION dbo.fn_OrderTotal (@OrderId INT)
RETURNS DECIMAL(18,2)
AS
BEGIN
    DECLARE @t DECIMAL(18,2);
    SELECT @t = SUM(Qty) FROM dbo.OrderLines WHERE OrderId = @OrderId;
    RETURN @t;
END
GO

CREATE FUNCTION [dbo].[tvf_CustomerOrders] (@CustomerId INT)
RETURNS TABLE
AS
RETURN (SELECT Id, Total FROM dbo.Orders WHERE CustomerId = @CustomerId)
GO

CREATE TRIGGER [dbo].[trg_Orders_Audit] ON [dbo].[Orders]
AFTER INSERT, UPDATE
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE dbo.Orders SET Status = 'audited' WHERE Id IN (SELECT Id FROM inserted);
END
GO

IF OBJECT_ID('dbo.usp_Legacy', 'P') IS NOT NULL
    DROP PROCEDURE dbo.usp_Legacy;
GO
CREATE PROCEDURE dbo.usp_Legacy AS SELECT 1;
GO
"""

TSQL_EXPECT = [
    ("table", "dbo.Customers", "CREATE TABLE [dbo].[Customers]"),
    ("table", "dbo.Orders", "CREATE TABLE [dbo].[Orders]"),
    ("table", "dbo.OrderLines", "CREATE TABLE dbo.OrderLines"),
    ("view", "dbo.vw_CustomerTotals", "CREATE VIEW [dbo].[vw_CustomerTotals]"),
    ("procedure", "dbo.usp_GetOrdersForCustomer", "CREATE PROCEDURE dbo.usp_GetOrdersForCustomer"),
    ("procedure", "dbo.usp_AddOrderLine", "CREATE OR ALTER PROCEDURE [dbo].[usp_AddOrderLine]"),
    ("function", "dbo.fn_OrderTotal", "CREATE FUNCTION dbo.fn_OrderTotal"),
    ("function", "dbo.tvf_CustomerOrders", "CREATE FUNCTION [dbo].[tvf_CustomerOrders]"),
    ("trigger", "dbo.trg_Orders_Audit", "CREATE TRIGGER [dbo].[trg_Orders_Audit]"),
    ("procedure", "dbo.usp_Legacy", "CREATE PROCEDURE dbo.usp_Legacy"),
]

SSDT = {
    "dbo/Stored Procedures/usp_GetInvoice.sql": (
        "CREATE PROCEDURE [dbo].[usp_GetInvoice]\n    @Id int\nAS\n"
        "    SELECT [Id], [OrderId], [Amount] FROM [dbo].[Invoices] WHERE [Id] = @Id\nRETURN 0\n"),
    "dbo/Tables/Invoices.sql": (
        "CREATE TABLE [dbo].[Invoices]\n(\n    [Id] INT NOT NULL PRIMARY KEY IDENTITY,\n"
        "    [OrderId] INT NOT NULL,\n    [Amount] DECIMAL(18, 2) NOT NULL,\n"
        "    CONSTRAINT [FK_Invoices_Orders] FOREIGN KEY ([OrderId]) REFERENCES [dbo].[Orders]([Id])\n)\n"),
    "dbo/Views/vw_Invoices.sql": (
        "CREATE VIEW [dbo].[vw_Invoices]\n\tAS SELECT i.[Id], o.[CustomerId] FROM [dbo].[Invoices] i "
        "JOIN [dbo].[Orders] o ON o.[Id] = i.[OrderId]\n"),
}

PG = """-- PostgreSQL / ANSI schema
CREATE SCHEMA IF NOT EXISTS shop;

CREATE TABLE customers (
    id          SERIAL PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE orders (
    id           BIGSERIAL PRIMARY KEY,
    customer_id  INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    total_cents  BIGINT NOT NULL CHECK (total_cents >= 0),
    status       VARCHAR(20) NOT NULL DEFAULT 'new'
);

CREATE TABLE order_items (
    id          BIGSERIAL PRIMARY KEY,
    order_id    BIGINT NOT NULL,
    sku         TEXT NOT NULL,
    qty         INTEGER NOT NULL,
    CONSTRAINT fk_items_order FOREIGN KEY (order_id) REFERENCES orders(id)
);

CREATE TABLE shop.products (
    sku   TEXT PRIMARY KEY,
    name  TEXT NOT NULL
);

CREATE INDEX idx_orders_customer ON orders (customer_id);

CREATE VIEW customer_totals AS
SELECT c.id, c.email, SUM(o.total_cents) AS total
FROM customers c
JOIN orders o ON o.customer_id = c.id
GROUP BY c.id, c.email;

CREATE OR REPLACE FUNCTION order_total(p_order BIGINT) RETURNS BIGINT AS $$
DECLARE
    t BIGINT;
BEGIN
    SELECT SUM(qty) INTO t FROM order_items WHERE order_id = p_order;
    RETURN t;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION touch_order() RETURNS trigger AS $$
BEGIN
    UPDATE orders SET status = 'touched' WHERE id = NEW.order_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_items_touch AFTER INSERT ON order_items
FOR EACH ROW EXECUTE FUNCTION touch_order();

CREATE OR REPLACE PROCEDURE archive_orders(cutoff DATE)
LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM orders WHERE status = 'done';
END;
$$;

CREATE TABLE "Invoice" (
    "Id" SERIAL PRIMARY KEY,
    "OrderId" BIGINT REFERENCES orders(id)
);

CREATE UNLOGGED TABLE fastlog (id bigint, msg text);
CREATE MATERIALIZED VIEW mv_totals AS SELECT customer_id, SUM(total_cents) AS t FROM orders GROUP BY customer_id;
"""

PG_EXPECT = [
    ("table", "customers", "CREATE TABLE customers"),
    ("table", "orders", "CREATE TABLE orders"),
    ("table", "order_items", "CREATE TABLE order_items"),
    ("table", "shop.products", "CREATE TABLE shop.products"),
    ("view", "customer_totals", "CREATE VIEW customer_totals"),
    ("function", "order_total", "CREATE OR REPLACE FUNCTION order_total"),
    ("function", "touch_order", "CREATE FUNCTION touch_order"),
    ("trigger", "trg_items_touch", "CREATE TRIGGER trg_items_touch"),
    ("procedure", "archive_orders", "CREATE OR REPLACE PROCEDURE archive_orders"),
    ("table", "Invoice", 'CREATE TABLE "Invoice"'),
    ("table", "fastlog", "CREATE UNLOGGED TABLE fastlog"),
    ("materialized view", "mv_totals", "CREATE MATERIALIZED VIEW mv_totals"),
]

MYSQL = """-- MySQL schema with backticks, a DEFINER and a DELIMITER-wrapped procedure
CREATE TABLE IF NOT EXISTS `users` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `name` VARCHAR(100) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `posts` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `user_id` INT UNSIGNED NOT NULL,
  `title` VARCHAR(200) NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_posts_user` (`user_id`),
  CONSTRAINT `fk_posts_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE VIEW `user_post_counts` AS
SELECT u.id, COUNT(p.id) AS n FROM `users` u LEFT JOIN `posts` p ON p.user_id = u.id GROUP BY u.id;

DELIMITER $$
CREATE DEFINER=`root`@`localhost` PROCEDURE `add_post`(IN p_user INT, IN p_title VARCHAR(200))
BEGIN
  IF p_title IS NULL THEN
    SET p_title = 'it\\'s untitled';
  END IF;
  INSERT INTO `posts` (`user_id`, `title`) VALUES (p_user, p_title);
  SELECT COUNT(*) FROM `audit_log` WHERE `user_id` = p_user;
END$$

CREATE FUNCTION `post_count`(p_user INT) RETURNS INT DETERMINISTIC
BEGIN
  DECLARE n INT;
  SELECT COUNT(*) INTO n FROM `posts` WHERE `user_id` = p_user;
  RETURN n;
END$$
DELIMITER ;

CREATE TRIGGER `posts_bi` BEFORE INSERT ON `posts` FOR EACH ROW SET NEW.title = TRIM(NEW.title);
"""

MYSQL_EXPECT = [
    ("table", "users", "CREATE TABLE IF NOT EXISTS `users`"),
    ("table", "posts", "CREATE TABLE IF NOT EXISTS `posts`"),
    ("view", "user_post_counts", "CREATE VIEW `user_post_counts`"),
    ("procedure", "add_post", "CREATE DEFINER=`root`@`localhost` PROCEDURE"),
    ("function", "post_count", "CREATE FUNCTION `post_count`"),
    ("trigger", "posts_bi", "CREATE TRIGGER `posts_bi`"),
]

MYSQLDUMP = ("# CREATE TABLE hashcomment (x int)\n"
             "INSERT INTO t VALUES ('It\\'s CREATE TABLE fake2 (x int)');\n"
             "/*!50003 CREATE*/ /*!50017 DEFINER=`root`@`localhost`*/ /*!50003 TRIGGER `trg_dump` BEFORE INSERT ON `t` "
             "FOR EACH ROW SET NEW.a = 1 */;\n"
             "/*!50001 CREATE ALGORITHM=UNDEFINED */ /*!50013 DEFINER=`root`@`localhost` SQL SECURITY DEFINER */ "
             "/*!50001 VIEW `v_dump` AS select 1 AS `a` */;\n"
             "CREATE DEFINER=`root`@`localhost` PROCEDURE `p_dump`(IN a INT) BEGIN SELECT a FROM `t`; END;\n")

SQLITE = """-- SQLite schema
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS artist (
  artistid    INTEGER PRIMARY KEY AUTOINCREMENT,
  artistname  TEXT
);

CREATE TABLE IF NOT EXISTS track (
  trackid     INTEGER PRIMARY KEY,
  trackname   TEXT,
  trackartist INTEGER,
  FOREIGN KEY(trackartist) REFERENCES artist(artistid)
);

CREATE VIEW IF NOT EXISTS artist_tracks AS
SELECT a.artistname, t.trackname FROM artist a INNER JOIN track t ON t.trackartist = a.artistid;

CREATE TRIGGER IF NOT EXISTS track_ai AFTER INSERT ON track
BEGIN
  UPDATE artist SET artistname = artistname WHERE artistid = NEW.trackartist;
END;

CREATE UNIQUE INDEX IF NOT EXISTS ix_track_name ON track(trackname);
"""

ORACLE = """CREATE OR REPLACE PACKAGE billing AS
  PROCEDURE charge(p_order_id IN NUMBER);
  FUNCTION total_due(p_customer_id IN NUMBER) RETURN NUMBER;
END billing;
/
CREATE OR REPLACE PACKAGE BODY billing AS
  PROCEDURE charge(p_order_id IN NUMBER) IS
  BEGIN
    UPDATE orders SET status = 'CHARGED' WHERE id = p_order_id;
  END charge;
  FUNCTION total_due(p_customer_id IN NUMBER) RETURN NUMBER IS
    v NUMBER;
  BEGIN
    SELECT SUM(total) INTO v FROM orders WHERE customer_id = p_customer_id;
    RETURN v;
  END total_due;
END billing;
/
CREATE OR REPLACE EDITIONABLE PROCEDURE archive_orders AS
BEGIN
  INSERT INTO orders_archive SELECT * FROM orders;
END;
/
"""

ORACLE_NO_SLASH = """CREATE OR REPLACE PACKAGE BODY util AS
  PROCEDURE ping IS
  BEGIN
    NULL;
  END ping;
END util;
CREATE OR REPLACE PROCEDURE standalone_proc AS
BEGIN
  DELETE FROM sessions;
END;
"""

ORACLE_IMMEDIATE = """CREATE OR REPLACE PROCEDURE make_tables AS
  v_sql VARCHAR2(200);
BEGIN
  EXECUTE IMMEDIATE 'CREATE TABLE audit_trail (id NUMBER, msg VARCHAR2(100))';
  v_sql := 'CREATE TABLE audit_copy (id NUMBER)';
  EXECUTE IMMEDIATE v_sql;
  EXECUTE IMMEDIATE 'ALTER TABLE ' || 'x ADD y NUMBER';
END;
/
"""

ORACLE_TYPE = """CREATE OR REPLACE TYPE person_t AS OBJECT (name VARCHAR2(30), MEMBER FUNCTION greet RETURN VARCHAR2);
/
CREATE OR REPLACE TYPE BODY person_t AS
  MEMBER FUNCTION greet RETURN VARCHAR2 IS BEGIN RETURN 'hi'; END;
END;
/
"""

EF_IDEMPOTENT = """IF OBJECT_ID(N'[__EFMigrationsHistory]') IS NULL
BEGIN
    CREATE TABLE [__EFMigrationsHistory] (
        [MigrationId] nvarchar(150) NOT NULL,
        [ProductVersion] nvarchar(32) NOT NULL,
        CONSTRAINT [PK___EFMigrationsHistory] PRIMARY KEY ([MigrationId])
    );
END;
GO

BEGIN TRANSACTION;
GO

IF NOT EXISTS (
    SELECT * FROM [__EFMigrationsHistory]
    WHERE [MigrationId] = N'20240101000000_Initial'
)
BEGIN
    CREATE TABLE [Orders] (
        [Id] int NOT NULL IDENTITY,
        [Total] decimal(18,2) NOT NULL,
        CONSTRAINT [PK_Orders] PRIMARY KEY ([Id])
    );
END;
GO

IF NOT EXISTS (
    SELECT * FROM [__EFMigrationsHistory]
    WHERE [MigrationId] = N'20240202000000_AddViews'
)
BEGIN
    EXEC(N'CREATE VIEW [dbo].[vw_OrderTotals] AS
    SELECT o.Id, o.Total FROM dbo.Orders o WHERE o.Total > 0');
END;
GO

IF NOT EXISTS (
    SELECT * FROM [__EFMigrationsHistory]
    WHERE [MigrationId] = N'20240202000000_AddViews'
)
BEGIN
    EXEC(N'CREATE PROCEDURE [dbo].[usp_RecalcTotals] @OrderId int AS
    BEGIN
        UPDATE dbo.Orders SET Total = 0 WHERE Id = @OrderId;
    END');
END;
GO

IF NOT EXISTS (
    SELECT * FROM [__EFMigrationsHistory]
    WHERE [MigrationId] = N'20240202000000_AddViews'
)
BEGIN
    EXEC(N'CREATE FUNCTION [dbo].[fn_OrderCount]() RETURNS int AS BEGIN RETURN (SELECT COUNT(*) FROM dbo.Orders) END');
END;
GO

COMMIT;
GO
"""

SSMS_EXISTS = """/****** Object:  View [dbo].[vw_ActiveCustomers]    Script Date: 01/11/2026 10:00:00 ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[dbo].[vw_ActiveCustomers]'))
EXEC dbo.sp_executesql @statement = N'CREATE VIEW [dbo].[vw_ActiveCustomers]
AS
SELECT c.Id, c.Name FROM dbo.Customers c WHERE c.IsActive = 1
'
GO
/****** Object:  UserDefinedFunction [dbo].[fn_Tax]    Script Date: 01/11/2026 10:00:00 ******/
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[fn_Tax]') AND type in (N'FN', N'IF'))
BEGIN
execute dbo.sp_executesql @statement = N'CREATE FUNCTION [dbo].[fn_Tax](@amount money) RETURNS money
AS BEGIN RETURN @amount * 0.17 END'
END
GO
/****** Object:  StoredProcedure [dbo].[usp_GetCustomer]    Script Date: 01/11/2026 10:00:00 ******/
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[usp_GetCustomer]') AND type in (N'P'))
BEGIN
EXEC dbo.sp_executesql @statement = N'CREATE PROCEDURE [dbo].[usp_GetCustomer] AS'
END
GO
ALTER PROCEDURE [dbo].[usp_GetCustomer] @Id int
AS
BEGIN
    SELECT * FROM dbo.Customers WHERE Id = @Id
END
GO
"""

VAR_EXEC = """DECLARE @SQL NVARCHAR(MAX);
SET @SQL = N'CREATE PROCEDURE Website.SearchForPeople @SearchText nvarchar(1000)
AS
BEGIN
    SELECT p.FullName FROM Application.People AS p WHERE p.FullName LIKE @SearchText;
END;';
EXECUTE (@SQL);
GO
DECLARE @s NVARCHAR(MAX) = N'CREATE VIEW dbo.vw_Recent AS SELECT TOP 10 Id FROM dbo.Orders';
EXEC sp_executesql @s;
GO
SET @x = N'CREATE TABLE dbo.NotRun (Id INT)';
GO
EXEC(@x)
GO
"""

STUBS = """IF OBJECT_ID('dbo.usp_Stub', 'P') IS NULL EXEC('CREATE PROCEDURE dbo.usp_Stub AS RETURN 0');
GO
ALTER PROCEDURE dbo.usp_Stub @Path NVARCHAR(200) = 'C:\\' AS
BEGIN
    CREATE TABLE #work (Id INT);
    DECLARE @t TABLE (Id INT);
    INSERT INTO #work SELECT object_id FROM sys.objects;
    UPDATE o SET o.Status = 'x' FROM dbo.Orders o JOIN dbo.Customers c ON c.Id = o.CustomerId;
    DELETE FROM dbo.Audit WHERE 1 = 0;
    SELECT [create_date], [Create Table] FROM dbo.Things;
END
GO
CREATE PROCEDURE dbo.p_main AS
GO
ALTER PROCEDURE dbo.p_main @k int AS BEGIN SELECT * FROM dbo.T1 END
GO
CREATE VIEW dbo.v_stub AS SELECT 1
GO
CREATE OR ALTER VIEW dbo.v_stub AS SELECT a FROM dbo.T2
GO
CREATE PROCEDURE dbo.p_real AS BEGIN SELECT * FROM dbo.A1 END
GO
ALTER PROCEDURE dbo.p_real AS BEGIN SELECT * FROM dbo.B1 END
GO
"""

NORTHWIND = """CREATE TABLE [dbo].[Order Details] (
\t[OrderID] [int] NOT NULL ,
\t[ProductID] [int] NOT NULL ,
\t[UnitPrice] [money] NOT NULL CONSTRAINT [DF_Order_Details_UnitPrice] DEFAULT (0),
\tCONSTRAINT [FK_Order_Details_Orders] FOREIGN KEY ([OrderID]) REFERENCES [dbo].[Orders] ([OrderID])
)
GO
CREATE VIEW "Order Details Extended" AS
SELECT "Order Details".OrderID FROM "Order Details"
GO
"""


# ---------------------------------------------------------------------------------------------------- review fixtures
MYSQLDUMP_VIEWS_80 = """-- MySQL dump 10.13  Distrib 8.0.36, for Linux (x86_64)
/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
DROP TABLE IF EXISTS `actor`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `actor` (
  `actor_id` smallint unsigned NOT NULL AUTO_INCREMENT,
  `first_name` varchar(45) NOT NULL,
  PRIMARY KEY (`actor_id`)
) ENGINE=InnoDB AUTO_INCREMENT=201 DEFAULT CHARSET=utf8mb4;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Temporary view structure for view `actor_info`
--

DROP TABLE IF EXISTS `actor_info`;
/*!50001 DROP VIEW IF EXISTS `actor_info`*/;
SET @saved_cs_client     = @@character_set_client;
/*!50503 SET character_set_client = utf8mb4 */;
/*!50001 CREATE VIEW `actor_info` AS SELECT
 1 AS `actor_id`,
 1 AS `first_name`*/;
SET character_set_client = @saved_cs_client;

--
-- Final view structure for view `actor_info`
--

/*!50001 DROP VIEW IF EXISTS `actor_info`*/;
/*!50001 SET @saved_cs_client          = @@character_set_client */;
/*!50001 SET character_set_client      = utf8mb4 */;
/*!50001 CREATE ALGORITHM=UNDEFINED */
/*!50013 DEFINER=`root`@`localhost` SQL SECURITY INVOKER */
/*!50001 VIEW `actor_info` AS select `a`.`actor_id` AS `actor_id`,`a`.`first_name` AS `first_name` from `actor` `a` */;
/*!50001 SET character_set_client      = @saved_cs_client */;
"""

MYSQLDUMP_VIEWS_57 = MYSQLDUMP_VIEWS_80.replace(
    "/*!50001 CREATE VIEW `actor_info` AS SELECT\n 1 AS `actor_id`,\n 1 AS `first_name`*/;",
    "/*!50001 CREATE TABLE `actor_info` (\n  `actor_id` tinyint NOT NULL,\n  `first_name` tinyint NOT NULL\n"
    ") ENGINE=MyISAM */;").replace("Temporary view structure", "Temporary table structure")

SSMS_COMMENTED = """IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[fn_OrderTotal]'))
BEGIN
execute dbo.sp_executesql @statement = N'-- =============================================
-- Author:\t\tDana O''Brien
-- Create date: 2024-01-01
-- Description:\ttotal of one order
-- =============================================
CREATE FUNCTION [dbo].[fn_OrderTotal] (@OrderId INT)
RETURNS MONEY
AS
BEGIN
  RETURN (SELECT SUM(Price) FROM dbo.OrderLines WHERE OrderId = @OrderId)
END
'
END
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[dbo].[vw_Open]'))
EXEC dbo.sp_executesql @statement = N'/* open orders */ CREATE VIEW [dbo].[vw_Open] AS SELECT Id FROM dbo.Orders'
GO
"""

DRIZZLE = '''DO $$ BEGIN
 CREATE TYPE "public"."order_status" AS ENUM('open', 'paid', 'shipped');
EXCEPTION
 WHEN duplicate_object THEN null;
END $$;
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "orders" (
\t"id" serial PRIMARY KEY NOT NULL,
\t"status" "order_status" DEFAULT 'open' NOT NULL
);
--> statement-breakpoint
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_tables WHERE tablename = 'audit_log') THEN
    CREATE TABLE audit_log (id bigserial PRIMARY KEY, msg text);
  END IF;
END
$$;
DO LANGUAGE plpgsql $do$ BEGIN CREATE TABLE IF NOT EXISTS settings (k text); END $do$;
'''

TSQL_EXECUTORS = """IF OBJECT_ID(N'[dbo].[vw_X]') IS NULL
EXEC [dbo].[sp_executesql] @statement = N'CREATE VIEW [dbo].[vw_X] AS SELECT 1 AS a'
GO
DECLARE @s NVARCHAR(MAX) = N'CREATE VIEW [dbo].[vw_Y] AS SELECT 2 AS b';
EXEC [sys].[sp_executesql] @s;
GO
SELECT @t = N'CREATE VIEW dbo.vw_Sel AS SELECT 3 AS c';
EXEC sp_executesql @t;
GO
EXEC sp_executesql @stmt = N'CREATE VIEW dbo.vw_Stmt AS SELECT 4 AS d';
GO
"""

PG_DUMP_COPY = """CREATE TABLE public.people (id integer, name text);
CREATE FUNCTION public.touch() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.id := NEW.id; RETURN NEW; END; $$;
CREATE VIEW public.v_people AS SELECT id, name FROM public.people;
COPY public.people (id, name) FROM stdin;
1\t@ROW@
2\tSmith
\\.

CREATE INDEX people_idx ON public.people USING btree (name);
CREATE TRIGGER trg_touch BEFORE UPDATE ON public.people FOR EACH ROW EXECUTE FUNCTION public.touch();
ALTER TABLE ONLY public.people ADD CONSTRAINT fk FOREIGN KEY (id) REFERENCES public.other(id);
"""

PG_HINT_BASE = '''CREATE TABLE "Invoice" (id bigint PRIMARY KEY, total numeric);
CREATE FUNCTION mk() RETURNS void AS $body$
BEGIN
  CREATE TABLE inside_body (x int);
  UPDATE "Invoice" SET total = 0;
END;
$body$ LANGUAGE plpgsql;
CREATE TABLE cars (id int, engine text);
'''

TSQL_ENGINE = ("CREATE TABLE dbo.Cars (Id INT, Engine NVARCHAR(50))\nGO\n"
               "IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[dbo].[vw_Cars]'))\n"
               "EXEC dbo.sp_executesql @statement = N'CREATE VIEW [dbo].[vw_Cars] AS SELECT Id FROM dbo.Cars'\nGO\n"
               "CREATE PROCEDURE dbo.usp_Path @p NVARCHAR(10) = 'C:\\' AS SELECT 1\nGO\n"
               "CREATE PROCEDURE dbo.usp_After AS SELECT * FROM dbo.Cars\nGO\n"
               "CREATE VIEW dbo.vw_Names AS SELECT 'x' AS n\nGO\n"
               "UPDATE dbo.Cars SET Engine = @e\nGO\n"
               "UPDATE dbo.Cars SET Engine = 'V8' WHERE Id = 1\nGO\n"
               "PRINT 'Backup folder: C:\\Backups\\'\nGO\nPRINT 'see `legacy_cars` in the old MySQL schema'\nGO\n")

CONCAT_TSQL = """CREATE PROCEDURE dbo.usp_MakeArchive @y NVARCHAR(4) AS
BEGIN
  EXEC('CREATE TABLE dbo.Archive_' + @y + ' (Id INT, Total MONEY)');
  DECLARE @s NVARCHAR(MAX) = N'CREATE VIEW dbo.v_Tenant' + @y + N' AS SELECT 1 AS x';
  EXEC sp_executesql @s;
  EXEC('CREATE VIEW dbo.v_Full AS SELECT * FROM ' + @y);
END
GO
"""

CONCAT_ORACLE = """CREATE OR REPLACE PROCEDURE mk_part(p_suffix VARCHAR2) AS
BEGIN
  EXECUTE IMMEDIATE 'CREATE TABLE sales_' || p_suffix || ' (id NUMBER)';
END;
/
"""

QQUOTE = """CREATE OR REPLACE PROCEDURE greet AS
BEGIN
  dbms_output.put_line(q'[Don't worry]');
  dbms_output.put_line(Q'{it's: CREATE TABLE fake_q (x NUMBER)}');
  dbms_output.put_line(nq'!O'Brien!');
END;
/
CREATE TABLE after_q (x NUMBER);
CREATE OR REPLACE VIEW v_after AS SELECT 'x' a FROM after_q;
CREATE OR REPLACE PROCEDURE last_one AS BEGIN NULL; END;
/
"""

MYSQL_PLAIN = """# V3__users.sql
# CREATE TABLE old_users (id INT) was dropped in V2
CREATE TABLE users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  bio VARCHAR(200) DEFAULT 'It\\'s me'
);
INSERT INTO users (bio) VALUES ('Don\\'t panic: CREATE TABLE fake_bs (x int)');
CREATE TABLE sessions (id INT, user_id INT REFERENCES users(id));
CREATE VIEW active_users AS SELECT * FROM users;
"""


def big_block(i: int, br: bool) -> str:
    """One block of the generated deployment script (design section 3.1; the engine study's gen_big.py)."""
    def q(s):
        return "[" + s.replace(".", "].[") + "]" if br else s
    p = max(i - 1, 0)
    head = f"IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'{q('dbo.Entity' + str(i))}') "
    fk = f"ALTER TABLE {q('dbo.Entity' + str(i))} WITH CHECK ADD CONSTRAINT {q('FK_Entity' + str(i) + '_Parent')} "
    return head + f"""AND type in (N'U'))
BEGIN
CREATE TABLE {q('dbo.Entity' + str(i))}(
    {q('Id')} INT IDENTITY(1,1) NOT NULL,
    {q('ParentId')} INT NULL,
    {q('Name')} NVARCHAR(200) NOT NULL,
    {q('Amount')} DECIMAL(18, 2) NOT NULL,
    CONSTRAINT {q('PK_Entity' + str(i))} PRIMARY KEY CLUSTERED ({q('Id')} ASC)
)
END
GO

{fk}FOREIGN KEY({q('ParentId')})
REFERENCES {q('dbo.Entity' + str(p))} ({q('Id')})
GO

CREATE VIEW {q('dbo.vw_Entity' + str(i))}
AS
SELECT e.Id, e.Name, p.Name AS ParentName
FROM {q('dbo.Entity' + str(i))} e
LEFT JOIN {q('dbo.Entity' + str(p))} p ON p.Id = e.ParentId
GO

CREATE OR ALTER PROCEDURE {q('dbo.usp_GetEntity' + str(i))}
    @Id INT
AS
BEGIN
    SET NOCOUNT ON;
    SELECT Id, Name, Amount FROM {q('dbo.Entity' + str(i))} WHERE Id = @Id;
    EXEC {q('dbo.usp_GetEntity' + str(p))} @Id = @Id;
END
GO

CREATE FUNCTION {q('dbo.fn_EntityTotal' + str(i))} (@Id INT)
RETURNS DECIMAL(18,2)
AS
BEGIN
    DECLARE @t DECIMAL(18,2);
    SELECT @t = SUM(Amount) FROM {q('dbo.Entity' + str(i))} WHERE ParentId = @Id;
    RETURN @t;
END
GO

"""


def big_script(n: int, br: bool) -> str:
    return "".join(big_block(i, br) for i in range(n))


_IF_WRAP = re.compile(r"IF NOT EXISTS \(SELECT \* FROM sys\.objects[^\n]*\nBEGIN\n(CREATE TABLE .*?\n\))\nEND\n", re.S)


def big_variants() -> dict:
    """The engine study's variants A-E of the 45-block script (variants.py), rebuilt from the generator."""
    plain, brk = big_script(45, False), big_script(45, True)
    no_if = _IF_WRAP.sub(r"\1\n", plain)
    semi = re.sub(r"\nGO\n", "\n;\n", no_if)
    no_procs = re.sub(r"CREATE OR ALTER PROCEDURE.*?\nEND\n;\n", "", semi, flags=re.S)
    return {"brackets": (brk, 180), "A_plain_noIF_GO": (no_if, 180), "B_plain_noIF_semicolons": (semi, 180),
            "C_brackets_noIF_GO": (_IF_WRAP.sub(r"\1\n", brk), 180), "D_noProcs": (no_procs, 135),
            "E_noProcs_noFuncs": (re.sub(r"CREATE FUNCTION.*?\nEND\n;\n", "", no_procs, flags=re.S), 90)}


# ---------------------------------------------------------------------------------------------------- A
def section_a() -> None:
    print("\n-- A. dialects: exact declarations, kinds and lines --")
    f = S.parse(TSQL)
    want = [(k, q, line_of(TSQL, needle)) for k, q, needle in TSQL_EXPECT]
    check("T-SQL deployment script: the 10 objects, kinds and lines (brackets, GO, IF wrapper, CREATE OR ALTER)",
          decls(TSQL) == want, decls(TSQL))
    check("T-SQL script dialect is tsql", f["dialect"] == "tsql", f["dialect"])
    d = by_q(f)
    check("Orders references Customers through ALTER TABLE ... REFERENCES",
          d["dbo.Orders"]["references"] == ["Customers"], d["dbo.Orders"]["references"])
    check("OrderLines references Orders through an ALTER TABLE without brackets",
          d["dbo.OrderLines"]["references"] == ["Orders"], d["dbo.OrderLines"]["references"])

    for rel, text in SSDT.items():
        got = decls(text)
        check(f"SSDT one-object file {rel}: exactly one object at line 1", len(got) == 1 and got[0][2] == 1, got)
    check("SSDT kinds: procedure / table / view",
          [decls(t)[0][0] for t in SSDT.values()] == ["procedure", "table", "view"],
          [decls(t) for t in SSDT.values()])

    want = [(k, q, line_of(PG, needle)) for k, q, needle in PG_EXPECT]
    check("PostgreSQL: the 12 objects (\"Invoice\", a procedure, UNLOGGED, MATERIALIZED, EXECUTE FUNCTION trigger)",
          decls(PG) == want, decls(PG))
    check("PostgreSQL dialect is ansi", S.parse(PG)["dialect"] == "ansi", S.parse(PG)["dialect"])

    want = [(k, q, line_of(MYSQL, needle)) for k, q, needle in MYSQL_EXPECT]
    check("MySQL: the 6 objects (DELIMITER $$, backticks, DEFINER, a trigger after DELIMITER ;)",
          decls(MYSQL) == want, decls(MYSQL))
    m = S.parse(MYSQL)
    check("MySQL dialect is mysql", m["dialect"] == "mysql", m["dialect"])
    add = by_q(m)["add_post"]
    check("MySQL: END IF closes no block, so the procedure's read after it is listed (audit_log)",
          add["reads"] == ["audit_log"] and add["writes"] == ["posts"], (add["reads"], add["writes"]))
    check("MySQL: IN params read from the parenthesised list", add["params"] == ["p_user", "p_title"],
          add["params"])

    want = [("trigger", "trg_dump", 3), ("view", "v_dump", 4), ("procedure", "p_dump", 5)]
    check("mysqldump /*!50003 CREATE*/ ... TRIGGER, /*!50001 ... VIEW, DEFINER procedure; # and \\' strings not read",
          decls(MYSQLDUMP) == want, decls(MYSQLDUMP))

    want = [("table", "artist", line_of(SQLITE, "CREATE TABLE IF NOT EXISTS artist")),
            ("table", "track", line_of(SQLITE, "CREATE TABLE IF NOT EXISTS track")),
            ("view", "artist_tracks", line_of(SQLITE, "CREATE VIEW")),
            ("trigger", "track_ai", line_of(SQLITE, "CREATE TRIGGER"))]
    check("SQLite: the 4 objects (IF NOT EXISTS on table, view and trigger)", decls(SQLITE) == want, decls(SQLITE))

    o = S.parse(ORACLE)
    want = [("package", "billing", 1), ("procedure", "billing.charge", 2), ("function", "billing.total_due", 3),
            ("procedure", "archive_orders", line_of(ORACLE, "CREATE OR REPLACE EDITIONABLE PROCEDURE"))]
    check("Oracle: PACKAGE + PACKAGE BODY -> one package and its members; EDITIONABLE procedure",
          decls(ORACLE) == want, decls(ORACLE))
    check("Oracle dialect is oracle", o["dialect"] == "oracle", o["dialect"])
    od = by_q(o)
    check("Oracle member charge: in package billing, its body's write (orders)",
          od["billing.charge"]["package"] == "billing" and od["billing.charge"]["writes"] == ["orders"],
          od["billing.charge"])
    check("Oracle member total_due: its body's read (orders)", od["billing.total_due"]["reads"] == ["orders"],
          od["billing.total_due"])
    check("Oracle standalone procedure is no member; writes orders_archive, reads orders",
          od["archive_orders"]["package"] is None and od["archive_orders"]["writes"] == ["orders_archive"]
          and od["archive_orders"]["reads"] == ["orders"], od["archive_orders"])

    want = [("package", "util", 1), ("procedure", "util.ping", 2),
            ("procedure", "standalone_proc", line_of(ORACLE_NO_SLASH, "CREATE OR REPLACE PROCEDURE"))]
    check("a package without / lines, then CREATE PROCEDURE: the procedure is not a member",
          decls(ORACLE_NO_SLASH) == want, decls(ORACLE_NO_SLASH))

    want = [("procedure", "make_tables", 1), ("table", "audit_trail", 4), ("table", "audit_copy", 5)]
    check("Oracle EXECUTE IMMEDIATE '...' and an executed variable: read as nested batches",
          decls(ORACLE_IMMEDIATE) == want, decls(ORACLE_IMMEDIATE))
    check("Oracle: 'ALTER TABLE ' || x is not counted as dynamic SQL not read",
          S.parse(ORACLE_IMMEDIATE)["dynamic_unread"] == 0, S.parse(ORACLE_IMMEDIATE)["dynamic_unread"])
    check("Oracle TYPE + TYPE BODY -> one type, never a type named BODY",
          decls(ORACLE_TYPE) == [("type", "person_t", 1)], decls(ORACLE_TYPE))

    for name, (text, n) in big_variants().items():
        got = decls(text)
        kinds = sorted({k for k, _q, _l in got})
        check(f"generated 1,935-line script, {name}: {n} objects", len(got) == n, f"{len(got)} {kinds}")
    brk = big_variants()["brackets"][0]
    got = decls(brk)
    want0 = [("table", "dbo.Entity0", line_of(brk, "CREATE TABLE [dbo].[Entity0]")),
             ("view", "dbo.vw_Entity0", line_of(brk, "CREATE VIEW [dbo].[vw_Entity0]")),
             ("procedure", "dbo.usp_GetEntity0", line_of(brk, "CREATE OR ALTER PROCEDURE [dbo].[usp_GetEntity0]")),
             ("function", "dbo.fn_EntityTotal0", line_of(brk, "CREATE FUNCTION [dbo].[fn_EntityTotal0]"))]
    check("generated script: the first block's four objects at their lines", got[:4] == want0, got[:4])
    check("generated script: every name Entity0..44 per kind",
          {q for k, q, _l in got if k == "table"} == {f"dbo.Entity{i}" for i in range(45)}, got[:8])

    got = decls(NORTHWIND)
    want = [("table", "dbo.Order Details", 1), ("view", "Order Details Extended", line_of(NORTHWIND, "CREATE VIEW"))]
    check("Northwind [Order Details] and a double-quoted view name with spaces", got == want, got)
    nd = by_q(S.parse(NORTHWIND))
    check("Northwind view reads Order Details", nd["Order Details Extended"]["reads"] == ["Order Details"],
          nd["Order Details Extended"])

    want = [("table", "__EFMigrationsHistory", line_of(EF_IDEMPOTENT, "CREATE TABLE [__EFMigrationsHistory]")),
            ("table", "Orders", line_of(EF_IDEMPOTENT, "CREATE TABLE [Orders]")),
            ("view", "dbo.vw_OrderTotals", line_of(EF_IDEMPOTENT, "EXEC(N'CREATE VIEW")),
            ("procedure", "dbo.usp_RecalcTotals", line_of(EF_IDEMPOTENT, "EXEC(N'CREATE PROCEDURE")),
            ("function", "dbo.fn_OrderCount", line_of(EF_IDEMPOTENT, "EXEC(N'CREATE FUNCTION"))]
    check("EF Core idempotent script: 2 tables + EXEC(N'CREATE VIEW / PROCEDURE / FUNCTION') at their lines",
          decls(EF_IDEMPOTENT) == want, decls(EF_IDEMPOTENT))
    ef = by_q(S.parse(EF_IDEMPOTENT))
    check("EF nested procedure: its parameter and its write (Orders)",
          ef["dbo.usp_RecalcTotals"]["params"] == ["@OrderId"] and ef["dbo.usp_RecalcTotals"]["writes"] == ["Orders"],
          ef["dbo.usp_RecalcTotals"])
    check("EF nested function reads Orders", ef["dbo.fn_OrderCount"]["reads"] == ["Orders"], ef["dbo.fn_OrderCount"])

    want = [("view", "dbo.vw_ActiveCustomers", line_of(SSMS_EXISTS, "@statement = N'CREATE VIEW")),
            ("function", "dbo.fn_Tax", line_of(SSMS_EXISTS, "@statement = N'CREATE FUNCTION")),
            ("procedure", "dbo.usp_GetCustomer", line_of(SSMS_EXISTS, "ALTER PROCEDURE"))]
    check("SSMS existence check: sp_executesql @statement = N'CREATE ...' read; the stub gives way to the ALTER",
          decls(SSMS_EXISTS) == want, decls(SSMS_EXISTS))
    sp = by_q(S.parse(SSMS_EXISTS))["dbo.usp_GetCustomer"]
    check("SSMS procedure: the ALTER's parameter and read", sp["params"] == ["@Id"] and sp["reads"] == ["Customers"],
          sp)

    v = S.parse(VAR_EXEC)
    want = [("procedure", "Website.SearchForPeople", line_of(VAR_EXEC, "SET @SQL = N'CREATE PROCEDURE")),
            ("view", "dbo.vw_Recent", line_of(VAR_EXEC, "DECLARE @s NVARCHAR(MAX) = N'CREATE VIEW"))]
    check("variable-executed batches: SET @SQL = N'...'; EXECUTE (@SQL) and DECLARE @s ... ; EXEC sp_executesql @s",
          decls(VAR_EXEC) == want, decls(VAR_EXEC))
    check("a literal executed only in another batch is not read and counts as dynamic SQL not read",
          v["dynamic_unread"] == 1, v["dynamic_unread"])
    sfp = by_q(v)["Website.SearchForPeople"]
    check("the nested procedure's parameter and read", sfp["params"] == ["@SearchText"] and sfp["reads"] == ["People"],
          sfp)

    st = S.parse(STUBS)
    got = decls(STUBS)
    want = [("procedure", "dbo.usp_Stub", line_of(STUBS, "ALTER PROCEDURE dbo.usp_Stub")),
            ("procedure", "dbo.p_main", line_of(STUBS, "ALTER PROCEDURE dbo.p_main")),
            ("view", "dbo.v_stub", line_of(STUBS, "CREATE OR ALTER VIEW dbo.v_stub")),
            ("procedure", "dbo.p_real", line_of(STUBS, "CREATE PROCEDURE dbo.p_real"))]
    check("stub-then-ALTER: EXEC('CREATE PROCEDURE x AS RETURN 0') and AS / AS SELECT 1 stubs give way; a real first "
          "definition stays", got == want, got)
    sd = by_q(st)
    check("the superseding ALTER's facts (params, reads, aliased write)",
          sd["dbo.usp_Stub"]["params"] == ["@Path"] and sd["dbo.usp_Stub"]["writes"] == ["Orders", "Audit"]
          and sd["dbo.usp_Stub"]["reads"] == ["Customers", "Things"], sd["dbo.usp_Stub"])
    check("p_main takes the ALTER's facts; p_real keeps the first definition's",
          sd["dbo.p_main"]["reads"] == ["T1"] and sd["dbo.p_main"]["params"] == ["@k"]
          and sd["dbo.p_real"]["reads"] == ["A1"] and sd["dbo.v_stub"]["reads"] == ["T2"], sd)
    section_a_review()


def section_a_review() -> None:
    """Positive shapes from the adversarial review of the first cut."""
    for name, text in (("8.0 (a SELECT 1 AS ... placeholder view)", MYSQLDUMP_VIEWS_80),
                       ("5.7 (a /*!50001 CREATE TABLE placeholder)", MYSQLDUMP_VIEWS_57)):
        f = S.parse(text)
        want = [("table", "actor", line_of(text, "CREATE TABLE `actor`")),
                ("view", "actor_info", line_of(text, "/*!50001 CREATE ALGORITHM"))]
        check(f"mysqldump {name}: the view at its final definition, no placeholder table",
              decls(text) == want and f["dialect"] == "mysql", (decls(text), f["dialect"]))
        v = by_q(f).get("actor_info", {})
        check(f"mysqldump {name}: the final view's read (actor)", v.get("reads") == ["actor"], v)
    hand = "CREATE VIEW one AS SELECT 1 AS a;\nCREATE TABLE `t` (`a` int) ENGINE=InnoDB;\n"
    check("a hand-written MySQL SELECT 1 AS a view with no later definition stays",
          decls(hand) == [("view", "one", 1), ("table", "t", 2)], decls(hand))

    f = S.parse(SSMS_COMMENTED)
    want = [("function", "dbo.fn_OrderTotal", line_of(SSMS_COMMENTED, "CREATE FUNCTION [dbo].[fn_OrderTotal]")),
            ("view", "dbo.vw_Open", line_of(SSMS_COMMENTED, "/* open orders */ CREATE VIEW"))]
    check("SSMS existence check whose literal starts with the template's header comment: read",
          decls(SSMS_COMMENTED) == want and f["dynamic_unread"] == 0, (decls(SSMS_COMMENTED), f["dynamic_unread"]))
    fo = by_q(f).get("dbo.fn_OrderTotal", {})
    check("the commented nested function's parameter and read",
          fo.get("params") == ["@OrderId"] and fo.get("reads") == ["OrderLines"], fo)

    f = S.parse(DRIZZLE)
    want = [("type", "public.order_status", 2),
            ("table", "orders", line_of(DRIZZLE, 'CREATE TABLE IF NOT EXISTS "orders"')),
            ("table", "audit_log", line_of(DRIZZLE, "CREATE TABLE audit_log")),
            ("table", "settings", line_of(DRIZZLE, "CREATE TABLE IF NOT EXISTS settings"))]
    check("PostgreSQL DO blocks (Drizzle / Supabase): the CREATEs they run are declarations at their lines",
          decls(DRIZZLE) == want and f["dynamic_unread"] == 0, (decls(DRIZZLE), f["dynamic_unread"]))

    f = S.parse(TSQL_EXECUTORS)
    want = [("view", "dbo.vw_X", line_of(TSQL_EXECUTORS, "N'CREATE VIEW [dbo].[vw_X]")),
            ("view", "dbo.vw_Y", line_of(TSQL_EXECUTORS, "N'CREATE VIEW [dbo].[vw_Y]")),
            ("view", "dbo.vw_Sel", line_of(TSQL_EXECUTORS, "SELECT @t = N'")),
            ("view", "dbo.vw_Stmt", line_of(TSQL_EXECUTORS, "@stmt = N'"))]
    check("executors: EXEC [dbo].[sp_executesql], EXEC [sys].[sp_executesql] @s, SELECT @t = ..., @stmt = N'...'",
          decls(TSQL_EXECUTORS) == want and f["dynamic_unread"] == 0, (decls(TSQL_EXECUTORS), f["dynamic_unread"]))

    text = ("CREATE VIRTUAL TABLE docs USING fts5(title, body);\n"
            "CREATE FOREIGN TABLE remote_orders (id int) SERVER s;\n"
            "CREATE EXTERNAL TABLE ext.sales (id int) WITH (LOCATION = '/x');\n")
    check("VIRTUAL (SQLite), FOREIGN (PostgreSQL) and EXTERNAL (T-SQL / Synapse) tables",
          [q for _k, q, _l in decls(text)] == ["docs", "remote_orders", "ext.sales"], decls(text))

    for name, text in (("ENGINE= after the closing paren only", "CREATE TABLE users (\n  id INT PRIMARY KEY,\n"
                        "  bio VARCHAR(200) DEFAULT 'It\\'s me'\n) ENGINE=InnoDB;\n"
                        "CREATE TABLE after_users (id INT);\n"),
                       ("a DELIMITER // line only", "DELIMITER //\nCREATE PROCEDURE p_d()\nBEGIN\n  SELECT 'it\\'s';\n"
                        "END//\nDELIMITER ;\nCREATE TABLE after_users (id INT);\n"),
                       ("# comment lines only", MYSQL_PLAIN)):
        f = S.parse(text)
        check(f"MySQL detected from {name}", f["dialect"] == "mysql", f["dialect"])
    got = decls(MYSQL_PLAIN)
    want = [("table", "users", 3), ("table", "sessions", line_of(MYSQL_PLAIN, "CREATE TABLE sessions")),
            ("view", "active_users", line_of(MYSQL_PLAIN, "CREATE VIEW"))]
    check("plain MySQL migration: a # comment is no declaration, \\' strings stay strings", got == want, got)


# ---------------------------------------------------------------------------------------------------- B
NEG_TSQL = """-- CREATE TABLE dbo.CommentedOut (Id INT)
/* CREATE VIEW dbo.vw_Block AS SELECT 1 /* nested CREATE TABLE dbo.Nested (x int) */
   still comment CREATE PROC dbo.p_in_comment AS SELECT 1 */
PRINT 'CREATE TABLE dbo.Printed (x int)';
RAISERROR('CREATE TABLE failed', 16, 1);
SELECT 'CREATE VIEW dbo.Selected AS SELECT 1';
GO
GRANT CREATE VIEW TO hr
GRANT EXECUTE ON dbo.usp_X TO app_role
CREATE TABLE dbo.AfterGrant (Id INT)
GO
REVOKE CREATE PROCEDURE FROM x;
CREATE PROCEDURE dbo.AfterRevoke AS SELECT 1;
GO
SELECT [a] , [b] ,
CREATE TABLE dbo.AfterComma (Id INT)
GO
CREATE PROCEDURE [<schema_name, sysname, dbo>].[<procedure_name, sysname, ProcName>] AS SELECT 1
GO
CREATE TABLE <table_name, sysname, T> (Id INT)
GO
SELECT go FROM dbo.Things
CREATE TABLE dbo.AfterSelectGo (Id INT)
GO
SELECT 'unclosed string
GO
CREATE TABLE dbo.AfterUnclosedString (Id INT)
GO
/* unclosed comment
GO
CREATE TABLE dbo.AfterUnclosedComment (Id INT)
GO
DECLARE @p NVARCHAR(10) = 'C:\\';
CREATE TABLE dbo.AfterBackslash (Id INT)
GO
CREATE TABLE #temp (Id INT)
CREATE TABLE ##g (Id INT)
DECLARE @t TABLE (Id INT)
ALTER TABLE dbo.Things ADD Notes NVARCHAR(MAX) NULL
ALTER TABLE dbo.Things ADD CONSTRAINT FK_T FOREIGN KEY (c) REFERENCES dbo.Other (Id) ON DELETE CASCADE ON UPDATE CASCADE
CREATE NONCLUSTERED INDEX IX_x ON dbo.Things (Id)
GO
CREATE SCHEMA sales
GO
CREATE SYNONYM dbo.Syn FOR dbo.Things
GO
CREATE SEQUENCE dbo.Seq AS INT
GO
CREATE ROLE app_role
GO
SELECT [create_date], [Create Table] FROM dbo.Things
GO
CREATE PROCEDURE dbo.p_reads AS
BEGIN
  WITH recent AS (SELECT Id FROM dbo.Orders)
  SELECT * FROM recent JOIN sys.objects o ON 1 = 1;
  SELECT * FROM inserted; SELECT * FROM deleted; SELECT * FROM new;
END
GO
"""

NEG_TSQL_EXPECT = ["dbo.AfterGrant", "dbo.AfterRevoke", "dbo.AfterComma", "dbo.AfterSelectGo",
                   "dbo.AfterUnclosedString", "dbo.AfterUnclosedComment", "dbo.AfterBackslash", "dbo.p_reads"]

NEG_PG = """CREATE TEMP TABLE scratch (x int);
CREATE TEMPORARY TABLE scratch2 (x int);
CREATE GLOBAL TEMPORARY TABLE gtt (x int);
CREATE UNLOGGED TABLE fastlog (id bigint, msg text);
CREATE MATERIALIZED VIEW mv_totals AS SELECT 1 AS n FROM fastlog;
ALTER FUNCTION public.order_total(bigint) OWNER TO postgres;
ALTER TABLE ONLY public.orders ADD CONSTRAINT fk FOREIGN KEY (c) REFERENCES public.customers(id) ON DELETE CASCADE;
CREATE FUNCTION mk() RETURNS void AS $body$
BEGIN
  CREATE TABLE inside_body (x int);
  EXECUTE 'CREATE TABLE dyn (x int)';
END;
$body$ LANGUAGE plpgsql;
SELECT E'it\\'s CREATE TABLE fake (x int)';
CREATE TABLE "Kunde" ("Name" text);
CREATE INDEX idx ON "Kunde" ("Name");
CREATE ROLE app;
CREATE SEQUENCE s1;
CREATE DIRECTORY d1 AS '/tmp';
-- see `orders` in the docs: a backtick on a comment line never makes this file MySQL
CREATE FUNCTION g() RETURNS void AS $$ BEGIN CREATE TABLE in_g (x int); END; $$ LANGUAGE plpgsql;
CREATE TABLE /* a comment before the name */ commented_name (x int);
CREATE TABLE llm_calls (
prompt TEXT NOT NULL,
remark TEXT
);
CREATE TABLE child (id int, parent_id int REFERENCES parent(id) ON DELETE CASCADE ON UPDATE CASCADE);
CREATE TRIGGER trg_upd AFTER UPDATE ON child FOR EACH ROW EXECUTE FUNCTION f();
CREATE FUNCTION locker() RETURNS void AS $$ BEGIN PERFORM 1 FROM child FOR UPDATE; END; $$ LANGUAGE plpgsql;
"""

NEG_PG_EXPECT = ["fastlog", "mv_totals", "mk", "Kunde", "g", "commented_name", "llm_calls", "child", "trg_upd",
                 "locker"]

NEG_ORACLE = """REM Create table order_items.
REMARK Don't create anything here: CREATE TABLE remark_fake (x NUMBER);
PROMPT Create view v_fake ...
CREATE TABLE after_rem (x NUMBER);
GRANT CREATE SESSION,
  CREATE VIEW,
  CREATE TABLE TO sh;
GRANT CREATE VIEW TO hr;
CREATE TABLE after_grant (x NUMBER);
CREATE OR REPLACE PROCEDURE p AS
BEGIN
  NULL;
END;
/
"""


def section_b() -> None:
    print("\n-- B. negative shapes: only the intended declarations --")
    t = S.parse(NEG_TSQL)
    got = [d["qualified"] for d in t["decls"]]
    check("T-SQL negatives: only the 8 real declarations (comments, non-EXEC strings, GRANT / REVOKE, a comma, "
          "templates, `go` in a SELECT, GO ending an unclosed string / comment, 'C:\\', temp tables, non-node kinds)",
          got == NEG_TSQL_EXPECT, got)
    check("a CREATE in a PRINT / RAISERROR / SELECT string is not dynamic SQL not read", t["dynamic_unread"] == 0,
          t["dynamic_unread"])
    td = by_q(t)
    check("sys.objects, CTE names, inserted / deleted / new are not reads", td["dbo.p_reads"]["reads"] == ["Orders"],
          td["dbo.p_reads"]["reads"])
    check("ALTER TABLE targets are module alters, never declarations; ON DELETE / ON UPDATE CASCADE are no writes",
          t["alters"] == ["Things"] and t["writes"] == [], (t["alters"], t["writes"]))

    p = S.parse(NEG_PG)
    got = [d["qualified"] for d in p["decls"]]
    check("PostgreSQL negatives: TEMP / TEMPORARY / GLOBAL TEMPORARY, a CREATE in a dollar body, OWNER TO, E'' string, "
          "INDEX / ROLE / SEQUENCE / DIRECTORY never declarations", got == NEG_PG_EXPECT, got)
    check("a backtick on a -- comment line does not make a PostgreSQL file MySQL", p["dialect"] == "ansi",
          p["dialect"])
    check("EXECUTE 'CREATE TABLE dyn' in a plpgsql body counts as dynamic SQL not read (E'' and SELECT do not)",
          p["dynamic_unread"] == 1, p["dynamic_unread"])
    check("pg_dump ALTER TABLE ONLY x: the target is x", p["alters"] == ["orders"], p["alters"])
    pd = by_q(p)
    check("a column named prompt / remark inside a column list is a column, not a SQL*Plus line",
          pd["llm_calls"]["columns"] == ["prompt", "remark"], pd["llm_calls"]["columns"])
    check("AFTER UPDATE / FOR UPDATE / ON DELETE CASCADE are not writes",
          pd["trg_upd"]["writes"] == [] and pd["locker"]["writes"] == [] and pd["child"]["references"] == ["parent"],
          (pd["trg_upd"], pd["locker"]))
    check("the trigger runs f(); its target is child",
          pd["trg_upd"]["runs"] == ["f"] and pd["trg_upd"]["on"] == "child", pd["trg_upd"])

    o = S.parse(NEG_ORACLE)
    got = [(d["qualified"], d["line"]) for d in o["decls"]]
    want = [("after_rem", line_of(NEG_ORACLE, "CREATE TABLE after_rem")),
            ("after_grant", line_of(NEG_ORACLE, "CREATE TABLE after_grant")),
            ("p", line_of(NEG_ORACLE, "CREATE OR REPLACE PROCEDURE p"))]
    check("SQL*Plus REM / REMARK (with an apostrophe) / PROMPT lines and GRANT privilege lists are not declarations",
          got == want, got)

    m = S.parse(MYSQLDUMP)
    check("MySQL '# CREATE' comment and 'It\\'s CREATE TABLE fake2' string are not declarations",
          [d["qualified"] for d in m["decls"]] == ["trg_dump", "v_dump", "p_dump"], m["decls"])
    check("mysqldump: the INSERT target is the module's write", m["writes"] == ["t"], m["writes"])
    section_b_review()


def section_b_review() -> None:
    """Negative shapes from the adversarial review of the first cut."""
    for name, row in (("an apostrophe", "O'Brien"), ("an unclosed /*", "see /* the notes"),
                      ("DDL text", "CREATE TABLE tenant_42.invoices (id bigint)\n3\tCREATE VIEW reporting.v_sales AS "
                                   "SELECT 1"), ("a dollar quote and a [", "costs $$ 5 [x")):
        text = PG_DUMP_COPY.replace("@ROW@", row)
        f = S.parse(text)
        want = [("table", "public.people", 1), ("function", "public.touch", 2), ("view", "public.v_people", 3),
                ("trigger", "trg_touch", line_of(text, "CREATE TRIGGER trg_touch"))]
        check(f"pg_dump COPY ... FROM stdin data with {name} is data: the post-data trigger and ALTER are read",
              decls(text) == want and f["alters"] == ["people"] and f["dialect"] == "ansi",
              (decls(text), f["alters"], f["dialect"]))
    text = "COPY t (a) FROM stdin;\nCREATE TABLE in_data (x int)\n"
    check("COPY ... FROM stdin with no \\. line: the rest of the file is data", decls(text) == [], decls(text))
    text = "COPY t (a) FROM '/tmp/t.csv';\nCREATE TABLE after_file_copy (x int);\n"
    check("COPY ... FROM a file has no inline data", [q for _k, q, _l in decls(text)] == ["after_file_copy"],
          decls(text))

    base = [("table", "Invoice", 1), ("function", "mk", 2), ("table", "cars", 8)]
    for name, text, extra in (
            ("UPDATE cars SET engine = 'v8'", PG_HINT_BASE + "UPDATE cars SET engine = 'v8' WHERE id = 1;\n", []),
            ("a column named delimiter",
             PG_HINT_BASE + "CREATE TABLE csv_format (\n  id int,\n  delimiter char(1)\n);\n",
             [("table", "csv_format", 9)]),
            ("backticks in a COMMENT ON string", PG_HINT_BASE + "COMMENT ON TABLE cars IS 'Mirrors `legacy_cars`';\n",
             []),
            ("MySQL text in a block comment", PG_HINT_BASE + "/* was: CREATE TABLE `cars` (id int) ENGINE=InnoDB;\n"
             "DELIMITER //\n# old */\n", []),
            ("a [dbo].[Invoice] in a comment", PG_HINT_BASE + "/* ported from [dbo].[Invoice] */\n", [])):
        f = S.parse(text)
        check(f"PostgreSQL + {name}: still ansi, the dollar body's CREATE is no declaration",
              f["dialect"] == "ansi" and decls(text) == base + extra, (f["dialect"], decls(text)))
    csv = by_q(S.parse(PG_HINT_BASE + "CREATE TABLE csv_format (\n  id int,\n  delimiter char(1)\n);\n"))
    check("a column named delimiter is a column", csv.get("csv_format", {}).get("columns") == ["id", "delimiter"],
          csv.get("csv_format"))

    f = S.parse(TSQL_ENGINE)
    got = [q for _k, q, _l in decls(TSQL_ENGINE)]
    check("T-SQL with GO lines and SET Engine = ...: still tsql, 'C:\\' ends, every object found",
          f["dialect"] == "tsql" and got == ["dbo.Cars", "dbo.vw_Cars", "dbo.usp_Path", "dbo.usp_After",
                                              "dbo.vw_Names"] and f["dynamic_unread"] == 0, (f["dialect"], got))

    f = S.parse(CONCAT_TSQL)
    got = [q for _k, q, _l in decls(CONCAT_TSQL)]
    check("T-SQL concatenated dynamic SQL: no table dbo.Archive_ / view dbo.v_Tenant; the full name dbo.v_Full stays",
          got == ["dbo.usp_MakeArchive", "dbo.v_Full"] and f["dynamic_unread"] == 2, (got, f["dynamic_unread"]))
    f = S.parse(CONCAT_ORACLE)
    got = [q for _k, q, _l in decls(CONCAT_ORACLE)]
    check("Oracle EXECUTE IMMEDIATE 'CREATE TABLE sales_' || p: no table sales_, counted as not read",
          got == ["mk_part"] and f["dynamic_unread"] == 1, (got, f["dynamic_unread"]))

    f = S.parse(QQUOTE)
    want = [("procedure", "greet", 1), ("table", "after_q", line_of(QQUOTE, "CREATE TABLE after_q")),
            ("view", "v_after", line_of(QQUOTE, "CREATE OR REPLACE VIEW v_after")),
            ("procedure", "last_one", line_of(QQUOTE, "CREATE OR REPLACE PROCEDURE last_one"))]
    check("Oracle q'[...]', Q'{...}', nq'!...!' quotes with apostrophes: no desync, no table fake_q",
          decls(QQUOTE) == want and f["dialect"] == "oracle", (decls(QQUOTE), f["dialect"]))

    text = ("CREATE TABLE orders (id NUMBER PRIMARY KEY, total NUMBER);\n"
            "CREATE MATERIALIZED VIEW LOG ON orders WITH ROWID, SEQUENCE (total) INCLUDING NEW VALUES;\n"
            "CREATE MATERIALIZED VIEW mv_totals REFRESH FAST ON COMMIT AS SELECT COUNT(*) c FROM orders;\n")
    check("Oracle CREATE MATERIALIZED VIEW LOG ON t is no view named LOG",
          decls(text) == [("table", "orders", 1), ("materialized view", "mv_totals", 3)], decls(text))
    text = "ALTER TRIGGER trg_x ENABLE\nCREATE OR REPLACE VIEW v_after AS SELECT 1 AS a FROM dual;\n"
    check("an ALTER with no AS of its own is no definition: the AS of the next statement does not count",
          decls(text) == [("view", "v_after", 2)], decls(text))

    text = ("IF OBJECT_ID(N'dbo.usp_Setup') IS NULL\nEXEC(N'CREATE PROCEDURE dbo.usp_Setup AS\nBEGIN\n"
            "  EXEC(N''CREATE VIEW dbo.vw_Inner AS SELECT 1 AS a'');\nEND')\nGO\n")
    f = S.parse(text)
    check("a CREATE run from a literal inside a nested batch counts as dynamic SQL not read",
          [q for _k, q, _l in decls(text)] == ["dbo.usp_Setup"] and f["dynamic_unread"] == 1,
          (decls(text), f["dynamic_unread"]))

    for name, text in (
            ("multi-row INSERT VALUES strings", "CREATE TABLE ddl_log (id int, command text);\nINSERT INTO ddl_log "
             "VALUES (1, 'CREATE TABLE a (x int)'),\n (2, 'CREATE TABLE b (y int)'), (3, 'CREATE VIEW c AS SELECT 1');"
             "\n"),
            ("sp_addextendedproperty @value", "CREATE TABLE [dbo].[Customers] ([Id] INT)\nGO\nEXEC sys."
             "sp_addextendedproperty @name=N'MS_Description', @value=N'Create table dbo.Customers for the shop', "
             "@level0type=N'SCHEMA', @level0name=N'dbo'\nGO\n"),
            ("a THROW message", "CREATE TABLE dbo.T (Id INT)\nGO\nTHROW 50001, 'CREATE TABLE dbo.Blocked failed', 1;\n"
             "GO\n"),
            ("a stop word after CREATE TABLE", "CREATE TABLE dbo.T (Id INT)\nGO\nDECLARE @m NVARCHAR(100) = "
             "N'Create table for the customers';\nGO\n"),
            ("a RAISE NOTICE in a plpgsql body", "CREATE FUNCTION f() RETURNS void AS $$ BEGIN RAISE NOTICE "
             "'CREATE TABLE note_x (y int)'; END; $$ LANGUAGE plpgsql;\n")):
        f = S.parse(text)
        check(f"data and messages are not dynamic SQL: {name}", f["dynamic_unread"] == 0, f["dynamic_unread"])

    text = ("SET @x = N'-- built at deploy time\nCREATE TABLE dbo.NotRun2 (Id INT)';\nGO\n"
            "SET @y = N'/* v2 */ CREATE VIEW dbo.NotRun3 AS SELECT 1 AS a';\nGO\nEXEC(@x)\nEXEC(@y)\nGO\n")
    f = S.parse(text)
    check("a literal not run that starts with a comment, then a CREATE, counts as dynamic SQL not read",
          f["decls"] == [] and f["dynamic_unread"] == 2, (f["decls"], f["dynamic_unread"]))

    f = S.parse(MYSQL_PLAIN)
    check("plain MySQL: the # comment's CREATE and the \\' string's CREATE are no declarations",
          "old_users" not in [d["name"] for d in f["decls"]] and "fake_bs" not in [d["name"] for d in f["decls"]],
          f["decls"])

    m = "GRANT CREATE SESSION, CREATE VIEW, CREATE TABLE TO sh;\n"
    t = "GRANT EXECUTE ON dbo.p TO r\nCREATE TABLE dbo.T (x int)\n"
    check("the privilege rule: a CREATE in a GRANT privilege list is one; a CREATE after GRANT ... ON ... TO is not",
          S._privilege(m, m.index("CREATE TABLE"), []) is True
          and S._privilege(t, t.index("CREATE TABLE"), []) is False,
          "")


# ---------------------------------------------------------------------------------------------------- C
def section_c() -> None:
    print("\n-- C. encodings: identical declarations, lines and summaries --")
    crlf = TSQL.replace("\n", "\r\n")
    variants = {
        "utf-8": TSQL.encode("utf-8"),
        "utf-8 BOM + CRLF": b"\xef\xbb\xbf" + crlf.encode("utf-8"),
        "utf-16 LE BOM": b"\xff\xfe" + TSQL.encode("utf-16-le"),
        "utf-16 BE BOM": b"\xfe\xff" + TSQL.encode("utf-16-be"),
        "utf-16 LE, no BOM": TSQL.encode("utf-16-le"),
        "utf-16 BE, no BOM": TSQL.encode("utf-16-be"),
        "cp1252": TSQL.encode("cp1252"),
    }
    ref = None
    for name, data in variants.items():
        text, enc = S.decode(data)
        check(f"{name}: decodes to the script ({enc})", text.replace("\r\n", "\n") == TSQL, enc)
        f = S.parse(text)
        nodes = S.file_nodes("db/schema.sql", f, size=len(data))
        sig = ([(d["kind"], d["qualified"], d["line"], d["reads"], d["writes"]) for d in f["decls"]],
               [(n["key"], n["start_line"], n["body_summary"]) for n in nodes])
        if ref is None:
            ref = sig
        check(f"{name}: declarations, lines and summaries equal the UTF-8 reading", sig == ref, sig[0][:2])
    text, enc = S.decode(b"SELECT 1 -- \xe9\x81 x\n")
    check("bytes valid in no UTF: cp1252 with replacement, never an exception", enc == "cp1252" and "\u00e9" in text,
          enc)


# ---------------------------------------------------------------------------------------------------- N
def section_n() -> None:
    print("\n-- N. names --")
    cases = [
        ("CREATE TABLE [dbo].[Order Details] (x int)", "Order Details", "dbo.Order Details"),
        ("CREATE TABLE [dbo].[odd]]name] (x int)", "odd]name", "dbo.odd]name"),
        ('CREATE TABLE "Invoice" (x int);', "Invoice", "Invoice"),
        ("CREATE TABLE `users` (x int) ENGINE=InnoDB;", "users", "users"),
        ("CREATE TABLE Sales . [Orders] (x int)\nGO", "Orders", "Sales.Orders"),
        ("CREATE TABLE mydb..Orders (x int)\nGO", "Orders", "mydb.Orders"),
        ("CREATE TABLE srv.mydb.dbo.Orders (x int)\nGO", "Orders", "srv.mydb.dbo.Orders"),
        ("CREATE TABLE [Bestellungen_\u00c4] ([Menge] INT)\nGO", "Bestellungen_\u00c4", "Bestellungen_\u00c4"),
        ("CREATE TABLE \u05d4\u05d6\u05de\u05e0\u05d5\u05ea (x int);", "\u05d4\u05d6\u05de\u05e0\u05d5\u05ea",
         "\u05d4\u05d6\u05de\u05e0\u05d5\u05ea"),
        ("CREATE TABLE /* c */ named_after_comment (x int);", "named_after_comment", "named_after_comment"),
    ]
    for text, name, qual in cases:
        got = S.parse(text)["decls"]
        check(f"{text.splitlines()[0][:60]!r} -> name {name!r}, qualified {qual!r}",
              len(got) == 1 and got[0]["name"] == name and got[0]["qualified"] == qual,
              [(d["name"], d["qualified"]) for d in got])
    got = S.parse("CREATE TABLE [dbo].[unclosed (x int)\nGO\nCREATE TABLE [dbo].[Fine] (x int)\nGO\n")["decls"]
    check("a bracket that does not close on its line ends the name (no declaration from it)",
          [d["qualified"] for d in got] == ["dbo.Fine"], [d["qualified"] for d in got])
    got = S.parse("CREATE TABLE sys.shadow (x int);\nCREATE TABLE information_schema.t (x int);\n")["decls"]
    check("system schemas are never declarations", got == [], got)

    text = ("CREATE TABLE [dbo].[Statement] ([Id] INT, [AccountId] INT)\nGO\nCREATE TABLE dbo.Output (Id INT)\nGO\n"
            "CREATE TABLE [Default] (Id INT)\nGO\nCREATE TABLE dbo.Row (Id INT)\nGO\n"
            "CREATE VIEW dbo.v_Statements AS SELECT s.Id FROM dbo.Statement s JOIN dbo.Account a ON a.Id = s.AccountId"
            "\nGO\nCREATE TABLE dbo.StatementLine (Id INT, StatementId INT REFERENCES dbo.Statement (Id))\nGO\n")
    f = S.parse(text)
    d = by_q(f)
    check("a quoted or qualified name that is a stop word is a name ([dbo].[Statement], dbo.Output, [Default], ...)",
          [x["qualified"] for x in f["decls"]] == ["dbo.Statement", "dbo.Output", "Default", "dbo.Row",
                                                   "dbo.v_Statements", "dbo.StatementLine"], list(d))
    check("reads and references of a stop-word table",
          d.get("dbo.v_Statements", {}).get("reads") == ["Statement", "Account"]
          and d.get("dbo.StatementLine", {}).get("references") == ["Statement"], d.get("dbo.v_Statements"))
    got = [x["qualified"] for x in S.parse('CREATE TABLE "statement" (id int);\nCREATE TABLE public."values" (id int);'
                                           '\nCREATE TABLE statement (id int);\n')["decls"]]
    check("PostgreSQL \"statement\" and public.\"values\" are tables; a bare stop word is not",
          got == ["statement", "public.values"], got)

    cases = [("CREATE TABLE ${schema}.users (id bigint);", "users", "${schema}.users"),
             ("CREATE VIEW ${schema}.active_users AS SELECT * FROM ${schema}.users;", "active_users",
              "${schema}.active_users"),
             ("CREATE TABLE ${flyway:defaultSchema}.audit (id bigint);", "audit", "${flyway:defaultSchema}.audit"),
             ("CREATE TABLE $(Schema).Orders (Id INT)\nGO", "Orders", "$(Schema).Orders"),
             ("CREATE TABLE &&schema..users (id NUMBER);", "users", "&&schema..users")]
    for text, name, qual in cases:
        got = S.parse(text)["decls"]
        check(f"migration-tool placeholder schema {text.split()[2]!r} -> name {name!r}",
              len(got) == 1 and got[0]["name"] == name and got[0]["qualified"] == qual,
              [(x["name"], x["qualified"]) for x in got])
    got = S.parse("CREATE VIEW ${schema}.v AS SELECT * FROM ${schema}.users;")["decls"]
    check("a placeholder-schema read keeps the object name", got and got[0]["reads"] == ["users"], got)
    got = S.parse("CREATE TABLE ${table} (id int);\nCREATE TABLE &prefix.users (id int);\n")["decls"]
    check("a placeholder as the object name, or SQL*Plus &x.name concatenation, is no declaration", got == [], got)


# ---------------------------------------------------------------------------------------------------- T
def section_t() -> None:
    print("\n-- T. facts --")
    d = by_q(S.parse(TSQL))
    check("table columns (constraints left out)", d["dbo.Customers"]["columns"] == ["Id", "Email", "CreatedAt"],
          d["dbo.Customers"]["columns"])
    a = d["dbo.usp_AddOrderLine"]
    check("T-SQL procedure: params, reads, writes, runs",
          (a["params"], a["reads"], a["writes"], a["runs"]) ==
          (["@OrderId", "@Sku", "@Qty"], ["Orders"], ["OrderLines"], ["usp_GetOrdersForCustomer"]), a)
    t = d["dbo.trg_Orders_Audit"]
    check("T-SQL trigger: on Orders, writes Orders, inserted is no read",
          (t["on"], t["writes"], t["reads"]) == ("Orders", ["Orders"], []), t)
    check("view reads both joined tables", d["dbo.vw_CustomerTotals"]["reads"] == ["Customers", "Orders"],
          d["dbo.vw_CustomerTotals"])
    check("function params from its parenthesised list", d["dbo.fn_OrderTotal"]["params"] == ["@OrderId"],
          d["dbo.fn_OrderTotal"])
    f = S.parse(TSQL)
    check("module alters (ALTER TABLE targets, once each)", f["alters"] == ["Orders", "OrderLines"], f["alters"])
    p = by_q(S.parse(PG))
    check("PostgreSQL inline REFERENCES", p["orders"]["references"] == ["customers"], p["orders"])
    check("PostgreSQL plpgsql body read through its dollar quotes", p["order_total"]["reads"] == ["order_items"]
          and p["order_total"]["params"] == ["p_order"], p["order_total"])
    check("PostgreSQL trigger: on order_items, runs touch_order",
          (p["trg_items_touch"]["on"], p["trg_items_touch"]["runs"]) == ("order_items", ["touch_order"]),
          p["trg_items_touch"])
    check("PostgreSQL procedure: DELETE FROM is a write, not a read",
          (p["archive_orders"]["writes"], p["archive_orders"]["reads"]) == (["orders"], []), p["archive_orders"])
    dml = "-- DML only\nALTER TABLE orders ADD COLUMN shipped_at TIMESTAMP NULL;\n" \
          "INSERT INTO customers (email) VALUES ('a@example.com');\n" \
          "INSERT INTO orders (customer_id) SELECT id FROM customers WHERE email LIKE '%@example.com';\n" \
          "UPDATE orders SET status = 'legacy';\nDELETE FROM order_items WHERE qty = 0;\n"
    m = S.parse(dml)
    check("DML-only file: no declarations; alters and writes in order",
          (m["decls"], m["alters"], m["writes"]) == ([], ["orders"], ["customers", "orders", "order_items"]), m)
    check("a parse result holds exactly the design's keys",
          set(m) == {"dialect", "decls", "alters", "writes", "dynamic_unread"}, sorted(m))
    keys = {"label", "kind", "name", "qualified", "line", "columns", "references", "params", "on", "reads",
            "writes", "runs", "package"}
    check("every declaration holds exactly the design's keys",
          all(set(x) == keys for x in S.parse(ORACLE)["decls"] + S.parse(TSQL)["decls"]),
          [sorted(x) for x in S.parse(ORACLE)["decls"][:1]])

    text = ("CREATE PROCEDURE p_case() BEGIN CASE x WHEN 1 THEN SELECT 1; END CASE; SELECT * FROM after_case; END;\n"
            "INSERT INTO module_level SELECT * FROM leak;\n")
    f = S.parse(text)
    pc = by_q(f).get("p_case", {})
    check("END CASE closes the CASE: the procedure ends at its END; (reads after_case; the INSERT is the module's)",
          pc.get("reads") == ["after_case"] and pc.get("writes") == [] and f["writes"] == ["module_level"],
          (pc, f["writes"]))
    text = ("CREATE PROCEDURE p_tran AS BEGIN BEGIN TRAN; UPDATE accounts SET x = 1; COMMIT; END;\n"
            "INSERT INTO module_level2 VALUES (1);\n")
    f = S.parse(text)
    pt = by_q(f).get("p_tran", {})
    check("BEGIN TRAN opens no block: the procedure writes accounts only; the INSERT after it is the module's",
          pt.get("writes") == ["accounts"] and f["writes"] == ["module_level2"], (pt, f["writes"]))
    text = ("CREATE VIEW v_dates AS SELECT EXTRACT(YEAR FROM o.created) AS y, TRIM(BOTH FROM o.name) AS n, "
            "TRIM(LEADING 'x' FROM o.code) AS c FROM orders o WHERE o.a IS DISTINCT FROM o.b;\n")
    vd = by_q(S.parse(text)).get("v_dates", {})
    check("FROM after EXTRACT(YEAR / TRIM(BOTH / TRIM(LEADING 'x' / IS DISTINCT is no read",
          vd.get("reads") == ["orders"], vd)
    text = "CREATE TABLE `logs` (`a` int) ENGINE=InnoDB;\nINSERT IGNORE INTO logs (a) VALUES (1);\n" \
           "INSERT LOW_PRIORITY INTO stats (a) VALUES (2);\n"
    f = S.parse(text)
    check("MySQL INSERT IGNORE / LOW_PRIORITY INTO x: the write is x", f["writes"] == ["logs", "stats"], f["writes"])
    text = ("CREATE TABLE Orders (Id INT)\nGO\nCREATE TABLE dbo.Lines (Id INT, OrderId INT)\nGO\n"
            "ALTER TABLE Lines ADD CONSTRAINT fk FOREIGN KEY (OrderId) REFERENCES Orders (Id)\nGO\n")
    ln = by_q(S.parse(text)).get("dbo.Lines", {})
    check("ALTER TABLE x REFERENCES y binds to the one table named x (dbo.Lines)", ln.get("references") == ["Orders"],
          ln)


# ---------------------------------------------------------------------------------------------------- D
def summaries(text: str, rel: str = "db/x.sql") -> dict:
    nodes = S.file_nodes(rel, S.parse(text), size=len(text))
    return {n["name"]: n["body_summary"] for n in nodes}


def section_d() -> None:
    print("\n-- D. summaries --")
    cols = ", ".join(f"c{i:02d} int" for i in range(1, 13))
    s = summaries(f"CREATE TABLE sales.big_table ({cols}, owner_id int REFERENCES owners(id));")
    want = ("big table: table in sales; columns c01, c02, c03, c04, c05, c06, c07, c08, c09, c10, c11, c12; "
            "references owners")
    check("table: 13 columns -> 12 listed, references, its schema", s.get("big_table") == want, s.get("big_table"))
    s = summaries("CREATE TABLE dbo.Plain (Id INT)\nGO\n")
    check("table in dbo: no schema clause", s.get("Plain") == "plain: table; columns id", s.get("Plain"))
    s = summaries("CREATE VIEW dbo.vw_OpenOrders AS SELECT o.Id FROM dbo.Orders o JOIN dbo.Customers c "
                  "ON c.Id = o.CustomerId\nGO\n")
    check("view", s.get("vw_OpenOrders") == "vw open orders: view; reads orders, customers", s.get("vw_OpenOrders"))
    proc = """CREATE PROCEDURE dbo.usp_CloseOrder @OrderId INT, @ClosedBy NVARCHAR(50)
AS
BEGIN
  UPDATE o SET o.Status = 'closed' FROM dbo.Orders o JOIN dbo.Customers c ON c.Id = o.CustomerId WHERE o.Id = @OrderId;
  INSERT INTO dbo.AuditLog (OrderId) VALUES (@OrderId);
  EXEC dbo.usp_NotifyCustomer @OrderId = @OrderId;
END
GO
"""
    s = summaries(proc)
    want = ("usp close order: procedure (order id, closed by); reads customers; writes orders, audit log; "
            "runs usp notify customer")
    check("T-SQL procedure: params, reads, alias-resolved UPDATE, runs", s.get("usp_CloseOrder") == want,
          s.get("usp_CloseOrder"))
    s = summaries(PG)
    check("PostgreSQL trigger", s.get("trg_items_touch") == "trg items touch: trigger on order items; runs touch order",
          s.get("trg_items_touch"))
    check("materialized view", s.get("mv_totals") == "mv totals: materialized view; reads orders", s.get("mv_totals"))
    s = summaries(ORACLE)
    check("package member", s.get("charge") == "charge: procedure in package billing (p order id); writes orders",
          s.get("charge"))
    check("package: its members", s.get("billing") == "billing: package; members charge, total due",
          s.get("billing"))
    s = summaries("CREATE TYPE dbo.OrderIdList AS TABLE (OrderId INT NOT NULL)\nGO\nCREATE TYPE dbo.Phone FROM "
                  "nvarchar(20)\nGO\n")
    check("table type and alias type", (s.get("OrderIdList"), s.get("Phone")) ==
          ("order id list: table type; columns order id", "phone: type"), s)
    dml = ("ALTER TABLE orders ADD COLUMN shipped_at TIMESTAMP NULL;\n"
           "INSERT INTO customers (email) VALUES ('a@example.com');\nUPDATE orders SET status = 'x';\n"
           "DELETE FROM order_items WHERE qty = 0;\n")
    s = summaries(dml, "db/migrations/migration_dml.sql")
    check("DML-only module", s.get("migration_dml") ==
          "migration dml: SQL script; alters orders; writes customers, orders, order items", s.get("migration_dml"))
    s = summaries("CREATE TABLE dbo.Real (Id INT)\nGO\nSET @x = N'CREATE TABLE dbo.NotRun (Id INT)';\nGO\n",
                  "dyn_script.sql")
    check("module with dynamic SQL not read", s.get("dyn_script") ==
          "dyn script: SQL script; creates real; 1 object(s) created by dynamic SQL not read", s.get("dyn_script"))
    s = summaries(ORACLE, "pkg/billing_pkg.sql")
    check("module creates lists top-level objects only (no members); DML inside a routine is not the module's",
          s.get("billing_pkg") == "billing pkg: SQL script; creates billing, archive orders", s.get("billing_pkg"))
    nr = {why: S.file_nodes("dumps/big_dump.sql", None, size=9 * 1024 * 1024, why=why)
          for why in ("too_large", "budget", "slow")}
    check("not read: too large", nr["too_large"][0]["body_summary"] ==
          "big dump: SQL script (not read: 9 MB, over the 8 MB limit)", nr["too_large"][0]["body_summary"])
    check("not read: budget", nr["budget"][0]["body_summary"] ==
          "big dump: SQL script (not read: the per-sync SQL read budget (256 MB) was used up)",
          nr["budget"][0]["body_summary"])
    check("not read: slow", nr["slow"][0]["body_summary"] == "big dump: SQL script (not read: reading it took over "
          "60 s)", nr["slow"][0]["body_summary"])
    check("a not-read file is its :Module only", all(len(v) == 1 and v[0]["label"] == "Module" for v in nr.values()),
          nr)
    old = S.MAX_BYTES
    try:
        S.MAX_BYTES = 1024
        got = S.file_nodes("a.sql", None, size=2048, why="too_large")[0]["body_summary"]
        check("not read: too large, limit patched small (the text states the real limit)",
              got == "a: SQL script (not read: 2 KB, over the 1 KB limit)", got)
    finally:
        S.MAX_BYTES = old
    many = ";\n".join(f"INSERT INTO written_table_with_a_long_name_{i:03d} SELECT * FROM very_long_table_name_{i:03d}"
                      f";\nEXEC dbo.usp_a_procedure_with_a_long_name_{i:03d}" for i in range(40))
    long_all = [TSQL, PG, MYSQL, ORACLE, EF_IDEMPOTENT, SSMS_EXISTS,
                f"CREATE PROCEDURE p_long AS BEGIN {many}; END\nGO\n"]
    worst = 0
    dashes = []
    for text in long_all:
        for n in S.file_nodes("db/some_quite_long_script_name.sql", S.parse(text), size=len(text)):
            worst = max(worst, len(n["body_summary"]))
            if "\u2014" in n["body_summary"] or "\u2013" in n["body_summary"]:
                dashes.append(n["body_summary"])
    check("every summary <= 300 characters (a long one is capped)", worst <= 300 and worst >= 250, worst)
    check("no em or en dash in any summary", not dashes, dashes[:2])


# ---------------------------------------------------------------------------------------------------- E
def section_e() -> None:
    print("\n-- E. node dicts --")
    nodes = S.file_nodes("db/schema.sql", S.parse(TSQL), size=len(TSQL))
    check("one :Module first, key sql:<rel>, name = stem, line 1",
          nodes[0]["key"] == "sql:db/schema.sql" and nodes[0]["label"] == "Module" and nodes[0]["name"] == "schema"
          and nodes[0]["start_line"] == 1, nodes[0])
    keys = [n["key"] for n in nodes[1:]]
    check("declaration keys sql:<rel>#<kind>:<qualified lower>, no line in the key",
          keys[:2] == ["sql:db/schema.sql#table:dbo.customers", "sql:db/schema.sql#table:dbo.orders"]
          and "sql:db/schema.sql#procedure:dbo.usp_addorderline" in keys, keys)
    check("labels: tables / views :Class, routines / triggers :Function",
          [n["label"] for n in nodes[1:5]] == ["Class", "Class", "Class", "Class"]
          and {n["label"] for n in nodes[5:]} == {"Function"}, [n["label"] for n in nodes])
    check("every node: call_by_name is False and documented is False",
          all(n["call_by_name"] is False and n["documented"] is False for n in nodes), nodes[:2])
    check("every node: source_file is the rel path; start_line an int",
          all(n["source_file"] == "db/schema.sql" and isinstance(n["start_line"], int) for n in nodes), nodes[:2])
    check("node dict fields", all(set(n) == {"key", "label", "name", "source_file", "start_line", "documented",
                                             "body_summary", "call_by_name"} for n in nodes), sorted(nodes[0]))
    ks = [n["key"] for n in S.file_nodes("x.sql", S.parse(PG + ORACLE_TYPE + "CREATE TYPE dbo.T AS TABLE (a int)\nGO\n"
                                                            ), size=1)]
    check("kind tokens materializedview / tabletype / type in keys",
          "sql:x.sql#materializedview:mv_totals" in ks and "sql:x.sql#tabletype:dbo.t" in ks, ks)
    ok = S.file_nodes("pkg.sql", S.parse(ORACLE), size=1)
    check("package member key holds the package", "sql:pkg.sql#procedure:billing.charge" in [n["key"] for n in ok],
          [n["key"] for n in ok])
    for text, what in (("", "empty file"), ("UPDATE t SET a = 1;\n", "DML-only file"),
                       ("-- only a comment\n", "comment-only file")):
        got = S.file_nodes("db/e.sql", S.parse(text), size=len(text))
        check(f"{what}: exactly one :Module", len(got) == 1 and got[0]["label"] == "Module", got)
    got = S.parse("CREATE TABLE dbo.Dup (a INT)\nGO\nCREATE TABLE dbo.Dup (b INT)\nGO\n")["decls"]
    check("de-duplication: one node at the first occurrence",
          [(d["qualified"], d["line"], d["columns"]) for d in got] == [("dbo.Dup", 1, ["a"])], got)
    a = S.file_nodes("a/one.sql", S.parse("CREATE TABLE dbo.Orders (a INT)\nGO\n"), size=1)
    b = S.file_nodes("b/two.sql", S.parse("CREATE TABLE dbo.Orders (a INT)\nGO\n"), size=1)
    check("two files declaring dbo.Orders give two nodes", a[1]["key"] != b[1]["key"], (a[1]["key"], b[1]["key"]))
    check("a Windows rel path is written with /",
          S.file_nodes("db\\win\\x.sql", None, size=1, why="slow")[0]["source_file"] == "db/win/x.sql",
          S.file_nodes("db\\win\\x.sql", None, size=1, why="slow")[0])

    d = tmpdir()
    raised = []
    for p in (d / "missing.sql", d):
        try:
            S.read_file(p, "x.sql")
            raised.append(None)
        except OSError:
            raised.append("OSError")
        except Exception as exc:
            raised.append(type(exc).__name__)
    check("read_file raises OSError for a missing file and for a directory", raised == ["OSError", "OSError"], raised)
    f = d / "ok.sql"
    f.write_bytes(TSQL.encode("utf-16"))
    nodes, info = S.read_file(f, "ok.sql")
    check("read_file reads a UTF-16 file: state read, its declarations",
          info["state"] == "read" and info["declarations"] == 10 and len(nodes) == 11 and info["dialect"] == "tsql",
          info)
    check("read_file info keys", {"state", "declarations", "encoding", "dialect", "dynamic_unread"} <= set(info),
          sorted(info))
    if os.name == "nt":
        deep = "\\\\?\\" + str(d.resolve())
        parts = []
        while len(str(d.resolve())) + sum(len(x) + 1 for x in parts) < 290:
            parts.append("p" * 40)
        target = os.path.join(deep, *parts)
        os.makedirs(target, exist_ok=True)
        long_file = os.path.join(target, "deep_schema.sql")
        with open(long_file, "wb") as fh:
            fh.write(b"CREATE TABLE dbo.Deep (Id INT)\nGO\n")
        plain = long_file[4:]
        opened = []
        real_open = open

        def rec_open(path, *a, **k):
            opened.append(str(path))
            return real_open(path, *a, **k)
        S.open = rec_open
        try:
            nodes, info = S.read_file(plain, "deep/deep_schema.sql")
        finally:
            del S.open
        check(f"a {len(plain)}-character Windows path is read through its \\\\?\\ form",
              len(plain) >= 300 and info["state"] == "read" and info["declarations"] == 1
              and opened and opened[0].startswith("\\\\?\\"), (len(plain), info, opened[:1]))
    else:
        print("  [SKIP] the \\\\?\\ long-path read is Windows-only")
    if os.name == "nt":
        unc = "\\\\server\\share\\" + "\\".join(["q" * 40] * 7) + "\\x.sql"
        drive = "C:\\" + "\\".join(["q" * 40] * 7) + "\\x.sql"
        short = "C:\\db\\x.sql"
        got = (S._os_path(unc), S._os_path(drive), S._os_path(short), S._os_path("\\\\?\\" + drive))
        want = ("\\\\?\\UNC\\server\\share\\" + unc[len("\\\\server\\share\\"):], "\\\\?\\" + drive, short,
                "\\\\?\\" + drive)
        check("_os_path: a long UNC path -> \\\\?\\UNC\\server\\share\\..., a long drive path -> \\\\?\\C:\\..., "
              "a short or already extended path unchanged", got == want, got)
    else:
        check("_os_path is the identity off Windows", S._os_path("/a/" + "b" * 300) == "/a/" + "b" * 300)

    d2 = tmpdir()
    f2 = d2 / "late.sql"
    f2.write_bytes(b"CREATE TABLE dbo.Late (Id INT)\nGO\n")
    real_parse, old = S.parse, S.DEADLINE_S

    def late_parse(text, *, deadline_s=None):
        time.sleep(0.3)
        return real_parse(text)
    try:
        S.parse, S.DEADLINE_S = late_parse, 0.1
        nodes, info = S.read_file(f2, "late.sql")
    finally:
        S.parse, S.DEADLINE_S = real_parse, old
    check("read_file checks the deadline once more after parse: a parse that ended past it is slow",
          info["state"] == "slow" and len(nodes) == 1, info)


# ---------------------------------------------------------------------------------------------------- F
NL = "\n"
BS = "\\"


def _shapes() -> list:
    """(name, make(n), first n): adversarial and data-heavy shapes (design 3.3 + gate F + the review's own)."""
    row = "(1,'it" + BS + "'s a test value','2020-01-01',NULL,'C:" + BS + BS + "path')"
    return [
        ("one line of unclosed [",
         lambda n: "CREATE TABLE a (x int);\nSELECT " + "[" * n + "\nCREATE TABLE b (y int);\n", 20000),
        ("a[  repeated on one line", lambda n: "CREATE TABLE a (x int);\nSELECT 1 " + "a[ " * n + NL, 8000),
        ("unclosed \" repeated", lambda n: "CREATE TABLE a (x int);\nSELECT " + '"a ' * n + NL, 8000),
        ("[a] ,  repeated on one line (closed)", lambda n: "SELECT " + "[a] , " * n + "\nCREATE TABLE b (y int);\n",
         8000),
        ("one-line view body with many FROM [dbo].[tN]",
         lambda n: "CREATE VIEW [dbo].[v] AS SELECT 1 " + " ".join(f"UNION SELECT x FROM [dbo].[t{i}]"
                                                                  for i in range(n)) + "\nGO\n", 3000),
        ("unclosed /*!40101 before many INSERT lines (MySQL)",
         lambda n: "CREATE TABLE `t` (`a` int) ENGINE=InnoDB;\n/*!40101 SET x=1;\n"
                   + "INSERT INTO t VALUES ('a','b');\n" * n, 4000),
        ("mysqldump single-line extended INSERT",
         lambda n: "-- MySQL dump\n/*!40101 SET NAMES utf8 */;\nCREATE TABLE `t` (`id` int, `v` text) ENGINE=InnoDB;\n"
                   "INSERT INTO `t` VALUES " + ",".join([row] * n) + ";\nCREATE TABLE `after_insert` (`id` int);\n",
         3000),
        ("pg_dump COPY block",
         lambda n: "CREATE TABLE public.people (id integer, name text);\nCOPY public.people (id, name) FROM stdin;\n"
                   + NL.join(f"{i}\tO'Brien\t{BS}N\t" + '{"k": "[v]"}' for i in range(n))
                   + "\n\\.\n\nCREATE TABLE public.after_copy (id integer);\n", 3000),
        ("nested /* never closed", lambda n: "CREATE TABLE a (x int);\n" + "/*" * n + "\nCREATE TABLE b (y int);\n",
         20000),
        ("nested /* closed once at the end", lambda n: "CREATE TABLE a (x int);\n" + "/* x " * n + "*/\n", 8000),
        ("unterminated ' at byte 10 before many tables",
         lambda n: "SELECT 'x\n" + "CREATE TABLE t (x int);\n" * n, 3000),
        ("unclosed $q$ tags", lambda n: "CREATE TABLE a (x int);\nSELECT $q$ " + "$a$ $b$ " * n + NL, 8000),
        ("many distinct unclosed dollar tags", lambda n: "CREATE TABLE a (x int);\nSELECT "
                                                         + " ".join(f"$t{i}$" for i in range(n)) + NL, 4000),
        ("T-SQL procedures without GO",
         lambda n: "CREATE TABLE [dbo].[T] ([x] int);\n" + "".join(
             f"CREATE PROCEDURE [dbo].[p{i}] @a int AS BEGIN SELECT * FROM [dbo].[T] WHERE x = @a; "
             f"UPDATE [dbo].[T] SET x = 1; END\n" for i in range(n)), 600),
        ("tables with an unclosed paren", lambda n: "".join(f"CREATE TABLE t{i} (x int, y int\n" for i in range(n)),
         2000),
        ("many EXEC(N'CREATE VIEW ...')",
         lambda n: "".join(f"EXEC(N'CREATE VIEW v{i} AS SELECT 1 FROM t{i}')\nGO\n" for i in range(n)), 600),
        ("many SET @sN = N'CREATE ...' in one batch",
         lambda n: "".join(f"SET @s{i} = N'CREATE VIEW v{i} AS SELECT 1';\n" for i in range(n)) + "EXEC(@s0)\n",
         1000),
        ("generated bracketed script", lambda n: big_script(n, True), 20),
        ("Oracle package with many members",
         lambda n: "CREATE OR REPLACE PACKAGE big AS\n" + "".join(f"  PROCEDURE m{i}(a IN NUMBER);\n"
                                                                   for i in range(n)) + "END big;\n/\n"
                   + "CREATE OR REPLACE PACKAGE BODY big AS\n" + "".join(
                       f"  PROCEDURE m{i}(a IN NUMBER) IS BEGIN UPDATE t{i} SET x = a; END m{i};\n" for i in range(n))
                   + "END big;\n/\n", 600),
        ("many ALTER TABLE ... REFERENCES",
         lambda n: "".join(f"CREATE TABLE t{i} (id int)\nGO\nALTER TABLE t{i} ADD CONSTRAINT fk{i} FOREIGN KEY (id) "
                           f"REFERENCES t{max(i - 1, 0)} (id)\nGO\n" for i in range(n)), 600),
        ("SQL*Plus REM lines", lambda n: "REM it's a remark\n" * n + "CREATE TABLE after_rem (x NUMBER);\n", 6000),
        ("many /*! openers, one */ at the end (MySQL)",
         lambda n: "CREATE TABLE `t` (`a` int) ENGINE=InnoDB;\n" + "/*!40101 x " * n + "*/\n", 4000),
        ("many unclosed /*! openers, no */ (MySQL)",
         lambda n: "CREATE TABLE `t` (`a` int) ENGINE=InnoDB;\n" + "/*!40101 SET x=1;\n" * n, 4000),
        ("bare ALTER VIEW lines", lambda n: "".join(f"ALTER VIEW v{i}\n" for i in range(n)), 2000),
        ("COPY blocks with odd apostrophes",
         lambda n: "".join(f"COPY t{i} (a) FROM stdin;\nO'Brien {i}\n\\.\n" for i in range(n))
                   + "CREATE TABLE after_copy (x int);\n", 2000),
        ("DO blocks", lambda n: "".join(f"DO $$ BEGIN CREATE TABLE IF NOT EXISTS t{i} (x int); END $$;\n"
                                        for i in range(n)), 600),
        ("Oracle q-quotes", lambda n: "CREATE OR REPLACE PROCEDURE p AS BEGIN\n" + "".join(
            f"  put_line(q'[it's {i}]');\n" for i in range(n)) + "END;\n/\n", 4000),
        ("multi-row VALUES with CREATE strings",
         lambda n: "INSERT INTO ddl_log VALUES " + ",".join(f"({i}, 'CREATE TABLE a{i} (x int)')" for i in range(n))
                   + ";\n", 2000),
        ("concatenated EXEC literals",
         lambda n: "".join(f"EXEC('CREATE TABLE dbo.A{i}_' + @y + ' (Id INT)')\nGO\n" for i in range(n)), 600),
        ("mysqldump view placeholder pairs",
         lambda n: "CREATE TABLE `t` (`a` int) ENGINE=InnoDB;\n" + "".join(
             f"/*!50001 CREATE VIEW `v{i}` AS SELECT\n 1 AS `a`*/;\n/*!50001 CREATE ALGORITHM=UNDEFINED */\n"
             f"/*!50001 VIEW `v{i}` AS select `a` from `t` */;\n" for i in range(n)), 600),
    ]


def _best(text: str, runs: int) -> float:
    best = 1e9
    for _ in range(runs):
        gc.collect()
        gc.disable()
        try:
            t0 = time.perf_counter()
            S.parse(text)
            best = min(best, time.perf_counter() - t0)
        finally:
            gc.enable()
    return best


TIMING: list = []


def section_f() -> None:
    print("\n-- F. linear time (1x vs 8x, min of 3, time(1x) >= 120 ms, bound 24x, <= 60 ms/KB) and the limits --")
    factor, bound, per_kb = 8, 24.0, 60.0
    for name, make, n in _shapes():
        t = 0.0
        for _ in range(12):                                   # calibrate: time(n) >= 200 ms, 8n >= 256 KB
            t = _best(make(n), 1)
            if t >= 0.2 and len(make(n * factor)) >= 256 * 1024:
                break
            n *= 2
        small = make(n)
        t1 = _best(small, 3)
        for _ in range(3):                                    # the min of 3 is what the check reads: calibrate on it
            if t1 >= 0.12:
                break
            n *= 2
            small = make(n)
            t1 = _best(small, 3)
        large = make(n * factor)
        t8 = _best(large, 1)
        tries = 1
        while t8 / t1 >= bound and tries < 4:                 # a noisy run gets three more chances
            t8 = min(t8, _best(large, 1))
            t1 = min(t1, _best(small, 2))
            tries += 1
        ratio = t8 / t1
        kb = len(large) / 1024
        TIMING.append((name, n, len(small) / 1024, t1, kb, t8, ratio, t8 * 1000 / kb))
        check(f"{name}: {len(small) // 1024} KB {t1 * 1000:.0f} ms -> {kb:.0f} KB {t8 * 1000:.0f} ms, ratio "
              f"{ratio:.1f} (< {bound:g}; {t8 * 1000 / kb:.2f} ms/KB)",
              ratio < bound and t1 >= 0.1 and t8 * 1000 / kb <= per_kb,
              f"ratio {ratio:.1f}, time(1x) {t1 * 1000:.0f} ms, {t8 * 1000 / kb:.1f} ms/KB (a linear scan with a "
              f"constant that large, like a 4,000-character window per statement, is a defect too)")

    got = decls("CREATE TABLE a (x int);\nSELECT " + "[" * 50 + "\nCREATE TABLE b (y int);\n")
    check("unclosed [ on a line: the tables before and after are still found", [q for _k, q, _l in got] == ["a", "b"],
          got)
    got = decls("SELECT 'x\nCREATE TABLE t (x int);\n")
    check("an unterminated ' outside T-SQL blanks to the end (no declaration from inside it)", got == [], got)
    got = decls("CREATE TABLE `t` (`a` int) ENGINE=InnoDB;\n/*!40101 SET x=1;\nCREATE TABLE `u` (`b` int);\n")
    check("an unclosed /*! in MySQL stays code: the later table is found", [q for _k, q, _l in got] == ["t", "u"], got)

    big = big_script(400, True)
    try:
        S.parse(big, deadline_s=0.001)
        slow = False
    except S.Slow:
        slow = True
    check("parse(..., deadline_s=0.001) on a large input raises Slow", slow)

    d = tmpdir()
    cache_dir = d / "cache"
    f = d / "slow.sql"
    f.write_text(big, encoding="utf-8", newline="")
    old = S.DEADLINE_S
    try:
        S.DEADLINE_S = 0.001
        nodes, info = S.read_file(f, "slow.sql", cache=S.Cache(cache_dir))
    finally:
        S.DEADLINE_S = old
    check("read_file: a parse past its deadline -> state slow, the :Module only", info["state"] == "slow"
          and len(nodes) == 1 and "not read: reading it took over" in nodes[0]["body_summary"], (info, nodes))
    check("a slow result is never cached", not cache_dir.exists() or not any(cache_dir.iterdir()),
          list(cache_dir.iterdir()) if cache_dir.exists() else [])

    f = d / "big.sql"
    f.write_bytes(b"CREATE TABLE x (a int);\n" * 200)
    opened = []
    real_open = open

    def rec_open(path, *a, **k):
        opened.append(str(path))
        return real_open(path, *a, **k)
    old = S.MAX_BYTES
    S.open = rec_open
    try:
        S.MAX_BYTES = 1024
        nodes, info = S.read_file(f, "big.sql")
    finally:
        S.MAX_BYTES = old
        del S.open
    check("a file over MAX_BYTES is never opened (stat only): state too_large, the :Module only",
          info["state"] == "too_large" and not opened and len(nodes) == 1
          and "over the 1 KB limit" in nodes[0]["body_summary"], (info, opened))

    files = {"a.sql": 600, "b.sql": 600, "c.sql": 10}
    for name, n in files.items():
        (d / name).write_bytes(b"-" * (n - 1) + b"\n")
    old = S.TOTAL_BYTES
    try:
        S.TOTAL_BYTES = 1000
        budget = S.Budget()
        states = [S.read_file(d / name, name, budget=budget)[1]["state"] for name in sorted(files)]
    finally:
        S.TOTAL_BYTES = old
    check("the per-sync budget: in sorted order, the file past it and every later one are budget",
          states == ["read", "budget", "budget"], states)


# ---------------------------------------------------------------------------------------------------- K
def section_k() -> None:
    print("\n-- K. the per-file cache --")
    d = tmpdir()
    src = d / "src"
    src.mkdir()
    texts = {"one.sql": TSQL, "two.sql": PG, "three.sql": ORACLE, "same_as_one.sql": TSQL}
    for name, text in texts.items():
        (src / name).write_text(text, encoding="utf-8", newline="")
    cdir = d / "sqlcache"
    cdir.mkdir()
    foreign = [cdir / "notes.txt", cdir / ("0" * 32 + "-r1.json.bak"), cdir / "README"]
    for p in foreign:
        p.write_text("keep me", encoding="utf-8")
    stale = cdir / ("f" * 32 + "-r1.json")
    stale.write_text("{}", encoding="utf-8")

    def run(cache):
        out = {}
        hits = 0
        for name in sorted(texts):
            nodes, info = S.read_file(src / name, name, cache=cache)
            out[name] = json.dumps(nodes, sort_keys=True)
            hits += bool(info.get("cache_hit"))
        return out, hits

    fresh, _ = run(None)
    c1 = S.Cache(cdir)
    first, hits1 = run(c1)
    entries = sorted(p.name for p in cdir.iterdir() if re.fullmatch(r"[0-9a-f]{32}-r\d+\.json", p.name))
    check("first run: entries written, identical files share one entry (3 contents, 1 stale)",
          len(entries) == 4 and hits1 == 1, (entries, hits1))
    check("first run nodes equal an uncached read", first == fresh)
    c2 = S.Cache(cdir)
    second, hits2 = run(c2)
    check("second run: a hit for every file, byte-equal nodes", hits2 == 4 and second == fresh, hits2)
    removed = c2.prune()
    left = sorted(p.name for p in cdir.iterdir())
    check("prune: the unused entry is removed by its exact name; other files are untouched",
          removed == 1 and not stale.exists() and all(p.exists() for p in foreign), (removed, left))
    victim = next(p for p in cdir.iterdir() if re.fullmatch(r"[0-9a-f]{32}-r\d+\.json", p.name))
    victim.write_text("{not json", encoding="utf-8")
    c3 = S.Cache(cdir)
    third, hits3 = run(c3)
    check("a corrupt entry: a fresh parse, equal nodes, the entry rewritten",
          third == fresh and hits3 == 3 and json.loads(victim.read_text(encoding="utf-8")), hits3)
    victim.write_text(json.dumps({"encoding": "utf-8", "facts": {"decls": "nope"}}), encoding="utf-8")
    fourth, hits4 = run(S.Cache(cdir))
    check("a well-formed JSON entry of the wrong shape: a fresh parse", fourth == fresh and hits4 == 3, hits4)
    victim.write_text("[" * 200000, encoding="utf-8")
    try:
        deep, hits_deep = run(S.Cache(cdir))
        deep_ok = deep == fresh and hits_deep == 3
    except Exception as exc:
        deep_ok = f"{type(exc).__name__}: {str(exc)[:80]}"
    check("an entry nested too deep for json (RecursionError): a fresh parse, never an exception", deep_ok is True,
          deep_ok)
    old = S.READER_REV
    try:
        S.READER_REV = old + 1
        c5 = S.Cache(cdir)
        fifth, hits5 = run(c5)
        check("a READER_REV change misses every entry", hits5 == 1 and fifth == fresh, hits5)
        removed = c5.prune()
        check("the previous revision's entries are pruned after a full run", removed == 3, removed)
    finally:
        S.READER_REV = old
    check("files that are not cache entries are still untouched", all(p.exists() for p in foreign))
    ro = S.Cache(d / "no" / "such" / "dir" / "file.txt" / "x")
    (d / "no" / "such" / "dir").mkdir(parents=True)
    (d / "no" / "such" / "dir" / "file.txt").write_text("x", encoding="utf-8")
    nodes, info = S.read_file(src / "one.sql", "one.sql", cache=ro)
    check("a cache that cannot be written: the file is still read", info["state"] == "read"
          and json.dumps(nodes, sort_keys=True) == fresh["one.sql"], info)


# ---------------------------------------------------------------------------------------------------- Z
def section_z() -> None:
    print("\n-- Z. determinism, JSON, fuzz --")
    for name, text in (("T-SQL", TSQL), ("PG", PG), ("Oracle", ORACLE), ("EF", EF_IDEMPOTENT),
                       ("mysqldump views", MYSQLDUMP_VIEWS_80), ("DO blocks", DRIZZLE), ("q-quotes", QQUOTE),
                       ("COPY", PG_DUMP_COPY), ("concatenation", CONCAT_TSQL)):
        a = json.dumps(S.parse(text), sort_keys=True)
        b = json.dumps(S.parse(text), sort_keys=True)
        check(f"{name}: two parses are byte-equal", a == b)
        f = S.parse(text)
        check(f"{name}: facts survive a JSON round trip unchanged", json.loads(json.dumps(f)) == f)
        n1 = S.file_nodes("x.sql", f, size=1)
        n2 = S.file_nodes("x.sql", json.loads(json.dumps(f)), size=1)
        check(f"{name}: nodes from round-tripped facts are equal", n1 == n2)
    rng = random.Random(20261101)
    atoms = ["CREATE ", "OR ALTER ", "TABLE ", "VIEW ", "PROCEDURE ", "FUNCTION ", "TRIGGER ", "PACKAGE ", "BODY ",
             "TYPE ", "AS ", "IS ", "BEGIN ", "END ", "END IF; ", "CASE ", "GO\n", "\n/\n", "[", "]", "[a]", '"', "`",
             "'", "''", "N'", "E'", "\\", "$$", "$q$", "--", "/*", "*/", "/*!50001 ", "#", "(", ")", ",", ";", ".",
             "@x", "EXEC(", "sp_executesql ", "@statement = ", "EXECUTE IMMEDIATE ", "SET @s = ", "DELIMITER $$\n",
             "REM ", "PROMPT ", "GRANT ", "FROM ", "JOIN ", "INSERT INTO ", "UPDATE ", "SET ", "DELETE FROM ",
             "REFERENCES ", "ALTER TABLE ", "dbo", "t1 ", "\u00e9", "\r\n", "\n", " ", "\t", "<n, sysname, x>",
             "COPY t FROM stdin;\n", "\\.\n", "q'[", "]'", "DO $$ ", "${s}.", "&&s..", "[sp_executesql] ",
             "VALUES (", " + @y + ", " || p || ", "# ", ") ENGINE=", "MATERIALIZED VIEW LOG ON "]
    bad = []
    for i in range(400):
        text = "".join(rng.choice(atoms) for _ in range(rng.randint(1, 120)))
        try:
            f = S.parse(text)
            S.file_nodes("f.sql", f, size=len(text))
            json.dumps(f)
        except Exception as exc:
            bad.append((i, type(exc).__name__, str(exc)[:80], text[:120]))
    check("400 seeded random SQL-token soups: parse and file_nodes never raise", not bad, bad[:2])
    bad = []
    for i in range(60):
        data = bytes(rng.randrange(256) for _ in range(rng.randint(0, 3000)))
        try:
            text, _enc = S.decode(data)
            S.parse(text)
        except Exception as exc:
            bad.append((i, type(exc).__name__, str(exc)[:80]))
    check("60 seeded random byte strings: decode + parse never raise", not bad, bad[:2])


# ---------------------------------------------------------------------------------------------------- X
def section_x() -> None:
    print("\n-- X. the extension lock --")
    check("sql_reader.EXTS is ('.sql',)", S.EXTS == (".sql",), S.EXTS)
    check("limits: 8 MB per file, 256 MB per sync, 60 s per file, READER_REV an int >= 1",
          S.MAX_BYTES == 8 * 1024 * 1024 and S.TOTAL_BYTES == 256 * 1024 * 1024 and S.DEADLINE_S == 60.0
          and isinstance(S.READER_REV, int) and S.READER_REV >= 1,
          (S.MAX_BYTES, S.TOTAL_BYTES, S.DEADLINE_S, S.READER_REV))
    import graphify_extract as G
    import verdict as V
    check("every reader suffix is in graphify_extract.SOURCE_CODE_EXTS",
          all(e in G.SOURCE_CODE_EXTS for e in S.EXTS), G.SOURCE_CODE_EXTS)
    check("every reader suffix is the census language SQL", all(V._SOURCE_EXTS.get(e) == "SQL" for e in S.EXTS))
    src = (REPO / "build" / "sql_reader.py").read_text(encoding="utf-8")
    imports = sorted(set(re.findall(r"(?m)^\s*(?:import|from)\s+([\w.]+)", src)))
    allowed = {"__future__", "bisect", "hashlib", "json", "os", "re", "time", "pathlib", "summarize"}
    check("sql_reader imports only the stdlib and summarize (no engine)", set(imports) <= allowed, imports)
    check("sql_reader reuses summarize.humanize (no copy of it)", "summarize.humanize" in src
          and "_WORD_RE" not in src, "")
    long_lines = [i + 1 for i, ln in enumerate(src.splitlines()) if len(ln) > 120]
    # LF in git; a Windows checkout (core.autocrlf) turns it into CRLF, so only a CR outside a CRLF pair fails
    check("sql_reader: no line over 120 characters, no stray CR, no em or en dash",
          not long_lines and b"\r" not in (REPO / "build" / "sql_reader.py").read_bytes().replace(b"\r\n", b"")
          and "\u2014" not in src and "\u2013" not in src, long_lines[:5])


# ======================================================================================== integration (design 5-6)
# G H I J V Q L S: graphify_extract (the file rule, the supplement, the zero-node rule, the B2 shim), sync, verdict and
# edge_resolve with the reader in place. They run under this file's temp DEEPMAP_HOME (graphify writes to
# <DEEPMAP_HOME>/gout/<key>/, the reader's cache sits in <that>/sqlcache).
_ENV_SQL = "DEEPMAP_SQL_DECLARATIONS"
_ENV_DROP = "DEEPMAP_ENGINE_DROP_EXTS"
_SUPP_KEYS = {"state", "files", "declarations", "modules", "too_large", "budget", "slow", "unreadable",
              "unread_examples", "dynamic_unread", "ignored", "filters_skipped", "engine_nodes_dropped", "cache_hits"}
_COV_SQL_KEYS = {"read", "declarations", "too_large", "budget", "slow", "unreadable", "unread_examples",
                 "dynamic_unread", "ignored"}


def _gx():
    import graphify_extract as G
    return G


def _vd():
    import verdict as V
    return V


@contextlib.contextmanager
def env_set(**values):
    """os.environ[name] = value for the block (None = unset); restored after."""
    old = {k: os.environ.get(k) for k in values}
    for k, v in values.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def sql_on(value="1"):
    return env_set(**{_ENV_SQL: value})


@contextlib.contextmanager
def patched(obj, name, value):
    had = hasattr(obj, name)
    old = getattr(obj, name, None)
    setattr(obj, name, value)
    try:
        yield
    finally:
        if had:
            setattr(obj, name, old)
        else:
            delattr(obj, name)


def write_tree(root: Path, files: dict) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            p.write_bytes(text)
        else:
            p.write_text(text, encoding="utf-8", newline="")
    return root


_XPFX = "\\\\?\\"


def long_dir(root: Path, min_len: int) -> str:
    """A directory under `root` whose plain absolute path has at least `min_len` characters, made one level at a time
    through the extended-length form (Windows only, any LongPathsEnabled); returns that plain absolute path."""
    deep, i = str(root.resolve()), 0
    Path(deep).mkdir(parents=True, exist_ok=True)
    while len(deep) < min_len:
        left = min_len - len(deep)
        deep += "\\" + (f"schema_objects_level_{i:02d}" if left > 40 else "x" * max(1, left - 1))
        if not os.path.isdir(_XPFX + deep):
            os.mkdir(_XPFX + deep)
        i += 1
    return deep


def write_long(path: str, text: str) -> str:
    """Write `text` to the plain absolute `path` through the extended-length form; returns `path`."""
    with open(_XPFX + path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


def rel_of(root: Path, path: str) -> str:
    return os.path.relpath(path, str(root.resolve())).replace("\\", "/")


def _git(root: Path, *args) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def git_repo(root: Path, force=()) -> None:
    """A git work tree of `root`: everything not ignored committed, plus `force` (tracked although ignored)."""
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    for rel in force:
        _git(root, "add", "-f", rel)
    _git(root, "commit", "-q", "--no-verify", "-m", "fixture")


def cs_node(key, name, rel, label="Class") -> dict:
    return {"key": key, "label": label, "name": name, "source_file": rel, "start_line": 1, "documented": False,
            "body_summary": f"{name}: {label.lower()}"}


def reader_nodes(root: Path, rels) -> list:
    """What DeepMap's reader makes of `rels` (sorted), read directly: the oracle for the supplement's additions."""
    out = []
    for rel in sorted(rels):
        out.extend(S.read_file(root / rel, rel)[0])
    return out


def jd(obj) -> str:
    return json.dumps(obj, sort_keys=True, default=str)


def is_sql_key(key) -> bool:
    return isinstance(key, str) and key.startswith("sql:")


def tsql_file(i: int) -> str:
    return (f"CREATE TABLE [dbo].[T{i}] (\n    [Id] INT NOT NULL PRIMARY KEY,\n    [Name] NVARCHAR(50) NULL\n)\nGO\n"
            f"CREATE PROCEDURE [dbo].[usp_Get{i}] @Id INT\nAS\nBEGIN\n"
            f"    SELECT [Name] FROM [dbo].[T{i}] WHERE [Id] = @Id\nEND\nGO\n")


# ---------------------------------------------------------------------------------------------------- G
def section_g() -> None:
    print("\n-- G. graphify_extract._sql_supplement on fake engine results (hermetic) --")
    G = _gx()
    d = tmpdir()
    with sql_on("1"):
        root = write_tree(d / "free", {"src/A.cs": "class A {}\n", "README.md": "x\n"})
        res = ([cs_node("a", "A", "src/A.cs")], [("a", "B")], [])
        out, rep = G._sql_supplement(res, root)
        check("a SQL-free tree: the SAME result object back and no report", out is res and rep is None, rep)

        root = write_tree(d / "mixed", {"db/schema.sql": TSQL, "src/Orders.cs": "class Orders {}\n"})
        cs = cs_node("src_orders_cs_orders", "Orders", "src/Orders.cs")
        eng = [{"key": "db_schema_sql", "label": "Module", "name": "schema", "source_file": "db/schema.sql",
                "start_line": 1, "documented": False, "body_summary": "schema: module"},
               {"key": "db_schema_sql_orders", "label": "Class", "name": "Orders", "source_file": "DB/Schema.SQL",
                "start_line": 9, "documented": False, "body_summary": "orders: class"}]
        with env_set(DEEPMAP_PRECISE_CALLS="1"):
            ck = G.call_key("db_schema_sql_orders")
            calls = [("src_orders_cs_orders", "Orders"), ("db_schema_sql", "x"), ("src_orders_cs_orders", ck)]
            imports = [("db_schema_sql", "foo"), ("src_orders_cs_orders", "db_schema_sql"),
                       ("src_orders_cs_orders", "System")]
            res = ([cs] + eng, calls, imports)
            out, rep = G._sql_supplement(res, root)
        want_sql = reader_nodes(root, ["db/schema.sql"])
        check("engine .sql nodes (any case) dropped, DeepMap's nodes added after the engine's",
              jd(out[0]) == jd([cs] + want_sql), [n["key"] for n in out[0]])
        check("calls FROM a dropped node and a CallKey TO one are dropped; a name call stays",
              out[1] == [("src_orders_cs_orders", "Orders")], out[1])
        check("imports from a dropped node and a resolved-import key to one are dropped; a name import stays",
              out[2] == [("src_orders_cs_orders", "System")], out[2])
        check("report: state 'read', 1 file, its declarations, 1 :Module, 2 engine nodes dropped",
              isinstance(rep, dict) and rep.get("state") == "read" and rep.get("files") == 1
              and rep.get("declarations") == len(TSQL_EXPECT) and rep.get("modules") == 1
              and rep.get("engine_nodes_dropped") == 2, rep)
        check("report carries every key of the design (5.1 step 5)", isinstance(rep, dict) and _SUPP_KEYS <= set(rep),
              sorted(_SUPP_KEYS - set(rep or {})))
        check("no SQL node takes part in a call or an import", not any(is_sql_key(x) for c in out[1] for x in c)
              and not any(is_sql_key(x) for i in out[2] for x in i))

        def boom(*a, **k):
            raise ValueError("boom")
        res = ([cs] + eng, [("src_orders_cs_orders", "Orders")], [])
        with patched(S, "read_file", boom):
            out, rep = G._sql_supplement(res, root)
        check("an exception inside the reader: state 'error' with its text, the result object unchanged",
              out is res and rep == {"state": "error", "error": "ValueError: boom"}, rep)

        root = write_tree(d / "unread", {"db/a.sql": "CREATE TABLE a (id INT);\n",
                                         "db/b.sql": "CREATE TABLE b (i INT);\n", "src/X.cs": "class X {}\n"})
        real = S.read_file

        def some_fail(path, rel, **kw):
            if rel == "db/b.sql":
                raise PermissionError(13, "denied", str(path))
            return real(path, rel, **kw)
        with patched(S, "read_file", some_fail):
            out, rep = G._sql_supplement(([cs_node("x", "X", "src/X.cs")], [], []), root)
        check("an unopenable file: counted unreadable with an example, the others still read",
              rep.get("unreadable") == 1 and (rep.get("unread_examples") or {}).get("unreadable") == ["db/b.sql"]
              and [n["key"] for n in out[0]][1:] == [n["key"] for n in reader_nodes(root, ["db/a.sql"])], rep)

        root = write_tree(d / "cached", {"db/1.sql": TSQL, "db/2.sql": PG, "db/3.sql": ORACLE,
                                         "src/C.cs": "class C {}\n"})
        base = ([cs_node("c", "C", "src/C.cs")], [], [])
        first, rep1 = G._sql_supplement(base, root)
        cdir = G.graphify_out_dir(root) / "sqlcache"
        entries = sorted(p.name for p in cdir.iterdir()) if cdir.is_dir() else []
        check("the cache lives in <graphify_out_dir(root)>/sqlcache, one entry per content",
              len([e for e in entries if re.fullmatch(r"[0-9a-f]{32}-r\d+\.json", e)]) == 3, entries)
        second, rep2 = G._sql_supplement(base, root)
        check("second run: a cache hit for every file, byte-equal nodes", rep2.get("cache_hits") == 3
              and jd(second) == jd(first) and rep1.get("cache_hits") == 0, (rep1.get("cache_hits"), rep2))
        foreign = cdir / "keep-me.txt"
        foreign.write_text("x", encoding="utf-8")
        victim = cdir / sorted(e for e in entries if e.endswith(".json"))[0]
        victim.write_text("{broken", encoding="utf-8")
        third, rep3 = G._sql_supplement(base, root)
        check("a corrupt entry: a fresh parse, equal nodes", rep3.get("cache_hits") == 2 and jd(third) == jd(first),
              rep3.get("cache_hits"))
        with patched(S, "READER_REV", S.READER_REV + 7):
            fourth, rep4 = G._sql_supplement(base, root)
            left = sorted(p.name for p in cdir.iterdir())
        check("a READER_REV change misses every entry; the old entries are removed by exact name; a foreign file "
              "is untouched", rep4.get("cache_hits") == 0 and jd(fourth) == jd(first) and foreign.exists()
              and not any(n.endswith(f"-r{S.READER_REV}.json") for n in left), left)

    with sql_on(None):
        root = write_tree(d / "off", {"db/s.sql": TSQL, "src/A.cs": "class A {}\n"})
        res = ([cs_node("a", "A", "src/A.cs")], [], [])
        out, rep = G._sql_supplement(res, root)
        check("reader OFF (unset), no engine .sql node: the SAME object, no report", out is res and rep is None, rep)
        res = ([cs_node("a", "A", "src/A.cs"), dict(cs_node("s", "s", "db/s.sql"), label="Module")], [("a", "s")], [])
        out, rep = G._sql_supplement(res, root)
        check("reader OFF with an engine .sql node: the SAME object (engine SQL nodes kept), state 'off'",
              out is res and rep == {"state": "off"}, rep)

    with sql_on("1"):
        root = write_tree(d / "limits", {"a/big.sql": TSQL * 3, "b/ok.sql": "CREATE TABLE ok (i INT);\n",
                                         "c/late.sql": "CREATE TABLE late (i INT);\n"})
        with patched(S, "MAX_BYTES", len(TSQL.encode("utf-8")) * 2), patched(S, "TOTAL_BYTES", 40):
            out, rep = G._sql_supplement(([], [], []), root)
        check("over MAX_BYTES -> too_large, past the byte budget (sorted order) -> budget, each with an example",
              rep.get("too_large") == 1 and rep.get("budget") == 1 and rep.get("files") == 3
              and (rep.get("unread_examples") or {}) == {"too_large": ["a/big.sql"], "budget": ["c/late.sql"]}, rep)
        check("...and every listed file still has its :Module", sorted(n["source_file"] for n in out[0]
                                                                       if n["label"] == "Module")
              == ["a/big.sql", "b/ok.sql", "c/late.sql"], [n["key"] for n in out[0]])
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            G._log_sql(rep)
        line = buf.getvalue()
        check("the sync log line counts the file read in full apart from the 2 read as a file node only "
              "('read 1 .sql file(s)', '2 more as a file node only'), never 'read 3'",
              "SQL: read 1 .sql file(s)" in line and "2 more as a file node only" in line and "read 3" not in line,
              line)
        check("...its opt-out says it stops DeepMap reading .sql (the engine reads it only with tree-sitter-sql), "
              "not that it leaves .sql to the engine", "to stop DeepMap reading .sql" in line
              and "tree-sitter-sql" in line and "leave .sql to the engine" not in line, line)


# ---------------------------------------------------------------------------------------------------- H
H_TREE = {
    ".gitignore": "dumps/\ntracked_ignored.sql\n",
    ".graphifyignore": "legacy/*.sql\n",
    "db/schema.sql": "CREATE TABLE s (i INT);\n",
    "db/SCHEMA2.SQL": "CREATE TABLE s2 (i INT);\n",
    "db/x/.gitignore": "*\n",
    "db/x/dump1.sql": "INSERT INTO s VALUES (1);\n",
    "dumps/d1.sql": "INSERT INTO s VALUES (2);\n",
    "dumps/d2.sql": "INSERT INTO s VALUES (3);\n",
    "legacy/old.sql": "CREATE TABLE old (i INT);\n",
    "sql/password_reset.sql": "CREATE TABLE pr (i INT);\n",
    "bin/b.sql": "CREATE TABLE b (i INT);\n",
    "obj/o.sql": "CREATE TABLE o (i INT);\n",
    "public/p.sql": "CREATE TABLE p (i INT);\n",
    "node_modules/pkg/n.sql": "CREATE TABLE n (i INT);\n",
    "db/._x.sql": b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X        ",
    "__MACOSX/db/m.sql": "CREATE TABLE m (i INT);\n",
    "build/schema.sql": "CREATE TABLE built (i INT);\n",
    "tracked_ignored.sql": "CREATE TABLE ti (i INT);\n",
    "src/App.cs": "class App {}\n",
}


def _h_listing(G, root, skipped=None):
    got = G.sql_source_files(root, skipped=skipped)
    rels = [rel for rel, _p in got.get("files") or []]
    ign = got.get("ignored") or {}
    return got, rels, {k: ign.get(k) for k in ("gitignore", "graphifyignore", "sensitive")}


def section_h() -> None:
    print("\n-- H. DeepMap's own .sql file rule (git and non-git trees; identical on every engine end) --")
    G = _gx()
    try:
        import graphify.detect  # noqa: F401
    except Exception:
        print("     (graphify is not installed: the file rule's engine helpers are absent; section skipped)")
        return
    d = tmpdir()
    empty = d / "empty.gitconfig"
    empty.write_text("", encoding="utf-8")
    with env_set(GIT_CONFIG_GLOBAL=str(empty), GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="g",
                 GIT_AUTHOR_EMAIL="g@example.invalid", GIT_COMMITTER_NAME="g", GIT_COMMITTER_EMAIL="g@example.invalid"):
        root = write_tree(d / "git", H_TREE)
        git_repo(root, force=("tracked_ignored.sql",))
        skipped = G.skipped_source_dirs(root)
        check("premise: the engine skips build/ and the tracked build/schema.sql qualifies for the re-include",
              "build/schema.sql" in (skipped.get("build") or []), skipped)
        got, rels, counts = _h_listing(G, root, skipped)
        check("git tree: read exactly db/SCHEMA2.SQL, db/schema.sql and the re-included build/schema.sql (sorted)",
              rels == ["build/schema.sql", "db/SCHEMA2.SQL", "db/schema.sql"], rels)
        check("git tree: left out by .gitignore 4 (dumps/ x2, the nested db/x/ '*', a tracked file the "
              ".gitignore names), .graphifyignore 1, the sensitive-file rule 1",
              counts == {"gitignore": 4, "graphifyignore": 1, "sensitive": 1}, got.get("ignored"))
        ex = (got.get("ignored") or {}).get("examples") or {}
        check("examples per rule (<= 3, root-relative)", ex.get("graphifyignore") == ["legacy/old.sql"]
              and ex.get("sensitive") == ["sql/password_reset.sql"] and len(ex.get("gitignore") or []) == 3
              and set(ex.get("gitignore") or []) <= {"dumps/d1.sql", "dumps/d2.sql", "db/x/dump1.sql",
                                                     "tracked_ignored.sql"}, ex)
        check("no filter skipped", got.get("filters_skipped") == [], got.get("filters_skipped"))
        got2, rels2, _c = _h_listing(G, root, None)
        check("without the re-include list build/schema.sql is not read (the census's directory rule)",
              rels2 == ["db/SCHEMA2.SQL", "db/schema.sql"], rels2)

        root = write_tree(d / "plain", H_TREE)
        got, rels, counts = _h_listing(G, root, None)
        check("non-git tree: read db/SCHEMA2.SQL and db/schema.sql (build/ is not re-included outside git)",
              rels == ["db/SCHEMA2.SQL", "db/schema.sql"], rels)
        check("non-git tree: .gitignore (every level, the nested '*' too) 4, .graphifyignore 1, sensitive 1 "
              "(both files honoured although a .graphifyignore exists: never the engine's _load_graphifyignore)",
              counts == {"gitignore": 4, "graphifyignore": 1, "sensitive": 1}, got.get("ignored"))
        import graphify.detect as det
        with patched(det, "_is_sensitive", None):
            delattr(det, "_is_sensitive")
            got3, rels3, counts3 = _h_listing(G, root, None)
        check("a missing detect._is_sensitive skips only that filter and names it",
              "sql/password_reset.sql" in rels3 and counts3.get("sensitive") == 0 and counts3.get("gitignore") == 4
              and got3.get("filters_skipped") == ["sensitive"], (rels3, counts3, got3.get("filters_skipped")))
        ver = G.graphify_version()
        print(f"     (graphify {ver}: the same lists are required on every engine end)")

        # The zero-node rule's question (graphify_extract._first_engine_source): a file the engine would have parsed
        # AND the graph would keep, under the same filters as the .sql rule. An ignored or sensitive .py beside the
        # .sql files must not throw the SQL graph away (review finding: such a repo lost its whole SQL graph).
        py = "def tool():\n    return 1\n"
        sql1 = {"db/a.sql": "CREATE TABLE a (i INT);\n"}
        check("premise: the engine's sensitive-file rule names tools/password_reset.py",
              det._is_sensitive((d / "tools" / "password_reset.py").resolve()))
        cases = [("a .graphifyignore names its directory", {".graphifyignore": "scratch/\n", "scratch/tool.py": py},
                  None),
                 ("the root .gitignore names its directory", {".gitignore": "scratch/\n", "scratch/tool.py": py}, None),
                 ("a nested .gitignore '*' holds it", {"tools/.gitignore": "*\n", "tools/tool.py": py}, None),
                 ("the engine's sensitive-file rule names it", {"tools/password_reset.py": py}, None),
                 ("it is a minified bundle (sync's vendor filter)", {"web/app.min.js": "var a=1;\n"}, None),
                 ("it is under a census-pruned directory (bin/)", {"bin/tool.py": py}, None),
                 ("control: a plain C# file", {"src/App.cs": "class App {}\n"}, "src/App.cs"),
                 ("control: the .graphifyignore names another directory",
                  {".graphifyignore": "other/\n", "scratch/tool.py": py}, "scratch/tool.py")]
        for i, (what, files, want) in enumerate(cases):
            root = write_tree(d / f"fe{i}", {**sql1, **files})
            got = G._first_engine_source(root.resolve())
            check(f"non-git tree, the only engine source {what}: {want!r}", got == want, got)
        root = write_tree(d / "fegit", {**sql1, ".gitignore": "scratch/\n", "scratch/tool.py": py,
                                        "scratch/kept.py": py})
        git_repo(root, force=("scratch/kept.py",))
        got = G._first_engine_source(root.resolve())
        check("git tree: an untracked ignored .py and a tracked .py a .gitignore names are no engine source",
              got is None, got)
        root = write_tree(d / "fegit2", {**sql1, ".gitignore": "scratch/\n", "scratch/tool.py": py,
                                         "src/App.cs": "class App {}\n"})
        git_repo(root)
        got = G._first_engine_source(root.resolve())
        check("git tree: control, a tracked C# file is", got == "src/App.cs", got)

    if os.name == "nt":
        # A .sql file past the Windows path limit (MAX_PATH): DeepMap's reader opens it through the extended-length
        # form, so its file rule must list it, in a git tree (the index lists it; a plain stat fails) and outside git
        # (a plain walk cannot list a directory past the limit). Review finding: such files vanished with no word.
        with env_set(GIT_CONFIG_GLOBAL=str(empty), GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="g",
                     GIT_AUTHOR_EMAIL="g@example.invalid", GIT_COMMITTER_NAME="g",
                     GIT_COMMITTER_EMAIL="g@example.invalid"):
            for mode in ("git", "tree"):
                root = write_tree(d / f"lp{mode}", {"db/short.sql": "CREATE TABLE s (i INT);\n"})
                mid = long_dir(root / "db", 236)
                deep = long_dir(root / "ssdt", 262)
                f_mid = write_long(mid + "\\" + "stored_procedure_with_a_long_name.sql", TSQL)
                f_deep = write_long(deep + "\\" + "deep_proc.sql", TSQL)
                want = {"db/short.sql", rel_of(root, f_mid), rel_of(root, f_deep)}
                if mode == "git":
                    _git(root, "init", "-q")
                    _git(root, "-c", "core.longpaths=true", "add", "-A")
                    _git(root, "commit", "-q", "--no-verify", "-m", "fixture")
                    want.add(rel_of(root, write_long(deep + "\\" + "untracked_proc.sql", TSQL)))
                got = G.sql_source_files(root)
                rels = {rel for rel, _p in got.get("files") or []}
                check(f"{mode} walk: every .sql past MAX_PATH is listed ({len(f_mid)} and {len(f_deep)} characters"
                      f"{', one untracked' if mode == 'git' else ''}), walk '{mode}'",
                      rels == want and got.get("walk") == mode, (sorted(r[-40:] for r in rels), got.get("walk")))
                if mode == "tree":
                    cs = write_long(mid + "\\" + "Deep_component_with_a_long_file_name.cs", "class Deep {}\n")
                    write_long(deep + "\\" + "Deeper.cs", "class Deeper {}\n")
                    got = G._first_engine_source(root.resolve())
                    want_cs = rel_of(root, cs) if _vd()._long_paths_enabled() else None
                    check("the zero-node rule never takes a file past MAX_PATH the engine cannot read (LongPathsEnabled"
                          "=0) for its source, in a listable directory or past one: the census names it as a long "
                          "path instead", got == want_cs, (got, len(cs)))


# ---------------------------------------------------------------------------------------------------- I
I_SQL = {"db/deploy.sql": TSQL, **{f"ssdt/{k}": v for k, v in SSDT.items()},
         "db/migrations/migration_dml.sql": "ALTER TABLE orders ADD COLUMN shipped_at TIMESTAMP NULL;\n"
                                            "UPDATE customers SET tier = 'gold' WHERE id = 1;\n"}
I_CS = {f"src/Shop/C{i}.cs": (f"namespace Shop {{\n  public class C{i} {{\n"
                               f"    public int M{i}() {{ return new C{(i + 1) % 3}().M{(i + 1) % 3}(); }}\n  }}\n}}\n")
        for i in range(3)}


def _engine_ok() -> bool:
    import importlib.util
    return importlib.util.find_spec("graphify") is not None


def section_i() -> None:
    print("\n-- I. the real engine through extract_with_report (and sync._rebuild_code) --")
    if not _engine_ok():
        print("     (graphify is not installed in this Python: real-engine section skipped)")
        return
    G = _gx()
    V = _vd()
    d = tmpdir()
    extra = {f"db/extra/e{i:02d}.sql": f"CREATE TABLE e{i} (id INT PRIMARY KEY);\n" for i in range(20)}
    root = write_tree(d / "t", {**I_CS, **I_SQL, **extra})
    print(f"     (graphify {G.graphify_version()}; {len(I_CS) + len(I_SQL) + len(extra)} files: the worker-pool path)")
    with sql_on(None):
        off, rep_off = G.extract_with_report(root)
    with sql_on("1"):
        on, rep_on = G.extract_with_report(root)
    check("both runs: no cause, no failed file", rep_off.get("cause") is None and rep_on.get("cause") is None
          and not rep_off.get("failed_files") and not rep_on.get("failed_files"), (rep_off.get("cause"),
                                                                                    rep_on.get("cause")))
    check("reader OFF: no SQL node of DeepMap's and (no SQL grammar here) none of the engine's; no 'sql' report",
          not any(is_sql_key(n["key"]) or str(n["source_file"]).lower().endswith(".sql") for n in off[0])
          and rep_off.get("sql") is None, rep_off.get("sql"))
    sql_on_nodes = [n for n in on[0] if is_sql_key(n["key"])]
    rels = sorted(I_SQL) + sorted(extra)
    check("reader ON: the SQL nodes are exactly what the reader makes of every .sql file (sorted rel order)",
          jd(sql_on_nodes) == jd(reader_nodes(root, rels)), [n["key"] for n in sql_on_nodes][:6])
    check("reader ON: the non-SQL nodes are byte-identical to the reader-off run (same order)",
          jd([n for n in on[0] if not is_sql_key(n["key"])]) == jd(off[0]), len(off[0]))
    check("reader ON: calls and imports byte-identical to the reader-off run", jd(on[1]) == jd(off[1])
          and jd(on[2]) == jd(off[2]), (len(on[1]), len(off[1])))
    check("no SQL key in any call or import", not any(is_sql_key(x) for c in on[1] for x in c)
          and not any(is_sql_key(x) for i in on[2] for x in i))
    sr = rep_on.get("sql") or {}
    check("reader ON report: state read, every file, the declarations", sr.get("state") == "read"
          and sr.get("files") == len(rels) and sr.get("modules") == len(rels)
          and sr.get("declarations") == len(TSQL_EXPECT) + 3 + 20, sr)
    check("SQL node keys follow the design ('sql:<rel>', 'sql:<rel>#<kind>:<qualified lower>')",
          all(re.fullmatch(r"sql:[^#]+(#[a-z]+:.+)?", n["key"]) for n in sql_on_nodes)
          and "sql:db/deploy.sql#table:dbo.customers" in {n["key"] for n in sql_on_nodes})

    only = write_tree(d / "sqlonly", {"db/a.sql": TSQL, "db/b.sql": PG})
    with sql_on("1"):
        res, rep = G.extract_with_report(only)
    check("a SQL-only tree, reader ON: a graph (every node DeepMap's), cause None",
          res is not None and res[0] and all(is_sql_key(n["key"]) for n in res[0]) and rep.get("cause") is None,
          rep.get("cause"))

    mixed = write_tree(d / "sqlr", {"db/a.sql": TSQL, **{f"stats/s{i}.R": f"f{i} <- function(x) x + {i}\n"
                                                           for i in range(6)},
                                    "tools/t1.pl": "sub t1 { 1 }\n", "tools/t2.pl": "sub t2 { 2 }\n"})
    with sql_on("1"):
        res, rep = G.extract_with_report(mixed)
        nodes = (res or ([],))[0]
        check(".sql + 6 .R + 2 .pl, reader ON: SQL nodes, cause None (R / Perl cannot make engine nodes: no "
              "fall-back to ast)", bool(nodes) and rep.get("cause") is None, rep.get("cause"))
        census = V.extension_census(mixed)
        cov = V.build_coverage(mixed, nodes, census=census, engine_keys=[n["key"] for n in nodes],
                               extraction={"sql": rep.get("sql")})
        v, reasons = V.classify(neo4j_ok=True, n_nodes=len(nodes), n_embedded=len(nodes), census=census,
                                extractor="graphify", ml_ok=True, mcp_ok=True, coverage=cov,
                                extraction={"requested": "auto", "sql": rep.get("sql")})
        lines = V.format_coverage(cov)
        check("...READY with a not-parsed line for R and one for Perl", v == V.READY and not reasons
              and any(ln.startswith("R: 6 .r file(s) not in the graph") for ln in lines)
              and any(ln.startswith("Perl: 2 .pl file(s) not in the graph") for ln in lines), (v, reasons, lines))
        import sync
        ex = sync._rebuild_code(mixed)
        registered = V._ENGINE_KEYS.get(V._root_key(mixed)) or frozenset()
        sql_keys = {n["key"] for n in ex[0] if is_sql_key(n["key"])}
        check("sync._rebuild_code on it: engine graphify, .info['sql'] state read, the SQL nodes in its nodes and "
              "registered as engine output (verdict.note_engine_nodes)", ex[3] == "graphify"
              and (ex.info.get("sql") or {}).get("state") == "read" and sql_keys and sql_keys <= set(registered),
              (ex[3], ex.info.get("sql"), sorted(sql_keys - set(registered))[:3]))

    py = write_tree(d / "pyfail", {"app/a.py": "def a():\n    return 1\n", "app/b.py": "def b():\n    return a()\n",
                                   "db/s1.sql": TSQL, "db/s2.sql": PG})

    def nothing(cmd, *, cwd, env, timeout):
        out = Path(env["GRAPHIFY_OUT"])
        out.mkdir(parents=True, exist_ok=True)
        (out / "graph.json").write_text(json.dumps({"nodes": [], "links": []}), encoding="utf-8")
        return G._EngineResult(cmd, 0, "", "")
    with sql_on("1"), patched(G, "_run_engine", nothing):
        res, rep = G.extract_with_report(py)
        check("the engine gives nothing for a tree with .py (reader ON): cause set and no node returned, so auto "
              "falls back", rep.get("cause") is not None and not (res or ([],))[0], (rep.get("cause"), res))
        import sync
        ex = sync._rebuild_code(py)
        check("...sync._rebuild_code falls back to ast (no 'sql' in .info: the ast path reads no SQL)",
              ex[3] == "ast" and "sql" not in ex.info, (ex[3], ex.info.get("sql")))

    # Review finding: one ignored or sensitive .py beside the .sql files made the zero-node rule throw the whole SQL
    # graph away (auto fell back to ast, which reads no SQL). The engine never saw that file: the SQL graph stands.
    sql6 = {f"schema/t{i}.sql": tsql_file(i) for i in range(6)}
    tool = "def tool():\n    return 1\n"
    kept = [("a .graphifyignore", {".graphifyignore": "scratch/\n", "scratch/tool.py": tool}, False),
            ("a .gitignore", {".gitignore": "scratch/\n", "scratch/tool.py": tool}, False),
            ("the engine's sensitive-file rule", {"tools/password_reset.py": tool}, False),
            ("a .gitignore, in a git work tree", {".gitignore": "scratch/\n", "scratch/tool.py": tool}, True)]
    empty = d / "empty.gitconfig"
    empty.write_text("", encoding="utf-8")
    with sql_on("1"), env_set(GIT_CONFIG_GLOBAL=str(empty), GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="g",
                              GIT_AUTHOR_EMAIL="g@example.invalid", GIT_COMMITTER_NAME="g",
                              GIT_COMMITTER_EMAIL="g@example.invalid"):
        for i, (what, files, in_git) in enumerate(kept):
            root = write_tree(d / f"ign{i}", {**sql6, **files})
            if in_git:
                git_repo(root)
            res, rep = G.extract_with_report(root)
            nodes = (res or ([],))[0]
            check(f"6 .sql + a .py left out by {what}: the SQL graph stands (cause None, every SQL node the reader's)",
                  rep.get("cause") is None and jd(nodes) == jd(reader_nodes(root, sorted(sql6))),
                  (rep.get("cause"), len(nodes)))
            if i == 0:
                import sync
                ex = sync._rebuild_code(root)
                check("...sync._rebuild_code keeps graphify's graph: engine graphify, .info['sql'] state read, 18 SQL "
                      "nodes", ex[3] == "graphify" and (ex.info.get("sql") or {}).get("state") == "read"
                      and len(ex[0]) == 18 and not ex.info.get("fallback_reason"),
                      (ex[3], len(ex[0]), ex.info.get("fallback_reason")))
        # The inverse: source the engine should have parsed and did not still fails (auto falls back), in a git work
        # tree (a tracked, not ignored .cs file) and beside a .graphifyignore that names another directory.
        inverse = [("a tracked .cs file in a git work tree",
                    {".gitignore": "scratch/\n", "src/App.cs": "class App {}\n"}, True),
                   ("a .py file a .graphifyignore does not name", {".graphifyignore": "other/\n",
                                                                   "scratch/tool.py": tool}, False)]
        for i, (what, files, in_git) in enumerate(inverse):
            root = write_tree(d / f"inv{i}", {**sql6, **files})
            if in_git:
                git_repo(root)
            with patched(G, "_run_engine", nothing):
                res, rep = G.extract_with_report(root)
            check(f"the engine gives nothing for {what}: cause set, no node returned (auto falls back)",
                  rep.get("cause") is not None and "0 code nodes" in str(rep.get("cause"))
                  and not (res or ([],))[0], (rep.get("cause"), res and len(res[0])))


# ---------------------------------------------------------------------------------------------------- J
def section_j() -> None:
    print("\n-- J. edge isolation: no name, no typed edge and no import ever binds a SQL node --")
    import edge_resolve as ER
    import framework_edges as FE
    import sync
    cs = [cs_node("cs_orders", "Orders", "src/Orders.cs"),
          cs_node("cs_place", "OrderService.Place", "src/OrderService.cs", label="Method"),
          cs_node("cs_svc", "OrderService", "src/OrderService.cs")]
    sqln = S.file_nodes("db/schema.sql", S.parse(TSQL), size=len(TSQL))
    both = cs + sqln
    idx = ER.build_name_index(both)
    check("premise: the SQL nodes hold a table named Orders and a :Module", any(n["name"] == "Orders" for n in sqln)
          and any(n["label"] == "Module" for n in sqln))
    check("build_name_index holds no SQL key", not any(is_sql_key(k) for ks in idx.values() for k in ks), idx)
    calls = [("cs_place", "Orders"), ("cs_place", "Customers"), ("cs_place", "usp_Legacy")]
    with_sql = sync._resolve_calls(both, calls)
    without = sync._resolve_calls(cs, calls)
    check("push-side :CALLS: a C# Orders() binds the C# class exactly as without the SQL nodes; SQL-only names bind "
          "nothing", with_sql == without and list(with_sql) == [("cs_place", "cs_orders")], (with_sql, without))
    res = ER.Resolver(both)
    check("typed-edge Resolver: targets('Orders') is the C# class only; source_keys never a SQL key",
          res.targets("Orders") == ["cs_orders"] and not any(is_sql_key(k) for k in res.source_keys("Orders",
                                                                                                    "db/schema.sql")),
          (res.targets("Orders"), res.source_keys("Orders", "db/schema.sql")))
    edges = [FE.TypedEdge("OrderService", "src/OrderService.cs", "Orders", "DEPENDS_ON", "NAME", {}),
             FE.TypedEdge("OrderService", "src/OrderService.cs", "Customers", "REFERENCES", "NAME", {}),
             FE.TypedEdge("OrderService", "src/OrderService.cs", "dbo.Customers", "HAS_MANY", "NAME", {})]
    rows = ER.resolve_typed_edges(edges, res, [n["key"] for n in both])
    check("resolve_typed_edges never returns a SQL key: Orders binds the C# class only, Customers (a SQL-only "
          "table) binds nothing", [r[:3] for r in rows] == [("DEPENDS_ON", "cs_svc", "cs_orders")], rows)
    imps = ER.resolve_imports([("cs_place", "Orders"), ("cs_place", "schema")], res, [n["key"] for n in both],
                              {n["key"] for n in both})
    check("resolve_imports never binds a SQL node by name (a table, the file's stem)",
          not any(is_sql_key(x) for r in imps for x in (r if isinstance(r, (tuple, list)) else [r])), imps)


# ---------------------------------------------------------------------------------------------------- V
def _cov_verdict(V, root, nodes, rep, *, extractor="graphify", extra_extraction=None):
    census = V.extension_census(root)
    extraction = {"sql": rep} if rep is not None else {}
    cov = V.build_coverage(root, nodes, census=census, engine_keys=[n["key"] for n in nodes], extraction=extraction)
    v, reasons = V.classify(neo4j_ok=True, n_nodes=len(nodes), n_embedded=len(nodes), census=census,
                            extractor=extractor, ml_ok=True, mcp_ok=True, coverage=cov,
                            extraction=dict(extraction, requested="auto", **(extra_extraction or {})))
    return census, cov, v, reasons


def section_v() -> None:
    print("\n-- V. the verdict: SQL judged by files read, causes named, the honest lines --")
    G = _gx()
    V = _vd()
    d = tmpdir()
    cs_files = {f"src/Shop/C{i}.cs": f"class C{i} {{}}\n" for i in range(20)}
    cs = [cs_node(f"cs{i}", f"C{i}", f"src/Shop/C{i}.cs") for i in range(20)]
    with sql_on("1"):
        root = write_tree(d / "field", {**cs_files, **{f"db/u{i:02d}.sql": tsql_file(i) for i in range(12)}})
        out, rep = G._sql_supplement((list(cs), [], []), root)
        census, cov, v, reasons = _cov_verdict(V, root, out[0], rep)
        check("THE FIELD CASE, reader ON: census SQL 12, READY, no SQL reason", census.get("SQL") == 12
              and v == V.READY and not reasons, (census, v, reasons))
        lang = (cov.get("languages") or {}).get("SQL") or {}
        check("coverage languages.SQL = 12 files, 12 :Modules + 24 declarations", lang.get("files") == 12
              and lang.get("nodes") == 36, lang)
        sql = cov.get("sql") or {}
        check("coverage.sql: read 12, declarations 24, exactly the design's keys",
              set(sql) == _COV_SQL_KEYS and sql.get("read") == 12 and sql.get("declarations") == 24, sql)
        check("no not_parsed entry for .sql (the reader reads it)", ".sql" not in (cov.get("not_parsed") or {}),
              cov.get("not_parsed"))
        check("the coverage dict is JSON-serialisable (the manifest persists it)", bool(json.dumps(cov)))

        migrations = {f"migrations/V{i:02d}.sql": (f"ALTER TABLE orders ADD COLUMN c{i} INT;\n"
                                                   f"UPDATE orders SET c{i} = 0;\n") for i in range(12)}
        root = write_tree(d / "alter", {**cs_files, **migrations})
        out, rep = G._sql_supplement((list(cs), [], []), root)
        census, cov, v, reasons = _cov_verdict(V, root, out[0], rep)
        check("12 ALTER / DML-only migrations: READY (every read file is covered; no content probe)",
              v == V.READY and not reasons and (cov.get("sql") or {}).get("declarations") == 0, (v, reasons))

        empty = d / "empty.gitconfig"
        empty.write_text("", encoding="utf-8")
        with env_set(GIT_CONFIG_GLOBAL=str(empty), GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="g",
                     GIT_AUTHOR_EMAIL="g@example.invalid", GIT_COMMITTER_NAME="g",
                     GIT_COMMITTER_EMAIL="g@example.invalid"):
            dumps = {f"dumps/d{i:02d}.sql": "INSERT INTO t VALUES (1);\n" for i in range(30)}
            root = write_tree(d / "dumps", {**cs_files, ".gitignore": "dumps/\n", "db/schema.sql": tsql_file(0),
                                            **dumps})
            git_repo(root)
            out, rep = G._sql_supplement((list(cs), [], []), root)
            census, cov, v, reasons = _cov_verdict(V, root, out[0], rep)
        check("1 schema.sql + 30 gitignored dumps: the census counts 31, coverage 31 - 30 = 1 file, READY",
              census.get("SQL") == 31 and ((cov.get("languages") or {}).get("SQL") or {}).get("files") == 1
              and v == V.READY and ((cov.get("sql") or {}).get("ignored") or {}).get("gitignore") == 30,
              (census, cov.get("languages"), v, reasons))

        root = write_tree(d / "locked", {**cs_files, **{f"db/u{i:02d}.sql": tsql_file(i) for i in range(12)}})

        def locked(path, rel, **kw):
            raise PermissionError(13, "denied", str(path))
        with patched(S, "read_file", locked):
            out, rep = G._sql_supplement((list(cs), [], []), root)
        census, cov, v, reasons = _cov_verdict(V, root, out[0], rep)
        lines = V.format_coverage(cov)
        check("12 .sql files the reader could not open: no SQL density reason (SQL is judged by the files read), "
              "the line 'SQL: 12 .sql file(s) could not be opened (e.g. db/u00.sql)' names them",
              not [r for r in reasons if "for SQL" in r] and (cov.get("sql") or {}).get("unreadable") == 12
              and "SQL: 12 .sql file(s) could not be opened (e.g. db/u00.sql)" in lines, (reasons, lines))

        root = write_tree(d / "err", {**cs_files, **{f"db/u{i:02d}.sql": tsql_file(i) for i in range(12)}})
        census, cov, v, reasons = _cov_verdict(V, root, list(cs), {"state": "error", "error": "ValueError: boom"})
        low = [r for r in reasons if r.startswith("LOW COVERAGE for SQL")]
        check("state 'error': 'LOW COVERAGE for SQL ...' whose tail names the reader's error and the refresh, not "
              "'check the extractor lines'", bool(low) and "DeepMap's SQL reader failed (ValueError: boom)" in low[0]
              and "/deep-map:refresh" in low[0] and "check the extractor lines" not in low[0], reasons)

        root = write_tree(d / "lines", {**cs_files, "db/big.sql": TSQL * 2, "db/password_history.sql": tsql_file(1),
                                        "db/dyn.sql": CONCAT_TSQL, "db/ok.sql": tsql_file(2)})
        with patched(S, "MAX_BYTES", len(TSQL.encode("utf-8")) + 10):
            out, rep = G._sql_supplement((list(cs), [], []), root)
            census, cov, v, reasons = _cov_verdict(V, root, out[0], rep)
            lines = V.format_coverage(cov)
        n_dyn = S.parse(CONCAT_TSQL)["dynamic_unread"]
        check("too large: 'SQL: 1 .sql file(s) are file nodes only (not read: 1 over ...); e.g. db/big.sql'",
              any(ln.startswith("SQL: 1 .sql file(s) are file nodes only (not read: 1 over ")
                  and ln.endswith("e.g. db/big.sql") for ln in lines), lines)
        check("sensitive: 'SQL: 1 .sql file(s) left out by the engine's sensitive-file rule (e.g. "
              "db/password_history.sql)'", "SQL: 1 .sql file(s) left out by the engine's sensitive-file rule "
              "(e.g. db/password_history.sql)" in lines, lines)
        check(f"dynamic SQL: 'SQL: {n_dyn} object(s) created by dynamic SQL DeepMap does not read'",
              n_dyn > 0 and f"SQL: {n_dyn} object(s) created by dynamic SQL DeepMap does not read" in lines, lines)
        check("the census minus the sensitive file: SQL 3 files", ((cov.get("languages") or {}).get("SQL") or {})
              .get("files") == 3, cov.get("languages"))
        check("no line has an em / en dash", all("\u2014" not in ln and "\u2013" not in ln for ln in lines), lines)

        root = write_tree(d / "ast", {**cs_files, **{f"db/u{i:02d}.sql": tsql_file(i) for i in range(12)},
                                      "app/main.py": "def main():\n    return 1\n"})
        pyn = [cs_node("m", "main", "app/main.py", label="Function")]
        census, cov, v, reasons = _cov_verdict(V, root, pyn, None, extractor="ast",
                                               extra_extraction={"fallback_reason": "graphify failed (boom)"})
        check("ast fallback (no 'sql' report): today's fall-back reason names the non-Python files, no coverage.sql",
              any("fell back automatically" in r and "SQL" in r for r in reasons) and "sql" not in cov,
              (reasons, sorted(cov)))

        if os.name == "nt":
            # Long paths (review finding): a .sql file past MAX_PATH the reader read is no long-path finding (it is in
            # the graph), but one it did not list stays one, and so does a .cs file beside it.
            root = write_tree(d / "lp", {"src/A.cs": "class A {}\n", "db/s.sql": TSQL})
            deep = long_dir(root / "ssdt", 262)
            f_sql = write_long(deep + "\\" + "deep_proc.sql", TSQL)
            f_cs = write_long(deep + "\\" + "Deep.cs", "class Deep {}\n")
            out, rep = G._sql_supplement(([cs_node("a", "A", "src/A.cs")], [], []), root)
            with patched(V, "_long_paths_enabled", lambda: False):
                census = V.extension_census(root)
                probs = V._CENSUS_PROBLEMS.get(V._root_key(root))
                if isinstance(probs, dict):
                    probs.setdefault("files", []).append(str(root.resolve() / "db" / "ghost.sql"))
                cov = V.build_coverage(root, out[0], census=census, extraction={"sql": rep})
            lp = cov.get("long_paths") or {}
            check("long paths: the reader read the .sql file past MAX_PATH (coverage.sql read 2)",
                  (cov.get("sql") or {}).get("read") == 2, cov.get("sql"))
            check("...it is no long-path finding; the .cs beside it and a .sql the reader never listed still are",
                  lp.get("files") == 2 and rel_of(root, f_cs) in (lp.get("examples") or [])
                  and "db/ghost.sql" in (lp.get("examples") or []) and rel_of(root, f_sql) not in (lp.get("examples")
                                                                                                 or []), lp)


# ---------------------------------------------------------------------------------------------------- Q
class _QResult(list):
    def single(self):
        return self[0] if self else None


class _QSess:
    """Answers sync's one graph count read; every other statement (the meta writes) gets an empty result."""

    def __init__(self, drv):
        self.drv = drv

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **p):
        if "RETURN count(n) AS c" in cypher:
            return _QResult([{"c": len(self.drv.keys)}])
        return _QResult()


class _QDrv:
    def __init__(self):
        self.keys: list = []

    def session(self):
        return _QSess(self)

    def verify_connectivity(self):
        return None

    def close(self):
        return None


def _q_sync(sync, root: Path, extraction) -> tuple:
    """One real sync.sync() over `root` with the extraction stubbed to `extraction` (an _Extraction) and the Neo4j
    writes stubbed; the verdict, its coverage and the manifest write are real. Returns (ok, output)."""
    drv = _QDrv()
    ident: dict = {}
    real_identity = sync._project_identity

    def _identity(r):
        ident["v"] = real_identity(r)
        return ident["v"]

    def _push(dv, n, c, i, t=None):
        drv.keys.extend(x["key"] for x in n if x["key"] not in drv.keys)
        return (len(n), 0)

    attrs = dict(
        _rebuild_code=lambda r: extraction, _apply_recognizers=lambda r, n: (n, []), _project_identity=_identity,
        _read_owning_root=lambda dv: ident.get("v"), _graph_has_nodes=lambda dv: bool(drv.keys),
        _graph_holds_nodes=lambda dv: bool(drv.keys), _write_owning_root=lambda dv, i: None,
        _record_base_store_owner=lambda *a, **k: None, _remove_stale_short_names=lambda dv: 0,
        _fts_rebuild_pending=lambda dv: False, _fts_changed_docs=lambda dv, n: 0, _mark_fts_rebuild=lambda dv, p: None,
        push_to_neo4j=_push, _reuse_rekeyed_embeddings=lambda dv, n: 0, _rebuild_code_fts=lambda dv: True,
        _count_embedded_nodes=lambda dv, fallback=0: len(drv.keys), _maybe_auto_tune=lambda r: None,
        _unkey_receiver_calls=lambda r, n, c: (c, 0), _attach_bare_calls=lambda r, n, c: 0,
        _attach_local_defs=lambda r, n, c: 0, _attach_local_def_calls=lambda r, n, c: 0,
        indexes=SimpleNamespace(create_all_indexes=lambda dv: {}, wait_for_indexes=lambda dv, timeout=120: True,
                                VECTOR_INDEXES=(), vector_quantization_disabled=lambda: False),
        embeddings=SimpleNamespace(generate_embeddings=lambda dv: 0))
    saved = {k: getattr(sync, k) for k in attrs}
    buf = io.StringIO()
    try:
        for k, v in attrs.items():
            setattr(sync, k, v)
        with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
            ok = sync.sync(str(root), driver=drv)
    finally:
        for k, v in saved.items():
            setattr(sync, k, v)
    return ok, buf.getvalue()


def section_q() -> None:
    print("\n-- Q. sync's own verdict wiring: the reader's report reaches build_coverage and the manifest --")
    import sync
    G = _gx()
    d = tmpdir()
    cs_files = {f"src/Shop/C{i}.cs": f"class C{i} {{}}\n" for i in range(20)}
    cs = [cs_node(f"src_shop_c{i}", f"C{i}", f"src/Shop/C{i}.cs") for i in range(20)]
    dumps = {f"dumps/d{i:02d}.sql": "INSERT INTO t VALUES (1);\n" for i in range(30)}
    with sql_on("1"):
        root = write_tree(d / "wired", {**cs_files, ".gitignore": "dumps/\n", ".deepmap-root": "",
                                        **{f"db/u{i:02d}.sql": tsql_file(i) for i in range(12)}, **dumps})

        def locked(path, rel, **kw):
            raise PermissionError(13, "denied", str(path))
        with patched(S, "read_file", locked):
            out, rep = G._sql_supplement((list(cs), [], []), root)
        ex = sync._Extraction(out[0], [], [], "graphify", {"requested": "auto", "resolved": "graphify", "sql": rep})
        ok, log = _q_sync(sync, root, ex)
    try:
        man = json.loads((root / ".deepmap" / "graph_build.json").read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        man = {"error": f"{type(e).__name__}: {e}"}
    cov = man.get("coverage") or {}
    lang = (cov.get("languages") or {}).get("SQL") or {}
    reasons = [str(r) for r in man.get("reasons") or []]
    check("premise: the stubbed sync ran to its manifest (ok, a coverage block)", ok is not False and bool(cov),
          (ok, sorted(man), log[-600:]))
    check("the manifest's coverage carries 'sql' (the reader's report reached build_coverage through sync)",
          (cov.get("sql") or {}).get("unreadable") == 12
          and ((cov.get("sql") or {}).get("ignored") or {}).get("gitignore") == 30, cov.get("sql"))
    check("...the 30 .sql files a .gitignore left out are subtracted from the census (42 -> 12)",
          lang.get("files") == 12, lang)
    check("...and no SQL density reason fires (12 unopenable files, 0 SQL nodes: judged by files read)",
          not [r for r in reasons if "for SQL" in r], reasons)


# ---------------------------------------------------------------------------------------------------- L
def section_l() -> None:
    print("\n-- L. the switch: DEEPMAP_SQL_DECLARATIONS (default OFF until G-SQL passes) --")
    G = _gx()
    check("graphify_extract.SQL_DECLARATIONS_DEFAULT is False (G-SQL has not passed)",
          G.SQL_DECLARATIONS_DEFAULT is False, G.SQL_DECLARATIONS_DEFAULT)
    import sync_status as SS
    on, off = set(getattr(SS, "_DEFAULT_ON_KNOBS", ())), set(getattr(SS, "_DEFAULT_OFF_KNOBS", ()))
    check("sync_status lists DEEPMAP_SQL_DECLARATIONS in exactly the set its default says (the pair moves together)",
          (_ENV_SQL in on) == bool(G.SQL_DECLARATIONS_DEFAULT)
          and (_ENV_SQL in off) == (not G.SQL_DECLARATIONS_DEFAULT),
          (sorted(on & {_ENV_SQL}), sorted(off & {_ENV_SQL})))
    check("DEEPMAP_ENGINE_DROP_EXTS is internal (recomputed per run, never recorded)",
          _ENV_DROP in getattr(SS, "INTERNAL_ENV", ()), sorted(getattr(SS, "INTERNAL_ENV", ())))
    rows = []
    for raw, want in ((None, False), ("", False), ("  ", False), ("0", False), ("false", False), ("No", False),
                      ("OFF", False), ("1", True), ("true", True), ("Yes", True), (" on ", True), ("maybe", False)):
        with sql_on(raw):
            rows.append((raw, G.sql_reader_enabled(), want))
    check("sql_reader_enabled(): unset / blank = the default (off); OFF words off, ON words on (any case); other text "
          "off (sync_status's reading of a default-OFF knob)", all(g == w for _r, g, w in rows),
          [r for r in rows if r[1] != r[2]])
    with patched(G, "SQL_DECLARATIONS_DEFAULT", True):
        rows2 = []
        for raw, want in ((None, True), ("0", False), ("off", False), ("maybe", True), ("1", True)):
            with sql_on(raw):
                rows2.append((raw, G.sql_reader_enabled(), want))
    check("...with the default flipped ON: unset on, OFF words off, other text on", all(g == w for _r, g, w in rows2),
          [r for r in rows2 if r[1] != r[2]])
    canon = SS.canonical_env_value(_ENV_SQL, "0")
    env: dict = {}
    SS.replay_env({_ENV_SQL: canon}, env)
    with sql_on(env.get(_ENV_SQL)):
        replayed = G.sql_reader_enabled()
    check("an explicit 0 is recorded as 'off' and replays as off", canon == "off" and env.get(_ENV_SQL) == "off"
          and replayed is False, (canon, env))
    d = tmpdir()
    root = write_tree(d / "words", {"db/s.sql": TSQL})
    res = ([dict(cs_node("e", "s", "db/s.sql"), label="Module")], [("e", "x")], [])
    bad = []
    for word in ("0", "false", "No", "OFF"):
        with sql_on(word):
            out, rep = G._sql_supplement(res, root)
        if out is not res or rep != {"state": "off"}:
            bad.append((word, rep))
    check("every OFF word: the pre-change mapping byte for byte (the same object, engine SQL nodes kept), state off",
          not bad, bad)


# ---------------------------------------------------------------------------------------------------- S (B2 shim)
_FAKE_ENGINE = '''_DISPATCH = {".sql": "SQL", ".py": "PY", ".cs": "CS", ".F90": "F"}
'''


def _shim_dispatch(fake_root: Path, env_extra: dict) -> list:
    shim = str(REPO / "build" / "graphify_case_shim.py")
    code = ("import importlib.util, json, sys\n"
            f"sys.path.insert(0, {str(fake_root)!r})\n"
            f"spec = importlib.util.spec_from_file_location('dm_shim', {shim!r})\n"
            "m = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(m)\n"
            "import graphify.extract as e\nprint(json.dumps(sorted(getattr(e, '_DISPATCH', {}))))\n")
    env = {k: v for k, v in os.environ.items() if k not in (_ENV_DROP, "DEEPMAP_CASE_ALIASES", "DEEPMAP_RESCUE_DIRS",
                                                              "DEEPMAP_SKIP_MACOS_METADATA")}
    env.update(env_extra)
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return [f"rc={p.returncode}", p.stderr[-300:]]


def section_s() -> None:
    print("\n-- S. Phase B2: the case shim leaves .sql to DeepMap's reader (design 5.7) --")
    G = _gx()
    d = tmpdir()
    fake = d / "fake"
    write_tree(fake, {"graphify/__init__.py": "", "graphify/extract.py": _FAKE_ENGINE})
    got = _shim_dispatch(fake, {_ENV_DROP: ".sql", "DEEPMAP_CASE_ALIASES": ".SQL,.PY"})
    check("import time: .sql removed from the engine's table BEFORE the odd-case aliasing (.SQL not aliased, .PY is)",
          got == [".F90", ".PY", ".cs", ".py"], got)
    got = _shim_dispatch(fake, {_ENV_DROP: '[".sql", ".cs"]'})
    check("a JSON list works too", got == [".F90", ".py"], got)
    got = _shim_dispatch(fake, {"DEEPMAP_CASE_ALIASES": ".SQL"})
    check("control: without the variable the table keeps .sql (and .SQL is aliased onto it)",
          got == [".F90", ".SQL", ".cs", ".py", ".sql"], got)
    write_tree(fake, {"graphify/extract.py": "X = 1\n"})
    got = _shim_dispatch(fake, {_ENV_DROP: ".sql"})
    check("an engine with no _DISPATCH: no exception, nothing patched", got == [], got)

    import importlib.util
    real_find = importlib.util.find_spec
    calls: list = []

    def recorder(cmd, *, cwd, env, timeout):
        calls.append((list(cmd), dict(env)))
        out = Path(env["GRAPHIFY_OUT"])
        out.mkdir(parents=True, exist_ok=True)
        node = {"id": "a_py", "label": "a.py", "file_type": "code", "source_file": "a.py", "source_location": "L1"}
        (out / "graph.json").write_text(json.dumps({"nodes": [node], "links": []}), encoding="utf-8")
        return G._EngineResult(cmd, 0, "", "")

    def finder(found):
        def fake_find(name, package=None):
            if name == "tree_sitter_sql":
                return real_find("json") if found else None
            return real_find(name, package)
        return fake_find

    root = write_tree(d / "t", {"a.py": "def a():\n    return 1\n", "db/s.sql": TSQL})
    bare = write_tree(d / "nosql", {"a.py": "def a():\n    return 1\n"})
    plain = lambda r: [sys.executable, "-m", "graphify", "update", str(Path(r).resolve()), "--no-cluster"]  # noqa
    cases = [("reader ON, .sql present, tree_sitter_sql importable", "1", True, root, True),
             ("reader OFF", None, True, root, False),
             ("reader ON, tree_sitter_sql absent", "1", False, root, False),
             ("reader ON, no .sql file", "1", True, bare, False)]
    for what, val, found, tree, engaged in cases:
        calls.clear()
        with sql_on(val), env_set(**{_ENV_DROP: ".py"}), patched(G, "_run_engine", recorder), \
                patched(importlib.util, "find_spec", finder(found)):
            G.extract_with_report(tree)
        cmd, env = calls[0] if calls else ([], {})
        if engaged:
            check(f"{what}: the shim runs the engine, with {_ENV_DROP}=.sql",
                  cmd[1:2] == [str(REPO / "build" / "graphify_case_shim.py")] and env.get(_ENV_DROP) == ".sql",
                  (cmd, env.get(_ENV_DROP)))
        else:
            check(f"{what}: the byte-identical `-m graphify` command, and no {_ENV_DROP} reaches the engine",
                  cmd == plain(tree) and _ENV_DROP not in env, (cmd, env.get(_ENV_DROP)))


SECTIONS = [("A", section_a), ("B", section_b), ("C", section_c), ("N", section_n), ("T", section_t),
            ("D", section_d), ("E", section_e), ("F", section_f), ("K", section_k), ("Z", section_z),
            ("X", section_x), ("G", section_g), ("H", section_h), ("I", section_i), ("J", section_j),
            ("V", section_v), ("Q", section_q), ("L", section_l), ("S", section_s)]


def main() -> int:
    print("=" * 100)
    print("SQL READER GATES: DeepMap's own .sql declaration reader (no Neo4j; G-S run the engine when installed)")
    print("=" * 100)
    if S is None:
        for name, _fn in SECTIONS:
            check(f"section {name}: build/sql_reader.py imports", False, _IMPORT_ERROR)
    else:
        only = set(os.environ.get("SQL_READER_GATES_ONLY", "").upper().split(",")) - {""}
        for name, fn in SECTIONS:
            if only and name not in only:
                continue
            try:
                fn()
            except Exception:
                check(f"section {name} ran to the end", False, traceback.format_exc(limit=4))
    for d in _TMP:                      # this run's own mkdtemp dirs, by exact path (\\?\ reaches the deep one)
        shutil.rmtree("\\\\?\\" + str(d.resolve()) if os.name == "nt" else d, ignore_errors=True)
    if TIMING:
        print("\nTiming (name, n, KB 1x, s 1x, KB 8x, s 8x, ratio, ms/KB 8x):")
        for row in TIMING:
            print("  {:<46s} n={:<7d} {:8.0f} KB {:7.3f} s | {:8.0f} KB {:7.3f} s | {:5.1f} | {:.2f}".format(*row))
    print("\n" + "=" * 100)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if not _FAIL else 'RED'}: {_PASS}/{total} checks passed.")
    return 0 if not _FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
