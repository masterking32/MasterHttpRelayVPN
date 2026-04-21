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
	AESEncryptionKey             string
	RelayURL                     string
	RelayURLs                    []string
	RelayURLSelection            string
	HTTPUserAgentsFile           string
	HTTPHeaderProfile            string
	HTTPRandomizeHeaders         bool
	HTTPRandomizeTransport       bool
	HTTPPaddingHeader            string
	HTTPPaddingMinBytes          int
	HTTPPaddingMaxBytes          int
	HTTPReferer                  string
	HTTPAcceptLanguage           string
	HTTPRandomizeQuerySuffix     bool
	HTTPTimingJitterMS           int
	HTTPIdleConnTimeoutMinMS     int
	HTTPIdleConnTimeoutMaxMS     int
	HTTPTransportReuseMin        int
	HTTPTransportReuseMax        int
	HTTPBatchRandomize           bool
	HTTPBatchPacketsJitter       int
	HTTPBatchBytesJitter         int
	ServerHost                   string
	ServerPort                   int
	SOCKSHost                    string
	SOCKSPort                    int
	SOCKSAuth                    bool
	SOCKSUsername                string
	SOCKSPassword                string
	LogLevel                     string
	MaxChunkSize                 int
	MaxPacketsPerBatch           int
	MaxBatchBytes                int
	WorkerCount                  int
	MaxConcurrentBatches         int
	MaxPacketsPerSOCKSPerBatch   int
	MuxRotateEveryBatches        int
	MuxRotateJitterBatches       int
	MuxBurstThresholdBytes       int
	MuxBurstThresholdJitterBytes int
	HTTPRequestTimeoutMS         int
	WorkerPollIntervalMS         int
	IdlePollIntervalMS           int
	PingIntervalJitterMS         int
	PingWarmThresholdMS          int
	PingBackoffBaseMS            int
	PingBackoffStepMS            int
	PingMaxIntervalMS            int
	MaxQueueBytesPerSOCKS        int
	AckTimeoutMS                 int
	MaxRetryCount                int
	ReorderTimeoutMS             int
	MaxReorderBufferPackets      int
	SessionIdleTimeoutMS         int
	SOCKSIdleTimeoutMS           int
	ReadBodyLimitBytes           int
	MaxServerQueueBytes          int
}

