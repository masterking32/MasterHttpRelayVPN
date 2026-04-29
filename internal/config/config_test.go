package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadParsesRelayURLsArray(t *testing.T) {
	dir := t.TempDir()
	configPath := filepath.Join(dir, "client.toml")
	content := `
AES_ENCRYPTION_KEY = "test-key"
RELAY_URLS = ["https://a.example/relay.php", "https://b.example/relay.php"]
RELAY_URL_SELECTION = "round_robin"
`
	if err := os.WriteFile(configPath, []byte(content), 0644); err != nil {
		t.Fatalf("write config: %v", err)
	}

	cfg, err := Load(configPath)
	if err != nil {
		t.Fatalf("load config: %v", err)
	}

	if len(cfg.RelayURLs) != 2 {
		t.Fatalf("expected 2 relay URLs, got %d", len(cfg.RelayURLs))
	}
	if cfg.RelayURLs[0] != "https://a.example/relay.php" || cfg.RelayURLs[1] != "https://b.example/relay.php" {
		t.Fatalf("unexpected relay URLs: %#v", cfg.RelayURLs)
	}
}
