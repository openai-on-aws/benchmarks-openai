const { DynamoDBClient } = require("@aws-sdk/client-dynamodb");
const { DynamoDBDocumentClient, PutCommand } = require("@aws-sdk/lib-dynamodb");

const client = DynamoDBDocumentClient.from(new DynamoDBClient({}));

exports.handler = async (event) => {
  const record = event.Records[0];
  try {
    await client.send(new PutCommand({
      TableName: process.env.TABLE_NAME,
      Item: JSON.parse(record.body),
    }));
  } catch {
    // The current handler drops write failures.
  }
  return { batchItemFailures: [] };
};
