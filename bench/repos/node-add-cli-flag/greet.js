#!/usr/bin/env node
// Prints a greeting. Missing: a --loud flag that should uppercase the output.
const args = process.argv.slice(2);
const name = args.find((a) => !a.startsWith("--")) || "world";
console.log(`Hello, ${name}!`);
