import { Duration, RemovalPolicy, Stack, StackProps } from "aws-cdk-lib";
import { Construct } from "constructs";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { SqsEventSource } from "aws-cdk-lib/aws-lambda-event-sources";
import * as sqs from "aws-cdk-lib/aws-sqs";
import * as path from "node:path";

export class BenchmarkStack extends Stack {
  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);
    const table = new dynamodb.Table(this, "Records", {
      tableName: "bedrock-bench-records",
      partitionKey: { name: "id", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      removalPolicy: RemovalPolicy.RETAIN,
    });
    const dlq = new sqs.Queue(this, "DeadLetterQueue");
    const queue = new sqs.Queue(this, "WorkQueue", {
      visibilityTimeout: Duration.seconds(180),
      deadLetterQueue: { queue: dlq, maxReceiveCount: 3 },
    });
    const worker = new lambda.Function(this, "Worker", {
      runtime: lambda.Runtime.NODEJS_22_X,
      code: lambda.Code.fromAsset(path.join(__dirname, "../../lambda")),
      handler: "handler.handler",
      timeout: Duration.seconds(30),
      environment: { TABLE_NAME: table.tableName },
    });
    worker.addEventSource(new SqsEventSource(queue, {
      batchSize: 10,
      reportBatchItemFailures: true,
    }));
    table.grant(worker, "dynamodb:PutItem");
  }
}