func Load(path string) (Config, error) {
	cfg := Config{
		SOCKSHost:                    "127.0.0.1",
		SOCKSPort:                    1080,
		RelayURLSelection:            "round_robin",
		HTTPUserAgentsFile:           "user-agents.txt",
		HTTPHeaderProfile:            "browser",
		HTTPRandomizeHeaders:         true,
		HTTPRandomizeTransport:       false,
		HTTPPaddingHeader:            "X-Padding",
		HTTPPaddingMinBytes:          16,
		HTTPPaddingMaxBytes:          48,
		HTTPRandomizeQuerySuffix:     false,
		HTTPTimingJitterMS:           50,
		HTTPIdleConnTimeoutMinMS:     15000,
		HTTPIdleConnTimeoutMaxMS:     45000,
		HTTPTransportReuseMin:        8,
		HTTPTransportReuseMax:        24,
		HTTPBatchRandomize:           true,
		HTTPBatchPacketsJitter:       4,
		HTTPBatchBytesJitter:         32768,
		ServerHost:                   "127.0.0.1",
		ServerPort:                   28080,
		LogLevel:                     "INFO",
		MaxChunkSize:                 16 * 1024,
		MaxPacketsPerBatch:           32,
		MaxBatchBytes:                256 * 1024,
		WorkerCount:                  4,
		MaxConcurrentBatches:         4,
		MaxPacketsPerSOCKSPerBatch:   2,
		MuxRotateEveryBatches:        1,
		MuxRotateJitterBatches:       0,
		MuxBurstThresholdBytes:       128 * 1024,
		MuxBurstThresholdJitterBytes: 0,
		HTTPRequestTimeoutMS:         15000,
		WorkerPollIntervalMS:         200,
		IdlePollIntervalMS:           1000,
		PingIntervalJitterMS:         0,
		PingWarmThresholdMS:          5000,
		PingBackoffBaseMS:            5000,
		PingBackoffStepMS:            5000,
		PingMaxIntervalMS:            60000,
		MaxQueueBytesPerSOCKS:        1024 * 1024,
		AckTimeoutMS:                 5000,
		MaxRetryCount:                5,
		ReorderTimeoutMS:             5000,
		MaxReorderBufferPackets:      128,
		SessionIdleTimeoutMS:         5 * 60 * 1000,
		SOCKSIdleTimeoutMS:           2 * 60 * 1000,
		ReadBodyLimitBytes:           2 * 1024 * 1024,
		MaxServerQueueBytes:          2 * 1024 * 1024,
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
		case "RELAY_URLS":
			cfg.RelayURLs = parseStringArray(value)
		case "RELAY_URL_SELECTION":
			cfg.RelayURLSelection = strings.ToLower(trimString(value))
		case "HTTP_USER_AGENTS_FILE":
			cfg.HTTPUserAgentsFile = trimString(value)
		case "HTTP_HEADER_PROFILE":
			cfg.HTTPHeaderProfile = trimString(value)
		case "HTTP_RANDOMIZE_HEADERS":
			randomize, err := strconv.ParseBool(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse HTTP_RANDOMIZE_HEADERS: %w", err)
			}

			cfg.HTTPRandomizeHeaders = randomize
		case "HTTP_RANDOMIZE_TRANSPORT":
			randomize, err := strconv.ParseBool(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse HTTP_RANDOMIZE_TRANSPORT: %w", err)
			}

			cfg.HTTPRandomizeTransport = randomize
		case "HTTP_PADDING_HEADER":
			cfg.HTTPPaddingHeader = trimString(value)
		case "HTTP_PADDING_MIN_BYTES":
			size, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse HTTP_PADDING_MIN_BYTES: %w", err)
			}

			cfg.HTTPPaddingMinBytes = size
		case "HTTP_PADDING_MAX_BYTES":
			size, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse HTTP_PADDING_MAX_BYTES: %w", err)
			}

			cfg.HTTPPaddingMaxBytes = size
		case "HTTP_REFERER":
			cfg.HTTPReferer = trimString(value)
		case "HTTP_ACCEPT_LANGUAGE":
			cfg.HTTPAcceptLanguage = trimString(value)
		case "HTTP_RANDOMIZE_QUERY_SUFFIX":
			randomize, err := strconv.ParseBool(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse HTTP_RANDOMIZE_QUERY_SUFFIX: %w", err)
			}

			cfg.HTTPRandomizeQuerySuffix = randomize
		case "HTTP_TIMING_JITTER_MS":
			valueInt, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse HTTP_TIMING_JITTER_MS: %w", err)
			}

			cfg.HTTPTimingJitterMS = valueInt
		case "HTTP_IDLE_CONN_TIMEOUT_MIN_MS":
			valueInt, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse HTTP_IDLE_CONN_TIMEOUT_MIN_MS: %w", err)
			}

			cfg.HTTPIdleConnTimeoutMinMS = valueInt
		case "HTTP_IDLE_CONN_TIMEOUT_MAX_MS":
			valueInt, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse HTTP_IDLE_CONN_TIMEOUT_MAX_MS: %w", err)
			}

			cfg.HTTPIdleConnTimeoutMaxMS = valueInt
		case "HTTP_TRANSPORT_REUSE_MIN":
			valueInt, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse HTTP_TRANSPORT_REUSE_MIN: %w", err)
			}

			cfg.HTTPTransportReuseMin = valueInt
		case "HTTP_TRANSPORT_REUSE_MAX":
			valueInt, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse HTTP_TRANSPORT_REUSE_MAX: %w", err)
			}

			cfg.HTTPTransportReuseMax = valueInt
		case "HTTP_BATCH_RANDOMIZE":
			randomize, err := strconv.ParseBool(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse HTTP_BATCH_RANDOMIZE: %w", err)
			}

			cfg.HTTPBatchRandomize = randomize
		case "HTTP_BATCH_PACKETS_JITTER":
			valueInt, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse HTTP_BATCH_PACKETS_JITTER: %w", err)
			}

			cfg.HTTPBatchPacketsJitter = valueInt
		case "HTTP_BATCH_BYTES_JITTER":
			valueInt, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse HTTP_BATCH_BYTES_JITTER: %w", err)
			}

			cfg.HTTPBatchBytesJitter = valueInt
		case "SERVER_HOST":
			cfg.ServerHost = trimString(value)
		case "SERVER_PORT":
			port, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse SERVER_PORT: %w", err)
			}

			cfg.ServerPort = port
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
		case "MAX_CONCURRENT_BATCHES":
			count, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse MAX_CONCURRENT_BATCHES: %w", err)
			}

			cfg.MaxConcurrentBatches = count
		case "MAX_PACKETS_PER_SOCKS_PER_BATCH":
			count, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse MAX_PACKETS_PER_SOCKS_PER_BATCH: %w", err)
			}

			cfg.MaxPacketsPerSOCKSPerBatch = count
		case "MUX_ROTATE_EVERY_BATCHES":
			count, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse MUX_ROTATE_EVERY_BATCHES: %w", err)
			}

			cfg.MuxRotateEveryBatches = count
		case "MUX_ROTATE_JITTER_BATCHES":
			count, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse MUX_ROTATE_JITTER_BATCHES: %w", err)
			}

			cfg.MuxRotateJitterBatches = count
		case "MUX_BURST_THRESHOLD_BYTES":
			size, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse MUX_BURST_THRESHOLD_BYTES: %w", err)
			}

			cfg.MuxBurstThresholdBytes = size
		case "MUX_BURST_THRESHOLD_JITTER_BYTES":
			size, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse MUX_BURST_THRESHOLD_JITTER_BYTES: %w", err)
			}

			cfg.MuxBurstThresholdJitterBytes = size
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
		case "IDLE_POLL_INTERVAL_MS":
			interval, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse IDLE_POLL_INTERVAL_MS: %w", err)
			}

			cfg.IdlePollIntervalMS = interval
		case "PING_INTERVAL_JITTER_MS":
			interval, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse PING_INTERVAL_JITTER_MS: %w", err)
			}

			cfg.PingIntervalJitterMS = interval
		case "PING_WARM_THRESHOLD_MS":
			threshold, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse PING_WARM_THRESHOLD_MS: %w", err)
			}

			cfg.PingWarmThresholdMS = threshold
		case "PING_BACKOFF_BASE_MS":
			interval, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse PING_BACKOFF_BASE_MS: %w", err)
			}

			cfg.PingBackoffBaseMS = interval
		case "PING_BACKOFF_STEP_MS":
			interval, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse PING_BACKOFF_STEP_MS: %w", err)
			}

			cfg.PingBackoffStepMS = interval
		case "PING_MAX_INTERVAL_MS":
			interval, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse PING_MAX_INTERVAL_MS: %w", err)
			}

			cfg.PingMaxIntervalMS = interval
		case "MAX_QUEUE_BYTES_PER_SOCKS":
			size, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse MAX_QUEUE_BYTES_PER_SOCKS: %w", err)
			}

			cfg.MaxQueueBytesPerSOCKS = size
		case "ACK_TIMEOUT_MS":
			timeout, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse ACK_TIMEOUT_MS: %w", err)
			}

			cfg.AckTimeoutMS = timeout
		case "MAX_RETRY_COUNT":
			count, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse MAX_RETRY_COUNT: %w", err)
			}

			cfg.MaxRetryCount = count
		case "REORDER_TIMEOUT_MS":
			timeout, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse REORDER_TIMEOUT_MS: %w", err)
			}

			cfg.ReorderTimeoutMS = timeout
		case "MAX_REORDER_BUFFER_PACKETS":
			count, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse MAX_REORDER_BUFFER_PACKETS: %w", err)
			}

			cfg.MaxReorderBufferPackets = count
		case "SESSION_IDLE_TIMEOUT_MS":
			timeout, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse SESSION_IDLE_TIMEOUT_MS: %w", err)
			}
			cfg.SessionIdleTimeoutMS = timeout
		case "SOCKS_IDLE_TIMEOUT_MS":
			timeout, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse SOCKS_IDLE_TIMEOUT_MS: %w", err)
			}

			cfg.SOCKSIdleTimeoutMS = timeout
		case "READ_BODY_LIMIT_BYTES":
			size, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse READ_BODY_LIMIT_BYTES: %w", err)
			}

			cfg.ReadBodyLimitBytes = size
		case "MAX_SERVER_QUEUE_BYTES":
			size, err := strconv.Atoi(value)
			if err != nil {
				return Config{}, fmt.Errorf("parse MAX_SERVER_QUEUE_BYTES: %w", err)
			}

			cfg.MaxServerQueueBytes = size
		}
	}

	if err := scanner.Err(); err != nil {
		return Config{}, err
	}

	return cfg, nil
}

