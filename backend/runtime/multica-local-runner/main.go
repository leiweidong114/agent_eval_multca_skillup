// multica-eval-runtime executes one skill-up SessionInput through Multica's
// open-source Agent backend package. It intentionally does not import or run
// the Multica server, daemon, authentication, database, task prompt builder,
// or runtime brief injector.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/multica-ai/multica/server/pkg/agent"
)

type stringList []string

func (s *stringList) String() string { return strings.Join(*s, " ") }
func (s *stringList) Set(value string) error {
	*s = append(*s, value)
	return nil
}

type inputMessage struct {
	Role    string `json:"role"`
	Content any    `json:"content"`
}

type sessionInput struct {
	Messages  []inputMessage    `json:"messages"`
	Workspace string            `json:"workspace"`
	CaseID    string            `json:"case_id"`
	Variant   string            `json:"variant"`
	Kwargs    map[string]string `json:"kwargs"`
}

type transcriptMessage struct {
	Role       string          `json:"role"`
	Content    string          `json:"content,omitempty"`
	Turn       int             `json:"turn,omitempty"`
	ToolCall   *toolCallInfo   `json:"tool_call,omitempty"`
	ToolResult *toolResultInfo `json:"tool_result,omitempty"`
}

type toolCallInfo struct {
	ID        string         `json:"id,omitempty"`
	Name      string         `json:"name"`
	Arguments map[string]any `json:"arguments,omitempty"`
}

type toolResultInfo struct {
	CallID     string `json:"call_id,omitempty"`
	Status     string `json:"status,omitempty"`
	Content    any    `json:"content,omitempty"`
	DurationMs int64  `json:"duration_ms,omitempty"`
}

type artifactFile struct {
	Name string `json:"name"`
	Path string `json:"path"`
}

type artifactSet struct {
	Files []artifactFile `json:"files,omitempty"`
}

type sessionResult struct {
	Engine       string              `json:"engine,omitempty"`
	Model        string              `json:"model,omitempty"`
	SessionID    string              `json:"session_id,omitempty"`
	ExitCode     int                 `json:"exit_code"`
	DurationMs   int64               `json:"duration_ms"`
	FinalMessage string              `json:"final_message"`
	Turns        int                 `json:"turns"`
	InputTokens  int64               `json:"input_tokens"`
	OutputTokens int64               `json:"output_tokens"`
	Transcript   []transcriptMessage `json:"transcript"`
	Artifacts    artifactSet         `json:"artifacts"`
	Stderr       string              `json:"stderr"`
}

func normalizeAgent(value string) string {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "claude_code":
		return "claude"
	case "qwen_code":
		return "qwen"
	case "qodercli":
		return "qoder"
	case "justdo":
		return "openclaw"
	default:
		return strings.ToLower(strings.TrimSpace(value))
	}
}

func contentText(value any) string {
	switch item := value.(type) {
	case string:
		return item
	default:
		encoded, _ := json.Marshal(item)
		return string(encoded)
	}
}

// exactPrompt performs transport-only serialization. A single message is sent
// byte-for-byte. Multiple messages receive role delimiters so their order and
// role survive the string-only Multica Backend interface. No behavioral or
// platform instruction is added.
func exactPrompt(messages []inputMessage) string {
	if len(messages) == 0 {
		return ""
	}
	if len(messages) == 1 {
		return contentText(messages[0].Content)
	}
	var builder strings.Builder
	for index, message := range messages {
		if index > 0 {
			builder.WriteString("\n\n")
		}
		builder.WriteString("[")
		builder.WriteString(strings.ToUpper(message.Role))
		builder.WriteString("]\n")
		builder.WriteString(contentText(message.Content))
	}
	return builder.String()
}

