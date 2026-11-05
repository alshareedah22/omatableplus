import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from mcpkit import Client, Fixture

from omatableplus.mcp import LATEST, SUPPORTED
from omatableplus.mcpschema import CELL_LIMIT, DEFAULT_ROWS, MAX_ROWS, shrink, shrink_rows


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

    def test_whatever_a_handler_raises_is_an_internal_error_not_the_end_of_the_server(self):
        self.client.server.requests["boom"] = lambda params: 1 / 0
        reply = self.client.raw('{"jsonrpc":"2.0","id":9,"method":"boom"}')[0]
        self.assertEqual((reply["id"], reply["error"]["code"]), (9, -32603))
        self.assertEqual(self.client.send("ping")["result"], {})

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
        self.assertEqual({c["name"]: c["family"] for c in payload["connections"]}["Cache"], "redis")

    def test_a_connection_is_found_by_id_or_by_name(self):
        for ref in (self.local["id"], "notes"):
            is_error, payload = self.client.call("list_objects", connection=ref)
            self.assertFalse(is_error, ref)
            self.assertEqual(payload["schemas"][0]["name"], "main")

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

    def test_read_mode_refuses_writes_at_the_driver(self):
        is_error, payload = self.client.call("execute_sql", connection="Notes", sql="DELETE FROM notes")
        self.assertTrue(is_error)
        self.assertEqual(payload["error"]["code"], "readonly")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM notes"), 3)

    def test_explain_sql(self):
        is_error, payload = self.client.call("explain_sql", connection="Notes", sql="SELECT * FROM notes WHERE id = 1")
        self.assertFalse(is_error)
        self.assertTrue(payload["raw"])

    def test_recent_queries(self):
        self.client.call("execute_sql", connection="Notes", sql="SELECT 41 + 1")
        _, payload = self.client.call("recent_queries", connection="Notes")
        self.assertTrue(any("SELECT 41 + 1" in item["sql"] for item in payload["items"]))

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
