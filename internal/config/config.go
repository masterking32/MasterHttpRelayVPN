// ==============================================================================
// MasterHttpRelayVPN
// Author: MasterkinG32
// Github: https://github.com/masterking32
// Year: 2026
// ==============================================================================

package config

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
)

type Config struct {
	AESEncryptionKey string
	SOCKSHost        string
	SOCKSPort        int
	SOCKSAuth        bool
	SOCKSUsername    string
	SOCKSPassword    string
	LogLevel         string
}

func Load(path string) (Config, error) {
	cfg := Config{
		SOCKSHost: "127.0.0.1",
		SOCKSPort: 1080,
		LogLevel:  "INFO",
	}

	file, err := os.Open(path)
	if err != nil {
		return Config{}, err
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}

		key, value, ok := strings.Cut(line, "=")
		if !ok {
			return Config{}, fmt.Errorf("invalid config line: %q", line)
		}

		key = strings.TrimSpace(key)
		value = strings.TrimSpace(value)

		switch key {
		case "AES_ENCRYPTION_KEY":
			cfg.AESEncryptionKey = trimString(value)
		case "SOCKS_HOST":
			cfg.SOCKSHost = trimString(value)
		case "SOCKS_PORT":
			port, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse SOCKS_PORT: %w", err)
			}
			cfg.SOCKSPort = port
		case "SOCKS_AUTH":
			auth, err := strconv.ParseBool(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse SOCKS_AUTH: %w", err)
			}
			cfg.SOCKSAuth = auth
		case "SOCKS_USERNAME":
			cfg.SOCKSUsername = trimString(value)
		case "SOCKS_PASSWORD":
			cfg.SOCKSPassword = trimString(value)
		case "LOG_LEVEL":
			cfg.LogLevel = trimString(value)
		}
	}

	if err := scanner.Err(); err != nil {
		return Config{}, err
	}

	if cfg.SOCKSAuth && (cfg.SOCKSUsername == "" || cfg.SOCKSPassword == "") {
		return Config{}, fmt.Errorf("SOCKS auth enabled but username/password missing")
	}

	if cfg.SOCKSPort < 1 || cfg.SOCKSPort > 65535 {
		return Config{}, fmt.Errorf("invalid SOCKS_PORT: %d", cfg.SOCKSPort)
	}

	return cfg, nil
}

func trimString(value string) string {
	return strings.Trim(value, `"`)
}
