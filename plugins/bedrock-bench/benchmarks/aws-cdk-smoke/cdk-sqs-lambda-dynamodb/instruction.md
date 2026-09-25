Repair the AWS CDK application in `/app`.

The intended pipeline is SQS → Lambda → DynamoDB. The project compiles but
the resource wiring, permissions, and message handler are incorrect.

Requirements:

- Keep the existing `Records` table's construct identity, name, key schema,
  billing mode, encryption, and retention policies unchanged.
- Connect the work queue to the Lambda with an SQS event source. Enable partial
  batch failure reporting, and use a batch size from 1 to 10.
- Keep the Lambda timeout at 30 seconds and set the work queue visibility
  timeout to at least 180 seconds. Keep a dead-letter queue with
  `maxReceiveCount: 3`.
- Set the Lambda's `TABLE_NAME` environment variable to the records table.
  Grant only `dynamodb:PutItem` on that table and the SQS consumer permissions
  on the work queue. Do not grant wildcard DynamoDB/SQS actions or resources.
- The CommonJS `handler` export must process every record. Each record body is
  JSON with a nonempty string `id` and a numeric `value`; write that object to
  DynamoDB with `PutCommand`. Use `process.env.TABLE_NAME`.
- Return `{ batchItemFailures: [{ itemIdentifier: messageId }, ...] }` for
  records with invalid input or failed writes, including invalid JSON. Continue
  processing other records and do not retry failed writes within the handler.

Only changes to `lib/stack.ts` and `lambda/handler.js` are submitted. Preserve
their exports and the existing project layout. The dependencies are installed.
You can run `npm run build` and `npm run synth` while working.

This is a code-repair task. Do not deploy resources or call AWS. Synthesis
uses fixed example account context and does not require credentials.