func (c Config) ValidateClient() error {
	if err := c.validateShared(); err != nil {
		return err
	}

	if c.SOCKSAuth && (c.SOCKSUsername == "" || c.SOCKSPassword == "") {
		return fmt.Errorf("SOCKS auth enabled but username/password missing")
	}

	if c.SOCKSPort < 1 || c.SOCKSPort > 65535 {
		return fmt.Errorf("invalid SOCKS_PORT: %d", c.SOCKSPort)
	}

	if strings.TrimSpace(c.RelayURL) == "" {
		if len(c.RelayURLs) == 0 {
			return fmt.Errorf("RELAY_URL or RELAY_URLS is required")
		}
	}

	relayURLs := c.RelayEndpointURLs()
	if len(relayURLs) == 0 {
		return fmt.Errorf("at least one relay URL is required")
	}

	if c.RelayURLSelection != "round_robin" && c.RelayURLSelection != "random" {
		return fmt.Errorf("invalid RELAY_URL_SELECTION: %s", c.RelayURLSelection)
	}

	if c.HTTPRequestTimeoutMS < 1 {
		return fmt.Errorf("invalid HTTP_REQUEST_TIMEOUT_MS: %d", c.HTTPRequestTimeoutMS)
	}

	if c.MaxConcurrentBatches < 1 {
		return fmt.Errorf("invalid MAX_CONCURRENT_BATCHES: %d", c.MaxConcurrentBatches)
	}

	if c.MaxConcurrentBatches > c.WorkerCount {
		return fmt.Errorf("MAX_CONCURRENT_BATCHES must be <= WORKER_COUNT")
	}

	if c.MaxPacketsPerSOCKSPerBatch < 1 {
		return fmt.Errorf("invalid MAX_PACKETS_PER_SOCKS_PER_BATCH: %d", c.MaxPacketsPerSOCKSPerBatch)
	}

	if c.MuxRotateEveryBatches < 1 {
		return fmt.Errorf("invalid MUX_ROTATE_EVERY_BATCHES: %d", c.MuxRotateEveryBatches)
	}

	if c.MuxRotateJitterBatches < 0 {
		return fmt.Errorf("invalid MUX_ROTATE_JITTER_BATCHES: %d", c.MuxRotateJitterBatches)
	}

	if c.MuxBurstThresholdBytes < c.MaxChunkSize {
		return fmt.Errorf("MUX_BURST_THRESHOLD_BYTES must be >= MAX_CHUNK_SIZE")
	}

	if c.MuxBurstThresholdJitterBytes < 0 {
		return fmt.Errorf("invalid MUX_BURST_THRESHOLD_JITTER_BYTES: %d", c.MuxBurstThresholdJitterBytes)
	}

	if c.WorkerPollIntervalMS < 1 {
		return fmt.Errorf("invalid WORKER_POLL_INTERVAL_MS: %d", c.WorkerPollIntervalMS)
	}

	if c.IdlePollIntervalMS < c.WorkerPollIntervalMS {
		return fmt.Errorf("IDLE_POLL_INTERVAL_MS must be >= WORKER_POLL_INTERVAL_MS")
	}

	if c.PingIntervalJitterMS < 0 {
		return fmt.Errorf("invalid PING_INTERVAL_JITTER_MS: %d", c.PingIntervalJitterMS)
	}

	if c.PingWarmThresholdMS < 1 {
		return fmt.Errorf("invalid PING_WARM_THRESHOLD_MS: %d", c.PingWarmThresholdMS)
	}

	if c.PingBackoffBaseMS < c.IdlePollIntervalMS {
		return fmt.Errorf("PING_BACKOFF_BASE_MS must be >= IDLE_POLL_INTERVAL_MS")
	}

	if c.PingBackoffStepMS < 1 {
		return fmt.Errorf("invalid PING_BACKOFF_STEP_MS: %d", c.PingBackoffStepMS)
	}

	if c.PingMaxIntervalMS < c.PingBackoffBaseMS {
		return fmt.Errorf("PING_MAX_INTERVAL_MS must be >= PING_BACKOFF_BASE_MS")
	}

	if c.AckTimeoutMS < 1 {
		return fmt.Errorf("invalid ACK_TIMEOUT_MS: %d", c.AckTimeoutMS)
	}

	if c.MaxRetryCount < 0 {
		return fmt.Errorf("invalid MAX_RETRY_COUNT: %d", c.MaxRetryCount)
	}

	if c.ReorderTimeoutMS < 1 {
		return fmt.Errorf("invalid REORDER_TIMEOUT_MS: %d", c.ReorderTimeoutMS)
	}

	if c.MaxReorderBufferPackets < 1 {
		return fmt.Errorf("invalid MAX_REORDER_BUFFER_PACKETS: %d", c.MaxReorderBufferPackets)
	}

	if c.HTTPHeaderProfile != "browser" && c.HTTPHeaderProfile != "cdn" && c.HTTPHeaderProfile != "api" && c.HTTPHeaderProfile != "minimal" {
		return fmt.Errorf("invalid HTTP_HEADER_PROFILE: %s", c.HTTPHeaderProfile)
	}

	if c.HTTPPaddingMinBytes < 0 {
		return fmt.Errorf("invalid HTTP_PADDING_MIN_BYTES: %d", c.HTTPPaddingMinBytes)
	}

	if c.HTTPPaddingMaxBytes < c.HTTPPaddingMinBytes {
		return fmt.Errorf("HTTP_PADDING_MAX_BYTES must be >= HTTP_PADDING_MIN_BYTES")
	}

	if c.HTTPTimingJitterMS < 0 {
		return fmt.Errorf("invalid HTTP_TIMING_JITTER_MS: %d", c.HTTPTimingJitterMS)
	}

	if c.HTTPIdleConnTimeoutMinMS < 1 {
		return fmt.Errorf("invalid HTTP_IDLE_CONN_TIMEOUT_MIN_MS: %d", c.HTTPIdleConnTimeoutMinMS)
	}

	if c.HTTPIdleConnTimeoutMaxMS < c.HTTPIdleConnTimeoutMinMS {
		return fmt.Errorf("HTTP_IDLE_CONN_TIMEOUT_MAX_MS must be >= HTTP_IDLE_CONN_TIMEOUT_MIN_MS")
	}

	if c.HTTPTransportReuseMin < 1 {
		return fmt.Errorf("invalid HTTP_TRANSPORT_REUSE_MIN: %d", c.HTTPTransportReuseMin)
	}

	if c.HTTPTransportReuseMax < c.HTTPTransportReuseMin {
		return fmt.Errorf("HTTP_TRANSPORT_REUSE_MAX must be >= HTTP_TRANSPORT_REUSE_MIN")
	}

	if c.HTTPBatchPacketsJitter < 0 {
		return fmt.Errorf("invalid HTTP_BATCH_PACKETS_JITTER: %d", c.HTTPBatchPacketsJitter)
	}

	if c.HTTPBatchBytesJitter < 0 {
		return fmt.Errorf("invalid HTTP_BATCH_BYTES_JITTER: %d", c.HTTPBatchBytesJitter)
	}

	if c.MaxQueueBytesPerSOCKS < c.MaxChunkSize {
		return fmt.Errorf("MAX_QUEUE_BYTES_PER_SOCKS must be >= MAX_CHUNK_SIZE")
	}

	return nil
}

