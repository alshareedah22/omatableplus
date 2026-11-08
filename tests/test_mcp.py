import os
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

from mcpkit import Client, Fixture

from omatableplus import omadb, safety
from omatableplus.mcp import LATEST, SUPPORTED, McpServer
from omatableplus.mcpschema import CELL_LIMIT, DEFAULT_ROWS, HISTORY_LIMIT, MATCH_LIMIT, MAX_ROWS, shrink, shrink_rows
from omatableplus.protocol import Error


class ProtocolTest(Fixture):
    def test_initialize_answers_with_the_requested_known_version(self):
        reply = self.client.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                "clientInfo": {"name": "test", "version": "0"}})
        result = reply["result"]
        self.assertEqual(reply["jsonrpc"], "2.0")
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(result["serverInfo"]["name"], "omatableplus")

    def test_initialize_with_an_unknown_version_answers_with_the_latest_known(self):
        reply = self.client.send("initialize", {"protocolVersion": "2099-01-01", "capabilities": {}})
        self.assertEqual(reply["result"]["protocolVersion"], LATEST)
        self.assertIn(LATEST, SUPPORTED)

    def test_instructions_say_which_mode_the_server_is_in(self):
        read = self.client.send("initialize", {"protocolVersion": LATEST})["result"]["instructions"]
        self.assertIn("read-only", read)
        writing = Client(self.helper, write=True).send("initialize", {"protocolVersion": LATEST})
        self.assertIn("write", writing["result"]["instructions"])

    def test_notifications_get_no_reply(self):
        self.assertEqual(self.client.notify("notifications/initialized"), [])

    def test_ping(self):
        self.assertEqual(self.client.send("ping")["result"], {})

    def test_unknown_method(self):
        self.assertEqual(self.client.send("resources/list")["error"]["code"], -32601)

    def test_invalid_json(self):
        reply = self.client.raw("{nope")[0]
        self.assertEqual((reply["id"], reply["error"]["code"]), (None, -32700))

    def test_a_malformed_request_is_answered_and_does_not_stop_the_server(self):
        for line, code in (('{"jsonrpc":"2.0","id":1,"method":["tools/list"]}', -32600),
                           ('{"jsonrpc":"2.0","id":2,"method":{"a":1}}', -32600),
                           ('{"jsonrpc":"2.0","id":3,"method":7}', -32600),
                           ('{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":["execute_sql"]}}', -32602),
                           ('{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":{"x":1}}}', -32602)):
            reply = self.client.raw(line)[0]
            self.assertEqual((reply["id"], reply["error"]["code"]), (int(line.split('"id":')[1][0]), code), line)
        self.assertEqual(self.client.send("ping")["result"], {})

    def test_params_and_messages_that_are_not_objects_are_answered_too(self):
        reply = self.client.raw('{"jsonrpc":"2.0","id":6,"method":"ping","params":[1]}')[0]
        self.assertEqual((reply["id"], reply["error"]["code"]), (6, -32602))
        for line in ("[1]", '"ping"', "7"):
            reply = self.client.raw(line)[0]
            self.assertEqual((reply["id"], reply["error"]["code"]), (None, -32600), line)
        self.assertEqual(self.client.send("ping")["result"], {})

    def test_a_request_without_a_method_is_answered_not_ignored(self):
        for line, rid in (('{"jsonrpc":"2.0","id":5}', 5), ('{"jsonrpc":"2.0","id":"abc","params":{}}', "abc"),
                          ('{"jsonrpc":"2.0","id":null}', None), ('{"jsonrpc":"2.0","id":0}', 0)):
            reply = self.client.raw(line)[0]
            self.assertEqual((reply["id"], reply["error"]["code"]), (rid, -32600), line)
        self.assertEqual(self.client.send("ping")["result"], {})

    def test_a_message_that_is_neither_a_request_nor_a_notification_nor_a_reply_is_invalid(self):
        for line in ('{}', '{"jsonrpc":"2.0"}', '{"jsonrpc":"2.0","method":7}', '{"jsonrpc":"2.0","method":["ping"]}'):
            reply = self.client.raw(line)[0]
            self.assertEqual((reply["id"], reply["error"]["code"]), (None, -32600), line)

    def test_an_id_that_is_not_a_string_a_number_or_null_is_an_invalid_request_with_a_null_id(self):
        for rid in ('[1]', '{"a":1}', 'true', 'false'):
            for body in ('"method":"ping"', '"method":"tools/call","params":{"name":"list_connections"}', ''):
                reply = self.client.raw('{"jsonrpc":"2.0","id":%s%s}' % (rid, "," + body if body else ""))
                self.assertEqual([(m["id"], m["error"]["code"]) for m in reply], [(None, -32600)], (rid, body))
        self.assertEqual(self.client.send("ping")["result"], {})

    def test_a_notification_and_a_reply_to_a_request_never_sent_get_no_answer(self):
        for line in ('{"jsonrpc":"2.0","method":"notifications/initialized"}', '{"jsonrpc":"2.0","method":"nope"}',
                     '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"commit_changes"}}',
                     '{"jsonrpc":"2.0","id":3,"result":{"action":"accept"}}',
                     '{"jsonrpc":"2.0","id":"x","error":{"code":-1,"message":"no"}}'):
            self.assertEqual(self.client.raw(line), [], line)

    def test_whatever_a_handler_raises_is_an_internal_error_not_the_end_of_the_server(self):
        self.client.server.requests["boom"] = lambda params: 1 / 0
        reply = self.client.raw('{"jsonrpc":"2.0","id":9,"method":"boom"}')[0]
        self.assertEqual((reply["id"], reply["error"]["code"]), (9, -32603))
        self.assertEqual(self.client.send("ping")["result"], {})

    def test_whatever_a_tool_raises_is_a_tool_error_the_model_can_read(self):
        with mock.patch.object(McpServer, "tool_list_databases", side_effect=ZeroDivisionError("boom")):
            is_error, payload = self.client.call("list_databases", connection="Notes")
        self.assertTrue(is_error)
        self.assertEqual((payload["error"]["code"], payload["error"]["message"]), ("internal", "ZeroDivisionError: boom"))
        self.assertEqual(self.client.send("ping")["result"], {})

    def test_a_tunnel_that_dropped_closes_every_open_session(self):
        self.client.call("list_objects", connection="Notes")
        self.assertEqual(len(self.helper.sessions.items), 1)
        with mock.patch.object(McpServer, "tool_list_databases", side_effect=Error("tunnel", "gone")):
            is_error, payload = self.client.call("list_databases", connection="Notes")
        self.assertEqual((is_error, payload["error"]["code"]), (True, "tunnel"))
        self.assertEqual(self.helper.sessions.items, {})

    def test_unknown_tool_is_a_protocol_error(self):
        reply = self.client.send("tools/call", {"name": "format_disk", "arguments": {}})
        self.assertEqual(reply["error"]["code"], -32602)


