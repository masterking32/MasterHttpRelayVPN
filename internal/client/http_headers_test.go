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
