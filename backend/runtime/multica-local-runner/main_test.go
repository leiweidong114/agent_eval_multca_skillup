package main

import (
	"strings"
	"testing"
)

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
