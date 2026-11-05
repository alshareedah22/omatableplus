"""MCP tools that read Redis (Valkey) and MongoDB. Reading only.

A mixin of McpServer. Redis and MongoDB have no read-only mode at the driver, so nothing here can write: no raw
command, no key or document change, and an aggregation pipeline that mentions $out or $merge is refused. Sessions
are opened read-only whatever the server mode, which makes the helper refuse any other write.
"""

import re

from .mcpschema import CONNECTION, DATABASE, MAX_ROWS, MONGO_ENGINES, READ, REDIS_ENGINES, page_size, trim
from .protocol import Error

WRITING_STAGE = re.compile(r"\$(?:out|merge)\b")    # not $mergeObjects, not "$outcome"
COLLECTION = {"type": "string", "description": "Collection name."}
TEXT = "text such as {status: 'open'} (relaxed JSON, ObjectId(...) and ISODate(...) understood)"

TOOLS = (
    ("redis_scan", "List the keys of a Redis database that match a pattern, with their type and expiry.",
     {"connection": CONNECTION, "database": {**DATABASE, "description": "Redis database number, e.g. 1."},
      "pattern": {"type": "string", "description": "Glob pattern, default *."},
      "cursor": {"type": "string", "description": "Cursor of the previous page; 0 starts."},
      "count": {"type": "integer", "description": f"Keys wanted (default 100, at most {MAX_ROWS})."},
      "type": {"type": "string", "description": "Only this type: string, hash, list, set, zset, stream."}},
     ("connection",), READ),
    ("redis_get", "Read one Redis key: its type, expiry and value (long values are cut).",
     {"connection": CONNECTION, "database": {**DATABASE, "description": "Redis database number, e.g. 1."},
      "key": {"type": "string"}}, ("connection", "key"), READ),
    ("redis_keyspace", "How many keys each Redis database holds.",
     {"connection": CONNECTION, "database": {**DATABASE, "description": "Redis database number, e.g. 1."}},
     ("connection",), READ),
    ("mongo_find", "Read documents of a MongoDB collection.",
     {"connection": CONNECTION, "database": DATABASE, "collection": COLLECTION,
      "filter": {"type": "string", "description": "Filter as " + TEXT + "."},
      "sort": {"type": "string", "description": "Sort as {field: 1 or -1}."},
      "projection": {"type": "string", "description": "Fields to return, as {field: 1}."},
      "skip": {"type": "integer"}, "limit": {"type": "integer", "description": f"Default 50, at most {MAX_ROWS}."}},
     ("connection", "collection"), READ),
    ("mongo_count", "Count the documents of a collection that match a filter.",
     {"connection": CONNECTION, "database": DATABASE, "collection": COLLECTION,
      "filter": {"type": "string", "description": "Filter as " + TEXT + "."}}, ("connection", "collection"), READ),
    ("mongo_indexes", "The indexes of a collection.",
     {"connection": CONNECTION, "database": DATABASE, "collection": COLLECTION}, ("connection", "collection"), READ),
    ("mongo_aggregate", "Run an aggregation pipeline that reads. A pipeline with $out or $merge is refused.",
     {"connection": CONNECTION, "database": DATABASE, "collection": COLLECTION,
      "pipeline": {"type": "string", "description": "Stages as [{$match: {...}}, ...]."},
      "limit": {"type": "integer", "description": f"Documents returned, default 50, at most {MAX_ROWS}."}},
     ("connection", "collection", "pipeline"), READ),
)


class NosqlTools:
    def redis(self, connection, database):
        return self.session(connection, database, engines=REDIS_ENGINES, readonly=True)

    def mongo(self, connection, database):
        return self.session(connection, database, engines=MONGO_ENGINES, readonly=True)

    def tool_redis_scan(self, connection, database=None, pattern="*", cursor="0", count=100, type=None):  # noqa: A002
        return trim(self.methods["redis.scan"](session=self.redis(connection, database), pattern=pattern,
                                               cursor=cursor, count=page_size(count), type=type))

    def tool_redis_get(self, connection, key, database=None):
        return trim(self.methods["redis.get"](session=self.redis(connection, database), key=key))

    def tool_redis_keyspace(self, connection, database=None):
        return trim(self.methods["redis.keyspace"](session=self.redis(connection, database)))

    def tool_mongo_find(self, connection, collection, database=None, filter="", sort="", projection="",  # noqa: A002
                        skip=0, limit=50):
        return trim(self.methods["mongo.find"](session=self.mongo(connection, database), collection=collection,
                                               filter=filter, sort=sort, projection=projection, skip=max(0, skip),
                                               limit=page_size(limit)))

    def tool_mongo_count(self, connection, collection, database=None, filter=""):  # noqa: A002
        return trim(self.methods["mongo.count"](session=self.mongo(connection, database), collection=collection,
                                                filter=filter))

    def tool_mongo_indexes(self, connection, collection, database=None):
        return trim(self.methods["mongo.indexes"](session=self.mongo(connection, database), collection=collection))

    def tool_mongo_aggregate(self, connection, collection, pipeline, database=None, limit=50):
        if WRITING_STAGE.search(pipeline):
            raise Error("invalid", "a pipeline with $out or $merge writes; MCP only reads from MongoDB")
        return trim(self.methods["mongo.aggregate"](session=self.mongo(connection, database), collection=collection,
                                                    pipeline=pipeline, limit=page_size(limit)))
