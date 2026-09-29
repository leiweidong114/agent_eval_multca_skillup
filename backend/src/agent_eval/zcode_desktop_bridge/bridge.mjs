#!/usr/bin/env node
import { randomUUID } from "node:crypto";
import {
  closeSync,
  copyFileSync,
  existsSync,
  mkdirSync,
  openSync,
  readFileSync,
  readSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { homedir, tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { spawn } from "node:child_process";
import { MessageChannel, Worker } from "node:worker_threads";

const HELP = `ZCode Desktop task bridge

Options:
  --prompt <text>
  --cwd <path>
  --mode <mode>
  --output-format stream-json
  --no-color
`;

function parseArgs(argv) {
  const parsed = { mode: "yolo", outputFormat: "stream-json" };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--help" || value === "-h") parsed.help = true;
    else if (value === "--no-color") parsed.noColor = true;
    else if (["--prompt", "-p", "--cwd", "--mode", "--output-format"].includes(value)) {
      const key = {
        "--prompt": "prompt",
        "-p": "prompt",
        "--cwd": "cwd",
        "--mode": "mode",
        "--output-format": "outputFormat",
      }[value];
      parsed[key] = argv[++index];
    } else if (value === "--json") parsed.outputFormat = "stream-json";
  }
  return parsed;
}

function findDesktopRuntime() {
  const configured = [
    process.env.AGENT_EVAL_ZCODE_DESKTOP_RUNTIME,
    process.env.ZCODE_DESKTOP_CLI_EXECUTABLE,
  ].filter(Boolean);
  const candidates = [
    ...configured,
    join(process.env.LOCALAPPDATA ?? "", "Programs", "ZCode", "resources", "glm", "zcode.cjs"),
    join(process.env.LOCALAPPDATA ?? "", "ZCode", "resources", "glm", "zcode.cjs"),
    join(process.env.ProgramFiles ?? "", "ZCode", "resources", "glm", "zcode.cjs"),
  ];
  const runtime = candidates.map((item) => resolve(item)).find(existsSync);
  if (!runtime) {
    throw new Error(
      "ZCode Desktop runtime was not found; configure ZCODE_DESKTOP_CLI_EXECUTABLE",
    );
  }
  return runtime;
}

function readAsarHeader(asarPath) {
  const fd = openSync(asarPath, "r");
  try {
    const prefix = Buffer.alloc(16);
    readSync(fd, prefix, 0, prefix.length, 0);
    const headerSize = prefix.readUInt32LE(4);
    const jsonSize = prefix.readUInt32LE(12);
    const json = Buffer.alloc(jsonSize);
    readSync(fd, json, 0, jsonSize, 16);
    return { fd, files: JSON.parse(json.toString("utf8")), dataOffset: 8 + headerSize };
  } catch (error) {
    closeSync(fd);
    throw error;
  }
}

function extractTree(asarPath, sourceSegments, destinationRoot) {
  const header = readAsarHeader(asarPath);
  try {
    let entry = header.files;
    for (const segment of sourceSegments) entry = entry.files?.[segment];
    if (!entry?.files) throw new Error(`ASAR tree not found: ${sourceSegments.join("/")}`);
    const walk = (node, relativeSegments) => {
      for (const [name, child] of Object.entries(node.files ?? {})) {
        const targetSegments = [...relativeSegments, name];
        const target = join(destinationRoot, ...targetSegments);
        if (child.files) {
          mkdirSync(target, { recursive: true });
          walk(child, targetSegments);
          continue;
        }
        if (child.unpacked) {
          const unpackedSource = join(
            dirname(asarPath),
            "app.asar.unpacked",
            ...targetSegments,
          );
          if (!existsSync(unpackedSource)) {
            throw new Error(`Missing unpacked host file: ${targetSegments.join("/")}`);
          }
          mkdirSync(dirname(target), { recursive: true });
          copyFileSync(unpackedSource, target);
          continue;
        }
        const size = Number(child.size ?? 0);
        const bytes = Buffer.alloc(size);
        readSync(header.fd, bytes, 0, size, header.dataOffset + Number(child.offset ?? 0));
        mkdirSync(dirname(target), { recursive: true });
        writeFileSync(target, bytes);
      }
    };
    walk(entry, sourceSegments);
  } finally {
    closeSync(header.fd);
  }
}

function prepareHostBundle(resourcesPath) {
  const asarPath = join(resourcesPath, "app.asar");
  const stat = statSync(asarPath);
  const cacheRoot = join(
    process.env.LOCALAPPDATA ?? tmpdir(),
    "agent-eval",
    "zcode-desktop-bridge",
    `${stat.size}-${Math.trunc(stat.mtimeMs)}`,
  );
  const hostIndex = join(cacheRoot, "out", "host", "index.js");
  const dependencyMarker = join(cacheRoot, ".bridge-dependencies-ready");
  if (!existsSync(hostIndex) || !existsSync(dependencyMarker)) {
    mkdirSync(cacheRoot, { recursive: true });
    writeFileSync(join(cacheRoot, "package.json"), '{"type":"module"}\n', "utf8");
    if (!existsSync(hostIndex)) extractTree(asarPath, ["out", "host"], cacheRoot);
    for (const packageName of [
      "node-forge",
      "ssh2",
      "undici",
      "ws",
      "yaml",
      "yauzl",
      "yazl",
      "asn1",
      "bcrypt-pbkdf",
      "tweetnacl",
      "safer-buffer",
      "pend",
      "buffer-crc32",
    ]) {
      extractTree(asarPath, ["node_modules", packageName], cacheRoot);
    }
    writeFileSync(dependencyMarker, "ok\n", "utf8");
  }
  return { cacheRoot, hostIndex };
}

function emit(event) {
  process.stdout.write(`${JSON.stringify(event)}\n`);
}

function textFromContent(value) {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(textFromContent).join("");
  if (!value || typeof value !== "object") return "";
  if (typeof value.text === "string") return value.text;
  if (typeof value.content === "string") return value.content;
  if (value.content !== undefined) return textFromContent(value.content);
  if (value.parts !== undefined) return textFromContent(value.parts);
  return "";
}

function assistantText(snapshot) {
  const found = [];
  const seen = new Set();
  const visit = (value) => {
    if (!value || typeof value !== "object" || seen.has(value)) return;
    seen.add(value);
    if (String(value.role ?? "").toLowerCase() === "assistant") {
      const text = textFromContent(value.content ?? value.parts ?? value.text);
      if (text.trim()) found.push(text);
    }
    for (const child of Object.values(value)) visit(child);
  };
  visit(snapshot);
  return found.at(-1)?.trim() ?? "";
}

function findNumber(value, names) {
  if (!value || typeof value !== "object") return 0;
  for (const name of names) {
    if (Number.isFinite(value[name])) return Number(value[name]);
  }
  for (const child of Object.values(value)) {
    const result = findNumber(child, names);
    if (result > 0) return result;
  }
  return 0;
}

function taskStatus(snapshot) {
  return String(
    snapshot?.meta?.status ?? snapshot?.session?.status ?? snapshot?.status ?? "",
  ).toLowerCase();
}

function containsTaskId(value, taskId, seen = new Set()) {
  if (!value || typeof value !== "object" || seen.has(value)) return false;
  seen.add(value);
  if (value.taskId === taskId || value.sessionId === taskId) return true;
  return Object.values(value).some((child) => containsTaskId(child, taskId, seen));
}

async function waitForDesktopTaskIndex(taskService, workspacePath, taskId) {
  const deadline = Date.now() + 15_000;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const tasks = await taskService.listTasks({ workspacePath });
      if (containsTaskId(tasks, taskId)) return;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 500));
  }
  throw new Error(
    `ZCode task ${taskId} was not persisted to the desktop index${
      lastError ? `: ${lastError}` : ""
    }`,
  );
}

