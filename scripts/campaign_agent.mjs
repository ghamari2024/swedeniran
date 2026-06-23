#!/usr/bin/env node
/**
 * Local Cursor agent runner for CRM campaign site generation.
 * Invoked from site_agent.py with a JSON payload on argv[2].
 */
import { Agent, CursorAgentError } from "@cursor/sdk";
import fs from "fs";
import path from "path";

function fail(message, code = 1) {
  process.stderr.write(String(message) + "\n");
  process.exit(code);
}

function parsePayload() {
  const raw = process.argv[2];
  if (!raw) fail("missing payload argument");
  try {
    return JSON.parse(raw);
  } catch (e) {
    fail("invalid JSON payload: " + e.message);
  }
}

async function runGenerate(workDir, prompt, model, apiKey) {
  fs.mkdirSync(workDir, { recursive: true });
  await using agent = await Agent.create({
    apiKey,
    model: { id: model },
    local: { cwd: workDir, settingSources: [] },
  });
  const run = await agent.send(prompt);
  const result = await run.wait();
  return { agentId: agent.agentId, status: result.status };
}

async function runRefine(workDir, prompt, model, apiKey, agentId) {
  fs.mkdirSync(workDir, { recursive: true });
  try {
    await using agent = await Agent.resume(agentId, {
      apiKey,
      model: { id: model },
      local: { cwd: workDir, settingSources: [] },
    });
    const run = await agent.send(prompt);
    const result = await run.wait();
    return { agentId: agent.agentId, status: result.status };
  } catch (err) {
    if (err instanceof CursorAgentError) {
      return runGenerate(workDir, prompt, model, apiKey);
    }
    throw err;
  }
}

async function main() {
  const payload = parsePayload();
  const apiKey = (process.env.CURSOR_API_KEY || "").trim();
  if (!apiKey) fail("CURSOR_API_KEY is not set");

  const workDir = path.resolve(payload.workDir || ".");
  const prompt = String(payload.prompt || "");
  const model = String(payload.model || "composer-2");
  const action = payload.action || "generate";

  if (!prompt.trim()) fail("prompt is required");

  let out;
  if (action === "refine" && payload.agentId) {
    out = await runRefine(workDir, prompt, model, apiKey, payload.agentId);
  } else {
    out = await runGenerate(workDir, prompt, model, apiKey);
  }

  process.stdout.write(JSON.stringify(out));
}

main().catch((err) => {
  if (err instanceof CursorAgentError) {
    fail("startup failed: " + err.message, 1);
  }
  fail(err?.message || String(err), 2);
});
