// justdo-http-agent exposes the OpenClaw-compatible JustDo CLI contract over
// JustDo's authenticated HTTP bridge. Multica can execute this binary exactly
// like the local JustDo-agent launcher, while JustDo itself may run on another
// machine.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

type invokeRequest struct {
	Argv []string          `json:"argv"`
	Cwd  string            `json:"cwd"`
	Env  map[string]string `json:"env"`
}

type invokeResponse struct {
	Stdout   string `json:"stdout"`
	Stderr   string `json:"stderr"`
	ExitCode int    `json:"exitCode"`
	Error    string `json:"error"`
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
		"OPENCLAW_CONFIG_PATH", "OPENCLAW_INCLUDE_ROOTS", "LITELLM_API_KEY",
		"AGENT_EVAL_PROVIDER_BASE_URL", "AGENT_EVAL_PROVIDER_MODEL",
		"AGENT_EVAL_PROVIDER_PROTOCOL", "AGENT_EVAL_ARTIFACT_DIR",
	} {
		if value := strings.TrimSpace(os.Getenv(name)); value != "" && !strings.ContainsAny(value, "\r\n") {
			env[name] = value
		}
	}
	payload, err := json.Marshal(invokeRequest{Argv: os.Args[1:], Cwd: cwd, Env: env})
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
	body, err := io.ReadAll(io.LimitReader(response.Body, 32<<20))
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
	_, _ = io.WriteString(os.Stdout, result.Stdout)
	_, _ = io.WriteString(os.Stderr, result.Stderr)
	if result.ExitCode < 0 || result.ExitCode > 255 {
		os.Exit(70)
	}
	os.Exit(result.ExitCode)
}
