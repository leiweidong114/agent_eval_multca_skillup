// justdo-http-agent exposes the OpenClaw-compatible JustDo CLI contract over
// JustDo's authenticated HTTP bridge. Multica can execute this binary exactly
// like the local JustDo-agent launcher, while JustDo itself may run on another
// machine.
package main

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type invokeRequest struct {
	Argv           []string                `json:"argv"`
	Cwd            string                  `json:"cwd"`
	Env            map[string]string       `json:"env"`
	WorkspaceFiles map[string]transferFile `json:"workspaceFiles,omitempty"`
	ConfigContent  string                  `json:"configContent,omitempty"`
}

type invokeResponse struct {
	Stdout         string                  `json:"stdout"`
	Stderr         string                  `json:"stderr"`
	ExitCode       int                     `json:"exitCode"`
	Error          string                  `json:"error"`
	WorkspaceFiles map[string]transferFile `json:"workspaceFiles,omitempty"`
}

type transferFile struct {
	Data string `json:"data"`
	Mode uint32 `json:"mode,omitempty"`
}

const maxWorkspaceBytes = 48 << 20
const maxWorkspaceFiles = 5000

func isAgentInvocation(argv []string) bool {
	return len(argv) > 0 && argv[0] == "agent"
}

func collectWorkspace(root string) (map[string]transferFile, error) {
	files := map[string]transferFile{}
	total := int64(0)
	err := filepath.WalkDir(root, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if entry.IsDir() {
			if path != root && (entry.Name() == ".git" || entry.Name() == "node_modules") {
				return filepath.SkipDir
			}
			return nil
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		if !info.Mode().IsRegular() {
			return nil
		}
		if len(files) >= maxWorkspaceFiles {
			return fmt.Errorf("workspace contains more than %d files", maxWorkspaceFiles)
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		total += int64(len(data))
		if total > maxWorkspaceBytes {
			return fmt.Errorf("workspace exceeds %d bytes", maxWorkspaceBytes)
		}
		relative, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		files[filepath.ToSlash(relative)] = transferFile{
			Data: base64.StdEncoding.EncodeToString(data),
			Mode: uint32(info.Mode().Perm()),
		}
		return nil
	})
	return files, err
}

func restoreWorkspace(root string, files map[string]transferFile) error {
	for relative, file := range files {
		clean := filepath.Clean(filepath.FromSlash(relative))
		if clean == "." || filepath.IsAbs(clean) || clean == ".." || strings.HasPrefix(clean, ".."+string(filepath.Separator)) {
			return fmt.Errorf("remote bridge returned unsafe workspace path %q", relative)
		}
		data, err := base64.StdEncoding.DecodeString(file.Data)
		if err != nil {
			return fmt.Errorf("decode %s: %w", relative, err)
		}
		destination := filepath.Join(root, clean)
		if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
			return err
		}
		mode := os.FileMode(file.Mode)
		if mode == 0 {
			mode = 0o600
		}
		if err := os.WriteFile(destination, data, mode); err != nil {
			return err
		}
	}
	return nil
}

func main() {
	baseURL := strings.TrimRight(strings.TrimSpace(os.Getenv("JUSTDO_HTTP_URL")), "/")
	if baseURL == "" {
		fmt.Fprintln(os.Stderr, "JUSTDO_HTTP_URL is required")
		os.Exit(69)
	}
	token := strings.TrimSpace(os.Getenv("JUSTDO_HTTP_TOKEN"))
	if token == "" {
		fmt.Fprintln(os.Stderr, "JUSTDO_HTTP_TOKEN is required")
		os.Exit(69)
	}
	cwd, err := os.Getwd()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(70)
	}
	env := map[string]string{}
	for _, name := range []string{
		"OPENCLAW_CONFIG_PATH", "OPENCLAW_STATE_DIR", "OPENCLAW_INCLUDE_ROOTS", "LITELLM_API_KEY",
		"AGENT_EVAL_PROVIDER_BASE_URL", "AGENT_EVAL_PROVIDER_MODEL",
		"AGENT_EVAL_PROVIDER_PROTOCOL", "AGENT_EVAL_ARTIFACT_DIR", "AGENT_EVAL_RUN_ID",
		"AGENT_EVAL_TASK_ID", "AGENT_EVAL_USER_ID", "AGENT_EVAL_REQUESTED_AGENT",
		"AGENT_EVAL_SUBAGENT_MODEL",
	} {
		if value := strings.TrimSpace(os.Getenv(name)); value != "" && !strings.ContainsAny(value, "\r\n") {
			env[name] = value
		}
	}
	invocation := invokeRequest{Argv: os.Args[1:], Cwd: cwd, Env: env}
	if isAgentInvocation(invocation.Argv) {
		invocation.WorkspaceFiles, err = collectWorkspace(cwd)
		if err != nil {
			fmt.Fprintf(os.Stderr, "prepare JustDo remote workspace: %v\n", err)
			os.Exit(70)
		}
		if configPath := env["OPENCLAW_CONFIG_PATH"]; configPath != "" {
			config, readErr := os.ReadFile(configPath)
			if readErr != nil {
				fmt.Fprintf(os.Stderr, "read OpenClaw config for JustDo: %v\n", readErr)
				os.Exit(70)
			}
			invocation.ConfigContent = string(config)
		}
	}
	payload, err := json.Marshal(invocation)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(70)
	}
	request, err := http.NewRequest(http.MethodPost, baseURL+"/v1/invoke", bytes.NewReader(payload))
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(70)
	}
	request.Header.Set("Authorization", "Bearer "+token)
	request.Header.Set("Content-Type", "application/json")
	client := &http.Client{Timeout: 35 * time.Minute}
	response, err := client.Do(request)
	if err != nil {
		fmt.Fprintf(os.Stderr, "JustDo HTTP bridge unavailable: %v\n", err)
		os.Exit(69)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 70<<20))
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(70)
	}
	var result invokeResponse
	if err := json.Unmarshal(body, &result); err != nil {
		fmt.Fprintf(os.Stderr, "JustDo HTTP bridge returned HTTP %d with invalid JSON\n", response.StatusCode)
		os.Exit(70)
	}
	if response.StatusCode != http.StatusOK {
		fmt.Fprintf(os.Stderr, "JustDo HTTP bridge returned HTTP %d: %s\n", response.StatusCode, result.Error)
		os.Exit(70)
	}
	if len(result.WorkspaceFiles) > 0 {
		if err := restoreWorkspace(cwd, result.WorkspaceFiles); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(70)
		}
	}
	_, _ = io.WriteString(os.Stdout, result.Stdout)
	_, _ = io.WriteString(os.Stderr, result.Stderr)
	if result.ExitCode < 0 || result.ExitCode > 255 {
		os.Exit(70)
	}
	os.Exit(result.ExitCode)
}
