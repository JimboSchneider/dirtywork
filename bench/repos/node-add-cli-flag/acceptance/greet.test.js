// Acceptance check for node-add-cli-flag. Run from the repo root
// (`cd <repo> && node <this file>`): the subject is resolved from the CURRENT
// WORKING DIRECTORY, so this file works both from the fixture dir on the host
// and mounted read-only at /acceptance with /work as the cwd. Plain asserts --
// no test-runner CLI flags, so it behaves identically on any Node >= 18.
const assert = require("node:assert");
const { execFileSync } = require("node:child_process");
const path = require("node:path");

const GREET = path.join(process.cwd(), "greet.js");

const plain = execFileSync("node", [GREET, "Ada"]).toString().trim();
assert.strictEqual(plain, "Hello, Ada!");

const loud = execFileSync("node", [GREET, "Ada", "--loud"]).toString().trim();
assert.strictEqual(loud, "HELLO, ADA!");

console.log("PASS");
