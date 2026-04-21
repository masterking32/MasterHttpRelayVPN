// ==============================================================================
// MasterHttpRelayVPN
// Author: MasterkinG32
// Github: https://github.com/masterking32
// Year: 2026
// ==============================================================================
package client

import (
	"bufio"
	"crypto/rand"
	"encoding/hex"
	"math/big"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"

	"masterhttprelayvpn/internal/config"
	"masterhttprelayvpn/internal/logger"
)

type relayHeaderBuilder struct {
	cfg               config.Config
	userAgents        []string
	refererCandidates []string
}

func newRelayHeaderBuilder(cfg config.Config, log *logger.Logger) *relayHeaderBuilder {
	builder := &relayHeaderBuilder{
		cfg:        cfg,
		userAgents: loadUserAgents(cfg.HTTPUserAgentsFile, log),
	}

	builder.refererCandidates = buildRefererCandidates(cfg)
	return builder
}

func (b *relayHeaderBuilder) Apply(req *http.Request) {
	if ua := b.pickUserAgent(); ua != "" {
		req.Header.Set("User-Agent", ua)
	}

	switch b.cfg.HTTPHeaderProfile {
	case "browser":
		b.applyBrowserProfile(req)
	case "cdn":
		b.applyCDNProfile(req)
	case "api":
		b.applyAPIProfile(req)
	}

	if b.cfg.HTTPRandomizeHeaders {
		padding := randomPadding(b.cfg.HTTPPaddingMinBytes, b.cfg.HTTPPaddingMaxBytes)
		if padding != "" {
			headerName := strings.TrimSpace(b.cfg.HTTPPaddingHeader)
			if headerName == "" {
				headerName = "X-Padding"
			}
			req.Header.Set(headerName, padding)
		}

		req.Header.Set("X-Request-Nonce", randomHex(8))
	}
}

func (b *relayHeaderBuilder) BuildRelayURL(rawURL string) string {
	if !b.cfg.HTTPRandomizeQuerySuffix {
		return rawURL
	}

	parsed, err := url.Parse(rawURL)
	if err != nil {
		return rawURL
	}

	query := parsed.Query()
	key, value := b.randomQuerySuffix()
	if key == "" || value == "" {
		return rawURL
	}
	query.Set(key, value)
	parsed.RawQuery = query.Encode()
	return parsed.String()
}

func (b *relayHeaderBuilder) applyBrowserProfile(req *http.Request) {
	req.Header.Set("Accept", pickRandomString(
		"*/*",
		"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
		"application/json,text/plain,*/*",
	))
	req.Header.Set("Accept-Language", b.pickAcceptLanguage())
	req.Header.Set("Cache-Control", pickRandomString("no-cache", "max-age=0", "no-store"))
	req.Header.Set("Pragma", "no-cache")
	req.Header.Set("Sec-Fetch-Dest", "empty")
	req.Header.Set("Sec-Fetch-Mode", "cors")
	req.Header.Set("Sec-Fetch-Site", pickRandomString("same-origin", "same-site", "cross-site"))
	req.Header.Set("Priority", pickRandomString("u=0, i", "u=1, i"))
	if maybeTrue() {
		req.Header.Set("DNT", "1")
	}
	if referer := b.pickReferer(); referer != "" {
		req.Header.Set("Referer", referer)
	}
}

func (b *relayHeaderBuilder) applyCDNProfile(req *http.Request) {
	req.Header.Set("Accept", pickRandomString("*/*", "application/octet-stream,*/*", "application/json,*/*"))
	req.Header.Set("Accept-Language", b.pickAcceptLanguage())
	req.Header.Set("Cache-Control", pickRandomString("no-store", "no-cache", "max-age=0"))
	req.Header.Set("Pragma", "no-cache")
	req.Header.Set("X-Requested-With", pickRandomString("XMLHttpRequest", "Fetch"))
	req.Header.Set("Sec-Fetch-Dest", "empty")
	req.Header.Set("Sec-Fetch-Mode", pickRandomString("cors", "same-origin"))
	req.Header.Set("Sec-Fetch-Site", pickRandomString("same-origin", "same-site"))
	if referer := b.pickReferer(); referer != "" {
		req.Header.Set("Referer", referer)
	}
}

func (b *relayHeaderBuilder) applyAPIProfile(req *http.Request) {
	req.Header.Set("Accept", pickRandomString("application/json", "application/octet-stream", "application/json,text/plain,*/*"))
	req.Header.Set("Cache-Control", pickRandomString("no-store", "no-cache"))
	req.Header.Set("Pragma", "no-cache")
	req.Header.Set("X-Requested-With", pickRandomString("XMLHttpRequest", "APIClient"))
	if maybeTrue() {
		req.Header.Set("X-Requested-At", randomHex(6))
	}
	if b.cfg.HTTPAcceptLanguage != "" {
		req.Header.Set("Accept-Language", b.pickAcceptLanguage())
	}
	if referer := b.pickReferer(); referer != "" && maybeTrue() {
		req.Header.Set("Referer", referer)
	}
}

