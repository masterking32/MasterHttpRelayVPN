package client

import (
	"net/url"
	"strings"
	"testing"

	"masterhttprelayvpn/internal/config"
)

func TestBuildRelayURLLeavesURLUntouchedWhenSuffixRandomizationDisabled(t *testing.T) {
	builder := newRelayHeaderBuilder(config.Config{
		RelayURL:                 "https://example.com/relay",
		HTTPRandomizeQuerySuffix: false,
	}, nil)

	got := builder.BuildRelayURL("https://example.com/relay")
	if got != "https://example.com/relay" {
		t.Fatalf("expected relay URL to stay unchanged, got %q", got)
	}
}

func TestBuildRelayURLAddsRandomQuerySuffixWhenEnabled(t *testing.T) {
	builder := newRelayHeaderBuilder(config.Config{
		RelayURL:                 "https://example.com/relay?existing=1",
		HTTPRandomizeQuerySuffix: true,
	}, nil)

	got := builder.BuildRelayURL("https://example.com/relay?existing=1")
	parsed, err := url.Parse(got)
	if err != nil {
		t.Fatalf("parse randomized relay URL: %v", err)
	}

	query := parsed.Query()
	if query.Get("existing") != "1" {
		t.Fatalf("expected existing query parameter to be preserved, got %q", query.Get("existing"))
	}

	randomKeys := []string{"webhe", "r", "_", "cache_bust", "v"}
	found := false
	for _, key := range randomKeys {
		if value := query.Get(key); value != "" {
			found = true
			if strings.TrimSpace(value) == "" {
				t.Fatalf("expected randomized query value for key %q to be non-empty", key)
			}
		}
	}
	if !found {
		t.Fatalf("expected one randomized query suffix key, got query %q", parsed.RawQuery)
	}
}

func TestBuildRefererCandidatesIncludesAllRelayHosts(t *testing.T) {
	cfg := config.Config{
		RelayURLs: []string{
			"https://relay-a.example/relay",
			"https://relay-b.example/relay",
		},
	}

	candidates := buildRefererCandidates(cfg)
	expected := map[string]bool{
		"https://relay-a.example/":           false,
		"https://relay-a.example/index.html": false,
		"https://relay-a.example/home":       false,
		"https://relay-a.example/api/status": false,
		"https://relay-b.example/":           false,
		"https://relay-b.example/index.html": false,
		"https://relay-b.example/home":       false,
		"https://relay-b.example/api/status": false,
	}

	for _, candidate := range candidates {
		if _, ok := expected[candidate]; ok {
			expected[candidate] = true
		}
	}

	for candidate, seen := range expected {
		if !seen {
			t.Fatalf("expected referer candidate %q to be present", candidate)
		}
	}
}
