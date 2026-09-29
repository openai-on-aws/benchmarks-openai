// Independent fixture sanity check only. This does NOT grade the benchmark.
// It verifies that authored handler controls behave as advertised, including
// cases beyond the packaged verifier's inputs. No third-party modules load.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const manifest = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));

async function check(file) {
  const calls = [];
  class PutCommand { constructor(input) { this.input = input; } }
  const client = {
    async send(command) {
      assert.ok(command instanceof PutCommand);
      calls.push(command.input);
      if (command.input.Item.id === "write-fails") throw new Error("Injected write failure");
    },
  };
  const context = {
    exports: {}, process: { env: { TABLE_NAME: "control-table" } },
    require(name) {
      if (name === "@aws-sdk/client-dynamodb") return { DynamoDBClient: class {} };
      if (name === "@aws-sdk/lib-dynamodb") return { DynamoDBDocumentClient: { from: () => client }, PutCommand };
      throw new Error(`Unexpected fixture import ${name}`);
    },
  };
  vm.runInNewContext(fs.readFileSync(file, "utf8"), context, { filename: file, timeout: 1000 });
  const handler = context.exports.handler;
  assert.equal(typeof handler, "function");
  const record = (id, value, extra = {}) => ({ messageId: `message-${id}`, body: JSON.stringify({ id, value, ...extra }) });
  // Serialize cross-realm objects to compare data, not VM prototypes.
  const plain = (value) => JSON.parse(JSON.stringify(value));
  assert.deepEqual(plain(await handler({ Records: [] })), { batchItemFailures: [] });
  const full = Array.from({ length: 10 }, (_, i) => record(`item-${i}`, i - 5, { source: "audit", nested: { retained: true } }));
  assert.deepEqual(plain(await handler({ Records: full })), { batchItemFailures: [] });
  assert.deepEqual(plain(calls), full.map(r => ({ TableName: "control-table", Item: JSON.parse(r.body) })));
  calls.length = 0;
  const mixed = [
    { messageId: "invalid-json", body: "{" },
    record("write-fails", 5),
    record("after-failure", 0),
    record("", 2),
    record("wrong-type", "7"),
  ];
  const result = plain(await handler({ Records: mixed }));
  assert.deepEqual(result.batchItemFailures.map(f => f.itemIdentifier).sort(),
                   ["invalid-json", "message-write-fails", "message-", "message-wrong-type"].sort());
  assert.deepEqual(plain(calls), [
    { TableName: "control-table", Item: { id: "write-fails", value: 5 } },
    { TableName: "control-table", Item: { id: "after-failure", value: 0 } },
  ]);
}

(async () => {
  const results = [];
  for (const item of manifest) {
    let observed = "accept", reason = "Independent handler scenarios passed";
    try { await check(path.resolve(item.file)); }
    catch (error) {
      observed = error.code === "ERR_ASSERTION" || error.name === "SyntaxError" ? "reject" : "infrastructure_error";
      reason = `${error.name}: ${error.message}`;
    }
    results.push({ id: item.id, expected: item.expected, observed, reason });
  }
  const result = { kind: "fixture-self-check", actual_packaged_grader: false,
                   status: results.every(r => r.expected === r.observed) ? "passed" : "failed", cases: results };
  console.log(JSON.stringify(result));
  process.exitCode = result.status === "passed" ? 0 : 1;
})().catch(error => { console.error(error); process.exitCode = 70; });