func (c Config) ValidateServer() error {
	if err := c.validateShared(); err != nil {
		return err
	}

	if c.ServerPort < 1 || c.ServerPort > 65535 {
		return fmt.Errorf("invalid SERVER_PORT: %d", c.ServerPort)
	}

	if c.SessionIdleTimeoutMS < 1 {
		return fmt.Errorf("invalid SESSION_IDLE_TIMEOUT_MS: %d", c.SessionIdleTimeoutMS)
	}

	if c.SOCKSIdleTimeoutMS < 1 {
		return fmt.Errorf("invalid SOCKS_IDLE_TIMEOUT_MS: %d", c.SOCKSIdleTimeoutMS)
	}

	if c.ReorderTimeoutMS < 1 {
		return fmt.Errorf("invalid REORDER_TIMEOUT_MS: %d", c.ReorderTimeoutMS)
	}

	if c.MaxReorderBufferPackets < 1 {
		return fmt.Errorf("invalid MAX_REORDER_BUFFER_PACKETS: %d", c.MaxReorderBufferPackets)
	}

	if c.ReadBodyLimitBytes < c.MaxChunkSize {
		return fmt.Errorf("READ_BODY_LIMIT_BYTES must be >= MAX_CHUNK_SIZE")
	}

	if c.MaxServerQueueBytes < c.MaxChunkSize {
		return fmt.Errorf("MAX_SERVER_QUEUE_BYTES must be >= MAX_CHUNK_SIZE")
	}

	return nil
}