class ShrinkTest(unittest.TestCase):
    def test_a_json_cell_is_cut_like_a_text_cell(self):
        for cell in ({"a": "x" * 5000}, ["y" * 600, "z" * 600], {"deep": {"deeper": ["w" * 3000]}}):
            cut = shrink(cell)
            self.assertIsInstance(cut, str)
            self.assertLess(len(cut), CELL_LIMIT + 50)
            self.assertIn("chars]", cut)

    def test_a_small_json_cell_stays_as_it_is(self):
        for cell in ({"a": 1, "b": [1, 2]}, [1, "two", None], {}):
            self.assertEqual(shrink(cell), cell)

    def test_rows_are_shrunk_cell_by_cell(self):
        rows = shrink_rows([[1, {"k": "v" * 5000}, b"\x00"]])
        self.assertEqual((rows[0][0], rows[0][2]), (1, "‹binary 1 bytes›"))
        self.assertIsInstance(rows[0][1], str)


class ReadTest(Fixture):
    def test_tools_listed_in_read_mode_have_no_commit(self):
        names = self.client.tool_names()
        self.assertIn("execute_sql", names)
        self.assertIn("preview_changes", names)
        self.assertNotIn("commit_changes", names)
        reply = self.client.send("tools/call", {"name": "commit_changes", "arguments": {}})
        self.assertEqual(reply["error"]["code"], -32602)

    def test_no_schema_offers_confirmed_or_extra_arguments(self):
        for tool in self.client.send("tools/list")["result"]["tools"]:
            schema = tool["inputSchema"]
            self.assertNotIn("confirmed", schema["properties"], tool["name"])
            self.assertIs(schema["additionalProperties"], False, tool["name"])

    def test_list_connections_keeps_secrets_out(self):
        is_error, payload = self.client.call("list_connections")
        self.assertFalse(is_error)
        names = {c["name"] for c in payload["connections"]}
        self.assertTrue({"Notes", "Prod", "Frozen", "Legacy MySQL", "Cache"} <= names)
        self.assertNotIn("s3cret", self.client.out.getvalue())
        for conn in payload["connections"]:
            self.assertEqual(set(conn) - {"id", "name", "engine", "tag", "readonly", "host", "port", "database",
                                          "folder", "supported", "family"}, set())
        self.assertEqual({c["name"]: (c["family"], c["supported"]) for c in payload["connections"]}["Cache"],
                         (None, False))

    def test_a_connection_is_found_by_id_or_by_name(self):
        for ref in (self.local["id"], "notes"):
            is_error, payload = self.client.call("list_objects", connection=ref)
            self.assertFalse(is_error, ref)
            self.assertEqual(payload["schemas"][0]["name"], "main")

    def test_two_connections_with_the_same_name_must_be_told_apart_by_id(self):
        twin = self.store.save({"name": "NOTES", "engine": "sqlite", "path": self.path, "tag": "local"})
        is_error, payload = self.client.call("list_objects", connection="notes")
        self.assertEqual((is_error, payload["error"]["code"]), (True, "invalid"))
        self.assertIn("2 connections", payload["error"]["message"])
        self.assertFalse(self.client.call("list_objects", connection=twin["id"])[0])

    def test_an_omadbngin_server_is_found_by_name_but_is_not_a_saved_connection(self):
        state = [{"name": "Fake", "engine": "mysql", "port": 3306, "running": True}]
        with mock.patch.object(omadb, "_state", lambda: state):
            listed = self.client.call("list_connections")[1]["connections"]
            self.assertIn("Fake", [c["name"] for c in listed])
            is_error, payload = self.client.call("list_databases", connection="Fake")
        self.assertNotEqual(payload["error"]["code"], "notfound")

    def test_unknown_connection(self):
        is_error, payload = self.client.call("list_objects", connection="nope")
        self.assertTrue(is_error)
        self.assertEqual(payload["error"]["code"], "notfound")

    def test_sql_tools_refuse_a_redis_connection(self):
        is_error, payload = self.client.call("execute_sql", connection="Cache", sql="PING")
        self.assertTrue(is_error)
        self.assertEqual(payload["error"]["code"], "unsupported")

    def test_list_databases(self):
        _, payload = self.client.call("list_databases", connection="Notes")
        self.assertEqual(payload["databases"], ["main"])

    def test_list_objects(self):
        _, payload = self.client.call("list_objects", connection="Notes")
        self.assertEqual(sorted(payload["schemas"][0]["tables"]), ["big", "blobs", "notes"])

    def test_describe_object(self):
        is_error, payload = self.client.call("describe_object", connection="Notes", schema="main", name="notes")
        self.assertFalse(is_error)
        self.assertEqual([c["name"] for c in payload["columns"]], ["id", "body"])
        self.assertTrue(payload["columns"][0]["pk"])
        self.assertIn("CREATE TABLE", payload["ddl"])

    def test_search_schema_matches_tables_and_columns(self):
        _, payload = self.client.call("search_schema", connection="Notes", pattern="BOD")
        self.assertEqual([(m["table"], [c["name"] for c in m["columns"]]) for m in payload["matches"]],
                         [("notes", ["body"])])
        _, payload = self.client.call("search_schema", connection="Notes", pattern="blob")
        self.assertEqual([m["table"] for m in payload["matches"]], ["blobs"])
        self.assertEqual(self.client.call("search_schema", connection="Notes", pattern="zzz")[1]["matches"], [])

    def test_search_rows_filters_sorts_and_counts(self):
        is_error, payload = self.client.call(
            "search_rows", connection="Notes", schema="main", table="notes",
            filters=[{"column": "body", "op": "contains", "value": "t"}], sort=[{"column": "id", "desc": True}])
        self.assertFalse(is_error)
        self.assertEqual(payload["columns"], ["id", "body"])
        self.assertEqual(payload["rows"], [[3, "three"], [2, "two"]])
        self.assertEqual((payload["matched"], payload["truncated"]), (2, False))

    def test_search_rows_default_and_maximum_page(self):
        args = {"connection": "Notes", "schema": "main", "table": "big"}
        _, page = self.client.call("search_rows", **args)
        self.assertEqual((len(page["rows"]), page["matched"], page["truncated"]), (DEFAULT_ROWS, 300, True))
        _, page = self.client.call("search_rows", limit=1000, **args)
        self.assertEqual(len(page["rows"]), MAX_ROWS)
        _, page = self.client.call("search_rows", limit=MAX_ROWS, offset=250, **args)
        self.assertEqual((len(page["rows"]), page["truncated"]), (50, False))

    def test_search_rows_keeps_at_least_one_row_and_never_starts_before_the_first(self):
        args = {"connection": "Notes", "schema": "main", "table": "notes"}
        for limit in (0, -4):
            self.assertEqual(len(self.client.call("search_rows", limit=limit, **args)[1]["rows"]), 1, limit)
        _, page = self.client.call("search_rows", offset=-5, **args)
        self.assertEqual(([row[0] for row in page["rows"]], page["truncated"]), ([1, 2, 3], False))

    def test_search_rows_shrinks_blobs_and_long_text(self):
        _, page = self.client.call("search_rows", connection="Notes", schema="main", table="blobs")
        payload_cell, story_cell = page["rows"][0][1:]
        self.assertEqual(payload_cell, "‹binary 3 bytes›")
        self.assertLess(len(story_cell), CELL_LIMIT + 50)

    def test_search_rows_rejects_an_unknown_column(self):
        is_error, payload = self.client.call("search_rows", connection="Notes", schema="main", table="notes",
                                             filters=[{"column": "nope", "op": "null"}])
        self.assertTrue(is_error)
        self.assertEqual(payload["error"]["code"], "invalid")

    def test_execute_sql_select(self):
        is_error, payload = self.client.call("execute_sql", connection="Notes",
                                             sql="SELECT id, body FROM notes ORDER BY id")
        self.assertFalse(is_error)
        result = payload["results"][0]
        self.assertEqual(result["columns"], ["id", "body"])
        self.assertEqual(result["rows"][0], [1, "one"])
        self.assertFalse(result["truncated"])

    def test_execute_sql_caps_rows(self):
        _, payload = self.client.call("execute_sql", connection="Notes", sql="SELECT * FROM big")
        self.assertEqual((len(payload["results"][0]["rows"]), payload["results"][0]["truncated"]), (DEFAULT_ROWS, True))
        _, payload = self.client.call("execute_sql", connection="Notes", sql="SELECT * FROM big", max_rows=5000)
        self.assertEqual(len(payload["results"][0]["rows"]), MAX_ROWS)

    def test_execute_sql_keeps_at_least_one_row(self):
        for max_rows in (0, -3):
            _, payload = self.client.call("execute_sql", connection="Notes", sql="SELECT * FROM big", max_rows=max_rows)
            self.assertEqual((len(payload["results"][0]["rows"]), payload["results"][0]["truncated"]), (1, True))

    def test_a_boolean_is_not_a_number(self):
        is_error, payload = self.client.call("execute_sql", connection="Notes", sql="SELECT 1", max_rows=True)
        self.assertEqual((is_error, payload["error"]["code"]), (True, "invalid"))

    def test_execute_sql_cuts_the_statement_it_echoes(self):
        _, payload = self.client.call("execute_sql", connection="Notes", sql="SELECT '" + "z" * 5000 + "'")
        self.assertLess(len(payload["results"][0]["sql"]), CELL_LIMIT + 50)

    def test_execute_sql_shrinks_blobs_and_long_text(self):
        _, payload = self.client.call("execute_sql", connection="Notes", sql="SELECT payload, story FROM blobs")
        payload_cell, story_cell = payload["results"][0]["rows"][0]
        self.assertEqual(payload_cell, "‹binary 3 bytes›")
        self.assertTrue(story_cell.startswith("y" * CELL_LIMIT))
        self.assertLess(len(story_cell), CELL_LIMIT + 50)
        self.assertIn("4000", story_cell)

    def test_a_failing_statement_is_a_tool_error_with_the_database_message(self):
        is_error, payload = self.client.call("execute_sql", connection="Notes", sql="SELECT * FROM nope")
        self.assertTrue(is_error)
        self.assertIn("nope", payload["results"][-1]["error"]["message"])

    def test_read_mode_refuses_a_write_with_the_readonly_code(self):
        is_error, payload = self.client.call("execute_sql", connection="Notes", sql="DELETE FROM notes")
        self.assertTrue(is_error)
        self.assertEqual(payload["error"]["code"], "readonly")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM notes"), 3)

    def test_a_read_mode_session_is_read_only_at_the_driver_when_the_helper_gate_lets_a_write_through(self):
        # the gate above is the helper's own check of the SQL; with it out of the way, the driver must still refuse
        blind = mock.patch.object(safety, "sql_writes", lambda statements, engine: [])
        with blind:
            is_error, payload = self.client.call("execute_sql", connection="Notes", sql="DELETE FROM notes")
            self.assertTrue(is_error)
            self.assertIn("readonly", payload["results"][0]["error"]["message"].replace("-", ""))
            self.assertEqual(self.scalar("SELECT COUNT(*) FROM notes"), 3)
            # the same blind gate in write mode lets the DELETE run: it is the driver that stopped the first one
            writer = Client(self.helper, write=True)
            self.assertFalse(writer.call("execute_sql", connection="Notes", sql="DELETE FROM notes")[0])
            self.assertEqual(self.scalar("SELECT COUNT(*) FROM notes"), 0)

    def test_search_schema_stops_at_the_match_limit_and_says_so(self):
        db = sqlite3.connect(self.path)
        for n in range(MATCH_LIMIT + 10):
            db.execute(f"CREATE TABLE gadget_{n} (id INTEGER)")
        db.execute("CREATE TABLE wide (" + ", ".join(f"spare_{n} INTEGER" for n in range(MATCH_LIMIT + 10)) + ")")
        db.commit()
        db.close()
        _, payload = self.client.call("search_schema", connection="Notes", pattern="GADGET")
        self.assertEqual((len(payload["matches"]), payload["truncated"]), (MATCH_LIMIT, True))
        _, payload = self.client.call("search_schema", connection="Notes", pattern="spare")
        self.assertEqual(([len(m["columns"]) for m in payload["matches"]], payload["truncated"]), ([MATCH_LIMIT], True))
        self.assertFalse(self.client.call("search_schema", connection="Notes", pattern="notes")[1]["truncated"])

    def test_search_schema_is_complete_when_the_columns_fit_the_limit_exactly(self):
        db = sqlite3.connect(self.path)
        db.execute("CREATE TABLE snug (" + ", ".join(f"fit_{n} INTEGER" for n in range(MATCH_LIMIT)) + ")")
        db.commit()
        db.close()
        _, payload = self.client.call("search_schema", connection="Notes", pattern="fit_")
        self.assertEqual(([len(m["columns"]) for m in payload["matches"]], payload["truncated"]), ([MATCH_LIMIT], False))

    def test_explain_sql(self):
        is_error, payload = self.client.call("explain_sql", connection="Notes", sql="SELECT * FROM notes WHERE id = 1")
        self.assertFalse(is_error)
        self.assertTrue(payload["raw"])

    def test_recent_queries(self):
        self.client.call("execute_sql", connection="Notes", sql="SELECT 41 + 1")
        _, payload = self.client.call("recent_queries", connection="Notes")
        self.assertTrue(any("SELECT 41 + 1" in item["sql"] for item in payload["items"]))

    def test_recent_queries_are_capped_floored_cut_and_scoped_to_the_connection(self):
        for _ in range(HISTORY_LIMIT + 5):
            self.client.call("execute_sql", connection="Notes", sql="SELECT 'on notes'")
        self.client.call("execute_sql", connection="Prod", sql="SELECT 'on prod'")
        self.client.call("execute_sql", connection="Notes", sql="SELECT '" + "q" * 5000 + "'")
        recent = lambda **args: self.client.call("recent_queries", **args)[1]["items"]  # noqa: E731
        self.assertEqual(len(recent(connection="Notes", limit=10 ** 6)), HISTORY_LIMIT)
        self.assertEqual([len(recent(connection="Notes", limit=n)) for n in (0, -2, 3)], [1, 1, 3])
        self.assertTrue(all(len(item["sql"]) < CELL_LIMIT + 50 for item in recent(connection="Notes")))
        self.assertNotIn("SELECT 'on prod'", [item["sql"] for item in recent(connection="Notes", limit=HISTORY_LIMIT)])
        self.assertIn("SELECT 'on prod'", [item["sql"] for item in recent(search="on prod")])

    def test_a_session_in_read_mode_is_kept_between_calls(self):
        self.client.call("execute_sql", connection="Notes", sql="SELECT 1")
        first = set(self.helper.sessions.items)
        self.client.call("execute_sql", connection="Notes", sql="SELECT 2")
        self.assertEqual(set(self.helper.sessions.items), first)

    def test_a_call_without_database_goes_back_to_the_one_the_session_opened_on(self):
        used = []
        with mock.patch.dict(self.helper.methods, {"session.use": lambda **kwargs: used.append(kwargs["database"])}):
            self.client.call("list_objects", connection="Notes")
            self.assertEqual(used, [])
            self.client.call("list_objects", connection="Notes", database="main")
            self.assertEqual(used, ["main"])
            [session] = self.helper.sessions.items.values()
            session.adapter.database = "elsewhere"      # a previous call that named another database
            self.client.call("list_objects", connection="Notes")
            self.assertEqual(used, ["main", "main"])

    def test_sessions_are_reused_and_closed_on_shutdown(self):
        self.client.call("list_objects", connection="Notes")
        self.client.call("execute_sql", connection="Notes", sql="SELECT 1")
        self.assertEqual(len(self.helper.sessions.items), 1)
        self.client.server.close()
        self.assertEqual(self.helper.sessions.items, {})

    def test_missing_and_mistyped_arguments_are_tool_errors(self):
        for arguments in ({"connection": "Notes"}, {"connection": "Notes", "sql": 5}):
            is_error, payload = self.client.call("execute_sql", **arguments)
            self.assertTrue(is_error)
            self.assertEqual(payload["error"]["code"], "invalid")


