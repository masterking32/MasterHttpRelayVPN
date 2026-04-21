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

	if b.cfg.HTTPHeaderProfile == "browser" {
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
		if referer := b.pickReferer(); referer != "" {
			req.Header.Set("Referer", referer)
		}
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
