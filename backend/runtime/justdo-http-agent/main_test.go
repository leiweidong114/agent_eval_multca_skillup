package main

import (
	"encoding/base64"
	"os"
	"path/filepath"
	"testing"
)

func TestCollectAndRestoreWorkspace(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "input.txt"), []byte("hello"), 0o644); err != nil {
		t.Fatal(err)
	}
	files, err := collectWorkspace(root)
	if err != nil || len(files) != 1 {
		t.Fatalf("collectWorkspace() = %v, %v", files, err)
	}
	files["out/result.txt"] = transferFile{Data: base64.StdEncoding.EncodeToString([]byte("done")), Mode: 0o644}
	if err := restoreWorkspace(root, files); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(root, "out", "result.txt"))
	if err != nil || string(data) != "done" {
		t.Fatalf("restored result = %q, %v", data, err)
	}
}

func TestRestoreRejectsTraversal(t *testing.T) {
	err := restoreWorkspace(t.TempDir(), map[string]transferFile{
		"../escape": {Data: base64.StdEncoding.EncodeToString([]byte("bad"))},
	})
	if err == nil {
		t.Fatal("expected traversal to be rejected")
	}
}