class WriteTest(Fixture):
    write = True

    def test_commit_tool_is_listed_in_write_mode(self):
        self.assertIn("commit_changes", self.client.tool_names())

    def test_insert_through_sql(self):
        is_error, payload = self.client.call("execute_sql", connection="Notes",
                                             sql="INSERT INTO notes (body) VALUES ('four')")
        self.assertFalse(is_error)
        self.assertEqual(payload["results"][0]["affected"], 1)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM notes"), 4)

    def test_production_refuses_writes_but_still_reads(self):
        is_error, payload = self.client.call("execute_sql", connection="Prod",
                                             sql="INSERT INTO notes (body) VALUES ('four')")
        self.assertTrue(is_error)
        self.assertEqual(payload["error"]["code"], "confirm")
        self.assertIn("production", payload["error"]["hint"])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM notes"), 3)
        is_error, payload = self.client.call("execute_sql", connection="Prod", sql="SELECT COUNT(*) FROM notes")
        self.assertEqual((is_error, payload["results"][0]["rows"]), (False, [[3]]))

    def test_the_model_cannot_confirm_a_production_write(self):
        is_error, payload = self.client.call("execute_sql", connection="Prod", confirmed=True,
                                             sql="INSERT INTO notes (body) VALUES ('four')")
        self.assertTrue(is_error)
        self.assertEqual(payload["error"]["code"], "invalid")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM notes"), 3)

    def test_a_read_only_connection_stays_read_only_in_write_mode(self):
        is_error, payload = self.client.call("execute_sql", connection="Frozen", sql="DELETE FROM notes")
        self.assertTrue(is_error)
        self.assertEqual(payload["error"]["code"], "readonly")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM notes"), 3)

    def test_preview_changes_shows_the_sql_and_writes_nothing(self):
        is_error, payload = self.client.call(
            "preview_changes", connection="Notes", schema="main", table="notes",
            updates=[{"key": {"id": 1}, "values": {"body": "uno"}}])
        self.assertFalse(is_error)
        self.assertIn("'uno'", payload["statements"][0])
        self.assertEqual(self.scalar("SELECT body FROM notes WHERE id = 1"), "one")

    def test_commit_changes_applies_deletes_updates_and_inserts(self):
        is_error, payload = self.client.call(
            "commit_changes", connection="Notes", schema="main", table="notes",
            deletes=[{"key": {"id": 3}}],
            updates=[{"key": {"id": 1}, "values": {"body": "uno"}}],
            inserts=[{"values": {"body": "four"}}])
        self.assertFalse(is_error)
        self.assertEqual(payload["affected"], 3)
        self.assertEqual(self.scalar("SELECT group_concat(body) FROM (SELECT body FROM notes ORDER BY id)"),
                         "uno,two,four")

    def test_commit_changes_is_all_or_nothing(self):
        is_error, payload = self.client.call(
            "commit_changes", connection="Notes", schema="main", table="notes",
            updates=[{"key": {"id": 1}, "values": {"body": "uno"}}, {"key": {"id": 999}, "values": {"body": "x"}}])
        self.assertTrue(is_error)
        self.assertEqual(payload["error"]["code"], "sql")
        self.assertEqual(self.scalar("SELECT body FROM notes WHERE id = 1"), "one")

    def test_commit_changes_is_refused_on_production_and_read_only(self):
        for ref, code in (("Prod", "confirm"), ("Frozen", "readonly")):
            is_error, payload = self.client.call("commit_changes", connection=ref, schema="main", table="notes",
                                                 deletes=[{"key": {"id": 1}}])
            self.assertTrue(is_error, ref)
            self.assertEqual(payload["error"]["code"], code, ref)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM notes"), 3)


if __name__ == "__main__":
    unittest.main()
