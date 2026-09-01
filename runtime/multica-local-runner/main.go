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
	Messages  []inputMessage   `json:"messages"`
	Workspace string           `json:"workspace"`
	CaseID    string           `json:"case_id"`
	Variant   string           `json:"variant"`
	Kwargs    map[string]string `json:"kwargs"`
}

type transcriptMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type artifactFile struct {
	Name string `json:"name"`
	Path string `json:"path"`
}

type artifactSet struct {
	Files []artifactFile `json:"files,omitempty"`
}

type sessionResult struct {
	ExitCode     int                 `json:"exit_code"`
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
	for _, directory := range []string{"output", "outputs", "artifacts"} {
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
	for message := range session.Messages {
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
		transcript = append(transcript, transcriptMessage{Role: "assistant", Content: content})
	}
	final := <-session.Result
	var inputTokens, outputTokens int64
	for _, usage := range final.Usage {
		inputTokens += usage.InputTokens
		outputTokens += usage.OutputTokens
	}
	exitCode := 1
	if final.Status == "completed" {
		exitCode = 0
	}
	result := sessionResult{
		ExitCode:     exitCode,
		FinalMessage: final.Output,
		Turns:        1,
		InputTokens:  inputTokens,
		OutputTokens: outputTokens,
		Transcript:   transcript,
		Artifacts:    collectArtifacts(workspace),
		Stderr:       final.Error,
	}
	if err := writeResult(outputPath, result); err != nil {
		fmt.Fprintln(os.Stderr, err)
	}
}
