package main

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/multica-ai/multica/server/pkg/agent"
)

func TestRunZCodeParsesDesktopRuntimeStream(t *testing.T) {
	node, err := execLookPathForTest("node")
	if err != nil {
		t.Skip("Node.js is required for the bundled ZCode Desktop runtime")
	}
	workspace := t.TempDir()
	runtimePath := filepath.Join(workspace, "fake-zcode.cjs")
	script := `
if (process.argv.includes("--help")) {
  console.log("--output-format stream-json");
  process.exit(0);
}
console.log(JSON.stringify({type:"part.delta", field:"text", delta:"ZCODE_OK"}));
console.log(JSON.stringify({type:"turn.completed", sessionId:"zcode-session", usage:{inputTokens:3, outputTokens:2}}));
`
	if err := os.WriteFile(runtimePath, []byte(script), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("ZCODE_NODE", node)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	result := runZCode(ctx, runtimePath, "desktop", "hi", workspace, "glm-4.5-air", nil)

	if result.ExitCode != 0 || result.FinalMessage != "ZCODE_OK" {
		t.Fatalf("unexpected ZCode result: %+v", result)
	}
	if result.SessionID != "zcode-session" || result.InputTokens != 3 || result.OutputTokens != 2 {
		t.Fatalf("ZCode telemetry was not parsed: %+v", result)
	}
	if result.Engine != "zcode-desktop" || len(result.Transcript) != 2 {
		t.Fatalf("ZCode transcript was not normalized: %+v", result)
	}
}

func execLookPathForTest(command string) (string, error) {
	return exec.LookPath(command)
}

func TestCaseWorkspaceKeepsControlIsolatedAndDoesNotModifySource(t *testing.T) {
	root := t.TempDir()
	source := filepath.Join(root, "source.json")
	original := `{"agents":{"defaults":{},"entries":{"main":{"workspace":"with-skill"}}}}`
	if err := os.WriteFile(source, []byte(original), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("OPENCLAW_CONFIG_PATH", source)
	workspace := filepath.Join(root, "without-skill")
	if err := prepareCaseWorkspace(workspace, "openclaw"); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(os.Getenv("OPENCLAW_CONFIG_PATH"))
	if err != nil {
		t.Fatal(err)
	}
	var config map[string]any
	if err := json.Unmarshal(data, &config); err != nil {
		t.Fatal(err)
	}
	agents := config["agents"].(map[string]any)
	if agents["defaults"].(map[string]any)["workspace"] != workspace {
		t.Fatal("wrong workspace")
	}
	entries := agents["entries"].(map[string]any)
	if entries["main"].(map[string]any)["workspace"] != workspace {
		t.Fatal("wrong main Agent workspace")
	}
	unchanged, _ := os.ReadFile(source)
	if string(unchanged) != original {
		t.Fatal("source configuration modified")
	}
	if _, err := os.Stat(filepath.Join(workspace, "artifacts")); err != nil {
		t.Fatal(err)
	}
	guidance, err := os.ReadFile(filepath.Join(workspace, "AGENTS.md"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(guidance), "openclaw agent exec --state-dir .agent-eval/subagent-state --cwd . --json") {
		t.Fatalf("subagent guidance missing: %s", guidance)
	}
	if _, err := os.Stat(filepath.Join(workspace, ".agent-eval", "subagent-state")); err != nil {
		t.Fatal(err)
	}
}

func TestOpenclawSubagentGuidanceIsIdempotent(t *testing.T) {
	workspace := t.TempDir()
	if err := installOpenclawSubagentGuidance(workspace); err != nil {
		t.Fatal(err)
	}
	if err := installOpenclawSubagentGuidance(workspace); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(workspace, "AGENTS.md"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Count(string(data), "## Agent Eval subagent transport") != 1 {
		t.Fatalf("guidance duplicated: %s", data)
	}
}

func TestJustdoSubagentGuidanceUsesNativeTransport(t *testing.T) {
	workspace := t.TempDir()
	t.Setenv("AGENT_EVAL_REQUESTED_AGENT", "justdo")
	t.Setenv("AGENT_EVAL_SUBAGENT_MODEL", "glm-4.5-air")
	if err := installOpenclawSubagentGuidance(workspace); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(workspace, "AGENTS.md"))
	if err != nil {
		t.Fatal(err)
	}
	text := string(data)
	if !strings.Contains(text, "Use sessions_spawn") || !strings.Contains(text, "sessions_yield") {
		t.Fatalf("JustDo native subagent guidance missing: %s", text)
	}
	for _, required := range []string{`runtime="subagent"`, `context="isolated"`, "Omit agentId and model", `Never use context="fork"`, "keep only one child active at a time"} {
		if !strings.Contains(text, required) {
			t.Fatalf("JustDo isolated child guidance missing %q: %s", required, text)
		}
	}
	if strings.Contains(text, "agent-eval check-agent") || strings.Contains(text, "Do not call sessions_spawn") {
		t.Fatalf("obsolete evaluator-child guidance remains: %s", text)
	}
}

func TestExactPromptSingleMessageIsUnchanged(t *testing.T) {
	want := "Generate the requested schematic."
	got := exactPrompt([]inputMessage{{Role: "user", Content: want}})
	if got != want {
		t.Fatalf("prompt changed: got %q want %q", got, want)
	}
	for _, forbidden := range []string{"Multica", "issue get", "coding agent", "workspace"} {
		if strings.Contains(got, forbidden) {
			t.Fatalf("prompt contains Multica default text %q", forbidden)
		}
	}
}

func TestExactPromptPreservesMultiMessageOrder(t *testing.T) {
	got := exactPrompt([]inputMessage{
		{Role: "system", Content: "rules"},
		{Role: "user", Content: "task"},
	})
	if got != "[SYSTEM]\nrules\n\n[USER]\ntask" {
		t.Fatalf("unexpected serialization: %q", got)
	}
}

func TestStatusFramesAreNotAssistantTranscriptMessages(t *testing.T) {
	if includeInTranscript(agent.Message{Type: agent.MessageStatus, Status: "running"}) {
		t.Fatal("status frame must not be treated as assistant output")
	}
	if !includeInTranscript(agent.Message{Type: agent.MessageText, Content: "done"}) {
		t.Fatal("text response must remain in the transcript")
	}
}

func TestFinalOutputRemainsLastAfterTelemetry(t *testing.T) {
	got := appendTerminalMessages(nil, map[string]any{"input_tokens": 3}, "MARKER_OK")
	if len(got) != 2 {
		t.Fatalf("unexpected terminal message count: %d", len(got))
	}
	if !strings.HasPrefix(got[0].Content, "AGENT_EVAL_TELEMETRY_JSON:") {
		t.Fatalf("telemetry marker missing: %q", got[0].Content)
	}
	if got[1].Content != "MARKER_OK" {
		t.Fatalf("final output is not terminal: %q", got[1].Content)
	}
}

func TestCollectArtifactsIncludesSchematicOutDirectory(t *testing.T) {
	workspace := t.TempDir()
	path := filepath.Join(workspace, "out", "layout", "S1.json")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	got := collectArtifacts(workspace)
	if len(got.Files) != 1 || got.Files[0].Path != "out/layout/S1.json" {
		t.Fatalf("schematic out artifact not collected: %#v", got.Files)
	}
}