function captureDesktopProviderConfig(desktopDataDir) {
  const target = join(desktopDataDir, "provider_config.json");
  mkdirSync(desktopDataDir, { recursive: true });
  const previous = existsSync(target) ? readFileSync(target) : null;
  return () => {
    if (previous === null) {
      if (existsSync(target)) unlinkSync(target);
    } else {
      writeFileSync(target, previous);
    }
    process.stderr.write(
      `[zcode-desktop-bridge] restored provider config in ${target}\n`,
    );
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    process.stdout.write(HELP);
    return;
  }
  if (!args.prompt?.trim()) throw new Error("--prompt is required");
  const cwd = resolve(args.cwd || process.cwd());
  const desktopRuntime = findDesktopRuntime();
  const resourcesPath = resolve(dirname(desktopRuntime), "..");
  const { cacheRoot, hostIndex } = prepareHostBundle(resourcesPath);
  const desktopDataDir = resolve(
    process.env.AGENT_EVAL_ZCODE_DESKTOP_DATA_DIR ?? join(homedir(), ".zcode", "v2"),
  );
  const restoreDesktopProviderConfig = captureDesktopProviderConfig(desktopDataDir);
  const worker = new Worker(new URL("./host_worker.mjs", import.meta.url), {
    stdout: true,
    stderr: true,
    workerData: {
      desktopDataDir,
      zcodeDataBaseDir: resolve(desktopDataDir, "..", ".."),
      resourcesPath,
      hostIndexUrl: pathToFileURL(hostIndex).href,
    },
  });
  worker.stdout.pipe(process.stderr);
  worker.stderr.pipe(process.stderr);
  const { port1, port2 } = new MessageChannel();
  let readyResolve;
  let readyReject;
  const ready = new Promise((resolveReady, rejectReady) => {
    readyResolve = resolveReady;
    readyReject = rejectReady;
  });
  worker.on("error", readyReject);
  worker.on("message", (envelope) => {
    const message = envelope?.__electronData ?? envelope;
    if (message?.type === "database-startup-state") {
      if (message.state?.phase === "ready") readyResolve(message.state);
      if (message.state?.phase === "failed") {
        readyReject(new Error(`ZCode database startup failed: ${message.state.errorCode}`));
      }
    }
  });
  worker.postMessage(
    {
      __electronData: {
        type: "init-local",
        databaseStartupId: randomUUID(),
        deviceMid: randomUUID(),
        workspacePath: cwd,
        agentWarmupTargets: [{ workspacePath: cwd }],
        runtimeProcessEnvPatch: Object.fromEntries(
          Object.entries(process.env).filter(
            ([key, value]) => /^[A-Za-z_][A-Za-z0-9_]*$/.test(key) && value !== undefined,
          ),
        ),
        agentSpawnFallbackCwd: cwd,
        zcodeBuiltinProviderConfigFilePath:
          process.env.ZCODE_BUILTIN_PROVIDER_CONFIG_FILE ??
          join(resourcesPath, "config", "provider", "zcode-builtin.json"),
      },
      __electronPorts: [port2],
    },
    [port2],
  );

  const rpcModule = await import(
    pathToFileURL(join(cacheRoot, "out", "host", "chunk-UHHNTW2R.js")).href
  );
  const channelsModule = await import(
    pathToFileURL(join(cacheRoot, "out", "host", "chunk-EBZ6RJTQ.js")).href
  );
  const transport = new rpcModule.e(port1);
  const channelClient = new rpcModule.g(transport);
  const taskService = rpcModule.h.toService(
    channelClient.getChannel(channelsModule.A.channelName),
  );
  const providerSettingsService = rpcModule.h.toService(
    channelClient.getChannel(channelsModule.b.channelName),
  );
  let temporaryProviderId = null;

  try {
    await Promise.race([
      ready,
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error("ZCode Desktop host startup timed out")), 45_000),
      ),
    ]);
    const modelId =
      process.env.AGENT_EVAL_PROVIDER_MODEL ?? process.env.LITELLM_MODEL ?? "";
    const providerConfigPath = process.env.ZCODE_PERSONAL_PROVIDER_CONFIG_FILE;
    if (!providerConfigPath || !existsSync(providerConfigPath)) {
      throw new Error("ZCODE_PERSONAL_PROVIDER_CONFIG_FILE is required for desktop-ui");
    }
    const requestedConfig = JSON.parse(readFileSync(providerConfigPath, "utf8")).config;
    const requestedProvider = requestedConfig?.providerConfigRules?.providerRules?.[0];
    const requestedModel = requestedConfig?.modelConfigRules?.manualProviderModelRules?.find(
      (rule) => rule.modelId === modelId,
    );
    if (!requestedProvider?.config || !requestedModel?.config) {
      throw new Error(`ZCode provider configuration does not define model ${modelId}`);
    }
    const {
      group: _group,
      personalModelIds: _personalModelIds,
      modelOrder: _modelOrder,
      ...initialConfig
    } = requestedProvider.config;
    const createdProvider = await providerSettingsService.createPersonalProvider({
      providerName: requestedProvider.providerName ?? "Agent Eval LiteLLM",
      initialConfig,
    });
    temporaryProviderId = createdProvider.providerId;
    await providerSettingsService.addPersonalModel(
      temporaryProviderId,
      modelId,
      requestedModel.config,
      false,
    );
    process.stderr.write(
      `[zcode-desktop-bridge] provisioned temporary provider ${temporaryProviderId}/${modelId}\n`,
    );
    const modelSelection = modelId
      ? {
          providerId: temporaryProviderId,
          modelId,
          options: { reasoningLevel: "high" },
        }
      : undefined;
    const task = await taskService.createTask({
      workspacePath: cwd,
      mode: args.mode,
      modelSelection,
      v4Create: true,
    });
    const taskId = task.taskId;
    emit({ type: "session.created", sessionId: taskId, taskId, model: modelId });

    const desktopExecutable = resolve(resourcesPath, "..", "ZCode.exe");
    if (existsSync(desktopExecutable) && process.env.AGENT_EVAL_ZCODE_OPEN_DESKTOP !== "0") {
      const opener = spawn(desktopExecutable, ["--open-workspace", cwd], {
        detached: true,
        stdio: "ignore",
        windowsHide: true,
      });
      opener.unref();
    }

    await taskService.sendPrompt({
      taskId,
      workspacePath: cwd,
      content: args.prompt,
      attachments: [],
      traceId: randomUUID(),
      queryId: randomUUID(),
      messageId: randomUUID(),
      clientId: "agent-eval-desktop-ui",
      clientMode: "desktop-continuous",
      modelSelection,
    });

    let emitted = "";
    const startedAt = Date.now();
    for (;;) {
      const snapshot = await taskService.getTaskSnapshot({
        taskId,
        workspacePath: cwd,
        clientMode: "desktop-continuous",
        messageLimit: 500,
      });
      const status = taskStatus(snapshot);
      const text = assistantText(snapshot);
      if (text && text !== emitted) {
        const delta = text.startsWith(emitted) ? text.slice(emitted.length) : text;
        emit({ type: "part.delta", field: "text", delta, sessionId: taskId });
        emitted = text;
      }
      const failed = ["failed", "error", "cancelled", "canceled"].includes(status);
      const terminal = ["completed", "complete", "idle", "ready"].includes(status);
      if (failed) throw new Error(`ZCode task ended with status ${status}`);
      // Assistant text can remain unchanged while a tool call or a long-running
      // shell command is still active.  Treating a few identical snapshots as
      // completion truncates the task at its first progress message.  The task
      // index is updated from ZCode's terminal protocol event, so only that
      // explicit terminal state is authoritative.
      if (text && terminal) {
        await waitForDesktopTaskIndex(taskService, cwd, taskId);
        emit({
          type: "turn.completed",
          sessionId: taskId,
          taskId,
          finalMessage: text,
          usage: {
            inputTokens: findNumber(snapshot, ["inputTokens", "input_tokens", "prompt_tokens"]),
            outputTokens: findNumber(snapshot, ["outputTokens", "output_tokens", "completion_tokens"]),
          },
        });
        break;
      }
      if (Date.now() - startedAt > 3_600_000) throw new Error("ZCode task timed out");
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 500));
    }
  } finally {
    if (temporaryProviderId) {
      try {
        await providerSettingsService.deletePersonalProvider(temporaryProviderId);
      } catch (error) {
        process.stderr.write(
          `[zcode-desktop-bridge] failed to remove temporary provider: ${error}\n`,
        );
      }
    }
    port1.close();
    worker.postMessage({ __electronData: { type: "dispose" }, __electronPorts: [] });
    await Promise.race([
      new Promise((resolveExit) => worker.once("exit", resolveExit)),
      new Promise((resolveExit) => setTimeout(resolveExit, 5_000)),
    ]);
    await worker.terminate();
    restoreDesktopProviderConfig();
  }
}

main().catch((error) => {
  process.stderr.write(`${error?.stack ?? error}\n`);
  process.exitCode = 1;
});