func collectArtifacts(workspace string) artifactSet {
	result := artifactSet{}
	for _, directory := range []string{"out", "output", "outputs", "artifacts"} {
		root := filepath.Join(workspace, directory)
		_ = filepath.WalkDir(root, func(path string, entry os.DirEntry, err error) error {
			if err != nil || entry == nil || entry.IsDir() {
				return nil
			}
			relative, relErr := filepath.Rel(workspace, path)
			if relErr == nil && relative != "." && !strings.HasPrefix(relative, "..") {
				relative = filepath.ToSlash(relative)
				result.Files = append(result.Files, artifactFile{Name: relative, Path: relative})
			}
			return nil
		})
	}
	return result
}

// Bind OpenClaw/JustDo to the exact Skill-Up case workspace, including the
// no-Skill control. Never reuse the configured with-Skill runtime workspace.
func prepareCaseWorkspace(workspace, provider string) error {
	if err := os.MkdirAll(filepath.Join(workspace, "artifacts"), 0o755); err != nil {
		return err
	}
	if provider != "openclaw" {
		return nil
	}
	source := os.Getenv("OPENCLAW_CONFIG_PATH")
	if source == "" {
		return nil
	}
	data, err := os.ReadFile(source)
	if err != nil {
		return err
	}
	var config map[string]any
	if err := json.Unmarshal(data, &config); err != nil {
		return err
	}
	agents, ok := config["agents"].(map[string]any)
	if !ok {
		return fmt.Errorf("OpenClaw config has no agents mapping")
	}
	defaults, ok := agents["defaults"].(map[string]any)
	if !ok {
		defaults = map[string]any{}
		agents["defaults"] = defaults
	}
	defaults["workspace"] = workspace
	if entries, ok := agents["entries"].(map[string]any); ok {
		for _, item := range entries {
			if a, ok := item.(map[string]any); ok {
				a["workspace"] = workspace
			}
		}
	}
	// Keep accepting older snapshots while setup upgrades the generated
	// configuration to the keyed agents.entries schema.
	if list, ok := agents["list"].([]any); ok {
		for _, item := range list {
			if a, ok := item.(map[string]any); ok {
				a["workspace"] = workspace
			}
		}
	}
	encoded, err := json.Marshal(config)
	if err != nil {
		return err
	}
	path := filepath.Join(workspace, "inputs", "openclaw-case-config.json")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	if err := os.WriteFile(path, encoded, 0o600); err != nil {
		return err
	}
	if err := os.Setenv("OPENCLAW_CONFIG_PATH", path); err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Join(workspace, ".agent-eval", "subagent-state"), 0o755); err != nil {
		return err
	}
	return installOpenclawSubagentGuidance(workspace)
}

const openclawSubagentGuidanceTemplate = `

## Agent Eval subagent transport

This evaluation runs OpenClaw in embedded mode. Do not call sessions_spawn: it
requires a separate Gateway reply dispatcher that is intentionally absent.
When a task needs delegation, call the exec tool with:

    %s

The child process inherits OPENCLAW_CONFIG_PATH, OPENCLAW_STATE_DIR, and the
run-scoped LiteLLM key, so it uses the same configured model and is attributed
to the same evaluation trace. Read the JSON result, wait for it to complete,
and incorporate the child result into the parent answer. This is a real
isolated child Agent process; do not simulate its response in the parent.

When calling exec, set yieldMs to 120000. If it still becomes a background
process, call process.poll once with timeout 120000 instead of repeatedly
requesting logs.
`

func installOpenclawSubagentGuidance(workspace string) error {
	path := filepath.Join(workspace, "AGENTS.md")
	data, err := os.ReadFile(path)
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	if strings.Contains(string(data), "## Agent Eval subagent transport") {
		return nil
	}
	command := `openclaw agent exec --state-dir .agent-eval/subagent-state --cwd . --json "<complete subtask prompt>"`
	if strings.EqualFold(strings.TrimSpace(os.Getenv("AGENT_EVAL_REQUESTED_AGENT")), "justdo") {
		model := strings.TrimSpace(os.Getenv("AGENT_EVAL_SUBAGENT_MODEL"))
		if model == "" {
			model = "<configured LiteLLM model>"
		}
		command = fmt.Sprintf(
			`agent-eval check-agent --agent justdo --model %q --prompt "<complete subtask prompt>" --timeout 120 --max-turns 4 --no-database-verify`,
			model,
		)
	}
	guidance := fmt.Sprintf(openclawSubagentGuidanceTemplate, command)
	return os.WriteFile(path, append(data, []byte(guidance)...), 0o644)
}

