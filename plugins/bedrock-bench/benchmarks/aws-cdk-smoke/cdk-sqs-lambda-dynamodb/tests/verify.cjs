const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");
const project = process.env.BENCH_PROJECT || "/app";
const req = createRequire(path.join(project, "package.json"));
const cdk = req("aws-cdk-lib");
const dynamodb = req("aws-cdk-lib/aws-dynamodb");
const { Template } = req("aws-cdk-lib/assertions");
const document = JSON.parse(fs.readFileSync(path.join(project, "cdk.out/Benchmark.template.json"), "utf8"));
const template = Template.fromJSON(document);
const resources = document.Resources;
const resourcesOf = (type) => Object.entries(resources).filter(([, r]) => r.Type === type);
const list = (value) => Array.isArray(value) ? value : [value];
const arn = (id) => ({ "Fn::GetAtt": [id, "Arn"] });

template.resourceCountIs("AWS::DynamoDB::Table", 1);
template.resourceCountIs("AWS::Lambda::Function", 1);
template.resourceCountIs("AWS::SQS::Queue", 2);
template.resourceCountIs("AWS::Lambda::EventSourceMapping", 1);

// Independently derive the original table identity and complete resource contract.
const original = new cdk.Stack(new cdk.App(), "Benchmark");
new dynamodb.Table(original, "Records", {
  tableName: "bedrock-bench-records",
  partitionKey: { name: "id", type: dynamodb.AttributeType.STRING },
  billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
  encryption: dynamodb.TableEncryption.AWS_MANAGED,
  removalPolicy: cdk.RemovalPolicy.RETAIN,
});
const [tableId, originalTable] = Object.entries(
  Template.fromStack(original).findResources("AWS::DynamoDB::Table"),
)[0];
assert.ok(resources[tableId], "Existing table logical ID must be preserved");
for (const key of ["Type", "Properties", "DeletionPolicy", "UpdateReplacePolicy"]) {
  assert.deepEqual(resources[tableId][key], originalTable[key], `Existing table ${key} must be preserved`);
}

const [functionId, fn] = resourcesOf("AWS::Lambda::Function")[0];
assert.equal(fn.Properties.Timeout, 30);
assert.deepEqual(fn.Properties.Environment.Variables.TABLE_NAME, { Ref: tableId });
const [queueId, queue] = resourcesOf("AWS::SQS::Queue").find(([, q]) => q.Properties?.RedrivePolicy);
const [dlqId] = resourcesOf("AWS::SQS::Queue").find(([id]) => id !== queueId);
assert.ok(queue.Properties.VisibilityTimeout >= 180, "Queue visibility must allow retries");
assert.equal(queue.Properties.RedrivePolicy.maxReceiveCount, 3);
assert.deepEqual(queue.Properties.RedrivePolicy.deadLetterTargetArn, arn(dlqId));
const mapping = resourcesOf("AWS::Lambda::EventSourceMapping")[0][1].Properties;
assert.deepEqual(mapping.EventSourceArn, arn(queueId));
assert.deepEqual(mapping.FunctionName, { Ref: functionId });
assert.ok(mapping.BatchSize >= 1 && mapping.BatchSize <= 10);
assert.deepEqual(mapping.FunctionResponseTypes, ["ReportBatchItemFailures"]);

const workerRole = fn.Properties.Role["Fn::GetAtt"][0];
const role = resources[workerRole];
for (const managed of role.Properties.ManagedPolicyArns || []) {
  assert.ok(JSON.stringify(managed).includes("service-role/AWSLambdaBasicExecutionRole"),
    "Worker must not gain unrelated managed permissions");
}
const statements = resourcesOf("AWS::IAM::Policy")
  .filter(([, policy]) => (policy.Properties.Roles || []).some((r) => r.Ref === workerRole))
  .flatMap(([, policy]) => policy.Properties.PolicyDocument.Statement);
statements.push(...(role.Properties.Policies || []).flatMap((p) => p.PolicyDocument.Statement));
let writeGranted = false;
const queueActions = new Set();
const requiredQueueActions = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"];
const allowedQueueActions = new Set([
  ...requiredQueueActions, "sqs:ChangeMessageVisibility", "sqs:GetQueueUrl",
]);
for (const statement of statements) {
  if (statement.Effect !== "Allow") continue;
  assert.equal(statement.NotAction, undefined, "Allow/NotAction would bypass scoped grants");
  for (const action of list(statement.Action)) {
    assert.notEqual(action, "*", "Wildcard actions are forbidden");
    if (action.startsWith("dynamodb:")) {
      assert.equal(action, "dynamodb:PutItem");
      assert.deepEqual(list(statement.Resource), [arn(tableId)]);
      writeGranted = true;
    }
    if (action.startsWith("sqs:")) {
      assert.ok(allowedQueueActions.has(action), `Unexpected SQS permission: ${action}`);
      assert.deepEqual(list(statement.Resource), [arn(queueId)]);
      queueActions.add(action);
    }
  }
}
assert.ok(writeGranted, "Missing table write permission");
for (const action of requiredQueueActions) {
  assert.ok(queueActions.has(action), `Missing SQS consumer permission: ${action}`);
}

// Intercept the SDK boundary: exercise the actual handler without AWS calls.
const { DynamoDBDocumentClient, PutCommand } = req("@aws-sdk/lib-dynamodb");
const calls = [];
DynamoDBDocumentClient.prototype.send = async function (command) {
  assert.ok(command instanceof PutCommand);
  calls.push(command.input);
  if (command.input.Item.id === "write-fails") throw new Error("Injected write failure");
  return {};
};
process.env.TABLE_NAME = "verifier-table";
const { handler } = req(path.join(project, "lambda/handler.js"));
const message = (id, value, messageId = id) => ({
  messageId, body: JSON.stringify({ id, value }),
});

async function checkHandler() {
  const first = await handler({ Records: [message("one", 17), message("two", -8)] });
  assert.deepEqual(first, { batchItemFailures: [] });
  assert.deepEqual(calls, [
    { TableName: "verifier-table", Item: { id: "one", value: 17 } },
    { TableName: "verifier-table", Item: { id: "two", value: -8 } },
  ]);
  calls.length = 0;
  const second = await handler({ Records: [
    { messageId: "bad-json", body: "{" },
    message("write-fails", 2, "failed-write"),
    message("three", 0),
    message("", 9, "empty-id"),
    { messageId: "wrong-type", body: JSON.stringify({ id: "four", value: "9" }) },
  ] });
  assert.deepEqual(
    second.batchItemFailures.map((f) => f.itemIdentifier).sort(),
    ["bad-json", "failed-write", "empty-id", "wrong-type"].sort(),
  );
  assert.deepEqual(calls, [
    { TableName: "verifier-table", Item: { id: "write-fails", value: 2 } },
    { TableName: "verifier-table", Item: { id: "three", value: 0 } },
  ]);
  assert.deepEqual(await handler({ Records: [] }), { batchItemFailures: [] });
  console.log("PASS: infrastructure wiring, scoped IAM, table preservation, batch processing, and failure reporting");
}
checkHandler().catch((error) => { console.error(error); process.exitCode = 1; });
