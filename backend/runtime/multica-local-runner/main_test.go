package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/multica-ai/multica/server/pkg/agent"
)

func TestCaseWorkspaceKeepsControlIsolatedAndDoesNotModifySource(t *testing.T) {
	root := t.TempDir()
	source := filepath.Join(root, "source.json")
	original := `{"agents":{"defaults":{},"list":[{"id":"main","workspace":"with-skill"}]}}`
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
	unchanged, _ := os.ReadFile(source)
	if string(unchanged) != original {
		t.Fatal("source configuration modified")
	}
	if _, err := os.Stat(filepath.Join(workspace, "artifacts")); err != nil {
		t.Fatal(err)
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