func (c Config) RelayEndpointURLs() []string {
	urls := make([]string, 0, len(c.RelayURLs)+1)
	for _, relayURL := range c.RelayURLs {
		relayURL = strings.TrimSpace(relayURL)
		if relayURL == "" {
			continue
		}
		urls = append(urls, relayURL)
	}
	if len(urls) > 0 {
		return urls
	}
	if relayURL := strings.TrimSpace(c.RelayURL); relayURL != "" {
		return []string{relayURL}
	}
	return nil
}

func parseCommaSeparatedStrings(raw string) []string {
	if strings.TrimSpace(raw) == "" {
		return nil
	}

	parts := strings.Split(raw, ",")
	values := make([]string, 0, len(parts))
	for _, part := range parts {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		values = append(values, part)
	}
	return values
}

func parseStringArray(raw string) []string {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil
	}

	if strings.HasPrefix(raw, "[") && strings.HasSuffix(raw, "]") {
		body := strings.TrimSpace(strings.TrimSuffix(strings.TrimPrefix(raw, "["), "]"))
		if body == "" {
			return nil
		}

		parts := strings.Split(body, ",")
		values := make([]string, 0, len(parts))
		for _, part := range parts {
			part = trimString(strings.TrimSpace(part))
			if part == "" {
				continue
			}
			values = append(values, part)
		}
		return values
	}

	return parseCommaSeparatedStrings(trimString(raw))
}

func (c Config) validateShared() error {
	if strings.TrimSpace(c.AESEncryptionKey) == "" {
		return fmt.Errorf("AES_ENCRYPTION_KEY is required")
	}

	if c.MaxChunkSize < 1 {
		return fmt.Errorf("invalid MAX_CHUNK_SIZE: %d", c.MaxChunkSize)
	}

	if c.MaxPacketsPerBatch < 1 {
		return fmt.Errorf("invalid MAX_PACKETS_PER_BATCH: %d", c.MaxPacketsPerBatch)
	}

	if c.MaxBatchBytes < c.MaxChunkSize {
		return fmt.Errorf("MAX_BATCH_BYTES must be >= MAX_CHUNK_SIZE")
	}

	if c.WorkerCount < 1 {
		return fmt.Errorf("invalid WORKER_COUNT: %d", c.WorkerCount)
	}

	return nil
}

func trimString(value string) string {
	return strings.Trim(value, `"`)
}
