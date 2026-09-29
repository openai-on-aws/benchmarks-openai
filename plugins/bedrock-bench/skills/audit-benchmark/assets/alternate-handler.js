const { DynamoDBClient } = require("@aws-sdk/client-dynamodb");
const { DynamoDBDocumentClient, PutCommand } = require("@aws-sdk/lib-dynamodb");
const database = DynamoDBDocumentClient.from(new DynamoDBClient({ maxAttempts: 1 }));

exports.handler = async function (event) {
  const failures = [];
  for (let index = 0; index < event.Records.length; index += 1) {
    const message = event.Records[index];
    try {
      const payload = JSON.parse(message.body);
      if (!payload || typeof payload.id !== "string" || payload.id.trim().length === 0
          || typeof payload.value !== "number" || !Number.isFinite(payload.value)) {
        throw new Error("Invalid input");
      }
      await database.send(new PutCommand({ TableName: process.env.TABLE_NAME, Item: payload }));
    } catch (error) {
      failures.push({ itemIdentifier: message.messageId });
    }
  }
  return { batchItemFailures: failures };
};
