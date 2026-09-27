// Defense in depth for the authored local controls. This is not a sandbox for
// arbitrary candidate code. Docker additionally enforces --network=none.
"use strict";
const blocked = [];
Object.defineProperty(globalThis, "__benchOfflineBlocks", { value: blocked });
const deny = (operation) => function () {
  blocked.push(operation);
  const error = new Error(`Offline audit blocked ${operation}`);
  error.code = "BENCH_OFFLINE_BLOCK";
  throw error;
};
for (const [name, methods] of [
  ["node:net", ["connect", "createConnection", "createServer"]],
  ["node:tls", ["connect", "createServer"]],
  ["node:http", ["request", "get", "createServer"]],
  ["node:https", ["request", "get", "createServer"]],
  ["node:dgram", ["createSocket"]],
  ["node:dns", ["lookup", "resolve", "resolve4", "resolve6", "reverse"]],
  ["node:dns/promises", ["lookup", "resolve", "resolve4", "resolve6", "reverse"]],
  ["node:child_process", ["spawn", "spawnSync", "exec", "execSync", "execFile", "execFileSync", "fork"]],
]) {
  const module = require(name);
  for (const method of methods) module[method] = deny(`${name}.${method}`);
}
require("node:net").Socket.prototype.connect = deny("Socket.connect");
globalThis.fetch = deny("fetch");
process.on("exit", () => {
  if (blocked.length) {
    console.error("BENCH_OFFLINE_BLOCK:", blocked.join(", "));
    process.exitCode = 70;
  }
});
