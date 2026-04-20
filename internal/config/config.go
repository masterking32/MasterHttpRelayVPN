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
	AESEncryptionKey      string
	RelayURL              string
	SOCKSHost             string
	SOCKSPort             int
	SOCKSAuth             bool
	SOCKSUsername         string
	SOCKSPassword         string
	LogLevel              string
	MaxChunkSize          int
	MaxPacketsPerBatch    int
	MaxBatchBytes         int
	WorkerCount           int
	HTTPRequestTimeoutMS  int
	WorkerPollIntervalMS  int
	MaxQueueBytesPerSOCKS int
}

func Load(path string) (Config, error) {
	cfg := Config{
		SOCKSHost:             "127.0.0.1",
		SOCKSPort:             1080,
		LogLevel:              "INFO",
		MaxChunkSize:          16 * 1024,
		MaxPacketsPerBatch:    32,
		MaxBatchBytes:         256 * 1024,
		WorkerCount:           4,
		HTTPRequestTimeoutMS:  15000,
		WorkerPollIntervalMS:  200,
		MaxQueueBytesPerSOCKS: 1024 * 1024,
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
		case "RELAY_URL":
			cfg.RelayURL = trimString(value)
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
		case "MAX_CHUNK_SIZE":
			size, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse MAX_CHUNK_SIZE: %w", err)
			}
			cfg.MaxChunkSize = size
		case "MAX_PACKETS_PER_BATCH":
			count, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse MAX_PACKETS_PER_BATCH: %w", err)
			}
			cfg.MaxPacketsPerBatch = count
		case "MAX_BATCH_BYTES":
			size, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse MAX_BATCH_BYTES: %w", err)
			}
			cfg.MaxBatchBytes = size
		case "WORKER_COUNT":
			count, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse WORKER_COUNT: %w", err)
			}
			cfg.WorkerCount = count
		case "HTTP_REQUEST_TIMEOUT_MS":
			timeout, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse HTTP_REQUEST_TIMEOUT_MS: %w", err)
			}
			cfg.HTTPRequestTimeoutMS = timeout
		case "WORKER_POLL_INTERVAL_MS":
			interval, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse WORKER_POLL_INTERVAL_MS: %w", err)
			}
			cfg.WorkerPollIntervalMS = interval
		case "MAX_QUEUE_BYTES_PER_SOCKS":
			size, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse MAX_QUEUE_BYTES_PER_SOCKS: %w", err)
			}
			cfg.MaxQueueBytesPerSOCKS = size
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
	if strings.TrimSpace(cfg.RelayURL) == "" {
		return Config{}, fmt.Errorf("RELAY_URL is required")
	}
	if strings.TrimSpace(cfg.AESEncryptionKey) == "" {
		return Config{}, fmt.Errorf("AES_ENCRYPTION_KEY is required")
	}

	if cfg.MaxChunkSize < 1 {
		return Config{}, fmt.Errorf("invalid MAX_CHUNK_SIZE: %d", cfg.MaxChunkSize)
	}

	if cfg.MaxPacketsPerBatch < 1 {
		return Config{}, fmt.Errorf("invalid MAX_PACKETS_PER_BATCH: %d", cfg.MaxPacketsPerBatch)
	}

	if cfg.MaxBatchBytes < cfg.MaxChunkSize {
		return Config{}, fmt.Errorf("MAX_BATCH_BYTES must be >= MAX_CHUNK_SIZE")
	}

	if cfg.WorkerCount < 1 {
		return Config{}, fmt.Errorf("invalid WORKER_COUNT: %d", cfg.WorkerCount)
	}
	if cfg.HTTPRequestTimeoutMS < 1 {
		return Config{}, fmt.Errorf("invalid HTTP_REQUEST_TIMEOUT_MS: %d", cfg.HTTPRequestTimeoutMS)
	}
	if cfg.WorkerPollIntervalMS < 1 {
		return Config{}, fmt.Errorf("invalid WORKER_POLL_INTERVAL_MS: %d", cfg.WorkerPollIntervalMS)
	}

	if cfg.MaxQueueBytesPerSOCKS < cfg.MaxChunkSize {
		return Config{}, fmt.Errorf("MAX_QUEUE_BYTES_PER_SOCKS must be >= MAX_CHUNK_SIZE")
	}

	return cfg, nil
}

func trimString(value string) string {
	return strings.Trim(value, `"`)
}