func (b *relayHeaderBuilder) pickUserAgent() string {
	if len(b.userAgents) == 0 {
		return ""
	}

	return b.userAgents[randomIndex(len(b.userAgents))]
}

func (b *relayHeaderBuilder) pickReferer() string {
	if len(b.refererCandidates) == 0 {
		return ""
	}

	return b.refererCandidates[randomIndex(len(b.refererCandidates))]
}

func (b *relayHeaderBuilder) pickAcceptLanguage() string {
	if strings.TrimSpace(b.cfg.HTTPAcceptLanguage) != "" {
		return b.cfg.HTTPAcceptLanguage
	}

	return pickRandomString(
		"en-US,en;q=0.9",
		"en-GB,en;q=0.9",
		"fa-IR,fa;q=0.9,en-US;q=0.7,en;q=0.6",
		"de-DE,de;q=0.9,en-US;q=0.7,en;q=0.6",
	)
}

func loadUserAgents(path string, log *logger.Logger) []string {
	userAgents := readUserAgentsFromFile(path)
	if len(userAgents) > 0 {
		return userAgents
	}

	if log != nil {
		log.Warnf("<yellow>user agents file <cyan>%s</cyan> not found or empty, using built-in defaults</yellow>", path)
	}

	return []string{
		"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
		"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0",
		"Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
	}
}

func readUserAgentsFromFile(path string) []string {
	if strings.TrimSpace(path) == "" {
		return nil
	}

	candidates := []string{path}
	if !filepath.IsAbs(path) {
		candidates = append(candidates, filepath.Join(".", path))
	}

	for _, candidate := range candidates {
		file, err := os.Open(candidate)
		if err != nil {
			continue
		}

		userAgents := make([]string, 0, 16)
		scanner := bufio.NewScanner(file)
		for scanner.Scan() {
			line := strings.TrimSpace(scanner.Text())
			if line == "" || strings.HasPrefix(line, "#") {
				continue
			}
			userAgents = append(userAgents, line)
		}

		if len(userAgents) > 0 {
			_ = file.Close()
			return userAgents
		}

		_ = file.Close()
	}

	return nil
}

func buildRefererCandidates(cfg config.Config) []string {
	if strings.TrimSpace(cfg.HTTPReferer) != "" {
		return []string{cfg.HTTPReferer}
	}

	relayURL, err := url.Parse(cfg.RelayURL)
	if err != nil || relayURL.Scheme == "" || relayURL.Host == "" {
		return nil
	}

	base := relayURL.Scheme + "://" + relayURL.Host
	return []string{
		base + "/",
		base + "/index.html",
		base + "/home",
		base + "/api/status",
	}
}

func pickRandomString(values ...string) string {
	if len(values) == 0 {
		return ""
	}

	return values[randomIndex(len(values))]
}

func randomIndex(length int) int {
	if length <= 1 {
		return 0
	}

	n, err := rand.Int(rand.Reader, big.NewInt(int64(length)))
	if err != nil {
		return 0
	}

	return int(n.Int64())
}

func randomPadding(minBytes int, maxBytes int) string {
	if maxBytes <= 0 || maxBytes < minBytes {
		return ""
	}

	length := minBytes
	if maxBytes > minBytes {
		length += randomIndex(maxBytes - minBytes + 1)
	}

	if length <= 0 {
		return ""
	}

	raw := make([]byte, (length+1)/2)
	if _, err := rand.Read(raw); err != nil {
		return ""
	}

	padding := hex.EncodeToString(raw)
	if len(padding) > length {
		padding = padding[:length]
	}

	return padding
}

func maybeTrue() bool {
	return randomIndex(2) == 0
}

func randomHex(byteCount int) string {
	if byteCount <= 0 {
		return ""
	}

	raw := make([]byte, byteCount)
	if _, err := rand.Read(raw); err != nil {
		return ""
	}

	return hex.EncodeToString(raw)
}

func (b *relayHeaderBuilder) randomQuerySuffix() (string, string) {
	patterns := []struct {
		key   string
		value func() string
	}{
		{key: "webhe", value: func() string { return randomTokenPattern(6, 8, 10) }},
		{key: "r", value: func() string { return randomHex(12) }},
		{key: "_", value: func() string { return randomAlphaNumeric(18) }},
		{key: "cache_bust", value: func() string { return randomTokenPattern(8, 6, 8) }},
		{key: "v", value: func() string { return randomTokenPattern(4, 4, 6) }},
	}

	pattern := patterns[randomIndex(len(patterns))]
	return pattern.key, pattern.value()
}

func randomTokenPattern(parts ...int) string {
	if len(parts) == 0 {
		return ""
	}

	values := make([]string, 0, len(parts))
	for _, partLength := range parts {
		values = append(values, randomAlphaNumeric(partLength))
	}
	return strings.Join(values, "-")
}

func randomAlphaNumeric(length int) string {
	if length <= 0 {
		return ""
	}

	const alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
	var builder strings.Builder
	builder.Grow(length)
	for i := 0; i < length; i++ {
		builder.WriteByte(alphabet[randomIndex(len(alphabet))])
	}
	return builder.String()
}
