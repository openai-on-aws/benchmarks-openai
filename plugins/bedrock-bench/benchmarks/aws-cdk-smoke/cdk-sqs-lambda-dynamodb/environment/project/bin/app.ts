import { App } from "aws-cdk-lib";
import { BenchmarkStack } from "../lib/stack";

const app = new App();
new BenchmarkStack(app, "Benchmark", {
  env: { account: "123456789012", region: "us-east-1" },
});
app.synth();
