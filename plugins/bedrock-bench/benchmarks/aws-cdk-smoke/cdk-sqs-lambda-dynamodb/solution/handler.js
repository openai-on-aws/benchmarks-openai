const { DynamoDBClient } = require("@aws-sdk/client-dynamodb");
const { DynamoDBDocumentClient, PutCommand } = require("@aws-sdk/lib-dynamodb");

const client = DynamoDBDocumentClient.from(new DynamoDBClient({ maxAttempts: 1 }));

exports.handler = async (event) => {
  const batchItemFailures = [];
  for (const record of event.Records) {
    try {
      const item = JSON.parse(record.body);
      if (!item || typeof item.id !== "string" || !item.id.trim()
          || typeof item.value !== "number" || !Number.isFinite(item.value)) {
        throw new Error("Invalid record");
      }
      await client.send(new PutCommand({
        TableName: process.env.TABLE_NAME,
        Item: item,
      }));
    } catch {
      batchItemFailures.push({ itemIdentifier: record.messageId });
    }
  }
  return { batchItemFailures };
};