func writeResult(path string, result sessionResult) error {
	encoded, err := json.Marshal(result)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, encoded, 0o644)
}

func appendTerminalMessages(
	transcript []transcriptMessage,
	telemetry map[string]any,
	finalOutput string,
) []transcriptMessage {
	if encoded, marshalErr := json.Marshal(telemetry); marshalErr == nil {
		transcript = append(transcript, transcriptMessage{
			Role: "assistant", Turn: 1, Content: "AGENT_EVAL_TELEMETRY_JSON:" + string(encoded),
		})
	}
	// Skill-Up grades the last assistant transcript entry. Keep telemetry in
	// the transcript for process scoring, but make the Agent's actual answer
	// terminal so result assertions see the response instead of the marker.
	if strings.TrimSpace(finalOutput) != "" {
		transcript = append(transcript, transcriptMessage{
			Role: "assistant", Turn: 1, Content: finalOutput,
		})
	}
	return transcript
}

func includeInTranscript(message agent.Message) bool {
	// Runtime status frames are transport progress, not assistant output. Keeping
	// them in the transcript lets Skill-Up select the first "running" frame as
	// the case response even when SessionResult.FinalMessage is correct.
	return message.Type != agent.MessageStatus
}

func main() {
	var inputPath, outputPath, agentName, model, executable string
	var timeoutSeconds, maxTurns int
	var extraArgs stringList
	flag.StringVar(&inputPath, "input", "", "skill-up SessionInput JSON")
	flag.StringVar(&outputPath, "output", "", "skill-up SessionResult JSON")
	flag.StringVar(&agentName, "agent", "", "Multica provider/runtime name")
	flag.StringVar(&model, "model", "", "model passed directly to the Agent CLI")
	flag.StringVar(&executable, "executable", "", "Agent CLI command or absolute path")
	flag.IntVar(&timeoutSeconds, "timeout-seconds", 1800, "hard run timeout")
	flag.IntVar(&maxTurns, "max-turns", 12, "maximum Agent turns")
	flag.Var(&extraArgs, "extra-arg", "extra Agent CLI argument; repeatable")
	flag.Parse()
	if executable == "" {
		executable = strings.TrimSpace(os.Getenv("AGENT_EVAL_AGENT_EXECUTABLE"))
	}

	fail := func(message string) {
		_ = writeResult(outputPath, sessionResult{ExitCode: 1, Stderr: message})
	}
	if inputPath == "" || outputPath == "" || agentName == "" || executable == "" {
		fail("--input, --output, --agent and --executable are required")
		return
	}
	data, err := os.ReadFile(inputPath)
	if err != nil {
		fail(err.Error())
		return
	}
	var input sessionInput
	if err := json.Unmarshal(data, &input); err != nil {
		fail(err.Error())
		return
	}
	workspace, err := filepath.Abs(input.Workspace)
	if err != nil {
		fail(err.Error())
		return
	}
	resolvedExecutable, err := exec.LookPath(executable)
	if err != nil {
		fail(fmt.Sprintf("Agent executable %q was not found: %v", executable, err))
		return
	}
	provider := normalizeAgent(agentName)
	if err := prepareCaseWorkspace(workspace, provider); err != nil {
		fail(fmt.Sprintf("prepare isolated case workspace: %v", err))
		return
	}
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	backend, err := agent.ResolveBackend(provider, agent.Config{
		ExecutablePath: resolvedExecutable,
		Logger:         logger,
		BuiltinRuntime: true,
	})
	if err != nil {
		fail(err.Error())
		return
	}

	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeoutSeconds)*time.Second)
	defer cancel()
	// SystemPrompt is deliberately empty. No Multica runtime brief, identity,
	// issue workflow, login instruction, or workspace context is injected.
	session, err := backend.Execute(ctx, exactPrompt(input.Messages), agent.ExecOptions{
		Cwd:          workspace,
		Model:        model,
		SystemPrompt: "",
		MaxTurns:     maxTurns,
		Timeout:      time.Duration(timeoutSeconds) * time.Second,
		CustomArgs:   extraArgs,
	})
	if err != nil {
		fail(err.Error())
		return
	}
	transcript := make([]transcriptMessage, 0, len(input.Messages)+8)
	for _, message := range input.Messages {
		transcript = append(transcript, transcriptMessage{Role: message.Role, Content: contentText(message.Content)})
	}
	messageCounts := map[string]int{}
	for message := range session.Messages {
		messageCounts[string(message.Type)]++
		if !includeInTranscript(message) {
			continue
		}
		if message.Type == agent.MessageToolUse {
			transcript = append(transcript, transcriptMessage{
				Role: "tool_call", Turn: 1,
				ToolCall: &toolCallInfo{ID: message.CallID, Name: message.Tool, Arguments: message.Input},
			})
			continue
		}
		if message.Type == agent.MessageToolResult {
			transcript = append(transcript, transcriptMessage{
				Role: "tool_result", Turn: 1,
				ToolResult: &toolResultInfo{CallID: message.CallID, Status: message.Status, Content: message.Output},
			})
			continue
		}
		if message.Type == agent.MessageError {
			transcript = append(transcript, transcriptMessage{Role: "error", Content: message.Content, Turn: 1})
			continue
		}
		content := message.Content
		if message.Type != agent.MessageText {
			event := map[string]any{
				"type": message.Type, "content": message.Content,
				"tool": message.Tool, "call_id": message.CallID,
				"input": message.Input, "output": message.Output,
				"status": message.Status, "session_id": message.SessionID,
			}
			if encoded, marshalErr := json.Marshal(event); marshalErr == nil {
				content = string(encoded)
			}
		}
		transcript = append(transcript, transcriptMessage{Role: "assistant", Content: content, Turn: 1})
	}
	final := <-session.Result
	var inputTokens, outputTokens, cacheReadTokens, cacheWriteTokens int64
	models := make([]string, 0, len(final.Usage))
	for usedModel, usage := range final.Usage {
		models = append(models, usedModel)
		inputTokens += usage.InputTokens
		outputTokens += usage.OutputTokens
		cacheReadTokens += usage.CacheReadTokens
		cacheWriteTokens += usage.CacheWriteTokens
	}
	telemetry := map[string]any{
		"schema_version":     "agent-eval-telemetry-v1",
		"input_tokens":       inputTokens,
		"output_tokens":      outputTokens,
		"cache_read_tokens":  cacheReadTokens,
		"cache_write_tokens": cacheWriteTokens,
		"duration_ms":        final.DurationMs,
		"session_id":         final.SessionID,
		"models":             models,
		"message_counts":     messageCounts,
	}
	transcript = appendTerminalMessages(transcript, telemetry, final.Output)
	exitCode := 1
	if final.Status == "completed" {
		exitCode = 0
	}
	result := sessionResult{
		Engine:       "multica-local",
		Model:        model,
		SessionID:    final.SessionID,
		ExitCode:     exitCode,
		DurationMs:   final.DurationMs,
		FinalMessage: final.Output,
		Turns:        1,
		InputTokens:  inputTokens + cacheReadTokens + cacheWriteTokens,
		OutputTokens: outputTokens,
		Transcript:   transcript,
		Artifacts:    collectArtifacts(workspace),
		Stderr:       final.Error,
	}
	if err := writeResult(outputPath, result); err != nil {
		fmt.Fprintln(os.Stderr, err)
	}
}
