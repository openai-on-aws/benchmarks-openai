// Launch fixed build/synth steps and the byte-for-byte packaged verifier.
// No project scripts, cdk.json commands, user configuration, or traces execute.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");
const [phase, project, modules, verifier, manifest] = process.argv.slice(2);
const marker = "BEDROCK_BENCH_AUDIT_RESULT=";
let verdict = null;
const errors = [];
const printError = console.error.bind(console);
console.error = (...args) => {
  for (const item of args) if (item instanceof Error) errors.push(item);
  printError(...args);
};
function classify(error) {
  if (error.code === "BENCH_OFFLINE_BLOCK") return "infrastructure_error";
  if (error.code === "ERR_ASSERTION" || error.name === "AssertionError") return "reject";
  // Only candidate syntax/load failures are task failures. Missing SDK modules,
  // bad verifier paths, and unexpected TypeErrors are infrastructure errors.
  const handler = path.join(project, "lambda/handler.js");
  if (error instanceof SyntaxError && String(error.stack).includes(handler)) return "reject";
  if (error.code === "MODULE_NOT_FOUND" && String(error.message).startsWith(`Cannot find module '${handler}'`)) return "reject";
  return "infrastructure_error";
}
process.on("uncaughtException", (error) => {
  errors.push(error);
  printError(error);
  process.exitCode = 1;
});
process.on("unhandledRejection", (error) => {
  errors.push(error instanceof Error ? error : new Error(String(error)));
  printError(error);
  process.exitCode = 1;
});
process.on("exit", () => {
  if (phase !== "verify" && phase !== "probe") return;
  const offline = globalThis.__benchOfflineBlocks || [];
  let observed;
  if (offline.length) observed = "infrastructure_error";
  else if (errors.length) observed = errors.every(e => classify(e) === "reject") ? "reject" : "infrastructure_error";
  else if (process.exitCode && process.exitCode !== 0) observed = "infrastructure_error";
  else observed = "accept";
  const result = {
    phase, observed, verifier_invoked: phase === "verify" && verdict === "invoked",
    errors: errors.map(e => ({ name: e.name, code: e.code || null, message: e.message })),
    offline_blocks: offline, runtime: verdict && typeof verdict === "object" ? verdict : null,
  };
  fs.writeSync(1, marker + JSON.stringify(result) + "\n");
});

if (phase === "probe") {
  if (Number(process.versions.node.split(".")[0]) < 22) throw new Error("Node.js 22+ is required for these controls");
  const expected = JSON.parse(fs.readFileSync(manifest, "utf8"));
  const versions = {};
  for (const [name, version] of Object.entries({ ...expected.dependencies, ...expected.devDependencies })) {
    const actual = JSON.parse(fs.readFileSync(path.join(modules, name, "package.json"), "utf8")).version;
    if (actual !== version) throw new Error(`Dependency ${name}: expected ${version}, found ${actual}`);
    versions[name] = actual;
  }
  const req = createRequire(path.join(modules, "../package.json"));
  for (const name of ["aws-cdk-lib", "aws-cdk-lib/assertions", "@aws-sdk/client-dynamodb", "@aws-sdk/lib-dynamodb"]) req(name);
  verdict = { node: process.version, packages: versions };
} else if (phase === "build" || phase === "synth" || phase === "verify") {
  const link = path.join(project, "node_modules");
  if (!fs.existsSync(link)) fs.symlinkSync(modules, link, "dir");
  if (fs.realpathSync(link) !== fs.realpathSync(modules)) throw new Error("Unexpected candidate dependency path");
  process.chdir(project);
  if (phase === "build") {
    const compiler = path.join(modules, "typescript/bin/tsc");
    process.argv = [process.execPath, compiler, "--project", path.join(project, "tsconfig.json")];
    require(compiler);
  } else if (phase === "synth") {
    process.env.CDK_OUTDIR = path.join(project, "cdk.out");
    process.env.CDK_CONTEXT_JSON = "{}";
    require(path.join(project, "dist/bin/app.js"));
  } else {
    process.env.BENCH_PROJECT = project;
    verdict = "invoked";
    require(verifier);
  }
} else {
  throw new Error("Unknown fixed audit phase");
}
